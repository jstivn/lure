import sys
import os

# Under pythonw.exe, sys.stdout and sys.stderr are None.
# Redirect them to prevent crashes in formatters and libraries.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import ctypes
try:
    # Explicitly register AppUserModelID so Windows Taskbar pins and identifies Lure with its own icon
    app_user_model_id = "Billion.Lure.Outreach.2.0"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_user_model_id)
except Exception:
    pass

import re
import shutil
import socket
import subprocess
import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

from core.db_sync import META_DIR, _load_meta_module, _read_json, db_sync
from core import onboarding
from core.log_stream import log_stream
from core.orchestrator import orchestrator
from core.reply_watcher import reply_watcher


def ensure_local_configs() -> None:
    """First run: create each tool's config.json from its config.example.json (config.json stays out of git)."""
    for tool_dir in (META_DIR, META_DIR.parent / "whatsapp-outreach"):
        cfg, example = tool_dir / "config.json", tool_dir / "config.example.json"
        if not cfg.exists() and example.exists():
            shutil.copyfile(example, cfg)


ensure_local_configs()

@asynccontextmanager
async def lifespan(_: FastAPI):
    db_sync.ensure_reply_columns()
    reply_watcher.start()
    yield


app = FastAPI(title="Lure", version="2.0", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = BASE_DIR / "static" / "index.html"
    return FileResponse(str(index_file))


@app.get("/api/metrics")
async def get_metrics():
    """Returns real-time metrics and daily progress rings data."""
    metrics = db_sync.get_channel_metrics(orchestrator.get_wa_niches())
    metrics["is_running"] = orchestrator.is_running()
    metrics["status"] = orchestrator.get_status_snapshot()
    return metrics


@app.get("/api/status")
async def get_status():
    """Returns current live orchestrator status and active tasks."""
    return orchestrator.get_status_snapshot()


@app.get("/api/leads")
async def get_leads(
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    hide_disqualified: bool = Query(False)
):
    """Returns unified leads with optional search and filters."""
    return db_sync.get_all_leads(
        platform_filter=platform,
        status_filter=status,
        search_query=q,
        hide_disqualified=hide_disqualified
    )


class ResetLeadPayload(BaseModel):
    source: str
    lead_id: int


@app.post("/api/leads/reset")
async def reset_lead(payload: ResetLeadPayload):
    """Resets lead to ready status."""
    ok = db_sync.reset_lead_to_ready(payload.source, payload.lead_id)
    if ok:
        await log_stream.broadcast("SYSTEM", f"Лид #{payload.lead_id} ({payload.source}) возвращен в статус READY_TO_SEND", "success")
    return {"success": ok}


@app.post("/api/leads/mockup")
async def lead_mockup(payload: ResetLeadPayload):
    """Генерирует картинку-макет для лида и сохраняет её на рабочий стол."""
    res = await asyncio.to_thread(db_sync.generate_mockup, payload.source, payload.lead_id)
    if res.get("success"):
        await log_stream.broadcast("SYSTEM", f"Макет для лида #{payload.lead_id} сохранён: {res['path']}", "success")
    return res


@app.get("/api/replies")
async def get_replies():
    """Лиды, ответившие на первое сообщение, и состояние опросчика по каждому каналу."""
    return {"items": db_sync.get_replies(), "watcher": reply_watcher.snapshot()}


@app.post("/api/replies/handled")
async def reply_handled(payload: ResetLeadPayload):
    """Лид отработан: убирает его из списка ответов."""
    return {"success": db_sync.mark_reply_handled(payload.source, payload.lead_id)}


@app.post("/api/replies/check")
async def check_replies():
    """Запускает внеочередную проверку ответов."""
    if reply_watcher.checking:
        return {"status": "busy"}
    asyncio.create_task(reply_watcher.check_all())
    return {"status": "started"}


class WatcherPayload(BaseModel):
    enabled: bool


@app.post("/api/replies/watcher")
async def toggle_reply_watcher(payload: WatcherPayload):
    reply_watcher.set_enabled(payload.enabled)
    return reply_watcher.snapshot()


class ScrapeOptions(BaseModel):
    niches: List[str] = []
    geos: List[str] = []
    custom_keywords: List[str] = []
    max_ads: Optional[int] = None
    queries_per_pair: Optional[int] = None
    workers: Optional[int] = None
    allow_websites: bool = False


class ActionPayload(BaseModel):
    target: str
    options: Optional[ScrapeOptions] = None


@app.get("/api/scrape/options")
async def get_scrape_options():
    """Каталог ниш и гео для окна «Параметры поиска» + последний сохранённый выбор."""
    catalog = _read_json(META_DIR / "catalog.json")
    cfg = _read_json(META_DIR / "config.json")
    campaign = cfg.get("campaign", {})
    saved = orchestrator.load_scrape_selection()
    selection = {
        "niches": saved.get("niches", campaign.get("niches", [])),
        "geos": saved.get("geos", campaign.get("geos", [])),
        "custom_keywords": saved.get("custom_keywords", []),
        "max_ads": saved.get("max_ads") or cfg.get("search", {}).get("max_ads_per_query", 10),
        "queries_per_pair": saved.get("queries_per_pair") or campaign.get("queries_per_pair", 8),
        "allow_websites": bool(saved.get("allow_websites", campaign.get("website_policy") == "any")),
    }
    return {
        "niches": [
            {"id": nid, "label": n.get("label", nid), "keywords": n.get("keywords", [])}
            for nid, n in catalog.get("niches", {}).items()
        ],
        "geos": [
            {"id": gid, "label": g.get("label", gid), "country": g.get("country", ""), "cities": g.get("cities", [])}
            for gid, g in catalog.get("geos", {}).items()
        ],
        "presets": catalog.get("presets", []),
        "selection": selection,
    }


@app.post("/api/scrape/preview")
async def preview_scrape(options: ScrapeOptions):
    """Считает, сколько запросов получится из выбора, и показывает первые из них."""
    catalog_mod = _load_meta_module("catalog")
    try:
        plan = catalog_mod.build_plan(
            catalog_mod.load_catalog(),
            options.niches,
            options.geos,
            custom_keywords=options.custom_keywords,
            queries_per_pair=options.queries_per_pair or 8,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return {
        "queries": len(plan),
        "max_ads_total": len(plan) * (options.max_ads or 10),
        "sample": [{"query": p["query"], "country": p["country"]} for p in plan[:6]],
    }



class EmailRunPayload(BaseModel):
    count: int
    source: str = "all"
    niche: str = "all"
    delay_min: Optional[int] = None
    delay_max: Optional[int] = None


class EmailPreviewPayload(BaseModel):
    source: str = "all"
    niche: str = "all"
    count: int = 3


class EmailTestPayload(BaseModel):
    to: str
    source: str = "all"
    niche: str = "all"


@app.get("/api/email/options")
async def get_email_options():
    """Данные окна «Рассылка на почту»: очередь по источникам, лимиты, отправитель, что уже отправлено."""
    ec = _load_meta_module("email_campaign")
    settings = ec.load_settings()
    counts = await asyncio.to_thread(ec.queue_counts)
    return {
        "counts": counts,
        "sender": settings["sender"],
        "configured": bool(settings["sender"] and settings["app_password"]),
        "max_per_run": settings["max_per_run"],
        "delay_min": settings["delay_min_sec"],
        "delay_max": settings["delay_max_sec"],
        "recommended_daily": 50,
    }


class EmailTemplatePayload(BaseModel):
    subjects: List[str]
    body: str


@app.get("/api/email/template")
async def get_email_template():
    """Общий текст письма (темы по одной на строку и тело); переопределения по нишам остаются в файле."""
    template = _load_meta_module("email_campaign").load_template()
    return {"subjects": template.get("subjects", []), "body": template.get("body", "")}


@app.put("/api/email/template")
async def save_email_template(payload: EmailTemplatePayload):
    ec = _load_meta_module("email_campaign")
    subjects = [s.strip() for s in payload.subjects if s.strip()]
    if not subjects or not payload.body.strip():
        raise HTTPException(status_code=400, detail="Нужны хотя бы одна тема и текст письма")
    template = ec.load_template()
    template["subjects"], template["body"] = subjects, payload.body.strip()
    await asyncio.to_thread(
        ec.TEMPLATE_PATH.write_text, json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "saved"}


@app.post("/api/email/preview")
async def preview_email(payload: EmailPreviewPayload):
    """Несколько писем в том виде, в каком они уйдут: у каждого свой спинтакс, свои название, ниша и ссылка."""
    ec = _load_meta_module("email_campaign")
    if payload.source not in ("all", *ec.SOURCES):
        raise HTTPException(status_code=400, detail="неизвестный источник получателей")
    queue = await asyncio.to_thread(ec.build_queue, payload.source, payload.niche)
    template, settings = ec.load_template(), ec.load_settings()
    samples = []
    for item in queue[:max(1, min(payload.count, 5))]:
        subject, body = ec.render_message(item, template, settings)
        samples.append({"to": item["email"], "name": item["name"], "subject": subject, "body": body})
    return {"available": len(queue), "samples": samples}


@app.post("/api/email/start")
async def start_email(payload: EmailRunPayload):
    ec = _load_meta_module("email_campaign")
    if not ec.load_settings()["app_password"]:
        raise HTTPException(status_code=400, detail="Почта не подключена: откройте «Настройку» и подключите Gmail")
    started = await orchestrator.start_channel("email_send", payload.model_dump())
    return {"status": "started" if started else "rejected"}


@app.post("/api/email/test")
async def test_email(payload: EmailTestPayload):
    """Одно настоящее письмо на указанный адрес, чтобы проверить Gmail и увидеть текст в почтовом клиенте."""
    to = payload.to.strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}", to):
        raise HTTPException(status_code=400, detail="некорректный адрес")
    if payload.source not in ("all", "leads", "rentals"):
        raise HTTPException(status_code=400, detail="неизвестный источник получателей")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", payload.niche):
        raise HTTPException(status_code=400, detail="неизвестная ниша получателей")
    meta_dir = META_DIR
    python_exe = meta_dir / "venv" / "Scripts" / "python.exe"
    cmd = [str(python_exe if python_exe.exists() else sys.executable), "send_emails.py",
           "--test", to, "--source", payload.source, "--niche", payload.niche]

    def run():
        return subprocess.run(cmd, cwd=str(meta_dir), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90, env={**os.environ, "PYTHONUTF8": "1"},
                              creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    try:
        res = await asyncio.to_thread(run)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Gmail не ответил за 90 секунд"}
    out = (res.stdout or "") + (res.stderr or "")
    ok = res.returncode == 0
    await log_stream.broadcast("EMAIL", f"Тестовое письмо на {to}: {'доставлено' if ok else 'не отправлено'}",
                               "success" if ok else "error")
    return {"success": ok, "error": "" if ok else (out.strip().splitlines() or ["ошибка отправки"])[-1][:300]}


class WaNichePayload(BaseModel):
    niche: str


@app.get("/api/wa/niches")
async def get_wa_niches():
    """Ниши в очереди WhatsApp (сколько ждёт отправки) и текущий выбор ниши для рассылки."""
    import sqlite3
    wa_db = META_DIR.parent / "whatsapp-outreach" / "data" / "wa_leads.db"
    meta_db = META_DIR / "data" / "leads.db"

    def read():
        # {ниша: {"fb": n, "ig": n, "wa": n}} по очередям всех каналов
        out: Dict[str, Dict[str, int]] = {}
        # A fresh install has empty database files without tables yet: that just means no queue
        try:
            if meta_db.exists():
                with sqlite3.connect(meta_db) as conn:
                    for niche, plat, c in conn.execute(
                            "SELECT niche, preferred_platform, COUNT(*) FROM leads WHERE status = 'READY_TO_SEND' GROUP BY 1, 2"):
                        out.setdefault(niche or "", {})[(plat or "").lower()] = c
        except sqlite3.OperationalError:
            pass
        try:
            if wa_db.exists():
                with sqlite3.connect(wa_db) as conn:
                    for niche, c in conn.execute("SELECT niche, COUNT(*) FROM leads WHERE status = 'QUEUED' GROUP BY 1"):
                        out.setdefault(niche or "", {})["wa"] = c
        except sqlite3.OperationalError:
            pass
        return out

    queues = await asyncio.to_thread(read)
    catalog = _read_json(META_DIR / "catalog.json").get("niches", {})
    current = orchestrator.get_wa_niche()
    if current == "auto":
        last = orchestrator.load_scrape_selection().get("niches", [])
        current = last[0] if last else "all"
    return {
        "current": current,
        "niches": sorted(
            [{"id": n, "label": "Свои запросы" if n == "custom" else catalog.get(n, {}).get("label", n),
              "fb": q.get("fb", 0), "ig": q.get("ig", 0), "wa": q.get("wa", 0)} for n, q in queues.items() if n],
            key=lambda x: -(x["fb"] + x["ig"] + x["wa"])),
    }


@app.post("/api/wa/niche")
async def set_wa_niche(payload: WaNichePayload):
    niche = payload.niche.strip()
    if niche not in ("all", "auto") and not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", niche):
        raise HTTPException(status_code=400, detail="недопустимая ниша")
    orchestrator.set_wa_niche(niche)
    return {"current": niche}


class EmailSetupPayload(BaseModel):
    sender: str
    app_password: str
    sender_name: str = ""


class LoginConfirmPayload(BaseModel):
    channel: str
    confirmed: bool = True


class WhatsAppSetupPayload(BaseModel):
    api_url: str = ""
    profile_1: str = ""
    profile_2: str = ""


class SetupDonePayload(BaseModel):
    completed: bool = True


@app.get("/api/setup/status")
async def get_setup_status():
    """First-run wizard state: Chrome, Gmail, Facebook/Instagram logins, WhatsApp profiles."""
    return await asyncio.to_thread(onboarding.status)


@app.post("/api/setup/email")
async def setup_email(payload: EmailSetupPayload):
    """Checks the Gmail login and, if it works, stores it in meta-lead-gen/config.json."""
    return await asyncio.to_thread(onboarding.save_email, payload.sender, payload.app_password, payload.sender_name)


@app.post("/api/setup/login")
async def setup_login(payload: LoginConfirmPayload):
    """Marks Facebook/Instagram as logged in (or not) in the dedicated Chrome profile."""
    return await asyncio.to_thread(onboarding.set_login, payload.channel, payload.confirmed)


@app.post("/api/setup/whatsapp")
async def setup_whatsapp(payload: WhatsAppSetupPayload):
    return await asyncio.to_thread(onboarding.save_whatsapp, payload.api_url, payload.profile_1, payload.profile_2)


@app.post("/api/setup/done")
async def setup_done(payload: SetupDonePayload):
    await asyncio.to_thread(onboarding.set_completed, payload.completed)
    return {"success": True}


@app.post("/api/action/start")
async def start_action(payload: ActionPayload):
    """Starts an automation outreach action."""
    blocked = await asyncio.to_thread(onboarding.blocker, payload.target)
    if blocked:
        raise HTTPException(status_code=400, detail=blocked)
    options = payload.options.model_dump() if payload.options else None
    started = await orchestrator.start_channel(payload.target, options)
    return {"status": "started" if started else "rejected", "target": payload.target}


@app.post("/api/action/stop")
async def stop_action(payload: ActionPayload):
    """Stops automation tasks."""
    await orchestrator.stop_channel(payload.target)
    return {"status": "stopped", "target": payload.target}


@app.post("/api/action/send_now")
async def send_now(payload: ActionPayload):
    """Skips the safety timer of a running channel (fb, ig, wa1, wa2)."""
    ok = await orchestrator.send_now(payload.target)
    return {"status": "skipped" if ok else "rejected", "target": payload.target}


class OpenBrowserPayload(BaseModel):
    channel: str
    url: Optional[str] = None


@app.post("/api/action/open_browser")
async def open_browser(payload: OpenBrowserPayload):
    """Opens isolated browser for specified channel and optional URL."""
    res = await orchestrator.open_browser(payload.channel, payload.url)
    return res


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await log_stream.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        log_stream.disconnect(websocket)
    except Exception:
        log_stream.disconnect(websocket)


def is_port_in_use(port: int = 8765) -> bool:
    """Checks if the backend port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_server():
    """Runs uvicorn in background thread without console formatters to support pythonw.exe."""
    uvicorn.run(app, host="127.0.0.1", port=8765, log_config=None)


def setup_icons(window, icon_path: str):
    """Configures both Taskbar WM_SETICON and System Tray Notification Icon."""
    import time
    time.sleep(0.4)
    try:
        # 1. Send WM_SETICON directly to the Win32 window handle
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040

        hicon_big = ctypes.windll.user32.LoadImageW(
            0, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        hicon_small = ctypes.windll.user32.LoadImageW(
            0, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE
        )
        hwnd = ctypes.windll.user32.FindWindowW(None, "Lure")
        if hwnd:
            if hicon_big:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
            if hicon_small:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
    except Exception:
        pass

    try:
        # 2. System Tray (Notification Area near the clock)
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Windows.Forms import NotifyIcon
        from System.Drawing import Icon

        tray = NotifyIcon()
        tray.Icon = Icon(icon_path)
        tray.Text = "Lure - Outreach System"

        def on_tray_click(sender, e):
            try:
                window.restore()
                hwnd = ctypes.windll.user32.FindWindowW(None, "Lure")
                if hwnd:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

        tray.Click += on_tray_click
        tray.Visible = True

        def on_closed():
            try:
                tray.Visible = False
                tray.Dispose()
            except Exception:
                pass

        window.events.closed += on_closed
    except Exception:
        pass


def main():
    import time

    # Only start background server if not already running on port 8765
    if not is_port_in_use(8765):
        t = threading.Thread(target=run_server, daemon=True)
        t.start()
        
        # Wait until port 8765 is actually listening before launching the window
        for _ in range(50):
            time.sleep(0.1)
            if is_port_in_use(8765):
                break

    # Try PyWebView for native window
    try:
        import webview
        window = webview.create_window(
            title="Lure",
            url="http://127.0.0.1:8765",
            width=1340,
            height=890,
            min_size=(1020, 700),
            background_color="#F5F5F7",
            text_select=True
        )
        icon_path = str(BASE_DIR / "static" / "img" / "logo.ico")
        webview.start(func=lambda: setup_icons(window, icon_path), icon=icon_path)
        sys.exit(0)
    except Exception:
        # Fallback to Edge / Chrome App mode (looks 100% native too)
        import subprocess
        edge_path = shutil.which("msedge") or os.path.join(
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.exists(edge_path):
            p = subprocess.Popen([edge_path, "--app=http://127.0.0.1:8765", "--window-size=1340,890"])
            p.wait()
            sys.exit(0)
        else:
            import webbrowser
            webbrowser.open("http://127.0.0.1:8765")
        
        # Keep main thread alive if browser fallback
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
