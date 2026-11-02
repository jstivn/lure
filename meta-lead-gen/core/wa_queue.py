import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from rich.console import Console

console = Console()

WA_DB_PATH = Path(__file__).resolve().parents[2] / "whatsapp-outreach" / "data" / "wa_leads.db"

# Код страны по полю country из catalog.json (нужен, чтобы дописать код к номеру без него)
DIAL_CODES = {
    "AE": "971", "SA": "966", "QA": "974", "US": "1", "CA": "1", "GB": "44", "AU": "61",
    "TR": "90", "PH": "63", "MX": "52", "TH": "66", "ID": "62", "CY": "357",
}


def normalize_phone(raw: str, country: str = "AE") -> Optional[str]:
    """
    Номер в международном виде +<цифры>. Для WhatsApp это просто идентификатор чата, поэтому
    принимается любая страна. Номер без кода страны достраивается кодом страны лида.
    """
    text = (raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    cc = DIAL_CODES.get((country or "AE").upper(), "971")

    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = cc + digits[1:]
    elif not digits.startswith(cc) or len(digits) <= len(cc) + 6:
        digits = cc + digits

    return f"+{digits}" if 8 <= len(digits) <= 15 else None


def find_phones(text: str, country: str = "AE") -> List[str]:
    """Номера из текста страницы: сначала с явным +код/00код и tel:/wa.me, потом локальные с 0 в начале."""
    explicit = [m.group(1) and f"+{m.group(1)}" or m.group(0) for m in re.finditer(
        r"(?:wa\.me/|phone=)\+?(\d{8,15})|(?:\+|(?<!\d)00)\d[\d\s\-\(\)]{7,16}\d", text or "")]
    local = re.findall(r"(?<![\d+])0\d[\d\s\-]{7,12}\d", text or "")
    out: List[str] = []
    for chunk in explicit + local:
        phone = normalize_phone(chunk, country)
        if phone and phone not in out:
            out.append(phone)
    return out


def queue_whatsapp_lead(phone: str, business_name: str, niche: str) -> bool:
    """
    Кладёт лид в очередь WhatsApp (whatsapp-outreach/data/wa_leads.db), поровну между profile_1 и profile_2.
    False, если базы нет или запись не удалась.
    """
    if not WA_DB_PATH.exists():
        console.print(f"  [yellow]⚠ Нет {WA_DB_PATH}: передать лид в WhatsApp нельзя[/yellow]")
        return False
    try:
        with sqlite3.connect(WA_DB_PATH, timeout=30) as conn:
            # файл мог быть создан до первого запуска WhatsApp-модуля: схема та же, что в whatsapp-outreach/core/database.py
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
            counts = dict(conn.execute(
                "SELECT assigned_account, COUNT(*) FROM leads WHERE status = 'QUEUED' GROUP BY assigned_account"
            ).fetchall())
            account = "profile_1" if counts.get("profile_1", 0) <= counts.get("profile_2", 0) else "profile_2"
            conn.execute(
                "INSERT OR IGNORE INTO leads (phone, business_name, niche, assigned_account, status) "
                "VALUES (?, ?, ?, ?, 'QUEUED')",
                (phone, business_name, niche or "", account),
            )
        return True
    except sqlite3.Error as e:
        console.print(f"  [yellow]⚠ Не удалось записать лид в wa_leads.db: {e}[/yellow]")
        return False
