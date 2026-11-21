"""Safe database migration with automatic backup and rollback.

    python migrate.py                    # back up the database, then apply migrations
    python migrate.py --list             # list available backups
    python migrate.py --rollback FILE    # restore the database from a backup file

Migrations are additive only: new tables (budget, prediction_log) are created,
existing tables and data (users, transactions) are never altered or deleted.
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# Resolve the path the same way app.py does, WITHOUT importing the app yet
# (importing it creates missing tables, which must happen after the backup).
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "instance", "finance.db")
BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")


def tables():
    if not os.path.exists(DB_PATH):
        return set()
    with sqlite3.connect(DB_PATH) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"finance-{datetime.now():%Y%m%d-%H%M%S}.db")
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)  # consistent copy even while the app is running
    return dest


def migrate():
    before = tables()
    if before:
        print("Backup created:", backup())
    from app import app, db
    with app.app_context():
        db.create_all()
    added = tables() - before
    print("Tables added:", ", ".join(sorted(added)) if added else "none (already up to date)")


def rollback(path):
    if not os.path.exists(path):
        sys.exit(f"Backup not found: {path}")
    shutil.copy2(DB_PATH, DB_PATH + ".before-rollback")
    shutil.copy2(path, DB_PATH)
    print(f"Database restored from {path} (previous file kept as {DB_PATH}.before-rollback)")


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        rollback(sys.argv[sys.argv.index("--rollback") + 1])
    elif "--list" in sys.argv:
        for f in sorted(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else []:
            print(os.path.join(BACKUP_DIR, f))
    else:
        migrate()
