"""
Telegram update handlers for Phase 1.
Only /start, /help and a basic message echo-router stub live here.
Later phases register more handlers on the same application.
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
    "Send any message and I will respond (local knowledge engine comes in Phase 3, "
    "Gemini fallback in Phase 6)."
)


def _safe_user_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "unknown"
    name = user.full_name or user.username or str(user.id)
    return html.escape(name)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info(
        "cmd_start user_id=%s chat_id=%s",
        user.id if user else None,
        update.effective_chat.id if update.effective_chat else None,
    )
    await update.effective_message.reply_text(
        f"👋 Welcome, {_safe_user_name(update)}!\n\n{HELP_TEXT}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main message entry point.
    Phase 1: log and acknowledge.
    Phase 3+: SECURITY -> RATE LIMIT -> NORMALIZE -> MATCH -> (local | cache | Gemini)
    will be invoked from here via the router.
    """
    message = update.effective_message
    if message is None or message.text is None:
        return

    user = update.effective_user
    logger.info(
        "on_message user_id=%s chat_id=%s len=%d text=%r",
        user.id if user else None,
        update.effective_chat.id if update.effective_chat else None,
        len(message.text),
        message.text[:120],
    )

    await message.reply_text(
        f"Received ({len(message.text)} chars). "
        "Knowledge engine arrives in Phase 3 — pipeline is alive."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global PTB error handler: one bad update must never crash the bot."""
    logger.error("Unhandled Telegram error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again in a moment."
            )
        except Exception:  # never raise from the error handler
            logger.exception("Failed to send error notice to user")
