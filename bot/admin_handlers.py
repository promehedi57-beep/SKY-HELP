"""
Admin command handlers. Phase 4 adds /preview (template sandbox).
Phase 9 replaces the ad-hoc admin check with the full permission system.
"""

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.formatter import DEFAULT_TEMPLATE, build_context, render, validate_template

logger = logging.getLogger(__name__)


def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    if user_id is None:
        return False
    admin_id = context.bot_data.get("settings").admin_ids
    return user_id in admin_id


PREVIEW_USAGE = (
    "<b>Template preview</b>\n\n"
    "Usage: <code>/preview &lt;template&gt;</code>\n\n"
    "Placeholders: {user} {user_id} {username} {chat} {trigger} {category} "
    "{method} {confidence} {timestamp}\n\n"
    "Example:\n<code>/preview &lt;b&gt;{method}&lt;/b&gt;\\nTrigger: {trigger} "
    "({confidence})\\nUser: {user}</code>"
)


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_admin(context, user.id if user else None):
        await update.effective_message.reply_text("⛔ Admins only.")
        return
    raw = (update.effective_message.text or "").split(" ", 1)
    template = raw[1] if len(raw) > 1 else ""
    if not template.strip():
        await update.effective_message.reply_text(
            PREVIEW_USAGE, parse_mode=ParseMode.HTML)
        return

    try:
        warnings = validate_template(template)
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Invalid template: {html.escape(str(e))}")
        return

    sample_ctx = build_context(
        user=user, chat=update.effective_chat, trigger="whatsapp",
        category="troubleshooting", method="WhatsApp Troubleshooting", confidence=0.95,
    )
    try:
        rendered = render(template, sample_ctx)
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Render failed: {html.escape(str(e))}")
        return

    out = "✅ <b>Rendered preview:</b>\n\n" + rendered
    if warnings:
        out += "\n\n⚠️ <b>Warnings:</b>\n• " + "\n• ".join(html.escape(w) for w in warnings)
    await update.effective_message.reply_text(out, parse_mode=ParseMode.HTML)
