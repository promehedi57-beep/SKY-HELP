"""
Message router — pipeline decision point.

Phase 4: methods render via the safe template engine (core.formatter) with
real Telegram context. Fallback template also rendered through the same path.
"""

import logging
import time

from core.formatter import DEFAULT_TEMPLATE, build_context, render, validate_template
from core.matcher import Matcher

logger = logging.getLogger(__name__)

FALLBACK_TEMPLATE = (
    "🤖 I don't have a ready method for that yet.\n"
    "An AI-assisted answer is coming in a future update — meanwhile, try: "
    "<code>bd</code>, <code>wa</code>, or <code>/help</code>."
)


class Router:
    def __init__(self, db, matcher: Matcher):
        self.db = db
        self.matcher = matcher

    async def handle_text(self, raw_text: str, *, user=None, chat=None) -> tuple[str, dict]:
        start = time.monotonic()
        meta: dict = {"source": "fallback", "trigger": None,
                      "match_type": None, "confidence": None}

        match = await self.matcher.match(raw_text)
        if match is None or not match.is_confident:
            meta["latency_ms"] = self._lat(start)
            return render(FALLBACK_TEMPLATE, build_context(user=user, chat=chat)), meta

        row = await self.db.fetchone(
            "SELECT m.*, mt.method_id FROM method_triggers mt "
            "JOIN methods m ON m.id = mt.method_id "
            "WHERE mt.trigger_id = ? AND m.enabled = 1 LIMIT 1",
            (match.trigger_id,),
        )
        meta.update(trigger=match.trigger, match_type=match.match_type,
                    confidence=match.confidence)
        if row is None:
            meta["latency_ms"] = self._lat(start)
            return render(FALLBACK_TEMPLATE, build_context(user=user, chat=chat)), meta

        meta["source"] = "local"
        meta["method_key"] = row["method_key"]
        meta["latency_ms"] = self._lat(start)

        template = row["template"] or DEFAULT_TEMPLATE
        try:
            ctx = build_context(
                user=user, chat=chat, trigger=match.trigger,
                category=row["category"], method=row["title"], confidence=match.confidence,
            )
            body = render(template, ctx)
        except Exception:
            # A broken saved template must never break user responses:
            logger.exception("Template render failed for method %s — using default",
                             row["method_key"])
            ctx = build_context(user=user, chat=chat, trigger=match.trigger,
                                category=row["category"], method=row["title"],
                                confidence=match.confidence)
            body = render(DEFAULT_TEMPLATE, ctx)
        return body, meta

    @staticmethod
    def _lat(start: float) -> int:
        return int((time.monotonic() - start) * 1000)
