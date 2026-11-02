import csv
from datetime import datetime, timezone, timedelta
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger("DatabaseManager")


def clean_company_name(name: str) -> str:
    """Cleans business name by removing LLC/Inc suffixes, emojis, and trailing punctuation."""
    if not name:
        return "Your Business"
    # Remove emojis
    cleaned = re.sub(r'[\U00010000-\U0010ffff]', '', name, flags=re.UNICODE)
    # Remove common corporate suffixes
    cleaned = re.sub(r'\b(LLC|L\.L\.C\.|Inc\.|Inc|Corp\.|Corp|Co\.|Co|LTD|Limited)\b', '', cleaned, flags=re.IGNORECASE)
    # Clean up double spaces, commas, hyphens at boundaries
    cleaned = re.sub(r'[\s,\.\-_|/]+$', '', cleaned)
    cleaned = re.sub(r'^[\s,\.\-_|/]+', '', cleaned)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    return cleaned.strip() or name.strip()


def detect_niche(query: str, default: str = "epoxy") -> str:
    """Detects primary niche from search query."""
    q = (query or "").lower()
    if any(k in q for k in ["epoxy", "garage", "concrete", "patio", "polyaspartic", "flake"]):
        return "epoxy"
    if any(k in q for k in ["detail", "wash", "ceramic", "tint", "wrap", "pdr"]):
        return "detailing"
    if any(k in q for k in ["roof", "gutter", "pressure", "clean", "home", "remodel", "plumb", "hvac"]):
        return "home"
    if any(k in q for k in ["car", "van", "rental", "rent", "auto", "vehicle"]):
        return "rental"
    if any(k in q for k in ["yacht", "boat", "charter", "buggy", "supercar"]):
        return "charter"
    return default


