"""
Application entry point.
Phase 4: admin handlers registered; settings stored in bot_data for permission checks.
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

from bot.admin_handlers import cmd_preview
from bot.handlers import cmd_help, cmd_start, on_error, on_message
from config import load_settings, setup_logging
from core.matcher import Matcher
from core.router import Router
from database.db import Database

logger = logging.getLogger("app")


def build_application(token: str, db: Database, settings) -> Application:
    async def post_init(application: Application) -> None:
        await db.connect()
        matcher = Matcher(db, threshold=settings.fuzzy_match_threshold)
        await matcher._ensure_loaded()
        application.bot_data["db"] = db
        application.bot_data["settings"] = settings
        application.bot_data["matcher"] = matcher
        application.bot_data["router"] = Router(db, matcher)
        me = await application.bot.get_me()
        logger.info("Bot online as @%s (id=%s)", me.username, me.id)

    async def post_shutdown(application: Application) -> None:
        await db.close()
        logger.info("Bot shut down cleanly.")

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir)
    logger.info("Starting telegram_ai_bot (Phase 4)...")

    db = Database(settings.database_path)
    app = build_application(settings.bot_token, db, settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
