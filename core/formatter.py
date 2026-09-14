"""
Safe dynamic template engine.

Security model:
- Only whitelisted placeholders are substituted.
- All substituted values are HTML-escaped.
- Unknown placeholders are escaped (rendered literally) — never executed, never dropped silently.
- No eval(), exec(), str.format(), or f-string interpolation of user data.

Supported placeholders (master spec §31):
    {user} {user_id} {username} {chat} {trigger} {category} {method} {confidence} {timestamp}
"""

import html
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

ALLOWED = frozenset({
    "user", "user_id", "username", "chat", "trigger",
    "category", "method", "confidence", "timestamp",
})

_TOKEN_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

DEFAULT_TEMPLATE = "<b>{method}</b>\n\n{content}"


class TemplateError(Exception):
    """Raised when a template is structurally invalid for storage."""


def validate_template(template: str) -> list[str]:
    """
    Validate a template before saving.
    Returns list of warning strings (empty = valid).
    Raises TemplateError for fatal problems.
    """
    if not template or not template.strip():
        raise TemplateError("Template is empty.")

    if "<script" in template.lower():
        raise TemplateError("Template must not contain <script> tags.")

    warnings: list[str] = []
    for name in _TOKEN_RE.findall(template):
        if name not in ALLOWED:
            warnings.append(f"Unknown placeholder '{{{name}}}' — it will render literally.")
    # Unbalanced braces that the regex didn't catch:
    stripped = _TOKEN_RE.sub("", template)
    if "{" in stripped or "}" in stripped:
        warnings.append("Template contains unbalanced braces; they will render literally.")
    # Tags the Telegram HTML parser rejects
    for bad in ("<iframe", "<form", "<input"):
        if bad in template.lower():
            raise TemplateError(f"Forbidden HTML tag: {bad}")
    return warnings


def render(template: str, context: dict) -> str:
    """
    Render a template with a context dict.
    Unknown keys -> literal braces; missing keys -> empty string (safe default).
    All substituted values are HTML-escaped.
    """
    warnings = validate_template(template)  # fatal errors raise
    unknown, missing = [], []
    for name in _TOKEN_RE.findall(template):
        if name not in ALLOWED:
            unknown.append(name)
        elif name not in context:
            missing.append(name)
    if unknown:
        logger.warning("Template render: unknown placeholders: %s", unknown)
    if missing:
        logger.debug("Template render: missing context keys (rendered as ''): %s", missing)

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in ALLOWED:
            return html.escape(m.group(0))
        value = context.get(name, "")
        return html.escape(str(value))

    return _TOKEN_RE.sub(_sub, template)


def build_context(*, user=None, chat=None, trigger: str = "", category: str = "",
                  method: str = "", confidence=None) -> dict:
    """Build a render context from Telegram objects + match metadata."""
    ctx = {
        "user": "unknown",
        "user_id": "",
        "username": "",
        "chat": "private",
        "trigger": trigger,
        "category": category,
        "method": method,
        "confidence": f"{confidence:.2f}" if isinstance(confidence, float) else str(confidence or ""),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if user is not None:
        ctx["user"] = user.full_name or user.username or str(user.id)
        ctx["user_id"] = str(user.id)
        ctx["username"] = f"@{user.username}" if user.username else ""
    if chat is not None:
        title = getattr(chat, "title", None)
        ctx["chat"] = title or ("private" if chat.type == "private" else str(chat.id))
    return ctx
