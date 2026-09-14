"""
Knowledge-base data access layer: admin/setting helpers (Phase 2) plus
trigger/method CRUD and lookups (Phase 3). All async, parameterized SQL.
"""

import logging

from database.db import Database

logger = logging.getLogger(__name__)

CORE_TABLES = frozenset({
    "admins", "triggers", "aliases", "methods", "method_triggers", "conversations",
    "conversation_summaries", "user_warnings", "moderation_actions", "response_cache",
    "bot_settings", "api_key_status", "scraped_sources", "pending_methods",
})


# ---------- Phase 2 helpers ----------

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
    if table not in CORE_TABLES:
        raise ValueError(f"Unknown table: {table}")
    return await db.fetchval(f"SELECT COUNT(*) FROM {table}")


# ---------- Trigger CRUD (Phase 3 / Phase 10) ----------

async def create_trigger(db: Database, trigger: str, category: str = "general",
                         description: str = "", priority: int = 0) -> int:
    return await db.execute(
        "INSERT INTO triggers (trigger, category, description, priority) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(trigger) DO UPDATE SET category=excluded.category, "
        "description=excluded.description, priority=excluded.priority, "
        "updated_at=datetime('now')",
        (trigger.strip().lower(), category.strip().lower(), description, priority),
    )


async def add_alias(db: Database, trigger_id: int, alias: str) -> bool:
    try:
        await db.execute(
            "INSERT INTO aliases (trigger_id, alias) VALUES (?, ?)",
            (trigger_id, alias.strip().lower()),
        )
        return True
    except Exception:
        return False


async def get_trigger_by_name(db: Database, trigger: str):
    return await db.fetchone(
        "SELECT * FROM triggers WHERE trigger = ?", (trigger.strip().lower(),))


# ---------- Method lookup ----------

async def get_method_for_trigger(db: Database, trigger_id: int):
    """Return the best enabled method linked to a trigger (highest priority trigger wins via JOIN)."""
    return await db.fetchone(
        """
        SELECT m.* FROM methods m
        JOIN method_triggers mt ON mt.method_id = m.id
        JOIN triggers t ON t.id = mt.trigger_id
        WHERE mt.trigger_id = ? AND m.enabled = 1
        ORDER BY t.priority DESC, m.updated_at DESC
        LIMIT 1
        """,
        (trigger_id,),
    )


async def get_method_by_key(db: Database, method_key: str):
    return await db.fetchone(
        "SELECT * FROM methods WHERE method_key = ?", (method_key.strip().lower(),))


async def create_full_method(db: Database, method_key: str, title: str, content: str,
                             template: str, category: str, trigger: str,
                             aliases: list[str]) -> int:
    """Create trigger + aliases + method + links in ONE transaction (used by seeding and /train in Phase 10)."""
    async with db.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO triggers (trigger, category) VALUES (?, ?) "
            "ON CONFLICT(trigger) DO UPDATE SET updated_at=datetime('now')",
            (trigger.strip().lower(), category.strip().lower()),
        )
        trigger_id = cur.lastrowid
        if trigger_id is None:
            row = await conn.execute(
                "SELECT id FROM triggers WHERE trigger = ?", (trigger.strip().lower(),))
            trigger_id = (await row.fetchone())[0]
        for alias in aliases:
            await conn.execute(
                "INSERT OR IGNORE INTO aliases (trigger_id, alias) VALUES (?, ?)",
                (trigger_id, alias.strip().lower()),
            )
        cur = await conn.execute(
            "INSERT INTO methods (method_key, title, content, template, category) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(method_key) DO UPDATE SET title=excluded.title, "
            "content=excluded.content, template=excluded.template, "
            "category=excluded.category, version=version+1, updated_at=datetime('now')",
            (method_key.strip().lower(), title, content, template, category.strip().lower()),
        )
        method_id = cur.lastrowid
        if method_id is None:
            row = await conn.execute(
                "SELECT id FROM methods WHERE method_key = ?", (method_key.strip().lower(),))
            method_id = (await row.fetchone())[0]
        await conn.execute(
            "INSERT OR IGNORE INTO method_triggers (trigger_id, method_id) VALUES (?, ?)",
            (trigger_id, method_id),
        )
    logger.info("Method '%s' saved (trigger='%s', aliases=%d)", method_key, trigger, len(aliases))
    return method_id
