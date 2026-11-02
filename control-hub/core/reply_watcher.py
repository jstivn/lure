import asyncio
import json
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from core.db_sync import db_sync
from core.log_stream import log_stream
from core.orchestrator import orchestrator

CHANNELS = ("fb", "ig", "wa1", "wa2")
CHANNEL_TITLES = {"fb": "Facebook", "ig": "Instagram", "wa1": "WhatsApp #1", "wa2": "WhatsApp #2"}
META_PORTS = {"fb": 9333, "ig": 9334}
TAB_HOSTS = {"ig": "instagram.com", "wa1": "web.whatsapp.com", "wa2": "web.whatsapp.com"}
FB_INBOX_URL = "https://www.facebook.com/messages/"
IG_INBOX_URL = "https://www.instagram.com/direct/inbox/"

# Instagram: служебный запрос direct_v2/inbox больше не отвечает (404), поэтому читаем список чатов со страницы Direct.
# Строка: название чата, затем превью последнего сообщения (наше начинается с «Вы:»), затем время и счётчики.
JS_IG = """
(() => {
  const out = [];
  document.querySelectorAll('div[role="listitem"], div[role="button"][tabindex="0"]').forEach(row => {
    const lines = (row.innerText || '').split('\\n').map(s => s.replace(/[\\u200e\\u200f]/g, '').trim())
      .filter(s => s && s !== '·' && !/^\\d+\\s*[а-яa-z]{0,4}\\.?$/i.test(s));
    if (lines.length < 2) return;
    out.push({title: lines.slice(0, -1).join(' '), preview: lines[lines.length - 1]});
  });
  return out;
})()
"""

# Messenger: строки списка чатов; ответом считаем непрочитанный чат или превью, которое написали не мы
JS_FB = """
(() => {
  const out = [], seen = new Set();
  document.querySelectorAll('a[href*="/t/"]').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (!/\\/(messages\\/)?t\\/\\d+/.test(href) || seen.has(href)) return;
    seen.add(href);
    const row = a.closest('[role="row"], [role="listitem"], li') || a;
    const lines = (row.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    if (!lines.length) return;
    const flags = (row.getAttribute('aria-label') || '') + ' ' + lines.join(' ');
    const unread = /unread|непрочитан/i.test(flags) || !!row.querySelector('[aria-label*="nread"], [aria-label*="епрочитан"]');
    out.push({title: lines[0], preview: lines[1] || '', unread, href});
  });
  return out;
})()
"""

# WhatsApp Web: чаты с зелёным бейджем непрочитанных и превью последнего сообщения
# (если чат уже открыт и прочитан, бейджа нет, поэтому ответ определяем ещё и по превью)
JS_WA = """
(() => {
  const out = [];
  document.querySelectorAll('#pane-side [role="listitem"], #pane-side [role="row"]').forEach(row => {
    const titleEl = row.querySelector('span[title]');
    if (!titleEl) return;
    const unread = !!row.querySelector('[aria-label*="nread"], [aria-label*="епрочитан"]');
    const texts = [...row.querySelectorAll('span[dir]')].map(e => (e.innerText || '').trim()).filter(Boolean);
    // галочка доставки перед превью: последнее сообщение в чате наше
    const outgoing = !!row.querySelector('span[data-icon^="msg-"], [data-icon^="status-"]');
    out.push({title: titleEl.getAttribute('title') || '', unread, outgoing, preview: texts[1] || ''});
  });
  return out;
})()
"""

NAME_STOPWORDS = {
    "the", "and", "dubai", "uae", "llc", "salon", "center", "centre", "beauty", "clinic", "club", "spa",
    "medical", "dental", "aesthetics", "aesthetic", "lounge", "studio",
}
OWN_PREFIX = re.compile(r"^\s*(you|вы|أنت)\s*:", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", "", (text or "").lower())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _name_match(lead_name: str, title: str) -> bool:
    a, b = _norm(lead_name), _norm(title)
    if len(a) < 3 or len(b) < 3:
        return False
    if a == b or a in b or b in a:
        return True
    # "Arfi Nails Al Barsha" в базе и "Arfi Nails Barsha" в контактах: все слова короткого названия есть в длинном
    ta, tb = _tokens(lead_name), _tokens(title)
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return bool(small) and (len(small) >= 2 or any(len(w) >= 5 for w in small)) and small <= big


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"\w+", (text or "").lower()) if len(w) >= 3 and w not in NAME_STOPWORDS}


