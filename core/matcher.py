"""
Trigger / Alias / Fuzzy matching engine with confidence scores.

Match order (per master spec):
  1. Exact trigger        -> 1.00
  2. Exact alias          -> 0.98
  3. Normalized alias     -> 0.95
  4. Keyword match        -> 0.90 (multi-word query contains trigger token)
  5. Fuzzy match          -> 0.82+ (RapidFuzz WRatio on aliases & triggers)
  6. Below threshold      -> None (rejected, never auto-used)

The index is loaded from SQLite once and refreshed with a TTL.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from core.parser import normalize, tokens

logger = logging.getLogger(__name__)

CONF_EXACT = 1.00
CONF_ALIAS = 0.98
CONF_NORMALIZED_ALIAS = 0.95
CONF_KEYWORD = 0.90


@dataclass(slots=True)
class MatchResult:
    trigger_id: int
    trigger: str
    category: str
    matched_text: str          # which trigger/alias text matched
    match_type: str            # EXACT / ALIAS / NORMALIZED_ALIAS / KEYWORD / FUZZY
    confidence: float

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.82


@dataclass(slots=True)
class _IndexEntry:
    trigger_id: int
    trigger: str
    category: str
    aliases: list[str]         # as stored
    normalized_aliases: list[str]


class Matcher:
    def __init__(self, db, threshold: float = 0.82, refresh_seconds: int = 60):
        self.db = db
        self.threshold = threshold
        self.refresh_seconds = refresh_seconds
        self._index: dict[str, _IndexEntry] = {}   # key: trigger text
        self._alias_map: dict[str, tuple[int, str]] = {}   # alias -> (trigger_id, trigger)
        self._nalias_map: dict[str, tuple[int, str]] = {}  # normalized alias -> (...)
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> None:
        async with self._lock:
            if time.monotonic() - self._loaded_at < self.refresh_seconds:
                return
            rows = await self.db.fetchall(
                """
                SELECT t.id, t.trigger, t.category, t.enabled,
                       GROUP_CONCAT(a.alias, '\x1f') AS aliases
                FROM triggers t
                LEFT JOIN aliases a ON a.trigger_id = t.id
                WHERE t.enabled = 1
                GROUP BY t.id
                """
            )
            index: dict[str, _IndexEntry] = {}
            alias_map: dict[str, tuple[int, str]] = {}
            nalias_map: dict[str, tuple[int, str]] = {}
            for r in rows:
                aliases = (r["aliases"] or "").split("\x1f") if r["aliases"] else []
                entry = _IndexEntry(
                    trigger_id=r["id"],
                    trigger=r["trigger"],
                    category=r["category"],
                    aliases=aliases,
                    normalized_aliases=[normalize(a) for a in aliases],
                )
                index[entry.trigger.lower()] = entry
                for a in aliases:
                    alias_map.setdefault(a.lower(), (r["id"], r["trigger"]))
                for na in entry.normalized_aliases:
                    nalias_map.setdefault(na, (r["id"], r["trigger"]))
            self._index, self._alias_map, self._nalias_map = index, alias_map, nalias_map
            self._loaded_at = time.monotonic()
            logger.info("Matcher index loaded: %d triggers, %d aliases",
                        len(index), len(alias_map))

    def force_reload(self) -> None:
        """Called by admin training commands so new methods are instantly live."""
        self._loaded_at = 0.0

    async def match(self, raw_text: str) -> MatchResult | None:
        await self._ensure_loaded()
        if not raw_text:
            return None

        norm = normalize(raw_text)
        if not norm:
            return None

        # 1. Exact trigger
        entry = self._index.get(norm) or self._index.get(norm.lower())
        if entry:
            return MatchResult(entry.trigger_id, entry.trigger, entry.category,
                               norm, "EXACT", CONF_EXACT)

        # 2. Exact alias
        hit = self._alias_map.get(norm) or self._alias_map.get(norm.lower())
        if hit:
            tid, trig = hit
            e = self._index[trig.lower()]
            return MatchResult(tid, trig, e.category, norm, "ALIAS", CONF_ALIAS)

        # 3. Normalized alias
        hit = self._nalias_map.get(norm)
        if hit:
            tid, trig = hit
            e = self._index[trig.lower()]
            return MatchResult(tid, trig, e.category, norm, "NORMALIZED_ALIAS",
                               CONF_NORMALIZED_ALIAS)

        # 4. Keyword: any query token equals a trigger/alias/normalized-alias
        tk = tokens(raw_text)
        for tok in tk:
            if tok in self._index or tok in self._alias_map or tok in self._nalias_map:
                e = self._index.get(tok) or self._index.get(
                    (self._alias_map.get(tok) or self._nalias_map.get(tok, (0, tok)))[1].lower()
                )
                if e:
                    return MatchResult(e.trigger_id, e.trigger, e.category,
                                       tok, "KEYWORD", CONF_KEYWORD)

        # 5. Fuzzy: candidates = triggers + normalized aliases
        candidates: dict[str, tuple[int, str]] = {
            t.lower(): (e.trigger_id, t) for t, e in self._index.items()
        }
        for na, (tid, trig) in self._nalias_map.items():
            candidates.setdefault(na, (tid, trig))
        for a in self._alias_map:
            candidates.setdefault(normalize(a), self._alias_map[a])

        query = " ".join(tk) or norm
        best = process.extractOne(
            query, list(candidates.keys()),
            scorer=fuzz.WRatio, score_cutoff=self.threshold * 100,
        )
        if best:
            matched_key, score, _ = best
            conf = round(score / 100.0, 2)
            tid, trig = candidates[matched_key]
            e = self._index[trig.lower()]
            return MatchResult(tid, trig, e.category, matched_key, "FUZZY", conf)

        # 6. No match above threshold
        return None
