"""
WhatsApp Outreach Sender Orchestrator.
Manages BitBrowser profile connection, lead dispatching in full_auto or copilot mode,
and anti-spam cooldown enforcement strictly complying with the configured daily limits and delays.
"""

import asyncio
import json
import os
import random
import shutil
import sys
from pathlib import Path

# Force UTF-8 on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
import rich.markup

from core.bitbrowser import BitBrowserClient
from core.database import WhatsAppDatabase
from core.spintax import SpintaxGenerator
from core.wa_driver import WhatsAppDriver

console = Console(force_terminal=True, legacy_windows=False)


BASE_DIR = Path(__file__).resolve().parent.parent


class WhatsAppSender:
    def __init__(self, config_path: str = "config.json"):
        cfg_p = Path(config_path)
        actual_cfg = cfg_p if cfg_p.is_absolute() else (BASE_DIR / cfg_p)
        if not actual_cfg.exists() and (BASE_DIR / "config.example.json").exists():
            shutil.copyfile(BASE_DIR / "config.example.json", actual_cfg)
        with open(actual_cfg, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        self.db = WhatsAppDatabase()
        self.spintax = SpintaxGenerator()
        self.bitbrowser = BitBrowserClient(self.config.get("bitbrowser_api_url", "http://127.0.0.1:54345"))
        raw_list = self.config.get("list_txt_path", "data/list.txt")
        lp = Path(raw_list)
        self.list_txt_path = str(lp if lp.is_absolute() else (BASE_DIR / lp).resolve())
        self.outreach_mode = self.config.get("outreach_mode", "full_auto")
        self.min_cross_delay = self.config.get("min_cross_platform_delay_sec", 300)
        # Lure передаёт ниши последнего поиска; пусто = вся очередь
        self.niches = [n.strip() for n in os.environ.get("WA_NICHES", "").split(",") if n.strip()]

    async def run_parallel_session(self):
        """Runs both WP 1 and WP 2 concurrently with safe 5-minute staggering and cross-profile interval."""
        console.print(Panel(
            "[bold green]⚡ ПАРАЛЛЕЛЬНАЯ РАССЫЛКА WHATSAPP (WP 1 + WP 2) АКТИВИРОВАНА[/bold green]\n\n"
            "• Номер 1: WP 1 (Бизнес #1) — стартует СРАЗУ\n"
            "• Номер 2: wp2 (Номер #2)   — стартует через 5 МИНУТ\n"
            "• Режим: 100% Автомат (без ручного подтверждения Enter)\n"
            "• Защита WhatsApp: строго не менее 5 минут между ЛЮБЫМИ отправками смс",
            border_style="green",
            title="[bold yellow]WhatsApp Multi-Account Safety Orchestrator[/bold yellow]"
        ))

        task_wp1 = self.run_session(target_account_key="profile_1", initial_delay_sec=0)
        task_wp2 = self.run_session(target_account_key="profile_2", initial_delay_sec=self.min_cross_delay)

        results = await asyncio.gather(task_wp1, task_wp2, return_exceptions=True)
        for name, result in zip(("WP 1", "WP 2"), results):
            if isinstance(result, BaseException):
                console.print(f"[bold red]✗ {name} аварийно остановлен: {rich.markup.escape(repr(result))}[/bold red]")

    async def _ensure_account_cooldown(self, account_key: str, min_delay: int, max_delay: int, account_name: str):
        """
        Per-account pause measured from the DB (last SENT of this account), so it survives
        restarts of the script/Lure: a fresh session can no longer send right after the previous one.
        """
        elapsed = self.db.get_seconds_since_last_sent(account_key)
        if elapsed is None:
            return
        required = random.randint(min_delay, max_delay)
        if elapsed < required:
            wait_time = int(required - elapsed)
            console.print(
                f"\n[bold yellow]🛡 [{account_name}] Последнее сообщение с этого номера было {int(elapsed)}с назад, "
                f"нужна пауза {required}с. Жду ещё {wait_time // 60}м {wait_time % 60:02d}с.[/bold yellow]"
            )
            await self._run_cooldown_lock(wait_time, description=f"Пауза номера [{account_name}]")

    async def _ensure_safe_wa_interval(self, min_interval_sec: int = 300, account_name: str = ""):
        """
        Enforces that at least min_interval_sec (default 300s = 5 minutes)
        has passed since ANY message was sent across either WhatsApp profile.
        Guarantees WP 1 and WP 2 never send in bursts or closer than 5 minutes.
        """
        elapsed = self.db.get_seconds_since_last_sent()
        if elapsed is not None and elapsed < min_interval_sec:
            wait_time = int(min_interval_sec - elapsed)
            if wait_time > 3:
                minutes, rem_sec = divmod(wait_time, 60)
                console.print(
                    f"\n[bold yellow]🛡 [{account_name}] Защита WhatsApp от спама: последнее сообщение в WhatsApp было {int(elapsed)}с назад.\n"
                    f"  Ожидание {minutes}м {rem_sec:02d}с для соблюдения строгого интервала не менее 5 минут между смс...[/bold yellow]"
                )
                await self._run_cooldown_lock(wait_time, description=f"Защита 5+ мин [{account_name}]")
                console.print(f"[bold green]🔔 Интервал 5+ минут соблюдён! [{account_name}] Продолжаю отправку...[/bold green]\n")

    async def run_session(self, target_account_key: str = "profile_1", initial_delay_sec: int = 0):
        """Runs an outreach session for the specified profile (profile_1 or profile_2)."""
        profile_conf = self.config["profiles"].get(target_account_key)
        if not profile_conf:
            console.print(f"[bold red]Profile '{target_account_key}' not found in config.json![/bold red]")
            return

        account_name = profile_conf["name"]
        profile_id = profile_conf["id"]
        daily_limit = profile_conf["daily_limit"]
        min_delay = profile_conf["min_delay_sec"]
        max_delay = profile_conf["max_delay_sec"]

        mode_str = "Полный автомат (full_auto)" if self.outreach_mode == "full_auto" else "Полуавтомат (Copilot)"

        console.print(Panel(
            f"[bold cyan]WhatsApp Outreach System (v2.0)[/bold cyan]\n"
            f"Account: [bold green]{account_name}[/bold green] (ID: {profile_id})\n"
            f"Daily Limit: [yellow]{daily_limit} dialogs[/yellow] | Safe Pause: [yellow]{min_delay//60}–{max_delay//60} min[/yellow]\n"
            f"Mode: [bold magenta]{mode_str}[/bold magenta]\n"
            f"Cross-Account Protection: [bold green]>= 5 мин между ЛЮБЫМИ смс[/bold green]",
            title="[bold blue]SESSION INITIALIZATION[/bold blue]",
            border_style="cyan"
        ))

        # Check API status
        if not await self.bitbrowser.check_api():
            console.print("[bold red]BitBrowser is not running or Local API is unreachable on 127.0.0.1:54345![/bold red]")
            console.print("Please launch BitBrowser and make sure it is open.")
            return

        # Check daily limit
        sent_today = self.db.count_sent_today(target_account_key)
        console.print(f"Sent today from this account: [bold green]{sent_today}/{daily_limit}[/bold green]")
        if sent_today >= daily_limit:
            console.print(f"[bold yellow]Daily limit reached for {account_name} ({sent_today}/{daily_limit}). Stopping to prevent ban.[/bold yellow]")
            return

        # Stagger delay at start if needed (e.g. for parallel second account)
        if initial_delay_sec > 0:
            minutes, rem_sec = divmod(initial_delay_sec, 60)
            console.print(f"\n[bold yellow]⏳ Стартовое разнесение рассылки [{account_name}]: ожидание {minutes}м {rem_sec:02d}с, чтобы не отправлять одновременно с другим номером...[/bold yellow]")
            await self._run_cooldown_lock(initial_delay_sec, description=f"Стартовая пауза [{account_name}]")
            console.print(f"[bold green]🔔 Стартовая пауза [{account_name}] завершена! Приступаю к отправке лидов...[/bold green]\n")

        # Sync known numbers from data/list.txt
        self.db.sync_from_list_txt(self.list_txt_path)

        # Open profile in BitBrowser
        console.print(f"Opening browser profile [bold]{account_name}[/bold] via BitBrowser API...")
        open_data = await self.bitbrowser.open_profile(profile_id)
        if not open_data or "http" not in open_data:
            console.print("[bold red]Failed to launch/connect to profile in BitBrowser![/bold red]")
            return

        cdp_port = open_data["http"]
        console.print(f"Connected to CDP endpoint: [green]{cdp_port}[/green]")

        # Connect Playwright driver
        driver = WhatsAppDriver(
            cdp_http_url=cdp_port,
            min_typing_ms=self.config.get("typing_speed", {}).get("min_ms", 35),
            max_typing_ms=self.config.get("typing_speed", {}).get("max_ms", 95)
        )

        connected = await driver.connect()
        if not connected:
            console.print("[bold red]Failed to attach Playwright to WhatsApp Web tab![/bold red]")
            return

        console.print("[bold green]Playwright attached to WhatsApp Web successfully![/bold green]\n")

        # Main Outreach Loop
        while sent_today < daily_limit:
            await self._ensure_account_cooldown(target_account_key, min_delay, max_delay, account_name)
            lead = self.db.get_next_lead(target_account_key, self.niches)
            if not lead:
                console.print(f"[yellow]No more QUEUED leads for {account_name} in database"
                              f"{' (ниши: ' + ', '.join(self.niches) + ')' if self.niches else ''}.[/yellow]")
                break

            phone = lead["phone"]
            b_name = lead["business_name"] or "Lead"
            niche = lead["niche"] or "recovery"

            console.print(f"\n━━━━━━━━━━━━━ [bold cyan]{account_name}[/bold cyan] | Lead: [bold white]{rich.markup.escape(b_name)}[/bold white] ━━━━━━━━━━━━━")
            console.print(f"Phone: [bold yellow]{phone}[/bold yellow] | Niche: [bold green]{niche}[/bold green] | Sent today: [bold]{sent_today}/{daily_limit}[/bold]")

            # Generate unique hook
            hook_text = self.spintax.generate_hook(niche=niche, business_name=b_name)
            console.print(f"Hook to send: [italic]\"{rich.markup.escape(hook_text)}\"[/italic]")

            console.print("  Navigating to direct WhatsApp dialog...")
            status, err = await driver.open_dialog_and_prepare(phone, hook_text)

            if status == "INVALID_PHONE":
                console.print(f"  [red]✗ Номер {phone} НЕ зарегистрирован в WhatsApp. Статус: INVALID_PHONE.[/red]")
                self.db.mark_status(phone, "INVALID_PHONE")
                console.print("  [dim]Перехожу к следующему лиду из очереди...[/dim]")
                await asyncio.sleep(2.0)
                continue

            elif status == "ALREADY_CONTACTED":
                console.print(f"  [yellow]⚠ С номером {phone} уже был диалог ранее. Статус: ALREADY_CONTACTED.[/yellow]")
                self.db.mark_status(phone, "ALREADY_CONTACTED")
                console.print("  [dim]Перехожу к следующему лиду из очереди...[/dim]")
                await asyncio.sleep(2.0)
                continue

            elif status != "READY_TO_CONFIRM":
                console.print(f"  [red]✗ Ошибка открытия диалога ({err}). Статус: FAILED.[/red]")
                self.db.mark_status(phone, "FAILED")
                console.print("  [dim]Перехожу к следующему лиду из очереди...[/dim]")
                await asyncio.sleep(2.0)
                continue

            # Check cross-account safe interval (strictly >= 5 minutes between any WhatsApp message)
            await self._ensure_safe_wa_interval(min_interval_sec=self.min_cross_delay, account_name=account_name)

            # Dispatch logic: full_auto vs semi_auto
            if self.outreach_mode == "full_auto":
                console.print("  [dim]Автоматический режим: небольшая естественная пауза перед отправкой (2-3с)...[/dim]")
                await asyncio.sleep(random.uniform(1.8, 3.2))
                console.print(f"  [bold green]🚀 Автоматическая отправка сообщения [{account_name}] (Enter)...[/bold green]")
                choice = "send"
            else:
                choice = await self._prompt_user_dispatch(account_name, b_name, phone, hook_text)

            if choice == "send":
                success, delivery_status = await driver.send_prepared_message()
                if success:
                    delivery_label = {
                        "DELIVERED": "Доставлено (2 галочки ✔✔)",
                        "SENT": "Отправлено на сервер WA (1 галочка ✔)",
                        "SENT_UNCONFIRMED_TICKS": "Отправлено (поле ввода очищено)"
                    }.get(delivery_status, delivery_status)

                    console.print(f"  [bold green]✔ Сообщение успешно отправлено на {phone}! Статус доставки: {delivery_label}[/bold green]")
                    self.db.mark_status(phone, "SENT", hook_text)
                    self.db.append_to_list_txt(self.list_txt_path, phone, account_name, b_name)
                    sent_today += 1

                    # Check if daily limit was just reached
                    if sent_today >= daily_limit:
                        console.print(f"\n[bold green]Goal reached for today ({sent_today}/{daily_limit}). Session complete![/bold green]")
                        break

                    # Пауза между сообщениями этого номера ждётся в начале цикла (_ensure_account_cooldown)
                else:
                    console.print(f"  [bold red]✗ Ошибка отправки/доставки на номер {phone} ({delivery_status}). Статус: FAILED.[/bold red]")
                    self.db.mark_status(phone, "FAILED")

            elif choice == "skip":
                console.print("  [yellow]Skipped by user. Clearing text and moving to next...[/yellow]")
                await driver.clear_input()
                self.db.mark_status(phone, "SKIPPED_BY_USER")

            elif choice == "quit":
                console.print("  [bold red]Quitting session on user request. Cleaning up...[/bold red]")
                await driver.clear_input()
                break

        await driver.close()
        console.print(f"\n[bold cyan]Session closed. Total sent today for {account_name}: {sent_today}/{daily_limit}[/bold cyan]")

    async def _prompt_user_dispatch(self, account: str, name: str, phone: str, hook: str) -> str:
        """Prompts user for manual dispatch confirmation in semi_auto mode."""
        console.print(Panel(
            "[bold green]Message is typed into WhatsApp input box.[/bold green]\n"
            "Review the text in the browser window before sending.\n\n"
            "[bold white][ENTER][/bold white] -> Отправить сообщение\n"
            "[bold yellow][S]    [/bold yellow] -> Пропустить этот контакт\n"
            "[bold red][Q]    [/bold red] -> Завершить работу скрипта",
            title=f"[bold]CONFIRM DISPATCH — {account}[/bold]",
            border_style="yellow"
        ))

        loop = asyncio.get_event_loop()
        while True:
            user_input = await loop.run_in_executor(None, input, "Your choice [Enter/s/q]: ")
            choice = user_input.strip().lower()
            if choice == "" or choice == "y" or choice == "yes":
                return "send"
            elif choice == "s":
                return "skip"
            elif choice == "q":
                return "quit"
            else:
                console.print("[dim]Invalid input. Press Enter to send, 's' to skip, or 'q' to quit.[/dim]")

    async def _run_cooldown_lock(self, seconds: int, description: str = "Sleeping..."):
        """Runs an anti-spam cooldown timer with live progress bar or clean ticks for pipes."""
        minutes, rem_sec = divmod(seconds, 60)
        console.print(f"\n[bold yellow]🔒 Таймер безопасности ({description}): {minutes}м {rem_sec:02d}с пауза...[/bold yellow]")

        # In non-interactive pipes (e.g. under Lure/orchestrator), avoid Rich Progress \r floods
        # that overrun asyncio StreamReader line buffers.
        is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        if is_tty:
            with Progress(
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=40, style="grey37", complete_style="green"),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console
            ) as progress:
                task = progress.add_task(description, total=seconds)
                for _ in range(seconds):
                    await asyncio.sleep(1)
                    progress.update(task, advance=1)
        else:
            for elapsed in range(seconds):
                await asyncio.sleep(1)
                remaining = seconds - elapsed - 1
                if remaining > 0 and (remaining % 30 == 0 or remaining in [60, 30, 10, 5]):
                    m, s = divmod(remaining, 60)
                    print(f"⏱ [{description}] Next lead in: {m:02d}:{s:02d}", flush=True)

        console.print("[bold green]✔ Пауза завершена. Готов к следующему лиду.[/bold green]\n")
