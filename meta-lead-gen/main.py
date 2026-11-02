import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys

# Ensure UTF-8 output on Windows consoles to prevent UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

BASE_DIR = Path(__file__).resolve().parent

from core.browser import BrowserSession, interactive_login
from core.catalog import build_plan, load_catalog
from core.database import DatabaseManager
from core.dolphin import DolphinConnectionError
from modules.gmaps import GMapsScraper
from modules.scraper import MetaScraperQualifier
from modules.sender import SafeOutreachSender

console = Console(force_terminal=True, legacy_windows=False)


def setup_logging(debug: bool = False):
    """Sets up formatted terminal logging using Rich."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console, show_path=False)]
    )


def load_configuration(config_path: str = None) -> dict:
    """Loads configuration with .env override support."""
    load_dotenv(BASE_DIR / ".env")
    if not config_path:
        cfg_file = BASE_DIR / "config.json"
    else:
        p = Path(config_path)
        cfg_file = p if p.is_absolute() else (BASE_DIR / p)

    config = {}
    if cfg_file.exists():
        with open(cfg_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        console.print(f"[yellow]Warning: {config_path} not found. Using defaults.[/yellow]")

    # Defaults
    config.setdefault("browser_engine", "playwright")
    config.setdefault("headless", False)

    # Environment overrides
    if os.getenv("DOLPHIN_API_BASE"):
        config.setdefault("dolphin", {})["api_base"] = os.getenv("DOLPHIN_API_BASE")
    if os.getenv("DOLPHIN_PROFILE_ID"):
        config.setdefault("dolphin", {})["profile_id"] = os.getenv("DOLPHIN_PROFILE_ID")
    if os.getenv("CAMPAIGN_NICHE"):
        config["niche"] = os.getenv("CAMPAIGN_NICHE")
    if os.getenv("MAX_DAILY_MESSAGES"):
        config.setdefault("limits", {})["max_daily_messages"] = int(os.getenv("MAX_DAILY_MESSAGES"))

    return config


def print_stats(db: DatabaseManager):
    """Displays a clean summary table of leads in the database."""
    stats = db.get_stats()
    sent_fb = db.get_sent_today_count(platform="FB")
    sent_ig = db.get_sent_today_count(platform="IG")
    sent_total = sent_fb + sent_ig

    table = Table(title="📊 Meta Lead Generation Database Overview", border_style="cyan")
    table.add_column("Status Category", style="bold white")
    table.add_column("Count", justify="right", style="bold green")

    for status, count in stats.items():
        if status != "TOTAL":
            style_color = (
                "green" if status == "READY_TO_SEND"
                else "yellow" if status == "STEP_1_SENT"
                else "magenta" if status == "EXISTING_CHAT"
                else "white"
            )
            table.add_row(status, f"[{style_color}]{count:,}[/{style_color}]")

    table.add_section()
    table.add_row("TOTAL LEADS IN DB", f"[bold cyan]{stats.get('TOTAL', 0):,}[/bold cyan]")
    table.add_row("OUTREACH SENT TODAY", f"[bold magenta]{sent_total:,}[/bold magenta] (FB: {sent_fb}/10 | IG: {sent_ig}/10)")

    console.print(table)
    console.print(f"[dim]📁 Local SQLite: {db.db_path} | Synced CSV: {db.csv_path}[/dim]\n")


def print_catalog():
    """Lists niches and geos that can be passed to `scrape --niches ... --geos ...`."""
    catalog = load_catalog()
    table = Table(title="Ниши (--niches)", border_style="cyan")
    table.add_column("id", style="bold white")
    table.add_column("Название")
    table.add_column("Ключевые слова")
    for nid, n in catalog["niches"].items():
        table.add_row(nid, n.get("label", ""), ", ".join(n.get("keywords", [])))
    console.print(table)
    table = Table(title="Гео (--geos)", border_style="cyan")
    table.add_column("id", style="bold white")
    table.add_column("Название")
    table.add_column("Страна")
    table.add_column("Города")
    for gid, g in catalog["geos"].items():
        table.add_row(gid, g.get("label", ""), g.get("country", ""), ", ".join(g.get("cities", [])))
    console.print(table)


def resolve_scrape_plan(args, config: dict):
    """
    Собирает план запросов из --niches/--geos/--custom или из config["campaign"].
    Если ничего не выбрано, возвращает None и скрапер работает по старому search.queries.
    """
    campaign = config.get("campaign", {})
    niches = args.niches or ",".join(campaign.get("niches", []))
    geos = args.geos or ",".join(campaign.get("geos", []))
    custom = args.custom
    if args.query:
        return None
    if not (niches or custom):
        return None
    per_pair = args.queries_per_pair or campaign.get("queries_per_pair", 8)
    try:
        plan = build_plan(load_catalog(), niches, geos, custom_keywords=custom, queries_per_pair=per_pair)
    except ValueError as err:
        console.print(f"[bold red]❌ {err}[/bold red]")
        sys.exit(2)
    console.print(f"[green]План поиска: {len(plan)} запросов[/green]")
    return plan


async def main():
    parser = argparse.ArgumentParser(
        description="Meta Ad Library Lead Generation & Safe Outreach Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py scrape          # Run Meta Ad Library Scraper & Qualifier
  python main.py scrape --niches car_rental,beauty_salon --geos AE_DUBAI,SA
  python main.py scrape --custom "tattoo studio,nail art" --geos AE_DUBAI
  python main.py gmaps --niches car_rental --geos AE_DUBAI   # Google Maps: companies without a website
  python main.py catalog         # Show available niches and geos
  python main.py send            # Run Safe Outreach Sender (Step 1)
  python main.py stats           # Display current database counts & summary
  python main.py export          # Force sync database into ./data/leads.csv
  python main.py login-fb        # Open Facebook in visible browser to log in
  python main.py login-ig        # Open Instagram in visible browser to log in
        """
    )
    parser.add_argument("command", choices=[
        "scrape", "gmaps", "send", "stats", "export", "catalog", "replied", "mockups",
        "login-fb", "login-ig",
        "set-profile", "set-profile-fb", "set-profile-ig"
    ], help="Action to execute")
    parser.add_argument("value", nargs="?", help="Optional value for command")
    parser.add_argument("--config", default="config.json", help="Path to config.json file")
    parser.add_argument("--profile-id", help="Override Dolphin profile ID")
    parser.add_argument("--niche", help="Override campaign niche (epoxy, detailing, shop, home_services)")
    parser.add_argument("--query", help="Run scrape on a single custom search query")
    parser.add_argument("--niches", help="Comma-separated niche ids from catalog.json (see: python main.py catalog)")
    parser.add_argument("--geos", help="Comma-separated geo ids from catalog.json")
    parser.add_argument("--custom", help="Comma-separated custom search keywords (no niche signal filter)")
    parser.add_argument("--max-ads", type=int, help="Ads to inspect per query")
    parser.add_argument("--queries-per-pair", type=int, help="Max queries per niche x geo pair (default 8)")
    parser.add_argument("--max-places", type=int, help="gmaps: places to inspect per query (default 40)")
    parser.add_argument("--allow-websites", action="store_true", help="Do not disqualify leads that already have a website")
    parser.add_argument("--engine", choices=["playwright", "dolphin"], help="Override browser engine")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless background mode")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()
    setup_logging(args.debug)

    config = load_configuration(args.config)
    if args.profile_id:
        config.setdefault("dolphin", {})["profile_id"] = args.profile_id
    if args.niche:
        config["niche"] = args.niche
    if args.query:
        config.setdefault("search", {})["queries"] = [args.query]
    if args.engine:
        config["browser_engine"] = args.engine
    if args.headless:
        config["headless"] = True
    if args.max_ads:
        config.setdefault("search", {})["max_ads_per_query"] = args.max_ads
    if args.allow_websites:
        config.setdefault("campaign", {})["website_policy"] = "any"

    # Initialize SQLite and CSV storage
    db = DatabaseManager(
        db_path=str(BASE_DIR / "data" / "leads.db"),
        csv_path=str(BASE_DIR / "data" / "leads.csv")
    )

    if args.command == "stats":
        print_stats(db)
        return

    if args.command == "catalog":
        print_catalog()
        return

    if args.command in ("replied", "mockups"):
        from core import mockup
        if args.command == "replied":
            lead = db.get_lead(int(args.value)) if args.value and args.value.isdigit() else None
            if not lead:
                console.print("[red]Usage: python main.py replied <lead_id>[/red]")
                return
            db.mark_as_replied(lead["id"])
            made = await mockup.generate_for_leads([lead], db=db)
        else:
            # mockups sample: 20 aesthetics-лидов для ручной проверки (без отметки об ответе)
            import sqlite3
            with db._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM leads WHERE niche = 'aesthetics' AND business_name <> '' "
                    "AND status IN ('READY_TO_SEND','ROUTED_WA','STEP_1_SENT') "
                    "ORDER BY RANDOM() LIMIT 20").fetchall()
            made = await mockup.generate_for_leads([dict(r) for r in rows], out_dir=mockup.OUT_DIR / "samples")
        console.print(f"[green]Mockups created: {len(made)}[/green]")
        for lead_id, path in made.items():
            console.print(f"  {lead_id}: {path}")
        return

    if args.command == "export":
        db.export_to_csv()
        console.print(f"[bold green]✔ Successfully exported leads to {db.csv_path}[/bold green]")
        return

    if args.command in ("login-fb", "login-ig"):
        platform = "fb" if args.command == "login-fb" else "ig"
        await interactive_login(platform=platform, config=config)
        return

    if args.command in ("set-profile", "set-profile-fb", "set-profile-ig"):
        new_id = (args.value or args.profile_id or "").strip()
        if not new_id:
            console.print("[red]Error: Please provide a profile ID, e.g.: python main.py set-profile-fb 12345678[/red]")
            return
        cfg_file = Path(args.config) if Path(args.config).is_absolute() else (BASE_DIR / args.config)
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg_data = json.load(f)
        if args.command == "set-profile-fb":
            cfg_data.setdefault("dolphin", {})["profile_id_fb"] = new_id
            console.print(f"[bold green]✔ Facebook Profile ID successfully set to: {new_id}[/bold green]")
        elif args.command == "set-profile-ig":
            cfg_data.setdefault("dolphin", {})["profile_id_ig"] = new_id
            console.print(f"[bold green]✔ Instagram Profile ID successfully set to: {new_id}[/bold green]")
        else:
            cfg_data.setdefault("dolphin", {})["profile_id"] = new_id
            console.print(f"[bold green]✔ Default Profile ID successfully set to: {new_id}[/bold green]")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f, indent=2, ensure_ascii=False)
        return

    if args.command == "gmaps":
        plan = resolve_scrape_plan(args, config)
        if not plan:
            console.print("[bold red]❌ Укажите --niches (и --geos) или --custom[/bold red]")
            sys.exit(2)
        config["plan"] = plan
        if args.max_places:
            config.setdefault("gmaps", {})["max_places_per_query"] = args.max_places
        try:
            await GMapsScraper(db=db, config=config).run()
        except KeyboardInterrupt:
            console.print("\n[yellow]Прервано (Ctrl+C).[/yellow]")
        db.export_to_csv()
        print_stats(db)
        return

    engine_name = config.get("browser_engine", "playwright").upper()

    try:
        if args.command == "scrape":
            plan = resolve_scrape_plan(args, config)
            if plan:
                config["plan"] = plan
            console.print(f"[cyan]Connecting browser ({engine_name}) for Meta Ad Library scraping...[/cyan]")
            session = BrowserSession(platform="fb", config=config)
            try:
                _, page = await session.start()
                scraper = MetaScraperQualifier(page=page, db=db, config=config)
                await scraper.run()
                print_stats(db)
            finally:
                await session.stop()

        elif args.command == "send":
            target_plat = (args.value or "").upper()
            if target_plat in ("INSTAGRAM", "IG"):
                target_plat = "IG"
            elif target_plat in ("FACEBOOK", "FB"):
                target_plat = "FB"
            else:
                target_plat = None

            max_daily_fb = config.get("limits", {}).get("max_daily_messages_fb", 10)
            max_daily_ig = config.get("limits", {}).get("max_daily_messages_ig", 10)
            sent_fb = db.get_sent_today_count(platform="FB")
            sent_ig = db.get_sent_today_count(platform="IG")
            rem_fb = max(0, max_daily_fb - sent_fb)
            rem_ig = max(0, max_daily_ig - sent_ig)

            console.print(
                f"[cyan]Outreach daily quota: "
                f"Facebook [bold]{sent_fb}/{max_daily_fb}[/bold] (rem: {rem_fb}) | "
                f"Instagram [bold]{sent_ig}/{max_daily_ig}[/bold] (rem: {rem_ig})[/cyan]"
            )

            if target_plat == "FB":
                if rem_fb <= 0:
                    console.print(f"[bold red]⛔ Лимит сообщений для Facebook на сегодня исчерпан ({sent_fb}/{max_daily_fb})![/bold red]")
                    return
                fb_leads = db.get_leads_ready_to_send(limit=rem_fb, platform="FB")
                ig_leads = []
            elif target_plat == "IG":
                if rem_ig <= 0:
                    console.print(f"[bold red]⛔ Лимит сообщений для Instagram на сегодня исчерпан ({sent_ig}/{max_daily_ig})![/bold red]")
                    return
                fb_leads = []
                ig_leads = db.get_leads_ready_to_send(limit=rem_ig, platform="IG")
            else:
                if rem_fb <= 0 and rem_ig <= 0:
                    console.print("[bold red]⛔ Daily message limits reached for both platforms today! Stopping outreach safely.[/bold red]")
                    return
                fb_leads = db.get_leads_ready_to_send(limit=rem_fb, platform="FB") if rem_fb > 0 else []
                ig_leads = db.get_leads_ready_to_send(limit=rem_ig, platform="IG") if rem_ig > 0 else []

            if not fb_leads and not ig_leads:
                target_text = f" для {target_plat}" if target_plat else ""
                console.print(f"[yellow]⚠ В базе нет лидов со статусом 'READY_TO_SEND'{target_text} для сегодняшней рассылки.[/yellow]")
                console.print("Сначала запустите поиск лидов: [bold cyan]python main.py scrape[/bold cyan]")
                return

            console.print(f"[green]Session queue: {len(fb_leads)} lead(s) for Facebook, {len(ig_leads)} lead(s) for Instagram.[/green]")

            async def run_fb_worker():
                if not fb_leads:
                    return
                console.print(f"\n[bold blue]━━━ Launching Facebook Browser ({engine_name}) for {len(fb_leads)} lead(s) ━━━[/bold blue]")
                session_fb = BrowserSession(platform="fb", config=config)
                try:
                    _, fb_page = await session_fb.start()
                    sender_fb = SafeOutreachSender(page=fb_page, db=db, config=config)
                    await sender_fb.run(leads=fb_leads, platform="FB", initial_delay_sec=0)
                finally:
                    await session_fb.stop()

            async def run_ig_worker():
                if not ig_leads:
                    return
                console.print(f"\n[bold magenta]━━━ Launching Instagram Browser ({engine_name}) for {len(ig_leads)} lead(s) ━━━[/bold magenta]")
                session_ig = BrowserSession(platform="ig", config=config)
                try:
                    _, ig_page = await session_ig.start()
                    sender_ig = SafeOutreachSender(page=ig_page, db=db, config=config)
                    cross_delay = config.get("delays", {}).get("min_cross_platform_delay_sec", 300)
                    init_delay = cross_delay if fb_leads else 0
                    await sender_ig.run(leads=ig_leads, platform="IG", initial_delay_sec=init_delay)
                finally:
                    await session_ig.stop()

            if fb_leads and ig_leads:
                from rich.panel import Panel
                console.print(Panel.fit(
                    "[bold green]⚡ ПАРАЛЛЕЛЬНАЯ РАССЫЛКА FACEBOOK + INSTAGRAM (АВТОМАТ)[/bold green]\n\n"
                    "• Окно 1: Facebook (порт 9333, профиль chrome_fb)\n"
                    "• Окно 2: Instagram (порт 9334, профиль chrome_ig)\n"
                    "• 100% Автоматическая отправка (Enter нажимается сам без ручного подтверждения)\n"
                    "• Разнесение стартов: Facebook начинает СРАЗУ, Instagram — через 5 минут\n"
                    "• Защита Meta: интервал между ЛЮБЫМИ смс в Meta строго не менее 5 минут",
                    border_style="green",
                    title="Meta Safety Orchestrator"
                ))
                await asyncio.gather(run_fb_worker(), run_ig_worker())
            elif fb_leads:
                await run_fb_worker()
            elif ig_leads:
                await run_ig_worker()

            print_stats(db)

    except DolphinConnectionError as d_err:
        console.print(f"\n[bold red]❌ Dolphin{{anty}} Connection Error:[/bold red]\n{d_err}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution interrupted by user (Ctrl+C). Cleaning up...[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]Unhandled Exception:[/bold red] {e}")
        logging.exception(e)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
