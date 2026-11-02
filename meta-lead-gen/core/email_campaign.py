"""
Почтовая рассылка: очередь получателей, шаблон со спинтаксом и журнал отправок.
Работает без пакета `core` (Lure подгружает файл по пути), поэтому только stdlib.
"""

import csv
import importlib.util
import json
import random
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

META_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = META_DIR / "data"
CONFIG_PATH = META_DIR / "config.json"
TEMPLATE_PATH = META_DIR / "email_template.json"
DB_PATH = DATA_DIR / "leads.db"

AUTO_QUEUE_PATH = DATA_DIR / "leads_emails_auto.csv"      # почты лидов из мессенджеров/Maps (email_queue.py)
RENTALS_PATH = DATA_DIR / "leads_emails.csv"              # почты автопрокатов с сайтов (extract_emails.py)
SENT_PATH = DATA_DIR / "sent_emails.txt"
FAILED_PATH = DATA_DIR / "email_failed.txt"
LOG_PATH = DATA_DIR / "email_log.csv"
LOG_FIELDS = ["sent_at", "email", "business_name", "subject", "status", "error", "message_id"]

SOURCES = ("leads", "rentals")
EXCLUDED_NAMES: List[str] = []
BAD_LOCALPARTS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")

_database_mod = None


def _database():
    """core/database.py подгружается по пути: build_demo_link нужен без конфликта с пакетом `core` Lure."""
    global _database_mod
    if _database_mod is None:
        spec = importlib.util.spec_from_file_location("meta_lead_gen_database_email", META_DIR / "core" / "database.py")
        _database_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_database_mod)
    return _database_mod


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_settings() -> Dict[str, Any]:
    """Блок `email` из config.json с значениями по умолчанию."""
    cfg = _read_json(CONFIG_PATH).get("email", {})
    return {
        "sender": cfg.get("sender", ""),
        "app_password": cfg.get("app_password", ""),
        "sender_name": cfg.get("sender_name", ""),
        "smtp_host": cfg.get("smtp_host", "smtp.gmail.com"),
        "smtp_port": int(cfg.get("smtp_port", 587)),
        "max_per_run": int(cfg.get("max_per_run", 100)),
        "delay_min_sec": int(cfg.get("delay_min_sec", 20)),
        "delay_max_sec": int(cfg.get("delay_max_sec", 45)),
    }


def load_template() -> Dict[str, Any]:
    return _read_json(TEMPLATE_PATH)


# ---------- Текст ----------

_SPIN = re.compile(r"\{([^{}]*\|[^{}]*)\}")


def spin(text: str) -> str:
    """Раскрывает спинтакс {a|b|c} (в том числе вложенный). Подстановки вида {company} не трогает."""
    while True:
        m = _SPIN.search(text)
        if not m:
            return text
        text = text[:m.start()] + random.choice(m.group(1).split("|")) + text[m.end():]


def clean_company_name(raw: str) -> str:
    """Название без SEO-хвостов Google Maps, эмодзи, арабицы и декоративных юникод-шрифтов."""
    if not raw:
        return "your company"
    name = unicodedata.normalize("NFKC", raw)
    name = re.sub(r"[\U00010000-\U0010ffff]", "", name)
    name = re.split(r"[|:–—]", name)[0]
    if " - " in name:
        name = name.split(" - ")[0]
    name = re.sub(r"[؀-ۿ]+", "", name)
    name = re.sub(r"\s+", " ", name).strip().strip("\"'.,")
    return name or raw.strip()


