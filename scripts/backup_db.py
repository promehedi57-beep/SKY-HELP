#!/usr/bin/env python3
"""
Safe SQLite backup using the online backup API (consistent even with WAL active).

Usage: python scripts/backup_db.py [backup_dir]
Default backup dir: data/backups
Filename: bot_YYYYmmdd_HHMMSS.db
"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_settings


def main() -> None:
    settings = load_settings()
    backup_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"bot_{time.strftime('%Y%m%d_%H%M%S')}.db"

    src_conn = sqlite3.connect(settings.database_path)
    dst_conn = sqlite3.connect(dest)
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    print(f"Backup written: {dest}")


if __name__ == "__main__":
    main()