def _is_own_message(preview: str, sent: str) -> bool:
    """Превью чата совпадает с нашим первым сообщением, значит, ответа ещё нет."""
    p = _norm(re.sub(r"^\s*(you|вы)\s*:\s*", "", preview or "", flags=re.IGNORECASE))
    s = _norm(sent)
    return bool(p) and bool(s) and (s.startswith(p) or p.startswith(s))


class ReplyWatcher:
    """Раз в несколько минут заходит в уже открытые профили, видит новые ответы и отмечает их в базе лидов."""

    # Как часто смотрим список чатов. WhatsApp и открытая вкладка входящих читаются без перезагрузки, поэтому можно часто.
    INTERVALS = {"fb": 20, "ig": 20, "wa1": 10, "wa2": 10}
    # Вкладку входящих Messenger/Instagram перезагружаем редко: между перезагрузками страница сама получает новые чаты
    INBOX_RELOAD_SEC = 240

    def __init__(self):
        self.enabled = True
        self.interval = min(self.INTERVALS.values())
        self.tasks: List[asyncio.Task] = []
        self.locks: Dict[str, asyncio.Lock] = {ch: asyncio.Lock() for ch in CHANNELS}
        self.inbox_targets: Dict[str, str] = {}
        self.inbox_loaded_at: Dict[str, float] = {}
        self.cdp_cache: Dict[str, tuple] = {}
        self.channels: Dict[str, Dict[str, Any]] = {
            ch: {"state": "wait", "text": "Ожидание первой проверки", "last_check": None, "found": 0}
            for ch in CHANNELS
        }

    @property
    def checking(self) -> bool:
        return any(lock.locked() for lock in self.locks.values())

    def snapshot(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "interval": self.interval, "checking": self.checking, "channels": self.channels}

    def start(self):
        if not any(not t.done() for t in self.tasks):
            self.tasks = [asyncio.create_task(self._loop(ch)) for ch in CHANNELS]

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    async def _loop(self, ch: str):
        """Каждый канал опрашивается своим циклом: медленный или зависший не тормозит остальные."""
        await asyncio.sleep(5 + 2 * CHANNELS.index(ch))
        async with aiohttp.ClientSession() as session:
            while True:
                if self.enabled:
                    await self._check_locked(session, ch)
                await asyncio.sleep(self.INTERVALS[ch] * random.uniform(0.85, 1.15))

    async def _check_locked(self, session: aiohttp.ClientSession, ch: str, force_reload: bool = False):
        if self.locks[ch].locked():
            return
        async with self.locks[ch]:
            if force_reload:
                self.inbox_loaded_at.pop(ch, None)
            await self._check_channel(session, ch)

    async def check_all(self):
        """Внеочередная проверка всех каналов сразу, со свежей перезагрузкой входящих."""
        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*(self._check_locked(session, ch, force_reload=True) for ch in CHANNELS))

    def _set(self, ch: str, state: str, text: str, found: int = 0):
        self.channels[ch].update(state=state, text=text, found=found, last_check=datetime.now().strftime("%H:%M:%S"))

    async def _check_channel(self, session: aiohttp.ClientSession, ch: str):
        candidates = db_sync.get_reply_candidates(ch)
        if not candidates:
            self._set(ch, "idle", "Нет лидов, ожидающих ответа")
            return
        try:
            http = await self._cdp_http(session, ch)
            if not http:
                self._set(ch, "off", "Браузер канала не открыт")
                return
            items = await self._read_channel(session, ch, http)
            if items is None:
                self._set(ch, "off", "Не открыта нужная вкладка (войдите в аккаунт)")
                return
            fresh = 0
            waiting = 0
            for lead in candidates:
                preview = self._incoming(ch, lead, items)
                if preview is None:
                    waiting += 1
                    continue
                key, last = _norm(preview), _norm(lead.get("last_preview") or "")
                if lead.get("replied_at") and not lead.get("last_preview"):
                    # ответ найден ещё до учёта текста: запоминаем его и не объявляем заново
                    db_sync.register_reply(lead["source"], lead["id"], preview, notify=False)
                elif lead.get("replied_at") and (not key or key == last):
                    pass  # то же сообщение, что уже учтено
                elif db_sync.register_reply(lead["source"], lead["id"], preview or "(новое сообщение)"):
                    fresh += 1
                    await self._notify(ch, lead, again=bool(lead.get("replied_at")))
            self._set(ch, "ok", f"Проверено, ждут ответа: {waiting}", fresh)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set(ch, "error", f"Ошибка проверки: {str(e)[:80]}")

    async def _notify(self, ch: str, lead: Dict[str, Any], again: bool = False):
        name = lead["name"] or lead.get("phone") or "Лид"
        await log_stream.broadcast(
            channel=ch.upper(),
            message=f"💬 {'Новое сообщение' if again else 'Ответ'} от {name} ({CHANNEL_TITLES[ch]})",
            level="success",
            event_type="reply_received",
            data={"channel": ch, "lead_name": name, "source": lead["source"], "lead_id": lead["id"]},
        )

    # ---------- подключение к браузерам ----------

    async def _cdp_http(self, session: aiohttp.ClientSession, ch: str) -> Optional[str]:
        """Адрес CDP уже запущенного браузера канала. Закрытые профили не запускаем."""
        if ch in META_PORTS:
            http = f"127.0.0.1:{META_PORTS[ch]}"
            try:
                async with session.get(f"http://{http}/json/version", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    return http if resp.status == 200 else None
            except Exception:
                return None

        cached = self.cdp_cache.get(ch)
        if cached and time.monotonic() - cached[1] < 60:
            return cached[0]
        cfg = orchestrator._wa_settings()
        profile_id = cfg.get("profiles", {}).get("profile_1" if ch == "wa1" else "profile_2", {}).get("id")
        api_url = cfg.get("bitbrowser_api_url", "http://127.0.0.1:54345")
        if not profile_id:
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.post(f"{api_url}/browser/pids", json={"ids": [profile_id]}, timeout=timeout) as resp:
                running = (await resp.json()).get("data") or {}
            if profile_id not in running:
                return None
            async with session.post(f"{api_url}/browser/open", json={"id": profile_id}, timeout=timeout) as resp:
                data = (await resp.json()).get("data") or {}
            http = data.get("http") if isinstance(data, dict) else None
            if http:
                self.cdp_cache[ch] = (http, time.monotonic())
            return http
        except Exception:
            self.cdp_cache.pop(ch, None)
            return None

    async def _list_pages(self, session: aiohttp.ClientSession, http: str) -> List[Dict[str, Any]]:
        async with session.get(f"http://{http}/json/list", timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return [t for t in await resp.json() if t.get("type") == "page"]

    @staticmethod
    async def _cdp_call(ws, msg_id: int, method: str, params: Dict[str, Any], timeout: float = 25.0) -> Dict[str, Any]:
        await ws.send_json({"id": msg_id, "method": method, "params": params})

        async def _wait():
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("id") == msg_id:
                    return data
            raise ConnectionError("CDP-соединение закрыто")

        return await asyncio.wait_for(_wait(), timeout)

    async def _evaluate(self, session: aiohttp.ClientSession, ws_url: str, expression: str) -> Any:
        async with session.ws_connect(ws_url, max_msg_size=0) as ws:
            res = await self._cdp_call(ws, 1, "Runtime.evaluate", {
                "expression": expression, "awaitPromise": True, "returnByValue": True,
            })
        result = res.get("result", {})
        if "exceptionDetails" in result:
            raise RuntimeError("ошибка скрипта на странице")
        return result.get("result", {}).get("value")

    async def _read_channel(self, session: aiohttp.ClientSession, ch: str, http: str) -> Optional[List[Dict[str, Any]]]:
        if ch == "fb":
            return await self._read_inbox_tab(session, http, ch, "facebook.com", FB_INBOX_URL, JS_FB)
        if ch == "ig":
            return await self._read_inbox_tab(session, http, ch, "instagram.com", IG_INBOX_URL, JS_IG)
        pages = await self._list_pages(session, http)
        page = next((p for p in pages if TAB_HOSTS[ch] in (p.get("url") or "")), None)
        if not page:
            return None
        return await self._evaluate(session, page["webSocketDebuggerUrl"], JS_WA) or []

    async def _read_inbox_tab(self, session: aiohttp.ClientSession, http: str, ch: str, host: str, inbox_url: str,
                              script: str) -> Optional[List[Dict[str, Any]]]:
        """Для Messenger и Instagram держим одну фоновую вкладку входящих. Список чатов читаем прямо с неё: страница сама
        получает новые сообщения. Перезагружаем только раз в несколько минут или если вкладка ушла со страницы входящих."""
        pages = await self._list_pages(session, http)
        if not any(host in (p.get("url") or "") for p in pages):
            return None  # аккаунт не открыт, входить сюда сами не будем
        marker = "/direct" if ch == "ig" else "facebook.com/messages"
        page = next((p for p in pages if p.get("id") == self.inbox_targets.get(ch)), None)
        if not page:
            # После перезапуска Lure id вкладки забыт: берём уже открытую вкладку входящих, а не плодим новые
            page = next((p for p in pages if host in (p.get("url") or "") and marker in (p.get("url") or "")), None)
            if page:
                self.inbox_targets[ch] = page["id"]
        loaded_at = self.inbox_loaded_at.get(ch, 0.0)
        stale = time.monotonic() - loaded_at > self.INBOX_RELOAD_SEC
        if not page:
            async with session.get(f"http://{http}/json/version", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                browser_ws = (await resp.json())["webSocketDebuggerUrl"]
            async with session.ws_connect(browser_ws, max_msg_size=0) as ws:
                res = await self._cdp_call(ws, 1, "Target.createTarget", {"url": inbox_url, "background": True})
            self.inbox_targets[ch] = res["result"]["targetId"]
            page = {"webSocketDebuggerUrl": f"ws://{http}/devtools/page/{self.inbox_targets[ch]}"}
            reload = True
        else:
            reload = stale or marker not in (page.get("url") or "")
            if reload:
                async with session.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                    await self._cdp_call(ws, 1, "Page.navigate", {"url": inbox_url})
        if reload:
            self.inbox_loaded_at[ch] = time.monotonic()
            await asyncio.sleep(9)
        return await self._evaluate(session, page["webSocketDebuggerUrl"], script) or []

    # ---------- сопоставление с лидами ----------

    def _incoming(self, ch: str, lead: Dict[str, Any], items: List[Dict[str, Any]]) -> Optional[str]:
        """Последнее входящее сообщение лида из списка чатов (текст превью) или None, если последнее в чате наше."""
        for it in items:
            preview = (it.get("preview") or "").strip()
            if ch == "ig":
                by_user = len(lead.get("username") or "") >= 4 and _norm(lead["username"]) in _norm(it["title"])
                if (by_user or _name_match(lead["name"], it["title"])) and preview and not OWN_PREFIX.match(preview):
                    return preview
            elif ch == "fb":
                if _name_match(lead["name"], it["title"]) and (
                        it["unread"] or (preview and not re.match(r"^(you|вы)\s*:", preview, re.IGNORECASE))):
                    return preview
            else:
                by_phone = len(lead["phone"]) >= 8 and _digits(it["title"]).endswith(lead["phone"][-9:]) and len(_digits(it["title"])) >= 8
                if (by_phone or _name_match(lead["name"], it["title"])) and not it.get("outgoing") and (
                        it["unread"] or (preview and not _is_own_message(preview, lead.get("sent_message", "")))):
                    return preview
        return None


reply_watcher = ReplyWatcher()
