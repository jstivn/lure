"""
Deletes leads that were wrongly disqualified so the next search re-checks them:
  - "has website" where the site was just a Messenger/WhatsApp link (fb.me, api.whatsapp.com, messenger.com)
  - "low audience" with 0/0 followers saved by the search browsers that had no Facebook login

Makes a backup copy of leads.db first. Run with Lure closed or open; asks before deleting.
    meta-lead-gen\\venv\\Scripts\\python.exe tools\\recheck_messenger_leads.py
"""
import datetime
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "meta-lead-gen" / "data" / "leads.db"
WHERE = (
    "(status = 'DISQUALIFIED_HAS_WEBSITE' AND website_found IN ('fb.me', 'api.whatsapp.com', 'messenger.com')) "
    "OR (status = 'DISQUALIFIED_LOW_AUDIENCE' AND created_at >= '2026-11-02T10:33' AND fb_followers = 0 AND ig_followers = 0)"
)


def main():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    count = conn.execute(f"SELECT COUNT(*) FROM leads WHERE {WHERE}").fetchone()[0]
    print(f"Rows to delete: {count} of {conn.execute('SELECT COUNT(*) FROM leads').fetchone()[0]}")
    if not count:
        return
    if input("Make a backup and delete them? [y/N] ").strip().lower() != "y":
        print("Nothing changed.")
        return
    backup = DB.with_name(f"leads.backup-{datetime.datetime.now():%Y%m%d-%H%M%S}.db")
    target = sqlite3.connect(backup)
    conn.backup(target)
    target.close()
    print(f"Backup: {backup}")
    with conn:
        conn.execute(f"DELETE FROM leads WHERE {WHERE}")
    print(f"Done. Leads left: {conn.execute('SELECT COUNT(*) FROM leads').fetchone()[0]}")


if __name__ == "__main__":
    main()
