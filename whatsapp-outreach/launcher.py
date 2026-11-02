"""
Interactive Launcher Menu for WhatsApp Outreach.
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
from rich.panel import Panel
from rich.prompt import Prompt

from core.database import WhatsAppDatabase
from modules.sender import WhatsAppSender
from modules.importer import LeadImporter

console = Console(force_terminal=True, legacy_windows=False)


def show_menu():
    db = WhatsAppDatabase()
    sent_p1 = db.count_sent_today("profile_1")
    sent_p2 = db.count_sent_today("profile_2")

    console.print(Panel(
        f"[bold cyan]📱 WHATSAPP OUTREACH v2.0 (ПОЛНЫЙ АВТОМАТ)[/bold cyan]\n"
        f"Статус на сегодня:\n"
        f"  • [green]WP 1 (Бизнес #1):[/green] отправлено [bold]{sent_p1}/6[/bold]\n"
        f"  • [green]wp2 (Номер #2):[/green]   отправлено [bold]{sent_p2}/6[/bold]\n\n"
        f"Выберите действие:\n"
        f"  [bold green][1][/bold green] ⚡ [bold green]ПАРАЛЛЕЛЬНО WP 1 + WP 2 (чередование каждые 5+ мин) [РЕКОМЕНДУЕТСЯ][/bold green]\n"
        f"  [bold cyan][2][/bold cyan] 🚀 Запустить рассылку только с [bold white]WP 1 (Бизнес #1)[/bold white]\n"
        f"  [bold cyan][3][/bold cyan] 🚀 Запустить рассылку только с [bold white]wp2 (Номер #2)[/bold white]\n"
        f"  [bold cyan][4][/bold cyan] 📥 Импортировать файл Google Maps (CSV/TXT)\n"
        f"  [bold cyan][5][/bold cyan] ➕ Добавить номер вручную в очередь\n"
        f"  [bold cyan][6][/bold cyan] 📊 Посмотреть детальную статистику базы\n"
        f"  [bold cyan][0][/bold cyan] ❌ Выход",
        title="[bold yellow]ГЛАВНОЕ МЕНЮ WHATSAPP OUTREACH[/bold yellow]",
        border_style="cyan"
    ))


async def run():
    while True:
        show_menu()
        choice = Prompt.ask("Ваш выбор", choices=["1", "2", "3", "4", "5", "6", "0"], default="1")

        if choice == "1":
            sender = WhatsAppSender()
            await sender.run_parallel_session()

        elif choice == "2":
            sender = WhatsAppSender()
            await sender.run_session(target_account_key="profile_1")

        elif choice == "3":
            sender = WhatsAppSender()
            await sender.run_session(target_account_key="profile_2")

        elif choice == "4":
            file_path = Prompt.ask("Введите путь к файлу выгрузки (например, leads.csv)").strip('"\'')
            niche = Prompt.ask("Ниша", choices=["recovery", "detailing"], default="recovery")
            importer = LeadImporter()
            importer.import_file(file_path, default_niche=niche)

        elif choice == "5":
            phone = Prompt.ask("Номер телефона (например, +971501234567)")
            name = Prompt.ask("Название компании/имя", default="Business")
            niche = Prompt.ask("Ниша", choices=["recovery", "detailing"], default="recovery")
            acc = Prompt.ask("В очередь какого аккаунта добавить?", choices=["1", "2"], default="1")
            target = "profile_1" if acc == "1" else "profile_2"

            db = WhatsAppDatabase()
            if db.add_lead(phone, name, niche, target):
                console.print(f"[bold green]✔ Номер {db.normalize_phone(phone)} успешно добавлен в очередь {target}![/bold green]")
            else:
                console.print("[bold yellow]Этот номер уже есть в базе или неверный формат.[/bold yellow]")

        elif choice == "6":
            db = WhatsAppDatabase()
            stats = db.get_stats()
            console.print("\n[bold]Статистика базы лидов:[/bold]")
            for k, v in stats["by_status"].items():
                console.print(f"  • {k}: [bold]{v}[/bold]")
            console.print(f"  Всего в базе: [bold cyan]{stats['total']}[/bold cyan]\n")

        elif choice == "0":
            console.print("[bold cyan]До свидания![/bold cyan]")
            break

        input("\nНажмите Enter, чтобы вернуться в меню...")


if __name__ == "__main__":
    asyncio.run(run())
