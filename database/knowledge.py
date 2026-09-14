"""
Knowledge-base data access layer.
Phase 2: seed/admin helpers only.
Phase 3 adds: match lookups (exact/alias/normalized), Phase 10 adds training CRUD.
All functions are async and use parameterized SQL.
"""

import logging

from database.db import Database

logger = logging.getLogger(__name__)


async def upsert_admin(db: Database, user_id: int, role: str = "OWNER") -> None:
    await db.execute(
        "INSERT INTO admins (user_id, role) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role, enabled=1",
        (user_id, role),
    )


async def get_admin(db: Database, user_id: int):
    return await db.fetchone("SELECT * FROM admins WHERE user_id = ?", (user_id,))


async def get_setting(db: Database, key: str, default: str = "") -> str:
    val = await db.fetchval("SELECT value FROM bot_settings WHERE key = ?", (key,))
    return val if val is not None else default


async def set_setting(db: Database, key: str, value: str) -> None:
    await db.execute(
        "INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
        (key, value),
    )


async def count_rows(db: Database, table: str) -> int:
    # table name is never user-controlled (only called with internal constants)
    if table not in {
        "admins", "triggers", "aliases", "methods", "method_triggers", "conversations",
        "conversation_summaries", "user_warnings", "moderation_actions", "response_cache",
        "bot_settings", "api_key_status", "scraped_sources", "pending_methods",
    }:
        raise ValueError(f"Unknown table: {table}")
    return await db.fetchval(f"SELECT COUNT(*) FROM {table}")