def render_message(item: Dict[str, Any], template: Optional[Dict[str, Any]] = None,
                   settings: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Тема и текст письма для одного получателя; спинтакс раскрывается заново при каждом вызове."""
    template = template or load_template()
    settings = settings or load_settings()
    niche = item.get("hook_niche") or "generic"
    override = (template.get("niches") or {}).get(niche, {})
    labels = template.get("niche_labels", {})
    subjects = override.get("subjects") or template.get("subjects") or ["Quick question regarding {company}"]
    body = override.get("body") or template.get("body") or ""

    business = labels.get(niche) or labels.get("generic", "business")
    source = item.get("source_phrase") or f"your {business} on Google Maps"
    values = {
        "[BUSINESS]": clean_company_name(item.get("name", "")),
        "[SOURCE]": source.replace("[TYPE]", business),
        "{company}": clean_company_name(item.get("name", "")),
        "{business}": business,
        "{demo_link}": item.get("demo_link", ""),
        "{sender}": settings["sender_name"],
    }

    def fill(text: str) -> str:
        text = spin(text)
        for key, val in values.items():
            text = text.replace(key, val)
        text = re.sub(r"[ \t]+([,.?!])", r"\1", text)
        return re.sub(r"[ \t]+\n", "\n", text).strip()

    return re.sub(r"\s+", " ", fill(random.choice(subjects))), fill(body)


def build_mime(settings: Dict[str, Any], item: Dict[str, Any], subject: str, body: str) -> MIMEText:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{settings['sender_name']} <{settings['sender']}>"
    msg["To"] = item["email"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings["sender"].rsplit("@", 1)[-1] or None)
    return msg


# ---------- Очередь ----------

def _read_lines(path: Path) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {ln.strip().lower() for ln in f if ln.strip()}
    except OSError:
        return set()


def load_sent() -> set:
    return _read_lines(SENT_PATH)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _db_leads() -> Dict[str, Dict[str, Any]]:
    """Лиды из leads.db, у которых есть почта: email -> строка (там демо-ссылка и ниша для подстановки)."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT business_name, email, hook_niche, demo_link, website_found, advertiser_id, "
                "preferred_platform, ig_username FROM leads "
                "WHERE email IS NOT NULL AND email != ''"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {(r["email"] or "").strip().lower(): dict(r) for r in rows}


def source_phrase(row: Dict[str, Any]) -> str:
    """Откуда лид, строго по факту: Google Maps (gmaps:...) или реклама Meta на Instagram/Facebook."""
    if str(row.get("advertiser_id") or "").startswith("gmaps:"):
        return "your [TYPE] on Google Maps"
    platform = (row.get("preferred_platform") or "").upper()
    if platform == "IG" or (platform not in ("FB",) and row.get("ig_username")):
        return "your ad on Instagram"
    return "your ad on Facebook"


def build_queue(source: str = "all", niche: str = "all") -> List[Dict[str, Any]]:
    """
    Получатели, которым письмо ещё не уходило: сначала лиды из мессенджеров (с демо-ссылкой и нишей),
    затем автопрокаты. Один адрес встречается один раз. niche: "all" или hook_niche (beauty, renovation, rental...).
    """
    sent = load_sent() | _read_lines(FAILED_PATH)
    db = _db_leads()
    seen: set = set()
    queue: List[Dict[str, Any]] = []
    database = None

    def add(name: str, email: str, website: str, hook_niche: str, demo_link: str, src: str, phrase: str = ""):
        email = (email or "").strip().lower()
        if not email or email in seen or email in sent or not EMAIL_RE.match(email):
            return
        if email.split("@")[0] in BAD_LOCALPARTS or any(bad in (name or "").lower() for bad in EXCLUDED_NAMES):
            return
        seen.add(email)
        queue.append({"name": name or "", "email": email, "website": website or "",
                      "hook_niche": hook_niche or "generic", "demo_link": demo_link or "", "source": src,
                      "source_phrase": phrase})

    if source in ("all", "leads"):
        for email, row in db.items():
            add(row["business_name"], email, row["website_found"], row["hook_niche"], row["demo_link"], "leads",
                source_phrase(row))
        for r in _read_csv(AUTO_QUEUE_PATH):
            email = (r.get("email") or "").strip().lower()
            if email in db:
                continue
            add(r.get("business_name"), email, r.get("website"), "generic", "", "leads")

    if source in ("all", "rentals"):
        for r in _read_csv(RENTALS_PATH):
            add(r.get("business_name"), r.get("email"), r.get("website"), "rental", "", "rentals")

    if niche != "all":
        queue = [item for item in queue if item["hook_niche"] == niche]

    for item in queue:
        if not item["demo_link"]:
            database = database or _database()
            item["demo_link"] = database.build_demo_link(
                item["name"], niche="rental" if item["hook_niche"] == "rental" else "epoxy", market="uae")
    return queue


def queue_counts() -> Dict[str, Any]:
    """Сколько адресов в очереди по источникам, сколько всего ушло и сколько за скользящие 24 часа."""
    pending = {src: len(build_queue(src)) for src in SOURCES}
    full_queue = build_queue("all")
    total_pending = len(full_queue)
    pending_by_niche: Dict[str, int] = {}
    for item in full_queue:
        pending_by_niche[item["hook_niche"]] = pending_by_niche.get(item["hook_niche"], 0) + 1
    sent = load_sent()
    return {
        "pending": total_pending,
        "pending_by_source": pending,
        "pending_by_niche": pending_by_niche,
        "sent": len(sent),
        "sent_24h": sent_last_24h(),
        "total": total_pending + len(sent),
    }


# ---------- Журнал ----------

def _append_line(path: Path, line: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.strip().lower() + "\n")


def log_result(item: Dict[str, Any], subject: str, status: str, error: str = "", permanent: bool = False,
               message_id: str = ""):
    """status: sent | failed. Отправленные и безвозвратно отклонённые адреса больше в очередь не попадают.
    message_id нужен, чтобы второе письмо ушло ответом в ту же цепочку (In-Reply-To)."""
    if status == "sent":
        _append_line(SENT_PATH, item["email"])
    elif permanent:
        _append_line(FAILED_PATH, item["email"])
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0
    with open(LOG_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({"sent_at": datetime.now().isoformat(timespec="seconds"), "email": item["email"],
                         "business_name": item.get("name", ""), "subject": subject, "status": status,
                         "error": error[:300], "message_id": message_id})


def sent_last_24h() -> int:
    cutoff = datetime.now() - timedelta(hours=24)
    count = 0
    for r in _read_csv(LOG_PATH):
        if r.get("status") != "sent":
            continue
        try:
            if datetime.fromisoformat(r["sent_at"]) >= cutoff:
                count += 1
        except (KeyError, ValueError):
            continue
    return count

