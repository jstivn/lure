"""First-run setup: what is configured, what is missing, and the writers behind the setup wizard.

Secrets (Gmail app password) go only into the tool's local, git-ignored config.json and are never returned by the API.
"""

import json
import os
import re
import shutil
import smtplib
import ssl
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from core.db_sync import BASE_DIR, META_DIR

WA_DIR = BASE_DIR / "whatsapp-outreach"
META_CONFIG = META_DIR / "config.json"
WA_CONFIG = WA_DIR / "config.json"
STATE_PATH = META_DIR / "data" / "setup.json"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
LOGIN_CHANNELS = ("fb", "ig")
CHANNEL_TITLES = {"fb": "Facebook", "ig": "Instagram"}


def find_chrome() -> Optional[str]:
    """LURE_CHROME_PATH, standard install folders, then PATH. None when Chrome is not installed."""
    candidates = [os.environ.get("LURE_CHROME_PATH", "")]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(var)
        if root:
            candidates.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return shutil.which("chrome") or shutil.which("google-chrome")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomic write: a crash mid-save must not leave a half-written config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _state() -> Dict[str, Any]:
    state = _read_json(STATE_PATH)
    state.setdefault("completed", False)
    state.setdefault("logins", {})
    return state


def _profile_dir(channel: str) -> Path:
    return META_DIR / "data" / "profiles" / f"chrome_{channel}"


def status() -> Dict[str, Any]:
    """Everything the wizard shows. Never includes the app password."""
    email = _read_json(META_CONFIG).get("email", {})
    wa = _read_json(WA_CONFIG)
    profiles = wa.get("profiles", {})
    state = _state()
    chrome = find_chrome()
    sender = str(email.get("sender", "")).strip()
    return {
        "completed": bool(state["completed"]),
        "chrome": {"ok": bool(chrome), "path": chrome or ""},
        "email": {
            "ok": bool(sender and email.get("app_password")),
            "sender": sender,
            "sender_name": str(email.get("sender_name", "")),
        },
        "logins": {
            ch: {"ok": bool(state["logins"].get(ch)), "profile_exists": _profile_dir(ch).exists()}
            for ch in LOGIN_CHANNELS
        },
        "whatsapp": {
            "ok": bool(profiles.get("profile_1", {}).get("id") and profiles.get("profile_2", {}).get("id")),
            "api_url": wa.get("bitbrowser_api_url", "http://127.0.0.1:54345"),
            "profile_1": profiles.get("profile_1", {}).get("id", ""),
            "profile_2": profiles.get("profile_2", {}).get("id", ""),
        },
    }


def save_email(sender: str, app_password: str, sender_name: str) -> Dict[str, Any]:
    """Checks the credentials against the SMTP server first, saves them only if the login works."""
    sender = (sender or "").strip()
    password = re.sub(r"\s+", "", app_password or "")  # Google shows app passwords in groups of four
    if not EMAIL_RE.match(sender):
        return {"success": False, "error": "Укажите адрес почты полностью, например name@gmail.com."}
    if not password:
        return {"success": False, "error": "Вставьте пароль приложения: 16 символов из настроек аккаунта Google."}

    config = _read_json(META_CONFIG)
    email = config.get("email", {})
    host, port = email.get("smtp_host", "smtp.gmail.com"), int(email.get("smtp_port", 587))
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(sender, password)
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Gmail не принял вход. Нужен пароль приложения (16 символов), а не обычный пароль аккаунта."}
    except (OSError, smtplib.SMTPException) as err:
        return {"success": False, "error": f"Не удалось связаться с {host}: {err}. Проверьте интернет и попробуйте снова."}

    email.update({"sender": sender, "app_password": password, "sender_name": (sender_name or "").strip()})
    config["email"] = email
    _write_json(META_CONFIG, config)
    return {"success": True}


def set_login(channel: str, confirmed: bool) -> Dict[str, Any]:
    if channel not in LOGIN_CHANNELS:
        return {"success": False, "error": "Неизвестный канал."}
    state = _state()
    state["logins"][channel] = bool(confirmed)
    _write_json(STATE_PATH, state)
    return {"success": True}


def save_whatsapp(api_url: str, profile_1: str, profile_2: str) -> Dict[str, Any]:
    api_url = (api_url or "").strip() or "http://127.0.0.1:54345"
    if not re.match(r"^https?://", api_url):
        return {"success": False, "error": "Адрес API должен начинаться с http:// или https://."}
    config = _read_json(WA_CONFIG)
    config["bitbrowser_api_url"] = api_url.rstrip("/")
    profiles = config.setdefault("profiles", {})
    for key, value in (("profile_1", profile_1), ("profile_2", profile_2)):
        profiles.setdefault(key, {"key": key, "name": key}).update({"id": (value or "").strip()})
    _write_json(WA_CONFIG, config)
    return {"success": True}


def set_completed(completed: bool) -> None:
    state = _state()
    state["completed"] = bool(completed)
    _write_json(STATE_PATH, state)


def blocker(target: str) -> Optional[str]:
    """Why `target` cannot start yet, in words for the user; None when everything it needs is set up."""
    current = status()
    meta_channels = {"meta_fb": ("fb",), "meta_ig": ("ig",), "meta_parallel": LOGIN_CHANNELS, "all": LOGIN_CHANNELS}
    if target in meta_channels or target in ("meta_scrape", "gmaps_scrape"):
        if not current["chrome"]["ok"]:
            return "Google Chrome не найден. Установите его и откройте «Настройку»."
    for channel in meta_channels.get(target, ()):
        if not current["logins"][channel]["ok"]:
            return f"Сначала подключите {CHANNEL_TITLES[channel]} в «Настройке»: войдите в аккаунт и подтвердите вход."
    if target in ("wa_parallel", "wa_wp1", "wa_wp2") and not current["whatsapp"]["ok"]:
        return "Укажите ID профилей BitBrowser для WhatsApp в «Настройке»."
    return None