def demo_base_url() -> str:
    """Base URL of the demo page: env LURE_DEMO_BASE_URL or `demo_base_url` in config.json. Empty = no demo links."""
    env = os.environ.get("LURE_DEMO_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(cfg.get("demo_base_url", "")).strip().rstrip("/")


def build_demo_link(company: str, niche: str = "epoxy", city: str = "", market: str = "usa", custom_info: str = "") -> str:
    """Generates 1-tap live simulator link for outreach and previews; empty string when no demo URL is configured."""
    base = demo_base_url()
    if not base:
        return ""
    clean_name = clean_company_name(company)
    params = [f"company={quote_plus(clean_name)}", f"niche={quote_plus(niche)}"]
    if city:
        params.append(f"city={quote_plus(city)}")
    if market:
        params.append(f"market={quote_plus(market)}")
    if custom_info:
        params.append(f"custom_info={quote_plus(custom_info)}")
    return f"{base}/?{'&'.join(params)}"


class DatabaseManager:
    """
    Manages SQLite storage (leads.db) with strict deduplication and maintains
    an automatically synchronized CSV export (leads.csv) for Excel viewing.
    """

    def __init__(self, db_path: Optional[str] = None, csv_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent
        if db_path:
            p = Path(db_path)
            self.db_path = p if p.is_absolute() else (base_dir / p)
        else:
            self.db_path = base_dir / "data" / "leads.db"

        if csv_path:
            p = Path(csv_path)
            self.csv_path = p if p.is_absolute() else (base_dir / p)
        else:
            self.csv_path = base_dir / "data" / "leads.csv" 
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self):
        """Initializes the database schema with necessary tables, columns, and indexes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    advertiser_id TEXT UNIQUE NOT NULL,
                    business_name TEXT,
                    search_query TEXT,
                    fb_page_url TEXT,
                    fb_followers INTEGER DEFAULT 0,
                    ig_username TEXT,
                    ig_profile_url TEXT,
                    ig_followers INTEGER DEFAULT 0,
                    preferred_platform TEXT,
                    preferred_profile_url TEXT,
                    preferred_message_url TEXT,
                    website_found TEXT,
                    has_website INTEGER DEFAULT 0,
                    ad_cta_type TEXT,
                    demo_link TEXT,
                    custom_info TEXT,
                    qualification_reason TEXT,
                    status TEXT NOT NULL,
                    sent_message_text TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_adv_id ON leads(advertiser_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_sent_at ON leads(sent_at)")

            # Auto-migration: check if columns exist in older database schema
            cursor.execute("PRAGMA table_info(leads)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            if "has_website" not in existing_cols:
                cursor.execute("ALTER TABLE leads ADD COLUMN has_website INTEGER DEFAULT 0")
            if "ad_cta_type" not in existing_cols:
                cursor.execute("ALTER TABLE leads ADD COLUMN ad_cta_type TEXT")
            if "demo_link" not in existing_cols:
                cursor.execute("ALTER TABLE leads ADD COLUMN demo_link TEXT")
            if "custom_info" not in existing_cols:
                cursor.execute("ALTER TABLE leads ADD COLUMN custom_info TEXT")
            for col in ("niche", "geo", "market", "hook_niche", "demo_niche", "phone", "email", "replied_at", "reply_handled_at", "mockup_path"):
                if col not in existing_cols:
                    cursor.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")

            conn.commit()

        # If CSV doesn't exist, generate initial one
        if not self.csv_path.exists():
            self.export_to_csv()

    def lead_exists(self, advertiser_id: str) -> bool:
        """Checks if advertiser already exists in the database by unique identifier."""
        if not advertiser_id:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM leads WHERE advertiser_id = ?", (advertiser_id.strip(),))
            return cursor.fetchone() is not None

    def set_lead_phone(self, advertiser_id: str, phone: str) -> None:
        """Сохраняет телефон лида (источник: Google Maps или кнопка WhatsApp в рекламе)."""
        with self._get_connection() as conn:
            conn.execute("UPDATE leads SET phone = ? WHERE advertiser_id = ?", (phone, advertiser_id.strip()))
            conn.commit()

    def set_lead_email(self, advertiser_id: str, email: str) -> None:
        """Сохраняет почту лида (источник: страница Facebook или карточка Google Maps)."""
        with self._get_connection() as conn:
            conn.execute("UPDATE leads SET email = ? WHERE advertiser_id = ?", (email, advertiser_id.strip()))
            conn.commit()

    def mark_as_replied(self, lead_id: int) -> None:
        """Отметка «лид ответил на шаг 1»."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("UPDATE leads SET replied_at = ?, updated_at = ? WHERE id = ?", (now, now, lead_id))
            conn.commit()

    def set_lead_mockup(self, lead_id: int, path: str) -> None:
        """Привязывает PNG-макет к лиду."""
        with self._get_connection() as conn:
            conn.execute("UPDATE leads SET mockup_path = ? WHERE id = ?", (path, lead_id))
            conn.commit()

    def get_lead(self, lead_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
            return dict(row) if row else None

    def upsert_lead(self, data: Dict[str, Any]) -> int:
        """
        Inserts a new lead or updates existing lead by advertiser_id.
        Automatically updates leads.csv and computes demo_link.
        """
        now = datetime.now(timezone.utc).isoformat()
        advertiser_id = str(data.get("advertiser_id", "")).strip()

        biz_name = data.get("business_name", "")
        search_query = data.get("search_query", "")
        niche = data.get("demo_niche") or detect_niche(search_query)

        # Auto-compute demo_link if not explicitly passed
        demo_link = data.get("demo_link")
        if not demo_link and biz_name:
            demo_link = build_demo_link(
                biz_name, niche=niche, market=data.get("market") or "usa",
                custom_info=data.get("custom_info", "")
            )

        website_val = data.get("website_found")
        has_site_val = 1 if (data.get("has_website") or website_val) else 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM leads WHERE advertiser_id = ?", (advertiser_id,))
            row = cursor.fetchone()

            if row:
                lead_id = row["id"]
                cursor.execute("""
                    UPDATE leads SET
                        business_name = COALESCE(?, business_name),
                        search_query = COALESCE(?, search_query),
                        fb_page_url = COALESCE(?, fb_page_url),
                        fb_followers = COALESCE(?, fb_followers),
                        ig_username = COALESCE(?, ig_username),
                        ig_profile_url = COALESCE(?, ig_profile_url),
                        ig_followers = COALESCE(?, ig_followers),
                        preferred_platform = COALESCE(?, preferred_platform),
                        preferred_profile_url = COALESCE(?, preferred_profile_url),
                        preferred_message_url = COALESCE(?, preferred_message_url),
                        website_found = ?,
                        has_website = ?,
                        ad_cta_type = COALESCE(?, ad_cta_type),
                        demo_link = COALESCE(?, demo_link),
                        custom_info = COALESCE(?, custom_info),
                        qualification_reason = COALESCE(?, qualification_reason),
                        status = COALESCE(?, status),
                        niche = COALESCE(?, niche),
                        geo = COALESCE(?, geo),
                        market = COALESCE(?, market),
                        hook_niche = COALESCE(?, hook_niche),
                        demo_niche = COALESCE(?, demo_niche),
                        updated_at = ?
                    WHERE id = ?
                """, (
                    biz_name,
                    search_query,
                    data.get("fb_page_url"),
                    data.get("fb_followers"),
                    data.get("ig_username"),
                    data.get("ig_profile_url"),
                    data.get("ig_followers"),
                    data.get("preferred_platform"),
                    data.get("preferred_profile_url"),
                    data.get("preferred_message_url"),
                    website_val,
                    has_site_val,
                    data.get("ad_cta_type"),
                    demo_link,
                    data.get("custom_info"),
                    data.get("qualification_reason"),
                    data.get("status"),
                    data.get("niche"),
                    data.get("geo"),
                    data.get("market"),
                    data.get("hook_niche"),
                    data.get("demo_niche"),
                    now,
                    lead_id
                ))
            else:
                cursor.execute("""
                    INSERT INTO leads (
                        advertiser_id, business_name, search_query, fb_page_url,
                        fb_followers, ig_username, ig_profile_url, ig_followers,
                        preferred_platform, preferred_profile_url, preferred_message_url,
                        website_found, has_website, ad_cta_type, demo_link, custom_info,
                        qualification_reason, status,
                        niche, geo, market, hook_niche, demo_niche,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    advertiser_id,
                    biz_name,
                    search_query,
                    data.get("fb_page_url", ""),
                    data.get("fb_followers", 0),
                    data.get("ig_username", ""),
                    data.get("ig_profile_url", ""),
                    data.get("ig_followers", 0),
                    data.get("preferred_platform", "FB"),
                    data.get("preferred_profile_url", ""),
                    data.get("preferred_message_url", ""),
                    website_val,
                    has_site_val,
                    data.get("ad_cta_type", ""),
                    demo_link,
                    data.get("custom_info", ""),
                    data.get("qualification_reason", ""),
                    data.get("status", "READY_TO_SEND"),
                    data.get("niche"),
                    data.get("geo"),
                    data.get("market"),
                    data.get("hook_niche"),
                    data.get("demo_niche"),
                    now,
                    now
                ))
                lead_id = cursor.lastrowid
            conn.commit()

        # Synchronize CSV immediately
        self.export_to_csv()
        return lead_id

    def update_custom_info(self, lead_id: int, custom_info: str) -> Optional[str]:
        """
        Updates lead custom_info and recomputes demo_link with &custom_info parameter.
        Returns the updated demo_link.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT business_name, search_query FROM leads WHERE id = ?", (lead_id,))
            row = cursor.fetchone()
            if not row:
                return None
            biz_name = row["business_name"]
            niche = detect_niche(row["search_query"])
            new_link = build_demo_link(biz_name, niche=niche, custom_info=custom_info)
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                UPDATE leads SET custom_info = ?, demo_link = ?, updated_at = ?
                WHERE id = ?
            """, (custom_info, new_link, now, lead_id))
            conn.commit()

        self.export_to_csv()
        return new_link

    def get_leads_ready_to_send(self, limit: int = 20, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns leads ready for outreach, sorted oldest first, optionally filtered by platform (FB or IG)."""
        # Lure передаёт выбранную нишу рассылки; пусто = все ниши
        niches = [n.strip() for n in os.environ.get("OUTREACH_NICHES", "").split(",") if n.strip()]
        niche_sql = f" AND niche IN ({','.join('?' * len(niches))})" if niches else ""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute(f"""
                    SELECT * FROM leads
                    WHERE status = 'READY_TO_SEND'
                    AND preferred_platform = ?{niche_sql}
                    ORDER BY id ASC
                    LIMIT ?
                """, (platform.upper(), *niches, limit))
            else:
                cursor.execute(f"""
                    SELECT * FROM leads
                    WHERE status = 'READY_TO_SEND'{niche_sql}
                    ORDER BY id ASC
                    LIMIT ?
                """, (*niches, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_sent_today_count(self, platform: Optional[str] = None) -> int:
        """Returns the number of messages sent in the current calendar date (UTC/Local), optionally filtered by platform."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute("""
                    SELECT COUNT(*) as count FROM leads
                    WHERE status = 'STEP_1_SENT'
                    AND preferred_platform = ?
                    AND sent_at >= ?
                """, (platform.upper(), cutoff))
            else:
                cursor.execute("""
                    SELECT COUNT(*) as count FROM leads
                    WHERE status = 'STEP_1_SENT'
                    AND sent_at >= ?
                """, (cutoff,))
            row = cursor.fetchone()
            return row["count"] if row else 0

    def get_seconds_since_last_sent(self, platform: Optional[str] = None) -> Optional[float]:
        """
        Returns the number of seconds elapsed since the last message was marked as STEP_1_SENT.
        If platform is specified ('FB' or 'IG'), checks that platform only.
        If platform is None, checks globally across all Meta platforms (cross-platform safety).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if platform:
                cursor.execute(
                    "SELECT sent_at FROM leads WHERE status = 'STEP_1_SENT' AND preferred_platform = ? AND sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 1",
                    (platform.upper(),)
                )
            else:
                cursor.execute(
                    "SELECT sent_at FROM leads WHERE status = 'STEP_1_SENT' AND sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 1"
                )
            row = cursor.fetchone()
            if not row or not row["sent_at"]:
                return None
            try:
                sent_time = datetime.fromisoformat(row["sent_at"])
                if sent_time.tzinfo is None:
                    sent_time = sent_time.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                return max(0.0, (now - sent_time).total_seconds())
            except Exception as e:
                logger.debug(f"Error parsing sent_at timestamp: {e}")
                return None

    def mark_as_sent(self, lead_id: int, message_text: str):
        """Marks lead as STEP_1_SENT with timestamp and message."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'STEP_1_SENT',
                    sent_message_text = ?,
                    sent_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (message_text, now, now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_failed(self, lead_id: int, reason: str):
        """Marks lead as FAILED_SEND with qualification reason."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'FAILED_SEND',
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (reason, now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_existing_chat(self, lead_id: int, reason: str = "Existing chat history detected in DM; Step 1 skipped"):
        """Marks lead as EXISTING_CHAT when previous messages are found in DM."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'EXISTING_CHAT',
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (reason, now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_unavailable(self, lead_id: int, reason: str = "Account unavailable or restricted in region"):
        """Marks lead as DISQUALIFIED_UNAVAILABLE so it is never retried in outreach runs."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'DISQUALIFIED_UNAVAILABLE',
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (reason, now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_has_website(self, lead_id: int, website_url: str):
        """Marks lead as qualified with website tag (High Priority in v3.0)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'READY_TO_SEND',
                    has_website = 1,
                    website_found = ?,
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (website_url, f"HAS_WEBSITE (HIGH_PRIORITY) — Verified Meta Advertiser: {website_url}", now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_disqualified_website(self, lead_id: int, website_url: str):
        """Сайт найден уже при отправке: лид не подходит под кампанию «только без сайта»."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE leads SET
                    status = 'DISQUALIFIED_HAS_WEBSITE',
                    has_website = 1,
                    website_found = ?,
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (website_url, f"Website found in profile: {website_url}", now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_skipped(self, lead_id: int, reason: str = "Skipped by user in Copilot mode"):
        """Marks lead as SKIPPED_BY_USER so it is not offered in future outreach runs."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'SKIPPED_BY_USER',
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (reason, now, lead_id))
            conn.commit()
        self.export_to_csv()

    def mark_as_ready_to_send(self, lead_id: int, reason: str = "Reset to READY_TO_SEND"):
        """Resets lead back to READY_TO_SEND, clearing sent_at and sent_message_text."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads SET
                    status = 'READY_TO_SEND',
                    sent_message_text = NULL,
                    sent_at = NULL,
                    qualification_reason = ?,
                    updated_at = ?
                WHERE id = ?
            """, (reason, now, lead_id))
            conn.commit()
        self.export_to_csv()

    def get_stats(self) -> Dict[str, int]:
        """Returns summary counts of all lead states in database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) as cnt FROM leads GROUP BY status")
            stats = {row["status"]: row["cnt"] for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) as total FROM leads")
            stats["TOTAL"] = cursor.fetchone()["total"]
            return stats

    def export_to_csv(self):
        """
        Exports entire leads table to CSV with UTF-8-sig encoding for perfect Excel compatibility.
        """
        fieldnames = [
            "id", "business_name", "status", "preferred_platform", "phone",
            "fb_followers", "ig_followers", "preferred_profile_url",
            "preferred_message_url", "fb_page_url", "ig_profile_url",
            "website_found", "has_website", "ad_cta_type", "demo_link", "custom_info",
            "qualification_reason", "sent_message_text",
            "sent_at", "search_query", "niche", "geo", "advertiser_id", "created_at"
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM leads ORDER BY id DESC")
            rows = cursor.fetchall()

        temp_csv = self.csv_path.with_name(f"leads_{os.getpid()}_{time.time_ns()}.tmp.csv")
        try:
            with open(temp_csv, mode="w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for r in rows:
                    writer.writerow(dict(r))

            temp_csv.replace(self.csv_path)
        except Exception as e:
            logger.debug(f"Notice during concurrent CSV export: {e}")
            if temp_csv.exists():
                try:
                    temp_csv.unlink()
                except Exception:
                    pass
        logger.debug(f"Exported {len(rows)} leads to {self.csv_path}")

    def clear_all_leads(self):
        """Completely purges all leads from database and resets CSV export."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM leads")
            try:
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='leads'")
            except Exception:
                pass
            conn.commit()
        self.export_to_csv()
        logger.info("All leads purged from database and CSV successfully.")
