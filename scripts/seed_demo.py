#!/usr/bin/env python3
"""
Seed demo knowledge so Phase 3 is testable:
  bangladesh/bd + whatsapp/wa triggers, each with a method.
Idempotent: safe to run repeatedly.

Usage: python scripts/seed_demo.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_settings
from database.db import Database
from database.knowledge import create_full_method, get_trigger_by_name

DEMO = [
    {
        "method_key": "bd_info",
        "trigger": "bangladesh",
        "aliases": ["bd", "bangla", "bangladesh"],
        "title": "Bangladesh — Info Method",
        "content": "You asked about Bangladesh (BD). This is the local knowledge response "
                   "served from the methods table — no AI needed.",
        "category": "general",
    },
    {
        "method_key": "wa_troubleshoot",
        "trigger": "whatsapp",
        "aliases": ["wa", "whatsapp", "whats app", "what's app"],
        "title": "WhatsApp Troubleshooting",
        "content": "WhatsApp not working? Basic steps:\n"
                   "1. Check internet connection\n"
                   "2. Update WhatsApp to latest version\n"
                   "3. Clear cache (Android: Settings → Apps → WhatsApp → Storage → Clear cache)\n"
                   "4. Re-verify your number\n"
                   "5. If chats were lost after changing phones, restore from Google Drive/iCloud backup "
                   "with the SAME phone number.",
        "category": "troubleshooting",
    },
]


async def main() -> None:
    settings = load_settings()
    db = Database(settings.database_path)
    await db.connect()
    try:
        for item in DEMO:
            existing = await get_trigger_by_name(db, item["trigger"])
            if existing is None:
                mid = await create_full_method(
                    db, item["method_key"], item["title"], item["content"],
                    "<b>{title}</b>\n\n{content}", item["category"],
                    item["trigger"], item["aliases"],
                )
                print(f"Seeded: trigger={item['trigger']} method_id={mid}")
            else:
                print(f"Exists, skipped: trigger={item['trigger']}")
        print("Seed complete.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
