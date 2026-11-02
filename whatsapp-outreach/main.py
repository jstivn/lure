"""
WhatsApp Outreach Tool CLI Entry Point.
Controls semi-automatic outreach across BitBrowser profiles,
lead imports, and database management.
"""

import asyncio
import sys

# Force UTF-8 on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table

from modules.sender import WhatsAppSender
from modules.importer import LeadImporter
from core.database import WhatsAppDatabase

console = Console(force_terminal=True, legacy_windows=False)


async def main():
    args = sys.argv[1:]
    command = args[0].lower() if args else "help"

    if command == "send":
        sender = WhatsAppSender()
        target = "parallel"
        if len(args) > 1:
            arg_val = args[1].lower()
            if arg_val in ["2", "wp2", "profile_2", "personal"]:
                target = "profile_2"
            elif arg_val in ["1", "wp1", "profile_1", "business"]:
                target = "profile_1"
            elif arg_val in ["all", "both", "parallel", "3"]:
                target = "parallel"

        if target == "parallel":
            await sender.run_parallel_session()
        else:
            await sender.run_session(target_account_key=target)

    elif command == "import":
        if len(args) < 2:
            console.print("[bold red]Usage: python main.py import <path_to_file.csv> [niche][/bold red]")
            return
        file_path = args[1]
        niche = args[2] if len(args) > 2 else "recovery"
        importer = LeadImporter()
        importer.import_file(file_path, default_niche=niche)

    elif command == "add":
        if len(args) < 2:
            console.print("[bold red]Usage: python main.py add <phone> [name] [niche] [account_1_or_2][/bold red]")
            return
        phone = args[1]
        name = args[2] if len(args) > 2 else "Lead"
        niche = args[3] if len(args) > 3 else "recovery"
        acc_num = args[4] if len(args) > 4 else "1"
        account = "profile_2" if acc_num == "2" else "profile_1"

        db = WhatsAppDatabase()
        added = db.add_lead(phone, name, niche, account)
        if added:
            norm = db.normalize_phone(phone)
            console.print(f"[bold green]✔ Added lead {norm} ({name}) to {account} queue![/bold green]")
        else:
            console.print(f"[bold yellow]Lead {phone} is already in database or invalid format.[/bold yellow]")

    elif command == "stats":
        db = WhatsAppDatabase()
        stats = db.get_stats()
        table = Table(title="WhatsApp Outreach Database Stats", border_style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Count", justify="right", style="bold")

        for status, count in stats["by_status"].items():
            color = "green" if status == "SENT" else ("yellow" if status == "QUEUED" else "red")
            table.add_row(status, f"[{color}]{count}[/{color}]")

        table.add_row("Total Records", f"[bold cyan]{stats['total']}[/bold cyan]")
        console.print(table)

        # Show today's count
        sent_p1 = db.count_sent_today("profile_1")
        sent_p2 = db.count_sent_today("profile_2")
        console.print("\nSent Today:")
        console.print(f"  • WP 1 (Бизнес #1): [bold green]{sent_p1}/6[/bold green]")
        console.print(f"  • wp2 (Номер #2):   [bold green]{sent_p2}/6[/bold green]")

    else:
        console.print("[bold cyan]WhatsApp Outreach Copilot CLI[/bold cyan]")
        console.print("Commands:")
        console.print("  [green]python main.py send all[/green]               - Parallel send from both WP 1 + WP 2 (5+ min interval)")
        console.print("  [green]python main.py send 1[/green]                 - Start sending from WP 1 (Business)")
        console.print("  [green]python main.py send 2[/green]                 - Start sending from wp2 (Personal)")
        console.print("  [green]python main.py import <file.csv>[/green]     - Import and filter Google Maps export")
        console.print("  [green]python main.py add <phone> [name][/green]      - Manually queue a single test number")
        console.print("  [green]python main.py stats[/green]                 - View database queue & today's counts")


if __name__ == "__main__":
    asyncio.run(main())
