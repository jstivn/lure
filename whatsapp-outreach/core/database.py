"""
SQLite database manager for WhatsApp outreach queue and history.
Tracks leads, daily sending limits, statuses, and synchronizes with data/list.txt.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "wa_leads.db"


class WhatsAppDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = DEFAULT_DB_PATH
        else:
            p = Path(db_path)
            self.db_path = p if p.is_absolute() else (BASE_DIR / p)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE NOT NULL,
                    business_name TEXT,
                    niche TEXT DEFAULT 'recovery',
                    assigned_account TEXT NOT NULL,
                    status TEXT DEFAULT 'QUEUED',
                    sent_message TEXT,
                    sent_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phone ON leads(phone)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_acc ON leads(status, assigned_account)")

    def add_lead(self, phone: str, business_name: str, niche: str, assigned_account: str) -> bool:
        """Adds a lead to the queue if not already present."""
        clean_phone = self.normalize_phone(phone)
        if not clean_phone:
            return False

        with self._get_conn() as conn:
            try:
                conn.execute("""
                    INSERT INTO leads (phone, business_name, niche, assigned_account, status)
                    VALUES (?, ?, ?, ?, 'QUEUED')
                """, (clean_phone, business_name, niche, assigned_account))
                return True
            except sqlite3.IntegrityError:
                return False

    def get_next_lead(self, assigned_account: str, niches: Optional[list] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves the next queued lead for the specified account.
        If no lead is queued specifically for this account, takes an unassigned or shared one.
        Leads assigned to the other account are never taken.
        niches: если задан, берутся только лиды этих ниш (старые ниши в очереди остаются нетронутыми).
        """
        niche_sql, niche_args = "", ()
        if niches:
            niche_sql = f" AND niche IN ({','.join('?' * len(niches))})"
            niche_args = tuple(niches)
        with self._get_conn() as conn:
            # 1. Primary: explicitly assigned to this account
            row = conn.execute(f"""
                SELECT * FROM leads
                WHERE status = 'QUEUED' AND assigned_account = ?{niche_sql}
                ORDER BY id ASC
                LIMIT 1
            """, (assigned_account, *niche_args)).fetchone()
            if row:
                return dict(row)

            # 2. Secondary: unassigned or shared
            row = conn.execute(f"""
                SELECT * FROM leads
                WHERE status = 'QUEUED' AND (assigned_account = '' OR assigned_account IS NULL OR assigned_account = 'shared'){niche_sql}
                ORDER BY id ASC
                LIMIT 1
            """, niche_args).fetchone()
            if row:
                lead = dict(row)
                conn.execute("UPDATE leads SET assigned_account = ? WHERE id = ?", (assigned_account, lead['id']))
                return lead

            # Чужие лиды не берём: каждый номер закреплён за одним аккаунтом, два аккаунта не пишут одному контакту
            return None

    def mark_status(self, phone: str, status: str, sent_message: Optional[str] = None):
        """Updates the status and timestamp of a lead."""
        with self._get_conn() as conn:
            now = datetime.now().isoformat() if status == "SENT" else None
            conn.execute("""
                UPDATE leads
                SET status = ?, sent_message = ?, sent_at = ?
                WHERE phone = ?
            """, (status, sent_message, now, phone))

    def count_sent_today(self, assigned_account: str) -> int:
        """Counts how many messages were successfully sent today from this account."""
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM leads
                WHERE status = 'SENT' AND assigned_account = ? AND sent_at >= ?
            """, (assigned_account, cutoff)).fetchone()
            return row["cnt"] if row else 0

    def get_seconds_since_last_sent(self, assigned_account: Optional[str] = None) -> Optional[float]:
        """
        Returns seconds elapsed since any WhatsApp message was sent.
        If assigned_account is None, checks globally across all WhatsApp accounts (cross-profile safety).
        """
        with self._get_conn() as conn:
            if assigned_account:
                row = conn.execute("""
                    SELECT sent_at FROM leads
                    WHERE status = 'SENT' AND assigned_account = ? AND sent_at IS NOT NULL
                    ORDER BY sent_at DESC
                    LIMIT 1
                """, (assigned_account,)).fetchone()
            else:
                row = conn.execute("""
                    SELECT sent_at FROM leads
                    WHERE status = 'SENT' AND sent_at IS NOT NULL
                    ORDER BY sent_at DESC
                    LIMIT 1
                """).fetchone()

            if not row or not row["sent_at"]:
                return None
            try:
                raw = str(row["sent_at"])
                sent_time = datetime.fromisoformat(raw)
                diff = (datetime.now() - sent_time).total_seconds()
                return max(0.0, diff)
            except Exception:
                return None

    def get_stats(self) -> Dict[str, Any]:
        """Returns overview statistics of the leads database."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT status, COUNT(*) as count FROM leads GROUP BY status
            """).fetchall()
            stats = {row["status"]: row["count"] for row in rows}
            total = sum(stats.values())
            return {"total": total, "by_status": stats}

    def is_known_phone(self, phone: str) -> bool:
        """Checks if a phone number already exists in the database."""
        clean_phone = self.normalize_phone(phone)
        if not clean_phone:
            return False
        with self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM leads WHERE phone = ?", (clean_phone,)).fetchone()
            return row is not None

    def sync_from_list_txt(self, list_txt_path: str):
        """
        Reads data/list.txt and marks any previously contacted numbers as ALREADY_CONTACTED
        so they are permanently blacklisted from new outreach.
        """
        path = Path(list_txt_path)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        with self._get_conn() as conn:
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Match numbers like +971501234567
                import re
                match = re.search(r"(\+971\d{8,9})", line)
                if match:
                    phone = match.group(1)
                    conn.execute("""
                        INSERT OR IGNORE INTO leads (phone, business_name, niche, assigned_account, status)
                        VALUES (?, 'From list.txt', 'history', 'unknown', 'ALREADY_CONTACTED')
                    """, (phone,))

    def append_to_list_txt(self, list_txt_path: str, phone: str, account_name: str, name: str):
        """Appends a newly contacted number to data/list.txt strictly formatted."""
        path = Path(list_txt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%d.%m.%Y")
        tag = "[Бизнес #1]" if "1" in account_name else "[Номер #2]"
        entry = f"{phone} | {tag} | {name} | {date_str}\n"

        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)

    @staticmethod
    def normalize_phone(phone: str) -> Optional[str]:
        """Normalizes a phone number to +<digits>; UAE local forms get the 971 code."""
        if not phone:
            return None
        import re
        digits = re.sub(r"\D", "", phone)
        
        # 0501234567 -> 971501234567
        if digits.startswith("05") and len(digits) == 10:
            digits = "971" + digits[1:]
        # 501234567 -> 971501234567
        elif digits.startswith("5") and len(digits) == 9:
            digits = "971" + digits
        # 00971501234567 -> 971501234567
        elif digits.startswith("00971"):
            digits = digits[2:]

        # Для WhatsApp номер это просто идентификатор чата: принимается любая страна (8-15 цифр)
        if 8 <= len(digits) <= 15:
            return f"+{digits}"
        return None
