import asyncio

import logging

import random

import re

from typing import Any, Dict, List, Optional, Tuple



from playwright.async_api import Page

from rich.console import Console

from rich.markup import escape

from rich.panel import Panel



from core.catalog import NAME_STOP_WORDS, find_stop_word, load_catalog, niche_profile

from core.database import DatabaseManager

from core.spintax import SpintaxEngine

from core.utils import human_delay, human_type, is_custom_website, smooth_scroll, terminal_countdown, unwrap_meta_redirect





logger = logging.getLogger("SenderModule")

console = Console()





class SafeOutreachSender:

    """

    Module 2: Safe Outreach Sender (Step 1).

    Reads leads from leads.db with status 'READY_TO_SEND', performs human warmup,

    types randomized Spintax hooks character-by-character, and enforces long

    inter-lead delays (4-9 mins) and strict daily limits (default 18/day).

    """



    def __init__(self, page: Page, db: DatabaseManager, config: Dict[str, Any]):

        self.page = page

        self.db = db

        self.config = config

        self.limits = config.get("limits", {})

        self.delays = config.get("delays", {})

        self.niche_type = config.get("niche", "epoxy")

        self.catalog = load_catalog()
        self.no_website_only = config.get("campaign", {}).get("website_policy", "no_website_only") != "any"

        self.outreach_mode = config.get("outreach_mode", "semi_auto")

        self.excluded_domains = config.get("excluded_domains", [])



        self.max_daily_fb = self.limits.get("max_daily_messages_fb", 10)

        self.max_daily_ig = self.limits.get("max_daily_messages_ig", 10)

        self.max_daily_total = self.limits.get("max_daily_messages", 20)



        self.min_lead_delay = self.delays.get("min_lead_delay_sec", 300)

        self.max_lead_delay = self.delays.get("max_lead_delay_sec", 600)

        self.min_typing_ms = self.delays.get("min_typing_delay_ms", 50)

        self.max_typing_ms = self.delays.get("max_typing_delay_ms", 140)

        self.min_warmup_s = self.delays.get("min_warmup_sec", 5)

        self.max_warmup_s = self.delays.get("max_warmup_sec", 12)

        self.min_cross_delay = self.delays.get("min_cross_platform_delay_sec", 300)



    async def run(

        self, 

        leads: Optional[List[Dict[str, Any]]] = None, 

        platform: Optional[str] = None,

        initial_delay_sec: int = 0

    ):

        """Main sender execution loop (or targeted batch execution)."""

        plat = platform.upper() if platform else None

        if plat == "FB":

            max_limit = self.max_daily_fb

        elif plat == "IG":

            max_limit = self.max_daily_ig

        else:

            max_limit = self.max_daily_total



        sent_today = self.db.get_sent_today_count(platform=plat)

        remaining_quota = max(0, max_limit - sent_today)



        plat_title = f" [{plat}]" if plat else ""

        console.print(Panel.fit(

            f"[bold cyan]Safe Outreach Sender{plat_title} (Step 1)[/bold cyan]\n"

            f"Messages sent today: [bold green]{sent_today}[/bold green] / [bold red]{max_limit}[/bold red]\n"

            f"Remaining daily quota: [bold yellow]{remaining_quota}[/bold yellow]\n"

            f"Campaign niche: [bold magenta]{self.niche_type}[/bold magenta]\n"

            f"Outreach Mode: [bold yellow]{'Полуавтомат (Copilot)' if self.outreach_mode == 'semi_auto' else 'Автомат'}[/bold yellow]\n"

            f"Delay between leads: [bold blue]{self.min_lead_delay // 60} to {self.max_lead_delay // 60} minutes[/bold blue]",

            title="Outreach Safety Monitor"

        ))



        if remaining_quota <= 0:

            console.print(f"[bold red]⛔ Daily message limit for {plat or 'campaign'} reached today ({sent_today}/{max_limit})! Stopping outreach safely.[/bold red]")

            return



        # Fetch leads ready to send if not provided

        auto_fetch = leads is None

        if leads is None:

            leads = self.db.get_leads_ready_to_send(limit=remaining_quota, platform=plat)



        if not leads:

            console.print("[yellow]⚠ No leads with status 'READY_TO_SEND' found for this session.[/yellow]")

            return



        console.print(f"[green]Loaded {len(leads)} lead(s) for this outreach session.[/green]\n")



        if initial_delay_sec > 0:

            mins, secs = divmod(initial_delay_sec, 60)

            plat_name = plat or "Meta"

            console.print(f"\n[bold yellow]⏳ Стартовая пауза [{plat_name}]: ожидание {mins}м {secs:02d}с, чтобы не отправлять одновременно с другой сетью...[/bold yellow]")

            await terminal_countdown(initial_delay_sec, prefix=f"{plat_name} Stagger Delay", skip_key=plat)

            console.print(f"[bold green]🔔 Стартовая пауза [{plat_name}] завершена! Приступаю к отправке лидов...[/bold green]\n")



        sent_count = 0

        consecutive_delivery_failures = 0

        for idx, lead in enumerate(leads):

            if sent_count >= remaining_quota:

                console.print(f"[bold yellow]Reached daily session quota ({remaining_quota} messages sent). Stopping.[/bold yellow]")

                break



            lead_id = lead["id"]

            biz_name = lead["business_name"]

            platform = lead["preferred_platform"]

            profile_url = lead["preferred_profile_url"]



            console.print(f"[bold cyan]━━━━━━━━━━━━━ Lead {idx + 1} of {len(leads)}: {escape(biz_name or '')} ━━━━━━━━━━━━━[/bold cyan]")

            plat_style = "yellow" if platform == "IG" else "blue"

            console.print(f"Platform: [{plat_style}]{platform}[/{plat_style}] | Profile: {profile_url}")



                        # 0. Niche Mismatch Safety Check
            biz_low = (biz_name or "").lower()
            niche_prof = niche_profile(self.catalog, lead.get("niche")) or {}
            matched = find_stop_word(biz_low, NAME_STOP_WORDS, niche_prof.get("allow_words", []))
            if matched:
                console.print(f"[bold yellow]⚠ Пропущен нерелевантный лид '{biz_name}' (стоп-слово: '{matched}').[/bold yellow]")
                self.db.upsert_lead({
                    "advertiser_id": lead.get("advertiser_id") or str(lead_id),
                    "business_name": biz_name,
                    "status": "DISQUALIFIED_NICHE_MISMATCH",
                    "qualification_reason": f"Niche mismatch stop-word: {matched}"
                })
                continue

            # 1. Warmup Simulation

            warmup_ok = await self._warmup_profile(profile_url, platform=platform)

            if not warmup_ok:

                if self.page.is_closed():
                    # Браузер/вкладку закрыли или он упал: это не вина лида, не сжигаем остальные
                    console.print(f"[bold red]🛑 Окно браузера закрыто. Рассылка {platform} остановлена, лид {biz_name} не потрачен.[/bold red]")
                    self.db.mark_as_ready_to_send(lead_id, reason="Reset: browser window was closed during warmup")
                    break

                console.print(f"[red]Could not load profile {profile_url}. Skipping...[/red]")

                self.db.mark_as_failed(lead_id, "Failed to load business profile during warmup")

                continue



            # 1.05 Страница должна быть именно профилем этого лида: иначе читаем шапку чужого профиля
            # (так 16 разных бизнесов получили сайт pedinimiami.com и были отсеяны как «есть сайт»)
            if platform == "IG":
                expected_user = (profile_url or "").rstrip("/").split("/")[-1].split("?")[0].lower()
                if expected_user and expected_user not in self.page.url.lower():
                    try:
                        await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(3)
                    except Exception:
                        pass
                    if expected_user not in self.page.url.lower():
                        console.print(f"[red]Открыт не тот профиль ({self.page.url}), ожидался {expected_user}. Пропускаю без отсева.[/red]")
                        self.db.mark_as_failed(lead_id, "Instagram opened a different profile than expected")
                        continue

            # 1.1 Fast Region & Account Availability Check

            is_restricted, rest_reason = await self._check_profile_restriction(platform)

            if is_restricted:

                console.print(

                    f"\n[bold red]🚫 РЕГИОНАЛЬНЫЙ БЛОК: {platform} аккаунт {escape(biz_name or '')} недоступен в вашем регионе ({rest_reason})![/bold red]"

                )

                console.print(f"[dim]Лид {biz_name} пропущен (аккаунт заблокирован для текущего региона / закрыты ЛС). Статус: DISQUALIFIED_UNAVAILABLE.[/dim]\n")

                self.db.mark_as_unavailable(lead_id, f"Region restricted: {rest_reason}")

                await human_delay(2.0, 4.0)

                continue



            # 2. Bio & Website Detection (Save website for AI bot training, DO NOT skip)
            console.print(f"  [dim]🔍 Сканирую описание и шапку профиля {platform}...[/dim]")
            if platform == "IG":
                has_site, detected_site = await self._check_instagram_profile_for_website()
            else:
                has_site, detected_site = await self._check_facebook_profile_for_website()

            if has_site and detected_site and self.no_website_only:
                console.print(f"  [yellow]⚠ Найден сайт {detected_site}: лид не подходит под кампанию «только без сайта», пропускаю.[/yellow]")
                self.db.mark_as_disqualified_website(lead_id, detected_site)
                continue
            elif has_site and detected_site:
                console.print(f"  [dim]✔ Обнаружен сайт компании: {detected_site} (сохранен для обучения AI-бота)[/dim]")
                self.db.mark_as_has_website(lead_id, detected_site)
            else:
                console.print("  [dim]✔ Прямого сайта в шапке нет, продолжаем рассылку...[/dim]")



            # 3. Generate Randomized Step 1 Hook

            hook_niche = lead.get("hook_niche") or niche_prof.get("hook_niche") or self.niche_type
            hook_message = SpintaxEngine.generate_hook(hook_niche, platform=platform)

            console.print(f"[italic green]Hook to send:[/italic green] \"{hook_message}\"")



            # Anti-Ban Safe Interval Check: Ensure at least min_cross_delay (300s = 5m) since ANY message on Meta

            await self._ensure_meta_interval(min_interval_sec=self.min_cross_delay, plat_name=platform)



            # 3. Deliver message based on platform (verifying no prior chat history)

            if platform == "IG":

                success, status_info = await self._send_instagram_message(hook_message, biz_name)

            else:

                success, status_info = await self._send_facebook_message(hook_message, biz_name, profile_url)



            if success:

                self._force_send = False
                self.db.mark_as_sent(lead_id, hook_message)

                sent_count += 1

                consecutive_delivery_failures = 0

                console.print(f"[bold green]✔ Outreach message successfully sent to {biz_name}![/bold green]")

                console.print(f"[dim]Updated status to STEP_1_SENT in SQLite and leads.csv (Sent today: {sent_today + sent_count}/{max_limit})[/dim]")



                # 4. Long Anti-Ban Delay only after an actual sent message

                if idx < len(leads) - 1 and sent_count < remaining_quota:

                    delay_sec = random.randint(self.min_lead_delay, self.max_lead_delay)

                    mins, secs = divmod(delay_sec, 60)

                    console.print(f"\n[bold cyan]🔒 Защита от бана: таймер заблокирован на {mins}м {secs:02d}с до следующего лида...[/bold cyan]")

                    if await terminal_countdown(delay_sec, prefix="Anti-Ban Cooldown", skip_key=platform):
                        self._force_send = True  # ручной пропуск: межсетевую паузу для следующего лида тоже не ждём

                    try:

                        import sys

                        sys.stdout.write("\a")

                        sys.stdout.flush()

                    except Exception:

                        pass

                    console.print("[bold green]🔔 Пауза завершена! Загружаю следующего лида...[/bold green]\n")



            elif status_info == "USER_QUIT":

                console.print("\n[yellow]Рассылка остановлена пользователем.[/yellow]")

                break



            elif "ACTION_BLOCKED" in status_info:

                console.print(f"\n[bold red]⛔ ОБНАРУЖЕН ACTION BLOCK В {platform}: {status_info}[/bold red]")

                self.db.mark_as_ready_to_send(lead_id, reason=f"Reset after Meta Action Block: {status_info}")

                console.print(f"[bold yellow]Лид {biz_name} возвращен в статус READY_TO_SEND (не потрачен).[/bold yellow]")

                console.print(f"[bold red]🛑 ПРИОСТАНОВКА РАССЫЛКИ {platform}: Meta временно ограничила действия вашего аккаунта. Отправка остановлена во избежание блокировки профиля.[/bold red]\n")

                break



            elif status_info == "USER_SKIPPED":

                self.db.mark_as_skipped(lead_id)

                console.print(f"[yellow]Лид {biz_name} пропущен по решению пользователя (помечен как SKIPPED_BY_USER).[/yellow]")

                await human_delay(2.0, 4.0)



            elif status_info == "EXISTING_CHAT":

                self.db.mark_as_existing_chat(lead_id)

                console.print(f"[bold yellow]⚠ Skipped: Existing messages already found in DM with {biz_name}![/bold yellow]")

                console.print("[dim]Step 1 was NOT sent. Status marked as EXISTING_CHAT in SQLite & CSV.[/dim]")

                # Short human-like pause before moving to next lead (does not waste cooldown)

                await human_delay(4.0, 8.0)



            elif any(tag in status_info for tag in ["REGION_RESTRICTED", "CANT_MESSAGE_ACCOUNT", "DELIVERY_FAILED", "COULDNT_SEND"]):

                consecutive_delivery_failures += 1

                console.print(f"\n[bold red]🚫 НЕДОСТУПНЫЙ АККАУНТ / РЕГИОНАЛЬНЫЙ БЛОК: {platform} лид {biz_name} ({status_info})[/bold red]")

                console.print(f"[dim]Лид {biz_name} исключен навсегда (аккаунт недоступен в регионе / закрыты ЛС / ошибка доставки). Статус: DISQUALIFIED_UNAVAILABLE.[/dim]\n")

                self.db.mark_as_unavailable(lead_id, f"Unavailable/Restricted: {status_info}")



                if consecutive_delivery_failures >= 4:

                    console.print("\n[bold red]🛑 ВНИМАНИЕ: 4 лида подряд вернули ошибку отправки. Возможно, Meta временно ограничила отправку новых сообщений с вашего профиля. Останавливаем рассылку для безопасности аккаунта.[/bold red]\n")

                    break



                await human_delay(3.0, 6.0)



            elif status_info in ["BUTTON_NOT_FOUND", "INPUT_NOT_FOUND"]:

                console.print(f"[bold yellow]⚠ У лида {biz_name} отсутствует кнопка сообщения или поле ввода ({status_info}). Исключаем навсегда.[/bold yellow]")

                self.db.mark_as_unavailable(lead_id, f"Could not message on {platform}: {status_info}")

                await human_delay(3.0, 5.0)



            elif "has been closed" in status_info:

                console.print(f"[bold red]🛑 Окно браузера закрыто. Рассылка {platform} остановлена, лид {biz_name} не потрачен.[/bold red]")

                self.db.mark_as_ready_to_send(lead_id, reason="Reset: browser window was closed while sending")

                break

            else:

                console.print(f"[bold red]✗ Failed to deliver message to {biz_name} ({status_info}). Marked as FAILED_SEND.[/bold red]")

                self.db.mark_as_failed(lead_id, f"Could not send on {platform}: {status_info}")

                await human_delay(5.0, 10.0)



        else:
            # Очередь могла пополниться, пока шла рассылка (поиск работает параллельно): берём новых лидов, пока есть квота
            if auto_fetch and sent_count < remaining_quota:
                fresh = self.db.get_leads_ready_to_send(limit=remaining_quota - sent_count, platform=plat)
                if fresh:
                    console.print(f"[bold cyan]Появились новые лиды ({len(fresh)}), продолжаю рассылку {plat or ''}...[/bold cyan]")
                    await self.run(leads=None, platform=platform, initial_delay_sec=0)
                    return

        console.print("\n[bold green]✔ All outreach tasks for this batch completed![/bold green]")



    async def _ensure_meta_interval(self, min_interval_sec: int = 300, plat_name: str = ""):

        """

        Enforces that at least min_interval_sec (default 300s = 5 minutes)

        has passed since ANY message was sent across either Facebook or Instagram.

        Guarantees that FB and IG never send in bursts or overlapping windows.

        """

        if getattr(self, "_force_send", False):
            return

        elapsed = self.db.get_seconds_since_last_sent()

        if elapsed is not None and elapsed < min_interval_sec:

            wait_time = int(min_interval_sec - elapsed)

            if wait_time > 3:

                mins, secs = divmod(wait_time, 60)

                plat_prefix = f"[{plat_name}] " if plat_name else ""

                console.print(

                    f"\n[bold yellow]🛡 {plat_prefix}Защита Meta от спама: последнее сообщение в Meta было {int(elapsed)}с назад.\n"

                    f"  Ожидание {mins}м {secs:02d}с для соблюдения строгого интервала не менее 5 минут между смс...[/bold yellow]"

                )

                if await terminal_countdown(wait_time, prefix="Meta Inter-Message Safe Delay", skip_key=plat_name or None):
                    self._force_send = True

                console.print(f"[bold green]🔔 Интервал 5+ минут соблюдён! {plat_prefix}Продолжаю отправку...[/bold green]\n")



    async def _warmup_profile(self, url: str, platform: str = "FB") -> bool:
        """
        Human warmup:
        - Stays on profile 8-16 seconds.
        - Scrolls down smoothly 400-800px to view posts.
        - Naturally likes a recent post/photo (Human engagement).
        - Scrolls slightly back up with human pauses.
        """
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=40000)

            # Random dwell time
            dwell = random.uniform(max(8.0, self.min_warmup_s), max(16.0, self.max_warmup_s))
            console.print(f"  [dim]Human warmup: Viewing {platform} profile for {dwell:.1f}s...[/dim]")
            await asyncio.sleep(dwell * 0.3)

            # Emulate natural scroll to look at posts
            scroll_dist = random.randint(400, 750)
            await smooth_scroll(self.page, distance=scroll_dist, direction="down")
            await asyncio.sleep(random.uniform(2.0, 3.5))

            # --- Organic Like on Latest Post ---
            if platform == "FB":
                try:
                    like_selectors = [
                        'div[aria-label="Like"][role="button"]',
                        'div[aria-label="Нравится"][role="button"]',
                        'div[role="button"]:has-text("Like")',
                        'div[role="button"]:has-text("Нравится")'
                    ]
                    for sel in like_selectors:
                        like_btn = await self.page.query_selector(sel)
                        if like_btn and await like_btn.is_visible():
                            pressed = await like_btn.get_attribute("aria-pressed")
                            if pressed != "true":
                                await like_btn.scroll_into_view_if_needed()
                                await asyncio.sleep(random.uniform(1.0, 2.0))
                                await like_btn.click()
                                console.print("  [bold green]👍 Human Warmup: Поставил органический лайк на пост компании[/bold green]")
                                await asyncio.sleep(random.uniform(2.0, 3.5))
                                break
                except Exception as ex_like:
                    logger.debug(f"Warmup like on FB skipped: {ex_like}")

            elif platform == "IG":
                try:
                    post_thumb = await self.page.query_selector('article a[href*="/p/"], a[href*="/p/"], article a[href*="/reel/"]')
                    if post_thumb and await post_thumb.is_visible():
                        await post_thumb.click()
                        await asyncio.sleep(random.uniform(2.5, 4.0))
                        like_svg = await self.page.query_selector('div[role="dialog"] svg[aria-label="Like"], div[role="dialog"] svg[aria-label="Нравится"], svg[aria-label="Like"]')
                        if like_svg:
                            parent_btn = await like_svg.evaluate_handle('el => el.closest("button") || el')
                            if parent_btn:
                                await parent_btn.click()
                                console.print("  [bold yellow]❤ Human Warmup: Поставил лайк на свежий пост Instagram[/bold yellow]")
                                await asyncio.sleep(random.uniform(1.5, 2.5))
                        await self.page.keyboard.press("Escape")
                        await asyncio.sleep(random.uniform(1.5, 2.5))
                except Exception as ex_ig_like:
                    logger.debug(f"Warmup like on IG skipped: {ex_ig_like}")

            # Scroll back up a bit
            await smooth_scroll(self.page, distance=random.randint(150, 300), direction="up")
            await asyncio.sleep(dwell * 0.3)
            return True

        except Exception as e:
            logger.warning(f"Error during warmup on {url}: {e}")
            return False

    async def _confirm_and_dispatch(self, biz_name: str, message: str, platform: str) -> Tuple[bool, str]:

            """

            In semi-automatic (copilot) mode: stops after typing, displays a clean prompt,

            and waits for user confirmation (Enter = send, s = skip, q = quit).

            In full-automatic mode: pauses naturally and presses Enter.

            """

            if self.outreach_mode == "semi_auto":

                console.print("")

                console.print(Panel.fit(

                    f"[bold yellow]🎯 ПОЛУАВТОМАТИЧЕСКИЙ РЕЖИМ (COPILOT)[/bold yellow]\n\n"

                    f"  👤 Получатель:  [bold white]{escape(biz_name or '')}[/bold white] ({platform})\n"

                    f"  💬 Сообщение:   [bold green]\"{escape(message)}\"[/bold green]\n\n"

                    f"  [dim]✔ Текст уже напечатан в поле ввода в окне Chrome.[/dim]\n"

                    f"  [dim]✔ Ты можешь подправить его руками прямо в браузере Chrome при желании.[/dim]\n\n"

                    f"  [bold green][Enter][/bold green]       -> Подтвердить отправку и запустить таймер\n"

                    f"  [bold yellow][s] + Enter[/bold yellow]  -> Пропустить этого лида (Skip)\n"

                    f"  [bold red][q] + Enter[/bold red]  -> Завершить рассылку (Quit)",

                    title="[bold cyan]Контроль перед отправкой[/bold cyan]",

                    border_style="yellow"

                ))



                loop = asyncio.get_event_loop()

                user_choice = await loop.run_in_executor(None, input, "▶ Твоё действие (нажми Enter для отправки): ")

                user_choice = user_choice.strip().lower()



                if user_choice == "q":

                    return False, "USER_QUIT"

                elif user_choice == "s":

                    return False, "USER_SKIPPED"

                else:

                    console.print(f"  [dim]Отправляю сообщение в {platform} (Enter + Send)...[/dim]")

                    await self.page.keyboard.press("Enter")

                    await asyncio.sleep(0.8)

                    try:

                        send_btn = await self.page.query_selector(

                            'div[aria-label="Press enter to send"], span[aria-label="Press enter to send"], '

                            '[aria-label*="Press enter to send"], [aria-label*="Отправить"], button:has-text("Send"), '

                            'button:has-text("Отправить")'

                        )

                        if send_btn and await send_btn.is_visible():

                            await send_btn.click()

                    except Exception:

                        pass



                    console.print(f"  [dim]Проверка реальной доставки сообщения в {platform}...[/dim]")

                    delivered, status = await self._verify_message_delivered(platform)

                    if not delivered:

                        console.print(f"  [bold red]⛔ {platform} вернул ошибку доставки ({status})![/bold red]")

                        return False, status



                    return True, "SENT"

            else:

                console.print("  [dim]Автоматический режим: небольшая естественная пауза перед отправкой (2-3с)...[/dim]")

                await human_delay(1.8, 3.2)

                console.print(f"  [bold green]🚀 Отправка сообщения в {platform} (Enter + Send)...[/bold green]")

                await self.page.keyboard.press("Enter")

                await asyncio.sleep(0.8)

                try:

                    send_btn = await self.page.query_selector(

                        'div[aria-label="Press enter to send"], span[aria-label="Press enter to send"], '

                        '[aria-label*="Press enter to send"], [aria-label*="Отправить"], button:has-text("Send"), '

                        'button:has-text("Отправить")'

                    )

                    if send_btn and await send_btn.is_visible():

                        await send_btn.click()

                except Exception:

                    pass



                console.print(f"  [dim]Проверка реальной доставки сообщения в {platform}...[/dim]")

                delivered, status = await self._verify_message_delivered(platform)

                if not delivered:

                    console.print(f"  [bold red]⛔ {platform} вернул ошибку доставки ({status})![/bold red]")

                    return False, status



                return True, "SENT"



    async def _verify_message_delivered(self, platform: str) -> Tuple[bool, str]:

        """

        Verifies whether the message was genuinely delivered, rejected by recipient / geo-block,

        or blocked by Meta account-level anti-spam.

        Returns: (True, 'SENT') or (False, reason)

        """

        await asyncio.sleep(2.8)  # Allow WebSocket / GraphQL dispatch to finalize



        try:

            status = await self.page.evaluate('''() => {

                const text = (document.body.innerText || '').toLowerCase();



                // 1. Check for genuine Meta Action Block / Account-wide Rate Limits

                const actionBlockKws = [

                    "action blocked",

                    "действие заблокировано",

                    "we limit how often you can do certain things",

                    "мы ограничиваем частоту некоторых действий",

                    "we restrict certain activity",

                    "мы ограничиваем определенные действия",

                    "your account is temporarily restricted",

                    "ваш аккаунт временно ограничен"

                ];

                for (const kw of actionBlockKws) {

                    if (text.includes(kw)) {

                        return { type: "ACTION_BLOCKED", reason: kw };

                    }

                }



                // 2. Check for Recipient / Region restrictions

                const recipientBlockKws = [

                    "you can't message this account",

                    "вы не можете отправить сообщение этому аккаунту",

                    "not everyone can message this account",

                    "can't receive your message",

                    "doesn't allow new message requests",

                    "не принимает новые запросы на переписку",

                    "this person isn't available on messenger",

                    "этот пользователь недоступен в messenger",

                    "you can't message this page",

                    "вы не можете отправлять сообщения этой странице",

                    "isn't available in your region",

                    "недоступен в вашем регионе",

                    "you cannot reply to this conversation",

                    "can't message this account unless they follow you"

                ];

                for (const kw of recipientBlockKws) {

                    if (text.includes(kw)) {

                        return { type: "RECIPIENT_RESTRICTED", reason: kw };

                    }

                }



                // 3. Check for specific message delivery failures on the bubble or alert

                const deliveryErrKws = [

                    "couldn't send",

                    "не удалось отправить",

                    "not delivered",

                    "failed to send",

                    "message wasn't sent",

                    "could not send"

                ];

                for (const kw of deliveryErrKws) {

                    if (text.includes(kw)) {

                        return { type: "DELIVERY_FAILED", reason: kw };

                    }

                }



                return { type: "OK", reason: null };

            }''')



            res_type = status.get("type", "OK")

            res_reason = status.get("reason", "")



            if res_type == "ACTION_BLOCKED":

                return False, f"ACTION_BLOCKED: {res_reason}"

            elif res_type == "RECIPIENT_RESTRICTED":

                return False, f"REGION_RESTRICTED: {res_reason}"

            elif res_type == "DELIVERY_FAILED":

                return False, f"DELIVERY_FAILED: {res_reason}"



            return True, "SENT"

        except Exception as e:

            logger.debug(f"Error checking message delivery status: {e}")

            return True, "SENT" 

        except Exception as e:

            logger.debug(f"Error checking message delivery status: {e}")

            return True, "SENT"



    @staticmethod

    def _get_facebook_messenger_url(profile_url: str) -> Optional[str]:

        """Converts Facebook profile/page URL into a direct full-page Messenger URL."""

        if not profile_url:

            return None

        import urllib.parse

        url = profile_url.strip().rstrip("/")

        if "profile.php" in url and "id=" in url:

            parsed = urllib.parse.urlparse(url)

            params = urllib.parse.parse_qs(parsed.query)

            page_id = params.get("id", [None])[0]

            if page_id:

                return f"https://www.facebook.com/messages/t/{page_id}"

        slug = url.split("/")[-1].split("?")[0]

        if slug and slug not in ["facebook.com", "www.facebook.com"]:

            return f"https://www.facebook.com/messages/t/{slug}"

        return None



    async def _send_facebook_message(self, message: str, biz_name: str, profile_url: str = "") -> Tuple[bool, str]:

        """

        Navigates directly to full-page Facebook Messenger (https://www.facebook.com/messages/t/{id}),

        completely bypassing docked mini-chats, overlapping popups, and wall comment bugs.

        Checks for prior chat history, types human-like characters with typos, and prompts for dispatch.

        """

        try:

            messenger_url = self._get_facebook_messenger_url(profile_url)

            if messenger_url:

                console.print(f"  [dim]Navigating directly to full-page Messenger: {messenger_url}[/dim]")

                await self.page.goto(messenger_url, wait_until="domcontentloaded", timeout=40000)

                await human_delay(3.0, 5.0)

            else:

                logger.warning(f"Could not build Messenger URL from {profile_url}. Using current page.")



            # Fast Region & Availability check for Facebook Messenger

            is_restr, restr_reason = await self._check_profile_restriction("FB")

            if is_restr:

                return False, f"REGION_RESTRICTED: {restr_reason}" 



            # CRITICAL CHECK: Check if there is already an ongoing conversation in DM

            has_history = await self._check_facebook_chat_history()

            if has_history:

                return False, "EXISTING_CHAT"



            # Locate chat input box inside full-page Messenger or dialog

            input_box = None

            for attempt in range(12):

                input_box = await self.page.query_selector('div[role="main"] div[role="textbox"][contenteditable="true"]')

                if not input_box:

                    # Fallback selectors

                    input_box = await self.page.query_selector('div[role="textbox"][contenteditable="true"]')

                

                if input_box and await input_box.is_visible():

                    break

                await asyncio.sleep(0.5)



            if not input_box:

                logger.warning("Could not locate Facebook Messenger chat input box.")

                is_restr, restr_reason = await self._check_profile_restriction("FB")

                if is_restr:

                    return False, f"REGION_RESTRICTED: {restr_reason}"

                return False, "INPUT_NOT_FOUND"



            # Focus and natural typing simulation (with realistic QWERTY typos + corrections)

            await input_box.click()

            await human_delay(0.8, 1.8)



            console.print("  [dim]Typing hook with human-like rhythm, natural bursts and typo corrections...[/dim]")

            await human_type(

                self.page,

                text=message,

                min_delay_ms=self.min_typing_ms,

                max_delay_ms=self.max_typing_ms,

                enable_typos=True,

                typo_chance=0.04

            )



            # Confirm and dispatch based on outreach mode (semi_auto vs full_auto)

            return await self._confirm_and_dispatch(biz_name, message, platform="Facebook")



        except Exception as e:

            logger.warning(f"Error sending Facebook message: {e}")

            return False, f"ERROR_{e}"



    async def _check_facebook_chat_history(self) -> bool:

        """

        Inspects the Messenger dialog to check if prior messages exist.

        Excludes default greeting cards, wall posts, sidebars, and automated FAQ prompt buttons.

        Returns: True if previous messages exist, False if fresh conversation.

        """

        try:

            return await self.page.evaluate('''() => {

                // 1. In full-page Messenger, main container is always div[role="main"]

                let chatContainer = document.querySelector('div[role="main"]');

                

                if (!chatContainer) {

                    const composer = document.querySelector('div[aria-label="Thread composer"], div[role="region"][aria-label*="composer"]');

                    if (composer) {

                        let curr = composer;

                        for (let i = 0; i < 8 && curr.parentElement; i++) {

                            curr = curr.parentElement;

                            if (curr.querySelector('[aria-label*="Close chat"], [aria-label*="Закрыть чат"], [aria-label*="Minimize"]')) {

                                chatContainer = curr;

                                break;

                            }

                        }

                        if (!chatContainer) chatContainer = composer.parentElement;

                    }

                }



                if (!chatContainer) {

                    return false;

                }



                // 2. Check for real messages inside chatContainer:

                const allTexts = Array.from(chatContainer.querySelectorAll('div[dir="auto"], span[dir="auto"]'))

                    .map(el => (el.innerText || '').trim().toLowerCase())

                    .filter(t => t.length > 0);



                const hasMessageSentLabel = allTexts.some(t => 

                    t.includes('message sent') || 

                    t.includes('сообщение отправлено') || 

                    t.includes('by you') || 

                    t.includes('sent by you')

                );

                if (hasMessageSentLabel) {

                    return true;

                }



                if (chatContainer.querySelector('[aria-label*="Sent by you"], [aria-label*="You sent"], [aria-label*="Вы отправили"], [data-testid*="outgoing"]')) {

                    return true;

                }



                const actionBtns = chatContainer.querySelectorAll('[aria-label*="React to"], [aria-label*="Reply to"], [aria-label*="Ответить на"]');

                if (actionBtns.length > 0) {

                    return true;

                }



                return false;

            }''')

        except Exception as e:

            logger.debug(f"Error inspecting FB chat history: {e}")

            return False



    async def _send_instagram_message(self, message: str, biz_name: str) -> Tuple[bool, str]:

        """

        Locates Instagram 'Message' button on profile (supporting multi-language UI, RU/EN/ES),

        handles notifications modal, waits for input in floating chat card or full-page direct,

        checks for existing chat history, types human-like characters (with natural typos/corrections), and sends.

        Returns: (success, status_code)

        """

        try:

            # Ensure page is scrolled back to top so header buttons are in viewport

            try:

                await self.page.evaluate('window.scrollTo({ top: 0, behavior: "instant" })')

                await asyncio.sleep(0.8)

            except Exception:

                pass



            console.print("  [dim]Locating Instagram 'Message' button...[/dim]")

            btn_selectors = [

                'div[role="button"]:has-text("Отправить сообщение")',

                'div[role="button"]:has-text("Message")',

                'div[role="button"]:has-text("Сообщение")',

                'button:has-text("Отправить сообщение")',

                'button:has-text("Message")',

                'button:has-text("Сообщение")',

                'header div[role="button"]:has-text("Отправить сообщение")',

                'header div[role="button"]:has-text("Message")',

                'header button:has-text("Отправить сообщение")',

                'header button:has-text("Message")',

                'div[role="button"]:has-text("Enviar mensaje")',

                'button:has-text("Enviar mensaje")',

                'a[href*="/direct/t/"]'

            ]



            msg_btn = None

            # Retry over 8 seconds to allow React/Next hydration

            for _ in range(16):

                for sel in btn_selectors:

                    try:

                        el = await self.page.query_selector(sel)

                        if el and await el.is_visible():

                            msg_btn = el

                            break

                    except Exception:

                        continue

                if msg_btn:

                    break

                await asyncio.sleep(0.5)



            # Fallback: search via JS evaluation if standard selectors missed it

            if not msg_btn:

                handle = await self.page.evaluate_handle('''() => {

                    const candidates = Array.from(document.querySelectorAll('header button, header div[role="button"], main button, main div[role="button"], div[role="button"], button, a[href*="/direct/t/"]'));

                    const keywords = ['отправить сообщение', 'message', 'сообщение', 'enviar mensaje'];

                    for (const el of candidates) {

                        if (!el || el.offsetParent === null) continue;

                        const txt = (el.innerText || el.textContent || '').trim().toLowerCase();

                        if (keywords.some(kw => txt === kw || txt.includes(kw))) {

                            return el;

                        }

                    }

                    return null;

                }''')

                if handle:

                    try:

                        el = handle.as_element()

                        if el:

                            msg_btn = el

                    except Exception:

                        pass



            if not msg_btn:

                logger.warning("Instagram 'Message' button not found on profile.")

                is_restr, restr_reason = await self._check_profile_restriction("IG")

                if is_restr:

                    return False, f"REGION_RESTRICTED: {restr_reason}"

                return False, "CANT_MESSAGE_ACCOUNT: Message button not found on profile"



            await msg_btn.click()

            await human_delay(3.0, 5.0)



            # Check if clicking Message triggered a restriction dialog/alert

            is_restr, restr_reason = await self._check_profile_restriction("IG")

            if is_restr:

                return False, f"REGION_RESTRICTED: {restr_reason}" 



            # Dismiss "Turn on Notifications" modal if Instagram displays it (supports RU and EN)

            try:

                not_now_selectors = [

                    'button:has-text("Не сейчас")',

                    'button:has-text("Not Now")',

                    'button:has-text("Отмена")',

                    'button:has-text("Cancel")',

                    'div[role="button"]:has-text("Не сейчас")',

                    'div[role="button"]:has-text("Not Now")'

                ]

                for n_sel in not_now_selectors:

                    btn = await self.page.query_selector(n_sel)

                    if btn and await btn.is_visible():

                        await btn.click()

                        await human_delay(1.0, 2.0)

                        break

            except Exception:

                pass



            # Locate IG direct message textarea (supports both floating chat popup and full page direct)

            input_selectors = [

                'div[role="textbox"][contenteditable="true"]',

                'div[role="textbox"]',

                'div[contenteditable="true"]',

                'div[aria-label*="Message"][role="textbox"]',

                'div[aria-label*="Сообщение"][role="textbox"]',

                'textarea[placeholder*="Message"]',

                'textarea[placeholder*="сообщени"]',

                'textarea'

            ]



            input_box = None

            for _ in range(16):

                for sel in input_selectors:

                    try:

                        el = await self.page.query_selector(sel)

                        if el and await el.is_visible():

                            input_box = el

                            break

                    except Exception:

                        continue

                if input_box:

                    break

                await asyncio.sleep(0.5)



            if not input_box:

                logger.warning("Could not locate Instagram Direct message input area.")

                is_restr, restr_reason = await self._check_profile_restriction("IG")

                if is_restr:

                    return False, f"REGION_RESTRICTED: {restr_reason}"

                return False, "INPUT_NOT_FOUND"



            # CRITICAL CHECK: Check if there is already an ongoing conversation in IG Direct

            has_history = await self._check_instagram_chat_history()

            if has_history:

                return False, "EXISTING_CHAT"



            await input_box.click()

            await human_delay(0.8, 1.8)



            console.print("  [dim]Typing hook with human-like rhythm, natural bursts and typo corrections...[/dim]")

            await human_type(

                self.page,

                text=message,

                min_delay_ms=self.min_typing_ms,

                max_delay_ms=self.max_typing_ms,

                enable_typos=True,

                typo_chance=0.04

            )



            # Confirm and dispatch based on outreach mode (semi_auto vs full_auto)

            return await self._confirm_and_dispatch(biz_name, message, platform="Instagram")



        except Exception as e:

            logger.warning(f"Error sending Instagram message: {e}")

            return False, f"ERROR_{e}"



    async def _check_instagram_chat_history(self) -> bool:

        """

        Inspects open Instagram Direct thread to check if prior messages exist.

        Supports both floating popup chat card and full-page /direct/t/ view.

        Returns: True if previous messages exist, False if fresh conversation.

        """

        try:

            return await self.page.evaluate('''() => {

                // 1. Locate active chat container

                let container = null;

                const textbox = document.querySelector('div[role="textbox"], div[contenteditable="true"], textarea');

                if (textbox) {

                    let curr = textbox;

                    for (let i = 0; i < 12 && curr.parentElement; i++) {

                        curr = curr.parentElement;

                        if (curr.querySelector('[aria-label*="Close"], [aria-label*="Закрыть"], svg[aria-label*="Close"], svg[aria-label*="Закрыть"]') ||

                            curr.getAttribute('role') === 'dialog' ||

                            curr.style.position === 'fixed' ||

                            curr.classList.contains('x1n2onr6')) {

                            container = curr;

                            break;

                        }

                    }

                }

                if (!container) {

                    container = document.querySelector('section main div[role="grid"], div[role="main"] div[role="grid"], section main, div[role="main"]');

                }

                if (!container) {

                    return false;

                }



                // 2. Action buttons on message bubbles (React, Reply, Like)

                const actionBtns = container.querySelectorAll(

                    '[aria-label*="React"], [aria-label*="Реакци"], [aria-label*="Reply"], [aria-label*="Ответить"], [aria-label*="Нравится"], [aria-label*="Like"]'

                );

                if (actionBtns.length > 0) {

                    return true;

                }



                // 3. Delivery indicators

                const deliveryIndicators = container.querySelectorAll(

                    '[aria-label*="Sent"], [aria-label*="Отправлено"], [aria-label*="Seen"], [aria-label*="Просмотрено"], [aria-label*="Delivered"], [aria-label*="Доставлено"]'

                );

                if (deliveryIndicators.length > 0) {

                    return true;

                }



                // 4. Message rows

                const rows = Array.from(container.querySelectorAll('div[role="row"]'));

                if (rows.length > 0) {

                    return true;

                }



                // 5. Check text elements, excluding system keywords

                const allTexts = Array.from(container.querySelectorAll('div[dir="auto"], span[dir="auto"]'))

                    .map(el => (el.innerText || '').trim())

                    .filter(t => t.length > 0);



                const systemKeywords = [

                    'active now', 'active today', 'followed by', 'followers', 'following', 'posts',

                    'view profile', 'you follow each other', 'suggested', 'message...', 'message',

                    'this message was deleted', 'seen',

                    'в сети', 'в сети сегодня', 'подписчиков', 'подписчика', 'подписчик',

                    'подписок', 'подписки', 'подписка', 'публикаций', 'публикации', 'публикация',

                    'смотреть профиль', 'посмотреть профиль', 'вы подписаны друг на друга',

                    'напишите сообщение...', 'напишите сообщение', 'сообщение...', 'сообщение',

                    'деятель искусств', 'автомобили', 'компания', 'бизнес', 'услуги', 'личный блог'

                ];



                const realMessages = allTexts.filter(txt => {

                    const lower = txt.toLowerCase();

                    if (txt.length < 3) return false;

                    if (systemKeywords.some(kw => lower === kw || lower.startsWith(kw) || lower.includes(kw))) {

                        return false;

                    }

                    if (window.location.pathname.includes(lower)) {

                        return false;

                    }

                    return true;

                });



                return realMessages.length > 0;

            }''')

        except Exception as e:

            logger.debug(f"Error inspecting IG chat history: {e}")

            return False



    def _detect_website(self, bio_text: str, links: List[Dict[str, str]]) -> Tuple[bool, Optional[str]]:

        """

        Analyzes profile links and bio text to detect if a legitimate business website is present.

        Excludes messaging links (wa.me), link-in-bio aggregators (linktr.ee), and social profiles.

        """

        # 1. Inspect all external anchor tags in profile header / intro

        for l in links:

            raw_href = l.get("href", "")

            link_text = l.get("text", "")



            # Check destination URL after unwrapping Meta redirects

            unwrapped = unwrap_meta_redirect(raw_href)

            has_site, domain = is_custom_website(unwrapped, self.excluded_domains)

            if has_site:

                return True, domain



            # Check visible link text (e.g. if Instagram shows 'austinepoxy.com')

            if link_text and "." in link_text and "@" not in link_text:

                has_site, domain = is_custom_website(link_text, self.excluded_domains)

                if has_site:

                    return True, domain



        # 2. Inspect bio / intro text (stripping emails first to avoid false positives)

        if bio_text:

            cleaned_bio = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', ' ', bio_text)



            # Match explicit URLs (http://, https://, www.)

            url_matches = re.findall(r'(?:https?://|www\.)[a-zA-Z0-9.\-]+(?:\.[a-zA-Z]{2,})[^\s]*', cleaned_bio, re.I)

            for u in url_matches:

                has_site, domain = is_custom_website(u, self.excluded_domains)

                if has_site:

                    return True, domain



            # Match standalone domain patterns (e.g. austinepoxy.com, dallasfloors.co)

            domain_matches = re.findall(r'\b[a-zA-Z0-9\-]+\.(?:com|net|org|co|us|biz|info|ae|io|online|tech|pro|site|me|floor|flooring)\b[^\s]*', cleaned_bio, re.I)

            for d in domain_matches:

                has_site, domain = is_custom_website(d, self.excluded_domains)

                if has_site:

                    return True, domain



        return False, None



    async def _check_instagram_profile_for_website(self) -> Tuple[bool, Optional[str]]:

        """

        Extracts links and bio text from the active Instagram profile page and checks for a website.

        """

        try:

            profile_data = await self.page.evaluate('''() => {

                const results = { links: [], bioText: "" };

                

                // Locate profile header container

                const header = document.querySelector('header') || document.querySelector('main header') || document.querySelector('main > div > div') || document.querySelector('main');

                if (!header) return results;



                // Extract all external links

                const anchors = Array.from(header.querySelectorAll('a'));

                for (const a of anchors) {

                    const href = (a.getAttribute('href') || a.href || '').trim();

                    const text = (a.innerText || a.textContent || '').trim();

                    

                    if (href && (href.includes('l.instagram.com') || (!href.startsWith('/') && !href.includes('instagram.com/')))) {

                        results.links.push({ href, text });

                    }

                }



                // Extract bio text

                results.bioText = (header.innerText || header.textContent || '').slice(0, 3000);

                return results;

            }''')



            links = profile_data.get("links", [])

            bio_text = profile_data.get("bioText", "")

            return self._detect_website(bio_text, links)

        except Exception as e:

            logger.debug(f"Error checking Instagram profile for website: {e}")

            return False, None



    async def _check_facebook_profile_for_website(self) -> Tuple[bool, Optional[str]]:

        """

        Extracts links and intro text from the active Facebook page and checks for a website.

        """

        try:

            page_data = await self.page.evaluate('''() => {

                const results = { links: [], bioText: "" };

                const main = document.querySelector('div[role="main"]') || document.body;

                if (!main) return results;



                // Extract external links from Intro / About cards

                const anchors = Array.from(main.querySelectorAll('a[href*="l.facebook.com"], a[target="_blank"]'));

                for (const a of anchors) {

                    const href = (a.getAttribute('href') || a.href || '').trim();

                    const text = (a.innerText || a.textContent || '').trim();

                    if (href && !href.includes('facebook.com/')) {

                        results.links.push({ href, text });

                    }

                }



                results.bioText = (main.innerText || main.textContent || '').slice(0, 4000);

                return results;

            }''')



            links = page_data.get("links", [])

            bio_text = page_data.get("bioText", "")

            return self._detect_website(bio_text, links)

        except Exception as e:

            logger.debug(f"Error checking Facebook page for website: {e}")

            return False, None



    async def _check_profile_restriction(self, platform: str) -> Tuple[bool, Optional[str]]:

        """

        Inspects the page to detect if the profile or account is region-restricted,

        geo-blocked, age-gated, deleted, or forbidden from receiving messages.

        """

        try:

            detected_reason = await self.page.evaluate('''() => {

                const text = (document.body.innerText || document.body.textContent || '').toLowerCase();

                

                // Specific region, account block, and messaging restriction keywords

                const regionKeywords = [

                    "isn't available in your region",

                    "is not available in your region",

                    "not available in your region",

                    "недоступен в вашем регионе",

                    "недоступно в вашем регионе",

                    "account isn't available",

                    "restricted profile",

                    "ограниченный профиль",

                    "you must be 18 or older",

                    "age-restricted",

                    "sorry, this page isn't available",

                    "page isn't available",

                    "эта страница недоступна",

                    "контент сейчас недоступен",

                    "this content isn't available right now",

                    "this content is not available right now",

                    "when this happens, it's usually because the owner only shared it with a small group of people",

                    "the link you followed may be broken",

                    "user not found",

                    "пользователь не найден",

                    "you can't message this account",

                    "вы не можете отправить сообщение этому аккаунту",

                    "вы не можете отправить сообщение",

                    "not everyone can message this account",

                    "can't receive your message",

                    "doesn't allow new message requests",

                    "не принимает новые запросы на переписку",

                    "this person isn't available on messenger",

                    "этот пользователь недоступен в messenger",

                    "you can't message this page",

                    "вы не можете отправлять сообщения этой странице",

                    "you cannot reply to this conversation",

                    "вы не можете отвечать в этой переписке",

                    "can't message this account unless they follow you",

                    "this account is private",

                    "это закрытый аккаунт",

                    "аккаунт является закрытым"

                ];



                for (const kw of regionKeywords) {

                    if (text.includes(kw)) {

                        return kw;

                    }

                }

                return null;

            }''')



            if detected_reason:

                return True, detected_reason

            return False, None

        except Exception as e:

            logger.debug(f"Error checking profile restriction: {e}")

            return False, None

        except Exception as e:

            logger.debug(f"Error checking profile restriction: {e}")

            return False, None
