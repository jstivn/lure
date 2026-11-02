import csv
import re
from pathlib import Path
from typing import List

from rich.console import Console

console = Console()

# Отдельный файл: leads_emails.csv перезаписывает extract_emails.py (цепочка 7_SCRAPE_GMAPS)
EMAIL_QUEUE_PATH = Path(__file__).resolve().parents[1] / "data" / "leads_emails_auto.csv"
EMAIL_QUEUE_FIELDS = ["business_name", "website", "email", "found_on_page", "all_emails"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
JUNK_DOMAINS = ("facebook.com", "instagram.com", "fb.com", "meta.com", "whatsapp.com", "google.com",
                "sentry.io", "example.com", "domain.com", "email.com")
JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")


def find_emails(text: str) -> List[str]:
    """Адреса из текста страницы и mailto-ссылок, без служебных и картиночных совпадений."""
    out: List[str] = []
    for raw in EMAIL_RE.findall(text or ""):
        email = raw.strip(".").lower()
        domain = email.rsplit("@", 1)[-1]
        if email.endswith(JUNK_SUFFIXES) or any(domain == d or domain.endswith("." + d) for d in JUNK_DOMAINS):
            continue
        if email not in out:
            out.append(email)
    return out


def queue_email_lead(email: str, business_name: str, source: str = "") -> bool:
    """Добавляет адрес в очередь почтовой рассылки (data/leads_emails_auto.csv). False, если уже есть или запись не удалась."""
    email = (email or "").strip().lower()
    if not email:
        return False
    try:
        EMAIL_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        known = set()
        if EMAIL_QUEUE_PATH.exists():
            with open(EMAIL_QUEUE_PATH, "r", encoding="utf-8-sig", newline="") as f:
                known = {(r.get("email") or "").strip().lower() for r in csv.DictReader(f)}
        if email in known:
            return False
        is_new = not EMAIL_QUEUE_PATH.exists() or EMAIL_QUEUE_PATH.stat().st_size == 0
        with open(EMAIL_QUEUE_PATH, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=EMAIL_QUEUE_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow({"business_name": business_name, "website": "", "email": email,
                             "found_on_page": source, "all_emails": email})
        return True
    except OSError as e:
        console.print(f"  [yellow]⚠ Не удалось записать почту в {EMAIL_QUEUE_PATH.name}: {e}[/yellow]")
        return False
