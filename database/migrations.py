"""
Forward-only migration system.
Each migration is (version, list of SQL statements). Applied inside one transaction,
recorded in schema_migrations. Idempotent: re-running skips applied versions.
"""

import logging

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        # ---- 0. meta ----
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 1. admins ----
        """
        CREATE TABLE IF NOT EXISTS admins (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL UNIQUE,
            role       TEXT NOT NULL DEFAULT 'MODERATOR'
                       CHECK (role IN ('OWNER','SUPER_ADMIN','ADMIN','MODERATOR','TRAINER')),
            enabled    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 2. triggers ----
        """
        CREATE TABLE IF NOT EXISTS triggers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger     TEXT NOT NULL UNIQUE,
            category    TEXT NOT NULL DEFAULT 'general',
            description TEXT NOT NULL DEFAULT '',
            priority    INTEGER NOT NULL DEFAULT 0,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 3. aliases ----
        """
        CREATE TABLE IF NOT EXISTS aliases (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_id INTEGER NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
            alias      TEXT NOT NULL,
            UNIQUE (trigger_id, alias)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias)",
        # ---- 4. methods ----
        """
        CREATE TABLE IF NOT EXISTS methods (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            method_key TEXT NOT NULL UNIQUE,
            title      TEXT NOT NULL,
            content    TEXT NOT NULL,
            template   TEXT NOT NULL DEFAULT '',
            category   TEXT NOT NULL DEFAULT 'general',
            enabled    INTEGER NOT NULL DEFAULT 1,
            version    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 5. method_triggers ----
        """
        CREATE TABLE IF NOT EXISTS method_triggers (
            trigger_id INTEGER NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
            method_id  INTEGER NOT NULL REFERENCES methods(id)  ON DELETE CASCADE,
            PRIMARY KEY (trigger_id, method_id)
        )
        """,
        # ---- 6. conversations ----
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            role       TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
            content    TEXT NOT NULL,
            message_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_conv_chat_user ON conversations(chat_id, user_id, created_at)",
        # ---- 7. conversation_summaries ----
        """
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            chat_id    INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL DEFAULT 0,
            summary    TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 8. user_warnings ----
        """
        CREATE TABLE IF NOT EXISTS user_warnings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            reason     TEXT NOT NULL DEFAULT '',
            issued_by  INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_warn_chat_user ON user_warnings(chat_id, user_id)",
        # ---- 9. moderation_actions ----
        """
        CREATE TABLE IF NOT EXISTS moderation_actions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            action     TEXT NOT NULL
                       CHECK (action IN ('WARN','DELETE','MUTE','RESTRICT','KICK','BAN','UNBAN')),
            reason     TEXT NOT NULL DEFAULT '',
            issued_by  INTEGER,
            auto       INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 10. response_cache ----
        """
        CREATE TABLE IF NOT EXISTS response_cache (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT NOT NULL UNIQUE,
            response   TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_cache_expires ON response_cache(expires_at)",
        # ---- 11. bot_settings ----
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 12. api_key_status ----
        """
        CREATE TABLE IF NOT EXISTS api_key_status (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            key_index          INTEGER NOT NULL,
            key_hash           TEXT NOT NULL UNIQUE,
            key_prefix         TEXT NOT NULL DEFAULT '',
            source             TEXT NOT NULL DEFAULT 'ENV',
            status             TEXT NOT NULL DEFAULT 'ACTIVE'
                               CHECK (status IN ('ACTIVE','COOLDOWN','ERROR','DISABLED')),
            total_requests     INTEGER NOT NULL DEFAULT 0,
            successful_requests INTEGER NOT NULL DEFAULT 0,
            failed_requests    INTEGER NOT NULL DEFAULT 0,
            rate_limit_errors  INTEGER NOT NULL DEFAULT 0,
            timeout_errors     INTEGER NOT NULL DEFAULT 0,
            last_used_at       TEXT,
            last_error_at      TEXT,
            cooldown_until     TEXT,
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 13. scraped_sources ----
        """
        CREATE TABLE IF NOT EXISTS scraped_sources (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id         INTEGER NOT NULL UNIQUE,
            username        TEXT NOT NULL DEFAULT '',
            title           TEXT NOT NULL DEFAULT '',
            source_type     TEXT NOT NULL DEFAULT 'CHANNEL' CHECK (source_type IN ('CHANNEL','GROUP')),
            enabled         INTEGER NOT NULL DEFAULT 1,
            keywords        TEXT NOT NULL DEFAULT '',
            poll_interval   INTEGER NOT NULL DEFAULT 300,
            last_message_id INTEGER NOT NULL DEFAULT 0,
            last_scan_at    TEXT,
            created_by      INTEGER,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # ---- 14. pending_methods ----
        """
        CREATE TABLE IF NOT EXISTS pending_methods (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id          INTEGER REFERENCES scraped_sources(id) ON DELETE SET NULL,
            source_chat_id     INTEGER,
            source_message_id  INTEGER,
            source_name        TEXT NOT NULL DEFAULT '',
            source_username    TEXT NOT NULL DEFAULT '',
            source_message_url TEXT NOT NULL DEFAULT '',
            raw_text           TEXT NOT NULL,
            extracted_text     TEXT NOT NULL DEFAULT '',
            media_type         TEXT NOT NULL DEFAULT 'text',
            media_file_id      TEXT NOT NULL DEFAULT '',
            detected_keywords  TEXT NOT NULL DEFAULT '',
            detected_trigger   TEXT NOT NULL DEFAULT '',
            suggested_title    TEXT NOT NULL DEFAULT '',
            suggested_category TEXT NOT NULL DEFAULT 'general',
            suggested_method   TEXT NOT NULL DEFAULT '',
            content_hash       TEXT NOT NULL DEFAULT '',
            status             TEXT NOT NULL DEFAULT 'PENDING'
                               CHECK (status IN ('PENDING','EDITING','APPROVED','REJECTED','PUBLISHED')),
            admin_notes        TEXT NOT NULL DEFAULT '',
            created_at         TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at        TEXT,
            reviewed_by        INTEGER
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_methods(status)",
        "CREATE INDEX IF NOT EXISTS idx_pending_hash ON pending_methods(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_pending_src_msg ON pending_methods(source_chat_id, source_message_id)",
    ]),
]


async def get_applied_versions(db) -> set[int]:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    rows = await db.fetchall("SELECT version FROM schema_migrations")
    return {r["version"] for r in rows}


async def run_migrations(db) -> list[int]:
    """Apply all pending migrations. Returns list of newly applied versions."""
    applied = await get_applied_versions(db)
    newly_applied: list[int] = []
    for version, statements in MIGRATIONS:
        if version in applied:
            continue
        async with db.transaction() as conn:
            for stmt in statements:
                await conn.execute(stmt)
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
            )
        newly_applied.append(version)
        logger.info("Applied migration %s (%d statements)", version, len(statements))
    if not newly_applied:
        logger.info("Migrations up to date (version %d)", max(applied) if applied else 0)
    return newly_applied
