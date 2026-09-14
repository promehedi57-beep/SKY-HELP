"""
Message router — the master pipeline decision point.

Phase 3:  normalize -> matcher -> local method OR fallback message.
Phase 6:  the fallback branch will call Gemini via the key manager.
Phase 16: cache check will be inserted before the fallback branch.
"""

import logging
import time

from core.matcher import Matcher
from core.parser import normalize

logger = logging.getLogger(__name__)

FALLBACK_TEXT = (
    "🤖 I don't have a ready method for that yet.\n"
    "An AI-assisted answer is coming in a future update — meanwhile, "
    "try one of these triggers: <code>bd</code>, <code>wa</code>, "
    "or type <code>/help</code>."
)


class Router:
    def __init__(self, db, matcher: Matcher):
        self.db = db
        self.matcher = matcher

    async def handle_text(self, raw_text: str) -> tuple[str, dict]:
        """
        Returns (response_html, meta).
        meta: matched trigger, match_type, confidence, source ('local'/'fallback'), latency_ms.
        """
        start = time.monotonic()
        meta: dict = {"source": "fallback", "trigger": None,
                      "match_type": None, "confidence": None}

        match = await self.matcher.match(raw_text)
        if match is None or not match.is_confident:
            meta["latency_ms"] = int((time.monotonic() - start) * 1000)
            return FALLBACK_TEXT, meta

        row = await self.db.fetchone(
            "SELECT m.*, mt.method_id FROM method_triggers mt "
            "JOIN methods m ON m.id = mt.method_id "
            "WHERE mt.trigger_id = ? AND m.enabled = 1 LIMIT 1",
            (match.trigger_id,),
        )
        meta.update(trigger=match.trigger, match_type=match.match_type,
                    confidence=match.confidence)
        if row is None:
            # Trigger exists but no published method yet — treat as fallback
            meta["latency_ms"] = int((time.monotonic() - start) * 1000)
            return FALLBACK_TEXT, meta

        meta["source"] = "local"
        meta["method_key"] = row["method_key"]
        meta["latency_ms"] = int((time.monotonic() - start) * 1000)
        body = row["template"] or "<b>{title}</b>\n\n{content}"
        body = (body
                .replace("{title}", _esc(row["title"]))
                .replace("{content}", _esc(row["content"]))
                .replace("{category}", _esc(row["category"]))
                .replace("{trigger}", _esc(match.trigger)))
        return body, meta


def _esc(text: str) -> str:
    import html
    return html.escape(text or "")
