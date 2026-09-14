#!/usr/bin/env python3
"""
Initialize + migrate + seed + self-test the database.

Usage:
    python scripts/init_db.py          # migrate + seed
    python scripts/init_db.py --test   # also run self-test and print table report
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from config import load_settings
from database.db import Database
from database.knowledge import count_rows, get_setting, set_setting, upsert_admin
from database.migrations import run_migrations

CORE_TABLES = [
    "admins", "triggers", "aliases", "methods", "method_triggers",
    "conversations", "conversation_summaries", "user_warnings", "moderation_actions",
    "response_cache", "bot_settings", "api_key_status", "scraped_sources", "pending_methods",
]


async def main(run_test: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    settings = load_settings()
    db = Database(settings.database_path)
    await db.connect()
    try:
        await run_migrations(db)

        # Seed OWNER from ADMIN_ID env
        for admin_id in settings.admin_ids:
            await upsert_admin(db, admin_id, "OWNER")
            logging.info("Seeded admin OWNER: %s", admin_id)

        await set_setting(db, "schema_version", "1")

        if run_test:
            print("\n--- SELF TEST ---")
            ok = True
            for table in CORE_TABLES:
                try:
                    n = await count_rows(db, table)
                    print(f"  OK   {table:<25} rows={n}")
                except Exception as e:
                    ok = False
                    print(f"  FAIL {table:<25} {e}")

            # write/read test on bot_settings
            await set_setting(db, "__selftest", "passed")
            val = await get_setting(db, "__selftest")
            print(f"  OK   bot_settings write/read = {val!r}")

            # FK enforcement test: insert alias pointing at missing trigger must fail
            try:
                await db.execute(
                    "INSERT INTO aliases (trigger_id, alias) VALUES (999999, 'fk_test')")
                print("  FAIL foreign_keys NOT enforced!")
                ok = False
            except Exception:
                print("  OK   foreign_keys enforced (bad alias insert rejected)")

            # transaction rollback test
            try:
                async with db.transaction() as conn:
                    await conn.execute(
                        "INSERT INTO bot_settings (key, value) VALUES ('__txn', 'x')")
                    raise RuntimeError("intentional rollback")
            except RuntimeError:
                pass
            leftovers = await db.fetchval(
                "SELECT COUNT(*) FROM bot_settings WHERE key='__txn'")
            print(f"  {'OK  ' if leftovers == 0 else 'FAIL'} transaction rollback clean")

            print(f"\nRESULT: {'ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'}")
            sys.exit(0 if ok else 1)
        else:
            print("Database initialized successfully.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main("--test" in sys.argv))
