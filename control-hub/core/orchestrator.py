import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from core.log_stream import log_stream
from core.onboarding import find_chrome

BASE_APPS_DIR = Path(__file__).resolve().parent.parent.parent  # repository root

# Regex for stripping ALL ANSI escape codes (colors, cursor control, erase in line, etc.)
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
# Regex for stripping Rich formatting markup like [bold], [/bold], [green], etc.
RICH_TAGS = re.compile(
    r'\[/?(?:bold|dim|italic|underline|blink|reverse|strike|black|red|green|yellow|blue|magenta|cyan|white|default|grey\d*|#[0-9a-fA-F]{6})[^\]]*\]'
)


def clean_terminal_output(raw: str) -> str:
    """Completely purges ANSI escape sequences, terminal control bytes, and rich markup tags."""
    if not raw:
        return ""
    # Strip ANSI escape sequences
    text = ANSI_ESCAPE.sub('', raw)
    # Strip Rich markup tags
    text = RICH_TAGS.sub('', text)
    # Strip terminal control characters like \r, \x00-\x1f
    text = re.sub(r'[\r\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text.strip()


def is_noise_or_progress_bar(text: str) -> bool:
    """Returns True if the line is raw terminal noise, box borders, or progress bar ticks."""
    if not text or len(text) < 2:
        return True
    # Box-drawing characters and horizontal borders
    if re.fullmatch(r'^[─━═\-_╭╮╰╯│┌┐└┘├┤┬┴┼\s\.\*]{3,}$', text):
        return True
    # Progress bar text e.g. "━━━━━ 45% 00:03" or "---- 100%" or with carriage return noise
    if '━' in text or '─' in text or '│' in text:
        return True
    if re.search(r'\b\d{1,3}%\b.*(?:\d{2}:\d{2}|remaining)', text, re.IGNORECASE):
        return True
    if re.search(r'^\s*⏱\s*\[.*\]\s*Next lead in:\s*\d{2}:\d{2}', text):
        return True
    return False


class CampaignOrchestrator:
    """Coordinates running automation processes, live status, and safety timers."""

    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        self.cooldown_tasks: Dict[str, asyncio.Task] = {}
        self.last_logged_message: Dict[str, str] = {}

        self.status = {
            "fb": {
                "running": False,
                "status_text": "Готов к запуску",
                "step": "idle",
                "current_lead": None,
                "current_hook": None,
                "cooldown_remaining": 0,
                "cooldown_total": 0,
            },
            "ig": {
                "running": False,
                "status_text": "Готов к запуску",
                "step": "idle",
                "current_lead": None,
                "current_hook": None,
                "cooldown_remaining": 0,
                "cooldown_total": 0,
            },
            "wa1": {
                "running": False,
                "status_text": "Готов к запуску",
                "step": "idle",
                "current_lead": None,
                "current_hook": None,
                "cooldown_remaining": 0,
                "cooldown_total": 0,
            },
            "wa2": {
                "running": False,
                "status_text": "Готов к запуску",
                "step": "idle",
                "current_lead": None,
                "current_hook": None,
                "cooldown_remaining": 0,
                "cooldown_total": 0,
            },
        }

        # Почтовая рассылка живёт отдельно от каналов: не входит в «активно каналов из 4»
        self.email_status = self._idle_email_status()

    @staticmethod
    def _idle_email_status() -> Dict[str, Any]:
        return {"running": False, "mode": "send", "status_text": "Готов к запуску", "sent": 0, "failed": 0, "total": 0,
                "current": None, "pause_until": None}

    def is_running(self, channel: Optional[str] = None) -> bool:
        if channel:
            return channel in self.active_tasks and not self.active_tasks[channel].done()
        return any(not task.done() for task in self.active_tasks.values())

    def get_status_snapshot(self) -> Dict[str, Any]:
        """Returns full reactive state of all channels for the UI."""
        is_active = self.is_running()
        active_channels = [k for k, v in self.status.items() if v["running"]]
        
        current_activity = "Система в режиме ожидания"
        for k in ["fb", "ig", "wa1", "wa2"]:
            if k in self.status:
                st = self.status[k]
                if st["running"]:
                    lead_name = st["current_lead"].get("name") if st["current_lead"] else ""
                    if lead_name:
                        current_activity = f"[{k.upper()}] {st['status_text']} • {lead_name}"
                    else:
                        current_activity = f"[{k.upper()}] {st['status_text']}"
                    break

        return {
            "is_running": is_active,
            "active_channels": active_channels,
            "current_activity": current_activity,
            "channels": self.status,
            "email": self.email_status,
        }

    async def _broadcast_state_update(self, channel: str = "SYSTEM"):
        await log_stream.broadcast(
            channel=channel,
            message="State updated",
            level="info",
            event_type="status_update",
            data=self.get_status_snapshot()
        )

    def _start_cooldown_timer(self, channel: str, total_seconds: int, description: str = ""):
        """Starts a live second-by-second countdown for safety timers."""
        if channel in self.cooldown_tasks and not self.cooldown_tasks[channel].done():
            self.cooldown_tasks[channel].cancel()

        self.status[channel]["step"] = "cooldown"
        self.status[channel]["cooldown_remaining"] = total_seconds
        self.status[channel]["cooldown_total"] = total_seconds

        async def _ticker():
            rem = total_seconds
            while rem > 0:
                mins, secs = divmod(rem, 60)
                desc = f" ({description})" if description else ""
                self.status[channel]["status_text"] = f"Пауза: {mins}:{secs:02d}{desc}"
                self.status[channel]["cooldown_remaining"] = rem

                # Broadcast tick WITHOUT appending to log feed (UI uses it for the progress bar)
                await log_stream.broadcast(
                    channel=channel.upper(),
                    message="",
                    level="info",
                    event_type="cooldown_tick",
                    data={
                        "channel": channel,
                        "remaining": rem,
                        "total": total_seconds,
                        "formatted": f"{mins}:{secs:02d}"
                    }
                )
                await asyncio.sleep(1.0)
                rem -= 1

            self.status[channel]["cooldown_remaining"] = 0
            self.status[channel]["status_text"] = "Пауза завершена"
            self.status[channel]["step"] = "idle" if not self.status[channel]["running"] else "warmup"

            await log_stream.broadcast(
                channel=channel.upper(),
                message="🔔 Пауза безопасности завершена! Продолжаю рассылку...",
                level="success",
                event_type="cooldown_done",
                data={"channel": channel}
            )
            await self._broadcast_state_update(channel.upper())

        self.cooldown_tasks[channel] = asyncio.create_task(_ticker())

    def _spawn(self, key: str, coro) -> bool:
        """Запускает задачу под ключом key; повторный запуск уже работающего ключа отклоняется."""
        if self.is_running(key):
            coro.close()
            return False
        task = asyncio.create_task(coro)
        self.active_tasks[key] = task
        return True

    async def start_channel(self, target: str, options: Optional[Dict[str, Any]] = None):
        """Starts an outreach task. Returns False if the same job is already running."""
        started = True

        if target == "all":
            if self.is_running("meta") or self.is_running("wa") or self.is_running("fb") or self.is_running("ig"):
                await log_stream.broadcast("SYSTEM", "⚠ Часть каналов уже работает, сначала остановите их.", "warning")
                return False
            await log_stream.broadcast("SYSTEM", "🚀 Запуск ВСЕХ каналов (Meta FB+IG + WhatsApp WP1+WP2)...", "info")
            self._spawn("meta", self._run_meta_job(platform="all"))
            self._spawn("wa", self._run_wa_job(target="all"))

        elif target == "meta_parallel":
            await log_stream.broadcast("META", "🚀 Запуск параллельной рассылки Meta (FB + IG)...", "info")
            started = not (self.is_running("fb") or self.is_running("ig")) and self._spawn("meta", self._run_meta_job(platform="all"))

        elif target == "meta_fb":
            await log_stream.broadcast("FB", "🚀 Запуск рассылки Facebook Messenger...", "info")
            started = not self.is_running("meta") and self._spawn("fb", self._run_meta_job(platform="FB"))

        elif target == "meta_ig":
            await log_stream.broadcast("IG", "🚀 Запуск рассылки Instagram Direct...", "info")
            started = not self.is_running("meta") and self._spawn("ig", self._run_meta_job(platform="IG"))

        elif target == "meta_scrape":
            try:
                scrape_args = self._build_scrape_args(options)
            except ValueError as err:
                await log_stream.broadcast("META", f"❌ Параметры поиска: {err}", "error")
                return False
            await log_stream.broadcast("META", "🔍 Запуск поиска в Meta Ads Library...", "info")
            started = self._spawn("meta_scrape", self._run_meta_scrape_job(scrape_args))

        elif target == "gmaps_scrape":
            try:
                scrape_args = self._build_scrape_args(options, gmaps=True)
            except ValueError as err:
                await log_stream.broadcast("META", f"❌ Параметры поиска: {err}", "error")
                return False
            await log_stream.broadcast("META", "🗺 Запуск поиска в Google Maps (компании без сайта)...", "info")
            started = self._spawn("gmaps_scrape", self._run_meta_scrape_job(scrape_args, command="gmaps", key="gmaps_scrape", title="Google Maps"))

        elif target == "email_send":
            try:
                email_args = self._build_email_args(options)
            except ValueError as err:
                await log_stream.broadcast("EMAIL", f"❌ Параметры рассылки: {err}", "error")
                return False
            started = self._spawn("email_send", self._run_email_job(email_args))

        elif target == "wa_parallel":
            await log_stream.broadcast("WA", "🚀 Запуск параллельной рассылки WhatsApp (WP1 + WP2)...", "info")
            started = not (self.is_running("wa1") or self.is_running("wa2")) and self._spawn("wa", self._run_wa_job(target="all"))

        elif target == "wa_wp1":
            await log_stream.broadcast("WA1", "🚀 Запуск рассылки WhatsApp WP1 (Бизнес #1)...", "info")
            started = not self.is_running("wa") and self._spawn("wa1", self._run_wa_job(target="profile_1"))

        elif target == "wa_wp2":
            await log_stream.broadcast("WA2", "🚀 Запуск рассылки WhatsApp wp2 (Номер #2)...", "info")
            started = not self.is_running("wa") and self._spawn("wa2", self._run_wa_job(target="profile_2"))

        else:
            await log_stream.broadcast("SYSTEM", f"⚠ Неизвестная команда запуска: {target}", "warning")
            return False

        if not started:
            await log_stream.broadcast("SYSTEM", f"⚠ '{target}' уже запущен или конфликтует с работающим каналом.", "warning")
        await self._broadcast_state_update("SYSTEM")
        return started

    @staticmethod
    def _build_email_args(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Проверяет окно «Рассылка на почту»: сколько писем, из какого источника, пауза между письмами."""
        options = options or {}
        try:
            count = int(options.get("count") or 0)
        except (TypeError, ValueError):
            raise ValueError("количество писем должно быть числом")
        if count < 1:
            raise ValueError("укажите, сколько писем отправить")
        source = options.get("source") or "all"
        if source not in ("all", "leads", "rentals"):
            raise ValueError("неизвестный источник получателей")
        niche = str(options.get("niche") or "all")
        if not re.fullmatch(r"[a-z0-9_]{1,40}", niche):
            raise ValueError("неизвестная ниша получателей")
        args = {"count": count, "source": source, "niche": niche, "delay_min": None, "delay_max": None}
        for key in ("delay_min", "delay_max"):
            if options.get(key) not in (None, ""):
                try:
                    args[key] = max(5, min(600, int(options[key])))
                except (TypeError, ValueError):
                    raise ValueError("пауза должна быть числом секунд")
        return args

    async def _run_email_job(self, email_args: Dict[str, Any]):
        """Запускает send_emails.py в скрытом процессе и переводит его вывод в журнал и счётчик прогресса."""
        meta_dir = BASE_APPS_DIR / "meta-lead-gen"
        python_exe = meta_dir / "venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = sys.executable

        followup = email_args.get("mode") == "followup"
        job_key = "email_followup" if followup else "email_send"
        if followup:
            cmd = [str(python_exe), "-u", "send_followups.py", "--send", "--limit", str(email_args["count"])]
        else:
            cmd = [str(python_exe), "-u", "send_emails.py", "--send",
                   "--limit", str(email_args["count"]), "--source", email_args["source"],
                   "--niche", email_args["niche"]]
            if email_args["delay_min"] is not None:
                cmd += ["--delay-min", str(email_args["delay_min"])]
            if email_args["delay_max"] is not None:
                cmd += ["--delay-max", str(email_args["delay_max"])]

        st = self._idle_email_status()
        st.update(running=True, mode="followup" if followup else "send",
                  status_text="Проверяю ответы..." if followup else "Подключение к Gmail...",
                  total=0 if followup else email_args["count"])
        self.email_status = st
        await log_stream.broadcast(
            "EMAIL",
            "✉ Проверка ответов на письма и отправка письма 2" if followup
            else f"✉ Запуск почтовой рассылки: до {email_args['count']} писем", "info")
        await self._broadcast_state_update("EMAIL")

        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = None
        finished_ok = False

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(meta_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                limit=10 * 1024 * 1024, creationflags=creationflags, env=env
            )
            self.active_processes[job_key] = proc

            while True:
                try:
                    line = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    line = await proc.stdout.read(65536)
                if not line:
                    break
                msg = clean_terminal_output(line.decode("utf-8", errors="replace"))
                msg = re.sub(r"^\d{2}:\d{2}:\d{2}\s+\[\w+\]\s*", "", msg).strip()
                if not msg:
                    continue

                level = "info"
                m = re.match(r"\[(\d+)/(\d+)\]\s+Отправка:\s*(.+)", msg)
                if m:
                    st["total"] = int(m.group(2))
                    st["current"] = m.group(3)
                    st["status_text"] = f"Письмо {m.group(1)} из {m.group(2)}"
                    st["pause_until"] = None
                elif msg.startswith("✔"):
                    st["sent"] += 1
                    level = "success"
                    await log_stream.broadcast("EMAIL", f"✔ Отправлено: {st['current']}", "success",
                                               event_type="email_sent", data={"sent": st["sent"], "total": st["total"]})
                    await self._broadcast_state_update("EMAIL")
                    continue
                elif msg.startswith("✖") or "Ошибка" in msg or "Не удалось" in msg:
                    level = "error"
                    if msg.startswith("✖"):
                        st["failed"] += 1
                elif (p := re.search(r"Пауза\s+(\d+)\s+сек", msg)):
                    st["status_text"] = f"Пауза {p.group(1)} сек до следующего письма"
                    st["pause_until"] = datetime.now().timestamp() + int(p.group(1))
                    await self._broadcast_state_update("EMAIL")
                    continue

                await log_stream.broadcast("EMAIL", msg, level)
                if m:
                    await self._broadcast_state_update("EMAIL")

            await proc.wait()
            finished_ok = proc.returncode == 0
            done_text = "Письма 2 отправлены" if followup else "Рассылка завершена"
            # Плановая проверка ответов без новостей идёт молча: без записи в журнал
            quiet = followup and finished_ok and not st["sent"] and not st["failed"]
            await log_stream.broadcast(
                "EMAIL",
                "" if quiet else (
                    f"{done_text}: отправлено {st['sent']}, ошибок {st['failed']}." if finished_ok
                    else f"Почтовый процесс остановился с ошибкой (код {proc.returncode}), отправлено {st['sent']}."),
                "success" if finished_ok else "error",
                event_type="email_finished",
                data={"sent": st["sent"], "failed": st["failed"], "ok": finished_ok, "mode": st["mode"], "quiet": quiet}
            )

        except asyncio.CancelledError:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            await log_stream.broadcast("EMAIL", f"Рассылка остановлена пользователем, отправлено {st['sent']}.", "warning",
                                       event_type="email_finished", data={"sent": st["sent"], "failed": st["failed"], "ok": False})

        except Exception as e:
            await log_stream.broadcast("EMAIL", f"Ошибка запуска рассылки: {e}", "error",
                                       event_type="email_finished", data={"sent": st["sent"], "failed": st["failed"], "ok": False})

        finally:
            self.active_processes.pop(job_key, None)
            st["running"] = False
            st["current"] = None
            st["pause_until"] = None
            st["status_text"] = "Завершено" if finished_ok else "Остановлено"
            await self._broadcast_state_update("EMAIL")

    def _build_scrape_args(self, options: Optional[Dict[str, Any]], gmaps: bool = False) -> list:
        """
        Переводит выбор из окна «Параметры поиска» в аргументы main.py scrape.
        Без options парсер сам возьмёт campaign из meta-lead-gen/config.json.
        """
        if not options:
            return []

        def ids(key: str) -> list:
            vals = options.get(key) or []
            clean = [str(v).strip() for v in vals if str(v).strip()]
            if any(not re.fullmatch(r"[A-Za-z0-9_\-]+", v) for v in clean):
                raise ValueError(f"недопустимое значение в '{key}'")
            return clean

        niches = ids("niches")
        geos = ids("geos")
        custom = [str(k).strip()[:80] for k in (options.get("custom_keywords") or []) if str(k).strip()][:20]
        if not niches and not custom:
            raise ValueError("выберите хотя бы одну нишу или введите свой запрос")
        if not geos:
            raise ValueError("выберите хотя бы одно гео")

        args = ["--geos", ",".join(geos)]
        if niches:
            args += ["--niches", ",".join(niches)]
        if custom:
            args += ["--custom", ",".join(k.replace(",", " ") for k in custom)]
        limits = (("max_ads", "--max-places", 1, 200), ("queries_per_pair", "--queries-per-pair", 1, 30), ("workers", "--workers", 1, 3)) if gmaps else \
                 (("max_ads", "--max-ads", 1, 100), ("queries_per_pair", "--queries-per-pair", 1, 30), ("workers", "--workers", 1, 3))
        for opt_key, flag, lo, hi in limits:
            if options.get(opt_key) not in (None, ""):
                try:
                    val = int(options[opt_key])
                except (TypeError, ValueError):
                    raise ValueError(f"'{opt_key}' должно быть числом")
                args += [flag, str(max(lo, min(hi, val)))]
        if options.get("allow_websites") and not gmaps:
            args.append("--allow-websites")

        self.save_scrape_selection(options)
        return args

    @property
    def _selection_path(self) -> Path:
        return BASE_APPS_DIR / "meta-lead-gen" / "data" / "last_scrape_selection.json"

    def save_scrape_selection(self, options: Dict[str, Any]):
        """Запоминает последний выбор, чтобы окно поиска открывалось с теми же параметрами."""
        try:
            self._selection_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._selection_path, "w", encoding="utf-8") as f:
                json.dump(options, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_scrape_selection(self) -> Dict[str, Any]:
        try:
            with open(self._selection_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @property
    def _wa_niche_path(self) -> Path:
        return BASE_APPS_DIR / "whatsapp-outreach" / "data" / "wa_niche.json"

    def set_wa_niche(self, niche: str):
        """Ниша, по которой идёт рассылка WhatsApp: 'all' = вся очередь, 'auto' = ниша последнего поиска."""
        self._wa_niche_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._wa_niche_path, "w", encoding="utf-8") as f:
            json.dump({"niche": niche}, f)

    def get_wa_niche(self) -> str:
        try:
            with open(self._wa_niche_path, "r", encoding="utf-8") as f:
                return str(json.load(f).get("niche") or "auto")
        except Exception:
            return "auto"

    def get_wa_niches(self) -> list:
        niche = self.get_wa_niche()
        if niche == "all":
            return []
        if niche == "auto":
            niches = (self.load_scrape_selection().get("niches") or [])[:1]
        else:
            niches = [niche]
        return [str(n) for n in niches if re.fullmatch(r"[A-Za-z0-9_\-]+", str(n))]

    async def stop_channel(self, target: str):
        """Stops active tasks and terminates underlying background processes."""
        if target == "all":
            await log_stream.broadcast("SYSTEM", "🛑 Экстренная остановка всех процессов рассылки...", "warning")
            for key, task in list(self.active_tasks.items()):
                if not task.done():
                    task.cancel()
            self.active_tasks.clear()

            for key, proc in list(self.active_processes.items()):
                try:
                    proc.terminate()
                except Exception:
                    pass
            self.active_processes.clear()

            for key, t in list(self.cooldown_tasks.items()):
                if not t.done():
                    t.cancel()
            self.cooldown_tasks.clear()

            self._reset_status()

        else:
            # Канал может работать в составе парного запуска (meta / wa): останавливаем и его
            group = {"fb": "meta", "ig": "meta", "wa1": "wa", "wa2": "wa"}.get(target)
            for key in [target, group]:
                if not key or key not in self.active_tasks:
                    continue
                await log_stream.broadcast("SYSTEM", f"🛑 Остановка процесса {key}...", "warning")
                self.active_tasks[key].cancel()
                del self.active_tasks[key]

                if key in self.active_processes:
                    try:
                        self.active_processes[key].terminate()
                    except Exception:
                        pass
                    del self.active_processes[key]

                if key in ["meta", "meta_scrape", "gmaps_scrape"]:
                    for k in ["fb", "ig"]:
                        self._reset_channel(k)
                elif key == "wa":
                    for k in ["wa1", "wa2"]:
                        self._reset_channel(k)
                elif key in self.status:
                    self._reset_channel(key)

        await self._broadcast_state_update("SYSTEM")

    @staticmethod
    def _find_chrome() -> str:
        return find_chrome() or "chrome.exe"

    async def _open_meta_chrome(self, ch: str, target_url: str) -> Dict[str, Any]:
        """Открывает изолированный Chrome канала fb/ig со своим профилем и портом отладки."""
        meta = {
            "fb": ("Facebook", "chrome_fb", 9333, "https://www.facebook.com"),
            "ig": ("Instagram", "chrome_ig", 9334, "https://www.instagram.com"),
        }[ch]
        title, profile_name, port, default_url = meta
        if not target_url:
            lead = self.status[ch].get("current_lead")
            target_url = (lead.get("profile_url") if lead else "") or default_url

        profile_dir = BASE_APPS_DIR / "meta-lead-gen" / "data" / "profiles" / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self._find_chrome(),
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={str(profile_dir)}",
            "--no-first-run",
            "--no-default-browser-check",
            target_url
        ]
        try:
            subprocess.Popen(cmd)
            await log_stream.broadcast(ch.upper(), f"🌐 Открыто окно Chrome {title}: {target_url}", "info")
            return {"success": True, "channel": ch, "url": target_url, "message": f"Браузер {title} открыт"}
        except Exception as e:
            await log_stream.broadcast(ch.upper(), f"Ошибка открытия Chrome: {e}", "error")
            return {"success": False, "channel": ch, "error": str(e)}

    @staticmethod
    def _wa_settings() -> Dict[str, Any]:
        """Настройки BitBrowser из whatsapp-outreach/config.json (ID профилей, адрес API, путь к exe)."""
        try:
            with open(BASE_APPS_DIR / "whatsapp-outreach" / "config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    async def _bitbrowser_health(self, session, api_url: str, timeout: float) -> bool:
        import aiohttp
        try:
            async with session.post(f"{api_url}/health", json={}, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                return bool((await resp.json()).get("success", False))
        except Exception:
            return False

    async def _open_whatsapp_profile(self, ch: str, target_url: str) -> Dict[str, Any]:
        import aiohttp

        is_wa1 = ch in ("wa1", "profile_1", "wp1")
        ch_key = "wa1" if is_wa1 else "wa2"
        ch_name = "WhatsApp #1 (Бизнес)" if is_wa1 else "WhatsApp #2 (Номер #2)"

        cfg = self._wa_settings()
        profile_id = cfg.get("profiles", {}).get("profile_1" if is_wa1 else "profile_2", {}).get("id")
        if not profile_id:
            msg = f"В whatsapp-outreach/config.json не задан id профиля BitBrowser для {ch_key}"
            await log_stream.broadcast(ch_key.upper(), msg, "error")
            return {"success": False, "channel": ch_key, "error": msg}
        api_url = cfg.get("bitbrowser_api_url", "http://127.0.0.1:54345")

        wa_me = re.match(r"https?://wa\.me/\+?(\d+)", target_url)
        if wa_me:
            # wa.me ведёт на страницу-посредник; прямая ссылка открывает чат в WhatsApp Web
            target_url = f"https://web.whatsapp.com/send?phone={wa_me.group(1)}"

        if not target_url:
            lead = self.status[ch_key].get("current_lead")
            phone = (lead.get("phone") if lead else "") or ""
            if phone:
                clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
                target_url = f"https://web.whatsapp.com/send?phone={clean_phone}"
            else:
                target_url = "https://web.whatsapp.com"

        async with aiohttp.ClientSession() as session:
            bit_running = await self._bitbrowser_health(session, api_url, 2.0)

            if not bit_running:
                bit_exe = Path(cfg["bitbrowser_exe"]) if cfg.get("bitbrowser_exe") else None
                if bit_exe and bit_exe.exists():
                    try:
                        subprocess.Popen([str(bit_exe)], cwd=str(bit_exe.parent))
                        for _ in range(8):
                            await asyncio.sleep(0.5)
                            if await self._bitbrowser_health(session, api_url, 1.0):
                                bit_running = True
                                break
                    except Exception:
                        pass

            if not bit_running:
                msg = "BitBrowser не запущен. Ссылка открыта в браузере по умолчанию."
                await log_stream.broadcast(ch_key.upper(), f"⚠ {msg}", "warning")
                import webbrowser
                webbrowser.open(target_url)
                return {"success": True, "fallback": True, "channel": ch_key, "url": target_url, "message": msg}

            try:
                async with session.post(
                    f"{api_url}/browser/open",
                    json={"id": profile_id},
                    timeout=aiohttp.ClientTimeout(total=15.0)
                ) as resp:
                    res_data = await resp.json()
                    if not res_data.get("success"):
                        err = res_data.get("msg", "Ошибка запуска профиля")
                        await log_stream.broadcast(ch_key.upper(), f"Ошибка BitBrowser: {err}", "warning")
                        return {"success": False, "channel": ch_key, "error": err}

                    cdp_data = res_data.get("data", {})
                    cdp_http = cdp_data.get("http") if isinstance(cdp_data, dict) else None
                    if target_url != "https://web.whatsapp.com" and cdp_http:
                        # Chrome 111+ принимает /json/new только методом PUT, GET молча отклоняется
                        try:
                            async with session.put(f"http://{cdp_http}/json/new?{target_url}", timeout=aiohttp.ClientTimeout(total=3.0)):
                                pass
                        except Exception:
                            pass

                    await log_stream.broadcast(ch_key.upper(), f"🌐 Открыт профиль BitBrowser {ch_name}", "info")
                    return {"success": True, "channel": ch_key, "url": target_url, "message": f"Окно {ch_name} открыто в BitBrowser"}
            except Exception as e:
                await log_stream.broadcast(ch_key.upper(), f"Ошибка подключения к BitBrowser: {e}", "error")
                return {"success": False, "channel": ch_key, "error": str(e)}

    async def open_browser(self, channel: str, custom_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Opens the dedicated browser instance with the target page or profile.
        - 'fb' / 'ig': Chrome с изолированным профилем data/profiles/chrome_fb (порт 9333) / chrome_ig (9334).
        - 'wa1' / 'wa2': профиль BitBrowser из whatsapp-outreach/config.json.
        """
        ch = (channel or "").lower().strip()
        target_url = (custom_url or "").strip()

        if ch in ("fb", "facebook"):
            return await self._open_meta_chrome("fb", target_url)
        if ch in ("ig", "instagram"):
            return await self._open_meta_chrome("ig", target_url)
        if ch in ("wa1", "profile_1", "wp1", "wa2", "profile_2", "wp2"):
            return await self._open_whatsapp_profile(ch, target_url)

        return {"success": False, "error": f"Неизвестный канал '{channel}'"}

    def _reset_channel(self, ch: str):
        if ch in self.status:
            self.status[ch]["running"] = False
            self.status[ch]["status_text"] = "Остановлено"
            self.status[ch]["step"] = "idle"
            self.status[ch]["current_lead"] = None
            self.status[ch]["current_hook"] = None
            self.status[ch]["cooldown_remaining"] = 0

    def _reset_status(self):
        for k in ["fb", "ig", "wa1", "wa2"]:
            self._reset_channel(k)

    async def _run_meta_scrape_job(self, scrape_args: Optional[list] = None, command: str = "scrape",
                                   key: str = "meta_scrape", title: str = "Meta Ads Library"):
        """Runs a lead scraper (Meta Ads Library or Google Maps) in a completely hidden background subprocess."""
        meta_dir = BASE_APPS_DIR / "meta-lead-gen"
        python_exe = meta_dir / "venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = sys.executable

        for ch in ["fb", "ig"]:
            if ch in self.status:
                self.status[ch]["running"] = True
                self.status[ch]["status_text"] = f"Поиск: {title}..."
                self.status[ch]["step"] = "scraping"

        await self._broadcast_state_update("META")

        cmd = [str(python_exe), "-u", "main.py", command, *(scrape_args or [])]
        await log_stream.broadcast("META", f"Старт парсера: {title}...", "info")

        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1"
        }

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(meta_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=10 * 1024 * 1024,
                creationflags=creationflags,
                env=env
            )
            self.active_processes[key] = proc

            while True:
                try:
                    line = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    line = await proc.stdout.read(65536)
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                clean_msg = clean_terminal_output(decoded)
                if not clean_msg:
                    continue

                try:
                    with open(meta_dir / "data" / "scrape.log", "a", encoding="utf-8") as lf:
                        lf.write(clean_msg + "\n")
                except OSError:
                    pass

                if "Поиск в Ad Library" in clean_msg:
                    q_match = re.search(r"Поиск в Ad Library:\s*'([^']+)'", clean_msg)
                    if q_match:
                        q_name = q_match.group(1)
                        w_match = re.search(r"\[(B\d)\]", clean_msg)
                        w_tag = f"[{w_match.group(1)}] " if w_match else ""
                        for ch in ["fb", "ig"]:
                            self.status[ch]["status_text"] = f"Запрос: {q_name}"
                        await log_stream.broadcast("META", f"🔍 {w_tag}Поиск в Ads Library: '{q_name}'", "info")
                    continue

                if "Candidate found" in clean_msg:
                    await log_stream.broadcast("META", clean_msg, "info")
                    continue

                if "Lead QUALIFIED" in clean_msg:
                    await log_stream.broadcast("META", "✔ Лид квалифицирован (без сайта) и сохранен в базу!", "success")
                    continue

                if "DISQUALIFIED" in clean_msg:
                    await log_stream.broadcast("META", clean_msg, "warning")
                    continue

                if is_noise_or_progress_bar(clean_msg):
                    continue

                lvl = "error" if any(w in clean_msg.lower() for w in ["error", "exception", "failed"]) else "info"
                await log_stream.broadcast("META", clean_msg, lvl)

            await proc.wait()
            for ch in ["fb", "ig"]:
                self._reset_channel(ch)

            await log_stream.broadcast(
                "META",
                f"Поиск лидов ({title}) завершен.",
                "success" if proc.returncode == 0 else "warning",
                event_type="session_finished",
                data={"channel": key, "code": proc.returncode}
            )

        except asyncio.CancelledError:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for ch in ["fb", "ig"]:
                self._reset_channel(ch)
            await log_stream.broadcast("META", f"Поиск ({title}) остановлен пользователем.", "warning")

        except Exception as e:
            for ch in ["fb", "ig"]:
                self._reset_channel(ch)
            await log_stream.broadcast("META", f"Ошибка поиска Meta: {e}", "error")

        finally:
            if key in self.active_processes:
                del self.active_processes[key]
            await self._broadcast_state_update("META")

    async def _run_meta_job(self, platform: str):
        """Runs Meta lead gen outreach in a completely hidden background subprocess."""
        meta_dir = BASE_APPS_DIR / "meta-lead-gen"
        python_exe = meta_dir / "venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = sys.executable

        active_channels = ["fb", "ig"] if platform.lower() == "all" else [platform.lower()]
        for ch in active_channels:
            if ch in self.status:
                self.status[ch]["running"] = True
                self.status[ch]["status_text"] = "Запуск Google Chrome CDP..."
                self.status[ch]["step"] = "connecting"

        await self._broadcast_state_update("META")

        cmd = [str(python_exe), "-u", "main.py", "send", platform.lower()]
        await log_stream.broadcast("META", f"Старт процесса Meta {platform.upper()}", "info")

        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1"
        }
        meta_niches = self.get_wa_niches()
        if meta_niches:
            env["OUTREACH_NICHES"] = ",".join(meta_niches)
            await log_stream.broadcast("META", f"Рассылка только по нише: {', '.join(meta_niches)}", "info")

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        proc = None
        current_ch = "fb" if platform.lower() == "fb" else ("ig" if platform.lower() == "ig" else "fb")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(meta_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=10 * 1024 * 1024,
                creationflags=creationflags,
                env=env
            )
            self.active_processes["meta"] = proc

            while True:
                try:
                    line = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    line = await proc.stdout.read(65536)
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                # Thorough ANSI and Rich tag cleaning
                clean_msg = clean_terminal_output(decoded)
                if not clean_msg:
                    continue

                # Channel detection
                if re.search(r"\bIG\b|Instagram", clean_msg):
                    current_ch = "ig"
                elif re.search(r"\bFB\b|Facebook", clean_msg):
                    current_ch = "fb"

                # 1. Lead Detection
                lead_match = re.search(r'Lead (\d+) of (\d+):\s*(.+)', clean_msg)
                if lead_match:
                    lead_name = lead_match.group(3).strip()
                    self.status[current_ch]["current_lead"] = {
                        "index": int(lead_match.group(1)),
                        "total": int(lead_match.group(2)),
                        "name": lead_name,
                    }
                    self.status[current_ch]["status_text"] = f"Лид: {lead_name}"
                    self.status[current_ch]["step"] = "warmup"
                    await log_stream.broadcast(current_ch.upper(), f"👤 Получен лид #{lead_match.group(1)}: {lead_name}", "info")
                    continue

                # 2. Profile URL
                plat_match = re.search(r'Platform:\s*(FB|IG)\s*\|\s*Profile:\s*(\S+)', clean_msg)
                if plat_match:
                    if self.status[current_ch]["current_lead"]:
                        self.status[current_ch]["current_lead"]["profile_url"] = plat_match.group(2)
                        self.status[current_ch]["current_lead"]["platform"] = plat_match.group(1)
                    continue

                # 3. Hook Detection
                hook_match = re.search(r'Hook to send:\s*\"([^\"]+)\"', clean_msg)
                if hook_match:
                    hook_text = hook_match.group(1)
                    self.status[current_ch]["current_hook"] = hook_text
                    self.status[current_ch]["status_text"] = "Печать Spintax-сообщения..."
                    self.status[current_ch]["step"] = "typing"
                    await log_stream.broadcast(current_ch.upper(), f"💬 Сообщение: \"{hook_text}\"", "info")
                    continue

                # 4. Message Sent Successfully!
                if "✔" in clean_msg and "successfully sent" in clean_msg.lower():
                    self.status[current_ch]["step"] = "sent"
                    self.status[current_ch]["status_text"] = "Сообщение успешно отправлено!"
                    lead_name = self.status[current_ch]["current_lead"].get("name", "Клиент") if self.status[current_ch]["current_lead"] else "Клиент"

                    await log_stream.broadcast(
                        channel=current_ch.upper(),
                        message=f"✔ Сообщение успешно отправлено клиенту {lead_name}!",
                        level="success",
                        event_type="message_sent",
                        data={
                            "channel": current_ch,
                            "lead_name": lead_name,
                            "lead": self.status[current_ch]["current_lead"]
                        }
                    )
                    continue

                # 5. Cooldown / Delay Detection
                delay_match = re.search(r'(?:Защита|таймер|пауза)[^\d]*(\d+)м\s*(\d+)с', clean_msg, re.IGNORECASE)
                if delay_match:
                    mins = int(delay_match.group(1))
                    secs = int(delay_match.group(2))
                    total_s = mins * 60 + secs
                    self._start_cooldown_timer(current_ch, total_s, description=f"{current_ch.upper()} Межсеть")
                    await log_stream.broadcast(current_ch.upper(), f"🛡 Пауза безопасности: {mins}м {secs:02d}с до следующего лида", "warning")
                    continue

                # Skip raw progress noise or repetitive lines
                if is_noise_or_progress_bar(clean_msg):
                    continue

                if self.last_logged_message.get(current_ch) == clean_msg:
                    continue
                self.last_logged_message[current_ch] = clean_msg

                lvl = "error" if any(w in clean_msg.lower() for w in ["error", "couldn't", "failed", "отклонено"]) else "info"
                await log_stream.broadcast(current_ch.upper(), clean_msg, lvl)

            await proc.wait()
            for ch in active_channels:
                if ch in self.status:
                    self.status[ch]["running"] = False
                    self.status[ch]["status_text"] = "Завершено"
                    self.status[ch]["step"] = "idle"

            await log_stream.broadcast(
                "META",
                f"Сессия Meta {platform.upper()} успешно завершена.",
                "success" if proc.returncode == 0 else "warning",
                event_type="session_finished",
                data={"channel": "meta", "code": proc.returncode}
            )

        except asyncio.CancelledError:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for ch in active_channels:
                self._reset_channel(ch)
            await log_stream.broadcast("META", "Процесс Meta остановлен пользователем.", "warning")

        except Exception as e:
            for ch in active_channels:
                self._reset_channel(ch)
            await log_stream.broadcast("META", f"Ошибка запуска Meta: {e}", "error")

        finally:
            if "meta" in self.active_processes:
                del self.active_processes["meta"]
            await self._broadcast_state_update("META")

    async def _run_wa_job(self, target: str):
        """Runs WhatsApp outreach in a completely hidden background subprocess."""
        wa_dir = BASE_APPS_DIR / "whatsapp-outreach"
        wa_venv = wa_dir / "venv" / "Scripts" / "python.exe"
        meta_venv = BASE_APPS_DIR / "meta-lead-gen" / "venv" / "Scripts" / "python.exe"
        python_exe = wa_venv if wa_venv.exists() else (meta_venv if meta_venv.exists() else sys.executable)

        if target == "all" or target == "parallel":
            active_channels = ["wa1", "wa2"]
        elif "2" in target:
            active_channels = ["wa2"]
        else:
            active_channels = ["wa1"]

        for ch in active_channels:
            if ch in self.status:
                self.status[ch]["running"] = True
                self.status[ch]["status_text"] = "Подключение к BitBrowser CDP..."
                self.status[ch]["step"] = "connecting"

        await self._broadcast_state_update("WA")

        cmd = [str(python_exe), "-u", "main.py", "send", target]
        await log_stream.broadcast("WA", "Старт параллельной рассылки WhatsApp (WP1 + WP2)", "info")

        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1"
        }
        wa_niches = self.get_wa_niches()
        if wa_niches:
            env["WA_NICHES"] = ",".join(wa_niches)
            await log_stream.broadcast("WA", f"Рассылка только по нише: {', '.join(wa_niches)}", "info")
        else:
            await log_stream.broadcast("WA", "Рассылка по всей очереди (все ниши)", "warning")

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        proc = None
        current_ch = "wa1"

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(wa_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=10 * 1024 * 1024,
                creationflags=creationflags,
                env=env
            )
            self.active_processes["wa"] = proc

            while True:
                try:
                    line = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    line = await proc.stdout.read(65536)
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue

                # Thorough ANSI and Rich tag cleaning
                clean_msg = clean_terminal_output(decoded)
                if not clean_msg:
                    continue

                if "WP 1" in clean_msg or "profile_1" in clean_msg or "Бизнес #1" in clean_msg:
                    current_ch = "wa1"
                elif "wp2" in clean_msg or "profile_2" in clean_msg or "Номер #2" in clean_msg:
                    current_ch = "wa2"

                # 1. Lead Detection
                wa_lead_match = re.search(r'Lead:\s*(.+)', clean_msg)
                if wa_lead_match:
                    lead_name = wa_lead_match.group(1).replace("━", "").strip()
                    self.status[current_ch]["current_lead"] = {
                        "name": lead_name,
                        "platform": current_ch.upper()
                    }
                    self.status[current_ch]["status_text"] = f"Лид: {lead_name}"
                    self.status[current_ch]["step"] = "warmup"
                    await log_stream.broadcast(current_ch.upper(), f"👤 Получен лид: {lead_name}", "info")
                    continue

                # 2. Phone detection
                phone_match = re.search(r'Phone:\s*(\+?\d+)', clean_msg)
                if phone_match:
                    phone_val = phone_match.group(1)
                    if self.status[current_ch]["current_lead"]:
                        self.status[current_ch]["current_lead"]["phone"] = phone_val
                    await log_stream.broadcast(current_ch.upper(), f"📞 Номер телефона: {phone_val}", "info")
                    continue

                # 3. Hook Detection
                hook_match = re.search(r'Hook to send:\s*\"([^\"]+)\"', clean_msg)
                if hook_match:
                    hook_text = hook_match.group(1)
                    self.status[current_ch]["current_hook"] = hook_text
                    self.status[current_ch]["status_text"] = "Печать Spintax в WhatsApp Web..."
                    self.status[current_ch]["step"] = "typing"
                    await log_stream.broadcast(current_ch.upper(), f"💬 Сообщение: \"{hook_text}\"", "info")
                    continue

                # 4. Message Sent Successfully!
                if "✔" in clean_msg and ("sent successfully" in clean_msg.lower() or "отправлено" in clean_msg.lower()):
                    self.status[current_ch]["step"] = "sent"
                    self.status[current_ch]["status_text"] = "Сообщение успешно отправлено!"
                    lead_name = self.status[current_ch]["current_lead"].get("name", "Клиент WhatsApp") if self.status[current_ch]["current_lead"] else "Клиент WhatsApp"

                    await log_stream.broadcast(
                        channel=current_ch.upper(),
                        message=f"✔ Сообщение успешно отправлено в WhatsApp клиенту {lead_name}!",
                        level="success",
                        event_type="message_sent",
                        data={
                            "channel": current_ch,
                            "lead_name": lead_name,
                            "lead": self.status[current_ch]["current_lead"]
                        }
                    )
                    continue

                # 5. Cooldown / Stagger Delay Detection
                delay_match = re.search(r'(?:Таймер|Защита|пауза|разнесение)[^\d]*(\d+)м\s*(\d+)с', clean_msg, re.IGNORECASE)
                if delay_match:
                    mins = int(delay_match.group(1))
                    secs = int(delay_match.group(2))
                    total_s = mins * 60 + secs
                    desc = "Стартовая пауза" if "стартов" in clean_msg.lower() or "разнесен" in clean_msg.lower() else "Защита 5+ мин"
                    self._start_cooldown_timer(current_ch, total_s, description=desc)
                    await log_stream.broadcast(current_ch.upper(), f"⏳ {desc}: ожидание {mins}м {secs:02d}с для безопасности...", "warning")
                    continue

                # 6. Specific status handling (Invalid / Already contacted)
                if "invalid_phone" in clean_msg.lower() or "не зарегистрирован" in clean_msg.lower():
                    await log_stream.broadcast(current_ch.upper(), "⚠ Номер не зарегистрирован в WhatsApp (пропуск лида)", "warning")
                    continue

                if "already_contacted" in clean_msg.lower() or "уже был диалог" in clean_msg.lower():
                    await log_stream.broadcast(current_ch.upper(), "ℹ С контактом уже был диалог ранее (пропуск лида)", "info")
                    continue

                # Skip raw progress noise, box borders, and timer ticks
                if is_noise_or_progress_bar(clean_msg):
                    continue

                # Deduplicate identical consecutive messages
                if self.last_logged_message.get(current_ch) == clean_msg:
                    continue
                self.last_logged_message[current_ch] = clean_msg

                lvl = "error" if any(w in clean_msg.lower() for w in ["error", "failed", "invalid", "отклонено"]) else "info"
                await log_stream.broadcast(current_ch.upper(), clean_msg, lvl)

            await proc.wait()
            for ch in active_channels:
                if ch in self.status:
                    self.status[ch]["running"] = False
                    self.status[ch]["status_text"] = "Завершено"
                    self.status[ch]["step"] = "idle"

            await log_stream.broadcast(
                "WA",
                f"Сессия WhatsApp {target.upper()} успешно завершена.",
                "success" if proc.returncode == 0 else "warning",
                event_type="session_finished",
                data={"channel": "wa", "code": proc.returncode}
            )

        except asyncio.CancelledError:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for ch in active_channels:
                self._reset_channel(ch)
            await log_stream.broadcast("WA", "Процесс WhatsApp остановлен пользователем.", "warning")

        except Exception as e:
            for ch in active_channels:
                self._reset_channel(ch)
            await log_stream.broadcast("WA", f"Ошибка запуска WhatsApp: {e}", "error")

        finally:
            if "wa" in self.active_processes:
                del self.active_processes["wa"]
            await self._broadcast_state_update("WA")


orchestrator = CampaignOrchestrator()
