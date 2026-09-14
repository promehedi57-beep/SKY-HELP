"""
Application entry point.

Lifecycle:
  load settings -> init logging -> build Telegram app -> register handlers
  -> run polling until Ctrl+C -> graceful shutdown.
Phase 5+ (key manager), Phase 11+ (scraper scheduler) will register
asyncio background jobs inside post_init / post_shutdown.
"""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.handlers import cmd_help, cmd_start, on_error, on_message
from config import load_settings, setup_logging

logger = logging.getLogger("app")


def build_application(token: str) -> Application:
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Catch-all text handler — always LAST.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    app.add_error_handler(on_error)
    return app


async def post_init(application: Application) -> None:
    me = await application.bot.get_me()
    logger.info("Bot online as @%s (id=%s)", me.username, me.id)


async def post_shutdown(application: Application) -> None:
    logger.info("Bot shut down cleanly.")


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir)
    logger.info("Starting telegram_ai_bot (Phase 1)...")

    app = build_application(settings.bot_token)
    app.post_init = post_init
    app.post_shutdown = post_shutdown

    # run_polling handles its own event loop, start/stop and SIGINT/SIGTERM.
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
