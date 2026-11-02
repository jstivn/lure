import importlib.util
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Iterator, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # repository root
META_DIR = BASE_DIR / "meta-lead-gen"
META_DB_PATH = META_DIR / "data" / "leads.db"
WA_DB_PATH = BASE_DIR / "whatsapp-outreach" / "data" / "wa_leads.db"


_meta_modules: Dict[str, Any] = {}


def _load_meta_module(name: str):
    """
    Подгружает модуль из meta-lead-gen/core под уникальным именем.
    Обычный `from core.x import ...` здесь не работает: пакет `core` уже занят Lure.
    """
    if name in _meta_modules:
        return _meta_modules[name]
    path = META_DIR / "core" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"meta_lead_gen_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _meta_modules[name] = module
    return module


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class UnifiedDataManager:
    """Provides unified query and update capabilities across Meta and WhatsApp databases."""

    @staticmethod
    @contextmanager
    def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
        """Соединение, которое гарантированно закрывается (with sqlite3.connect только коммитит)."""
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_meta_conn(self):
        return self._connect(META_DB_PATH)

    def get_wa_conn(self):
        return self._connect(WA_DB_PATH)

    @staticmethod
    def get_wa_limits() -> Tuple[int, int]:
        profiles = _read_json(BASE_DIR / "whatsapp-outreach" / "config.json").get("profiles", {})
        try:
            return (
                int(profiles.get("profile_1", {}).get("daily_limit", 6)),
                int(profiles.get("profile_2", {}).get("daily_limit", 6)),
            )
        except (TypeError, ValueError):
            return 6, 6

    @staticmethod
    def get_meta_limits() -> Tuple[int, int]:
        limits = _read_json(META_DIR / "config.json").get("limits", {})
        try:
            return (
                int(limits.get("max_daily_messages_fb", 10)),
                int(limits.get("max_daily_messages_ig", 10)),
            )
        except (TypeError, ValueError):
            return 10, 10

    @staticmethod
    def get_meta_target_label() -> str:
        """Подпись канала на карточке: выбранные в кампании ниши и гео."""
        campaign = _read_json(META_DIR / "config.json").get("campaign", {})
        catalog = _read_json(META_DIR / "catalog.json")
        niches = [catalog.get("niches", {}).get(n, {}).get("label", n) for n in campaign.get("niches", [])]
        geos = [catalog.get("geos", {}).get(g, {}).get("label", g) for g in campaign.get("geos", [])]
        if not niches and not geos:
            return "Meta Ads"
        return f"{', '.join(geos) or 'Гео не выбрано'} ({', '.join(niches) or 'ниша не выбрана'})"

    @staticmethod
    def _calc_next_slot(oldest_iso: Optional[str], now_dt: datetime) -> Tuple[Optional[str], Optional[str]]:
        """Calculates time remaining until the oldest message in the 24h rolling window expires."""
        if not oldest_iso:
            return None, None
        try:
            sent_dt = datetime.fromisoformat(str(oldest_iso))
            if sent_dt.tzinfo is None and now_dt.tzinfo is not None:
                sent_dt = sent_dt.replace(tzinfo=now_dt.tzinfo)
            elif sent_dt.tzinfo is not None and now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=sent_dt.tzinfo)

            expires_dt = sent_dt + timedelta(hours=24)
            diff_sec = int((expires_dt - now_dt).total_seconds())
            if diff_sec <= 0:
                return None, None

            mins = diff_sec // 60
            hours = mins // 60
            rem_mins = mins % 60
            if hours > 0:
                next_in = f"через {hours}ч {rem_mins}м"
            else:
                next_in = f"через {rem_mins}м"

            local_time_str = expires_dt.astimezone().strftime("%H:%M")
            return next_in, local_time_str
        except Exception:
            return None, None

    def _channel_window(
        self,
        db_path: Path,
        sent_status: str,
        ready_status: str,
        account_col: str,
        account_value: str,
        now_dt: datetime,
        niches: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Отправлено за скользящие 24ч, ближайший освобождающийся слот и очередь для одного канала (по выбранным нишам)."""
        cutoff = (now_dt - timedelta(hours=24)).isoformat()
        out: Dict[str, Any] = {"sent": 0, "ready": 0, "next_in": None, "next_time": None}
        try:
            with self._connect(db_path) as conn:
                sent_where = f"status = ? AND {account_col} = ? AND sent_at >= ?"
                out["sent"] = conn.execute(
                    f"SELECT COUNT(*) FROM leads WHERE {sent_where}", (sent_status, account_value, cutoff)
                ).fetchone()[0]
                oldest = conn.execute(
                    f"SELECT sent_at FROM leads WHERE {sent_where} ORDER BY sent_at ASC LIMIT 1",
                    (sent_status, account_value, cutoff),
                ).fetchone()
                if oldest and oldest[0]:
                    out["next_in"], out["next_time"] = self._calc_next_slot(oldest[0], now_dt)
                niche_sql = f" AND niche IN ({','.join('?' * len(niches))})" if niches else ""
                out["ready"] = conn.execute(
                    f"SELECT COUNT(*) FROM leads WHERE status = ? AND {account_col} = ?{niche_sql}",
                    (ready_status, account_value, *(niches or [])),
                ).fetchone()[0]
        except Exception:
            pass
        return out

    @staticmethod
    def _channel_payload(name: str, target: str, color: str, limit: int, w: Dict[str, Any]) -> Dict[str, Any]:
        sent = w["sent"]
        return {
            "name": name,
            "target": target,
            "sent_today": sent,
            "daily_limit": limit,
            "available_slots": max(0, limit - sent),
            "ready_leads": w["ready"],
            "color": color,
            "percentage": min(100, int((sent / max(1, limit)) * 100)),
            "next_slot_in": w["next_in"],
            "next_slot_time": w["next_time"],
        }

    @staticmethod
    def get_email_stats() -> Dict[str, int]:
        """Очередь почтовой рассылки (лиды + автопрокаты), сколько писем ушло всего и за последние 24 часа."""
        try:
            return _load_meta_module("email_campaign").queue_counts()
        except Exception:
            return {"total": 0, "sent": 0, "pending": 0, "sent_24h": 0, "pending_by_source": {}}

    def get_channel_metrics(self, niches: Optional[list] = None) -> Dict[str, Any]:
        """Calculates rolling 24-hour metrics, available slots and next slot cooldown for all channels.
        niches: выбранная ниша рассылки, по ней считается очередь («В очереди»)."""
        fb_lim, ig_lim = self.get_meta_limits()
        wa1_lim, wa2_lim = self.get_wa_limits()
        meta_target = self.get_meta_target_label()

        # Meta хранит время в UTC с поясом, WhatsApp в локальном времени без пояса
        now_utc = datetime.now(timezone.utc)
        now_local = datetime.now()

        fb = self._channel_window(META_DB_PATH, "STEP_1_SENT", "READY_TO_SEND", "preferred_platform", "FB", now_utc, niches)
        ig = self._channel_window(META_DB_PATH, "STEP_1_SENT", "READY_TO_SEND", "preferred_platform", "IG", now_utc, niches)
        wa1 = self._channel_window(WA_DB_PATH, "SENT", "QUEUED", "assigned_account", "profile_1", now_local, niches)
        wa2 = self._channel_window(WA_DB_PATH, "SENT", "QUEUED", "assigned_account", "profile_2", now_local, niches)

        return {
            "fb": self._channel_payload("Facebook Messenger", meta_target, "#0a84ff", fb_lim, fb),
            "ig": self._channel_payload("Instagram Direct", meta_target, "#ff375f", ig_lim, ig),
            "wa1": self._channel_payload("WhatsApp Бизнес #1", "ОАЭ Дубай", "#30d158", wa1_lim, wa1),
            "wa2": self._channel_payload("WhatsApp Номер #2", "ОАЭ Дубай", "#ffd60a", wa2_lim, wa2),
            "total_sent_today": fb["sent"] + ig["sent"] + wa1["sent"] + wa2["sent"],
            "total_limit_today": fb_lim + ig_lim + wa1_lim + wa2_lim,
            "total_ready": fb["ready"] + ig["ready"] + wa1["ready"] + wa2["ready"],
            "email": self.get_email_stats(),
        }

    @staticmethod
    def _niche_label(row_niche: Optional[str], search_query: str, catalog: Dict[str, Any]) -> str:
        niches = catalog.get("niches", {})
        if row_niche in niches:
            return niches[row_niche].get("label", row_niche)
        if row_niche == "custom":
            return "Свои запросы"
        # лиды, собранные до появления каталога: определяем по тексту запроса
        sq = (search_query or "").lower()
        for nid, n in niches.items():
            if any(kw.lower() in sq for kw in n.get("keywords", [])):
                return n.get("label", nid)
        return "Без ниши"

    def get_all_leads(
        self,
        platform_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        hide_disqualified: bool = False
    ) -> List[Dict[str, Any]]:
        """Retrieves leads unified across all databases with sorting and filtering."""
        leads: List[Dict[str, Any]] = []
        catalog = _read_json(META_DIR / "catalog.json")
        geos = catalog.get("geos", {})
        meta_db = _load_meta_module("database")

        # 1. Fetch Meta leads
        if platform_filter in [None, "ALL", "FB", "IG", "META"]:
            try:
                with self.get_meta_conn() as conn:
                    query = "SELECT * FROM leads WHERE 1=1"
                    params: List[Any] = []

                    if platform_filter in ["FB", "IG"]:
                        query += " AND preferred_platform = ?"
                        params.append(platform_filter)

                    if status_filter and status_filter != "ALL":
                        query += " AND status = ?"
                        params.append(status_filter)

                    if hide_disqualified:
                        query += " AND status NOT LIKE 'DISQUALIFIED%'"

                    if search_query:
                        query += " AND (business_name LIKE ? OR search_query LIKE ?)"
                        params.extend([f"%{search_query}%", f"%{search_query}%"])

                    query += " ORDER BY id DESC LIMIT 150"
                    rows = conn.execute(query, params).fetchall()

                    for r in rows:
                        r_keys = r.keys()

                        def col(name, default=""):
                            return r[name] if name in r_keys and r[name] is not None else default

                        niche_label = self._niche_label(col("niche", None), r["search_query"], catalog)
                        geo_id = col("geo", None)
                        geo_label = geos.get(geo_id, {}).get("label", geo_id) if geo_id else ""
                        website_found = col("website_found", None)
                        has_website = col("has_website", 1 if website_found else 0)

                        leads.append({
                            "source": "META",
                            "id": r["id"],
                            "business_name": r["business_name"] or "Unknown Business",
                            "platform": r["preferred_platform"],
                            "profile_url": r["preferred_profile_url"] or r["fb_page_url"] or r["ig_profile_url"] or "",
                            "contact_info": r["ig_username"] or r["business_name"],
                            "email": col("email"),
                            "status": r["status"],
                            "niche": f"{niche_label} ({geo_label})" if geo_label else niche_label,
                            "sent_at": r["sent_at"],
                            "message_preview": (r["sent_message_text"] or "")[:60],
                            "website_found": website_found,
                            "has_website": has_website,
                            "ad_cta_type": col("ad_cta_type"),
                            "replied_at": col("replied_at", None),
                        })
            except Exception:
                pass

        # 2. Fetch WhatsApp leads
        if platform_filter in [None, "ALL", "WA", "WA1", "WA2"]:
            try:
                with self.get_wa_conn() as conn:
                    query = "SELECT * FROM leads WHERE 1=1"
                    params = []

                    if platform_filter == "WA1":
                        query += " AND assigned_account = 'profile_1'"
                    elif platform_filter == "WA2":
                        query += " AND assigned_account = 'profile_2'"

                    if status_filter and status_filter != "ALL":
                        # map status
                        if status_filter == "READY_TO_SEND":
                            query += " AND status = 'QUEUED'"
                        elif status_filter == "STEP_1_SENT":
                            query += " AND status = 'SENT'"
                        else:
                            query += " AND status = ?"
                            params.append(status_filter)

                    if search_query:
                        query += " AND (business_name LIKE ? OR phone LIKE ?)"
                        params.extend([f"%{search_query}%", f"%{search_query}%"])

                    query += " ORDER BY id DESC LIMIT 150"
                    rows = conn.execute(query, params).fetchall()

                    for r in rows:
                        acc_label = "WA1" if r["assigned_account"] == "profile_1" else "WA2"
                        leads.append({
                            "source": "WHATSAPP",
                            "id": r["id"],
                            "business_name": r["business_name"] or "WhatsApp Contact",
                            "platform": acc_label,
                            "profile_url": f"https://wa.me/{(r['phone'] or '').replace('+', '')}",
                            "contact_info": r["phone"],
                            "email": "",
                            "status": "READY_TO_SEND" if r["status"] == "QUEUED" else ("STEP_1_SENT" if r["status"] == "SENT" else r["status"]),
                            "niche": f"{r['niche'] or 'Detailing'} (Dubai UAE)",
                            "sent_at": r["sent_at"],
                            "message_preview": (r["sent_message"] or "")[:60],
                            "website_found": None,
                            "has_website": 0,
                            "ad_cta_type": "WhatsApp Direct",
                            "replied_at": r["replied_at"] if "replied_at" in r.keys() else None,
                        })
            except Exception:
                pass

        return leads

    def reset_lead_to_ready(self, source: str, lead_id: int) -> bool:
        """Resets lead back to ready status."""
        try:
            if source == "META":
                with self.get_meta_conn() as conn:
                    conn.execute("""
                        UPDATE leads SET status = 'READY_TO_SEND', sent_at = NULL, sent_message_text = NULL
                        WHERE id = ?
                    """, (lead_id,))
                return True
            elif source == "WHATSAPP":
                with self.get_wa_conn() as conn:
                    conn.execute("""
                        UPDATE leads SET status = 'QUEUED', sent_at = NULL, sent_message = NULL
                        WHERE id = ?
                    """, (lead_id,))
                return True
        except Exception:
            pass
        return False

    def ensure_reply_columns(self) -> None:
        """Колонки ответа: replied_at (в Meta уже есть) и reply_handled_at («отработал», ответ скрыт из списка)."""
        for opener in (self.get_wa_conn, self.get_meta_conn):
            try:
                with opener() as conn:
                    cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
                    for col in ("replied_at", "reply_handled_at", "last_reply_preview"):
                        if cols and col not in cols:
                            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
            except Exception:
                pass

    def mark_reply_handled(self, source: str, lead_id: int) -> bool:
        """Лид из списка ответов отработан: убираем его оттуда, replied_at остаётся, чтобы ответ не засчитался снова."""
        self.ensure_reply_columns()
        now = datetime.now(timezone.utc).isoformat()
        opener = self.get_meta_conn if source == "META" else self.get_wa_conn if source == "WHATSAPP" else None
        if opener is None:
            return False
        try:
            with opener() as conn:
                cur = conn.execute("UPDATE leads SET reply_handled_at = ? WHERE id = ? AND replied_at IS NOT NULL", (now, lead_id))
                return cur.rowcount > 0
        except Exception:
            return False

    def get_reply_candidates(self, channel: str) -> List[Dict[str, Any]]:
        """Все лиды канала, которым отправлено сообщение. Ответившие тоже остаются: от них ждём следующие сообщения
        (replied_at и last_reply_preview говорят, что уже учтено)."""
        out: List[Dict[str, Any]] = []
        try:
            self.ensure_reply_columns()
            if channel in ("fb", "ig"):
                with self.get_meta_conn() as conn:
                    rows = conn.execute(
                        "SELECT id, business_name, ig_username, sent_at, replied_at, last_reply_preview FROM leads "
                        "WHERE status = 'STEP_1_SENT' AND preferred_platform = ?",
                        (channel.upper(),),
                    ).fetchall()
                source = "META"
                for r in rows:
                    out.append({"source": source, "id": r["id"], "name": r["business_name"] or "",
                                "username": (r["ig_username"] or "").lstrip("@").lower(), "phone": "", "sent_at": r["sent_at"],
                                "replied_at": r["replied_at"], "last_preview": r["last_reply_preview"]})
            elif channel in ("wa1", "wa2"):
                account = "profile_1" if channel == "wa1" else "profile_2"
                with self.get_wa_conn() as conn:
                    rows = conn.execute(
                        "SELECT id, business_name, phone, sent_at, sent_message, replied_at, last_reply_preview FROM leads "
                        "WHERE status = 'SENT' AND assigned_account = ?",
                        (account,),
                    ).fetchall()
                for r in rows:
                    out.append({"source": "WHATSAPP", "id": r["id"], "name": r["business_name"] or "",
                                "username": "", "phone": re.sub(r"\D", "", r["phone"] or ""), "sent_at": r["sent_at"],
                                "sent_message": r["sent_message"] or "",
                                "replied_at": r["replied_at"], "last_preview": r["last_reply_preview"]})
        except Exception:
            pass
        return out

    def register_reply(self, source: str, lead_id: int, preview: str, notify: bool = True) -> bool:
        """Запоминает последнее входящее сообщение лида. notify=True: отмечает новый ответ (снова показывает лид в списке);
        notify=False: только запоминает текст (ответ, найденный до этой версии, повторно не объявляется)."""
        now = datetime.now(timezone.utc).isoformat()
        opener = self.get_meta_conn if source == "META" else self.get_wa_conn if source == "WHATSAPP" else None
        if opener is None:
            return False
        try:
            self.ensure_reply_columns()
            with opener() as conn:
                if notify:
                    cur = conn.execute(
                        "UPDATE leads SET replied_at = ?, reply_handled_at = NULL, last_reply_preview = ? WHERE id = ?",
                        (now, preview, lead_id))
                else:
                    cur = conn.execute("UPDATE leads SET last_reply_preview = ? WHERE id = ?", (preview, lead_id))
                return cur.rowcount > 0
        except Exception:
            return False

    def mark_replied(self, source: str, lead_id: int) -> bool:
        """Отмечает ответ лида один раз (время в UTC). False, если уже было отмечено."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            if source == "META":
                with self.get_meta_conn() as conn:
                    cur = conn.execute(
                        "UPDATE leads SET replied_at = ?, updated_at = ? WHERE id = ? AND replied_at IS NULL",
                        (now, now, lead_id),
                    )
                    return cur.rowcount > 0
            elif source == "WHATSAPP":
                self.ensure_reply_columns()
                with self.get_wa_conn() as conn:
                    cur = conn.execute(
                        "UPDATE leads SET replied_at = ? WHERE id = ? AND replied_at IS NULL", (now, lead_id)
                    )
                    return cur.rowcount > 0
        except Exception:
            pass
        return False

    def get_replies(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Лиды, которые ответили, новые сверху (Meta и WhatsApp вместе)."""
        replies: List[Dict[str, Any]] = []
        try:
            with self.get_meta_conn() as conn:
                for r in conn.execute(
                    "SELECT id, business_name, preferred_platform, preferred_profile_url, fb_page_url, ig_profile_url, "
                    "sent_at, replied_at FROM leads WHERE replied_at IS NOT NULL AND reply_handled_at IS NULL "
                    "ORDER BY replied_at DESC LIMIT ?",
                    (limit,),
                ).fetchall():
                    replies.append({
                        "source": "META", "id": r["id"], "name": r["business_name"] or "Unknown Business",
                        "channel": (r["preferred_platform"] or "FB").lower(),
                        "url": r["preferred_profile_url"] or r["fb_page_url"] or r["ig_profile_url"] or "",
                        "sent_at": r["sent_at"], "replied_at": r["replied_at"],
                    })
        except Exception:
            pass
        try:
            self.ensure_reply_columns()
            with self.get_wa_conn() as conn:
                for r in conn.execute(
                    "SELECT id, business_name, phone, assigned_account, sent_at, replied_at FROM leads "
                    "WHERE replied_at IS NOT NULL AND reply_handled_at IS NULL ORDER BY replied_at DESC LIMIT ?", (limit,),
                ).fetchall():
                    replies.append({
                        "source": "WHATSAPP", "id": r["id"], "name": r["business_name"] or r["phone"] or "WhatsApp Contact",
                        "channel": "wa1" if r["assigned_account"] == "profile_1" else "wa2",
                        "url": f"https://wa.me/{(r['phone'] or '').replace('+', '')}",
                        "sent_at": r["sent_at"], "replied_at": r["replied_at"],
                    })
        except Exception:
            pass
        replies.sort(key=lambda x: str(x["replied_at"]), reverse=True)
        return replies[:limit]

    def generate_mockup(self, source: str, lead_id: int) -> Dict[str, Any]:
        """
        Делает PNG-макет сайта для лида и кладёт его на рабочий стол.
        Рендер идёт отдельным процессом в venv meta-lead-gen (там Playwright).
        """
        import ctypes
        import shutil
        import subprocess

        if source == "META":
            cmd_args = ["--meta-id", str(lead_id)]
        elif source == "WHATSAPP":
            # WhatsApp-лиды без гео: все номера +971, значит Дубай
            with self.get_wa_conn() as conn:
                row = conn.execute("SELECT business_name FROM leads WHERE id = ?", (lead_id,)).fetchone()
            if not row:
                return {"success": False, "error": f"Лид #{lead_id} не найден"}
            cmd_args = ["--name", row["business_name"] or "", "--geo", "AE_DUBAI"]
        else:
            return {"success": False, "error": "Неизвестный источник лида"}

        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
        desktop = Path(buf.value) if buf.value else Path.home() / "Desktop"

        python_exe = META_DIR / "venv" / "Scripts" / "python.exe"
        tmp_dir = META_DIR / "data" / "mockups"
        proc = subprocess.run(
            [str(python_exe), "-m", "core.mockup", *cmd_args, "--out", str(tmp_dir)],
            cwd=str(META_DIR), capture_output=True, text=True, encoding="utf-8", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception:
            return {"success": False, "error": (proc.stderr or proc.stdout or "Рендер не удался")[-300:]}
        if "path" not in out:
            return {"success": False, "error": out.get("error", "Рендер не удался")}

        target = desktop / Path(out["path"]).name
        shutil.copyfile(out["path"], target)
        return {"success": True, "path": str(target)}


db_sync = UnifiedDataManager()
