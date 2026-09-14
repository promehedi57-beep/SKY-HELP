"""
Async SQLite connection manager.

- Single shared aiosqlite connection with WAL mode.
- Foreign keys ON, busy_timeout set.
- Helpers: execute, executemany, fetchone, fetchall.
- All callers MUST use parameterized SQL (this module never string-formats user input).
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
]


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call await db.connect() first.")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        for pragma in PRAGMAS:
            await self._conn.execute(pragma)
        await self._conn.commit()
        logger.info("Database connected: %s (WAL mode)", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")

    # ---- write helpers ----
    async def execute(self, sql: str, params: tuple = ()) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid

    async def executemany(self, sql: str, seq: list[tuple]) -> None:
        await self.conn.executemany(sql, seq)
        await self.conn.commit()

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self.conn
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    # ---- read helpers ----
    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchall()

    async def fetchval(self, sql: str, params: tuple = ()):
        row = await self.fetchone(sql, params)
        return row[0] if row is not None else None


# Singleton holder — assigned by app startup / init script.
db = Database("data/bot.db")
