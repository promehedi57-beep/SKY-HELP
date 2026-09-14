"""
Telegram update handlers. Phase 3: messages route through core.router.
"""

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>AI Community Support Bot</b>\n\n"
    "/start — start the bot\n"
    "/help — show this help\n\n"
    "Try: <code>bd</code>, <code>BD</code>, <code>Bangladesh</code>, "
    "<code>wa</code>, <code>WhatsApp is not working after changing phones</code>"
)


def _safe_user_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "unknown"
    return html.escape(user.full_name or user.username or str(user.id))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_start user_id=%s chat_id=%s",
                update.effective_user.id if update.effective_user else None,
                update.effective_chat.id if update.effective_chat else None)
    await update.effective_message.reply_text(
        f"👋 Welcome, {_safe_user_name(update)}!\n\n{HELP_TEXT}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    router = context.bot_data.get("router")
    if router is None:  # DB not ready (e.g. first startup race) — fail soft
        await message.reply_text("⚠️ Bot is still starting up. Try again in a second.")
        return

    text, meta = await router.handle_text(message.text)
    logger.info(
        "on_message chat_id=%s source=%s trigger=%s type=%s conf=%s latency=%sms",
        update.effective_chat.id if update.effective_chat else None,
        meta["source"], meta["trigger"], meta["match_type"],
        meta["confidence"], meta["latency_ms"],
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled Telegram error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again in a moment."
            )
        except Exception:
            logger.exception("Failed to send error notice to user")
