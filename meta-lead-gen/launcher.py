import json
import os
from pathlib import Path
import subprocess
import sys

# Windows UTF-8 console output enforcement
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core.browser import BrowserSession
from core.database import DatabaseManager

console = Console(force_terminal=True, legacy_windows=False)


def get_python_exe() -> str:
    venv_py = BASE_DIR / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def load_config() -> dict:
    cfg_file = BASE_DIR / "config.json"
    if cfg_file.exists():
        with open(cfg_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    cfg_file = BASE_DIR / "config.json"
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def run_command(args: list):
    py_exe = get_python_exe()
    cmd = [py_exe, str(BASE_DIR / "main.py")] + args
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        console.print("\n[yellow]Действие прервано пользователем (Ctrl+C).[/yellow]")


def display_dashboard():
    os.system("cls" if os.name == "nt" else "clear")
    cfg = load_config()
    db = DatabaseManager(
        db_path=str(BASE_DIR / "data" / "leads.db"),
        csv_path=str(BASE_DIR / "data" / "leads.csv")
    )
    stats = db.get_stats()
    sent_fb = db.get_sent_today_count(platform="FB")
    sent_ig = db.get_sent_today_count(platform="IG")
    limit_fb = cfg.get("limits", {}).get("max_daily_messages_fb", 10)
    limit_ig = cfg.get("limits", {}).get("max_daily_messages_ig", 10)

    delays = cfg.get("delays", {})
    min_pause = delays.get("min_lead_delay_sec", 300) // 60
    max_pause = delays.get("max_lead_delay_sec", 600) // 60

    has_session = BrowserSession.has_saved_session("fb")
    session_status = "[bold green]✔ Настроен (Сессия найдена)[/bold green]" if has_session else "[bold yellow]⚠ Требуется вход (Пункт 5)[/bold yellow]"

    niche = cfg.get("niche", "epoxy")
    outreach_mode_str = "Полуавтомат (Copilot - подтверждение Enter)" if cfg.get("outreach_mode", "semi_auto") == "semi_auto" else "Полный автомат"
    ready_count = stats.get("READY_TO_SEND", 0)
    total_leads = stats.get("TOTAL", 0)

    console.print(Panel.fit(
        f"[bold cyan]META AD LIBRARY LEAD GENERATOR & OUTREACH TOOL v2.0[/bold cyan]\n"
        f"[dim]Поиск локального бизнеса США без сайтов • Сравнение FB vs IG • Безопасный Step 1[/dim]\n\n"
        f"  🌐 Браузер:             [bold green]Официальный Google Chrome (CDP Port 9333)[/bold green]\n"
        f"  🔑 Статус аккаунтов:    {session_status}\n"
        f"  🎮 Режим рассылки:      [bold yellow]{outreach_mode_str}[/bold yellow]\n"
        f"  📘 Лимит Facebook:      [dim]{sent_fb} / {limit_fb} сегодня[/dim]\n"
        f"  📸 Лимит Instagram:     [dim]{sent_ig} / {limit_ig} сегодня[/dim]\n"
        f"  🎯 Ниша кампании:       [bold magenta]{niche}[/bold magenta]\n"
        f"  ⏱️ Пауза между смс:     [bold blue]от {min_pause} до {max_pause} минут[/bold blue]\n"
        f"  📥 База лидов:          [bold green]{total_leads}[/bold green] (Готовы к отправке: [bold green]{ready_count}[/bold green])",
        title="[bold white]Панель управления[/bold white]",
        border_style="cyan"
    ))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold yellow")
    table.add_column("Description", style="white")

    table.add_row("[1]", "🔍 Начать поиск лидов в Meta Ad Library (Scraper)")
    table.add_row("[2]", "🚀 Запустить безопасную рассылку Step 1 (Facebook + Instagram)")
    table.add_row("[3]", "📊 Посмотреть детальную статистику базы")
    table.add_row("[4]", "📑 Открыть таблицу leads.csv в Microsoft Excel")
    table.add_row("[5]", "👤 Войти в Facebook и Instagram (откроется чистый Chrome)")
    table.add_row("[6]", "⚙️  Открыть config.json в Блокноте")
    table.add_row("[7]", "🧹 Очистить базу контактов (удалить всех лидов и начать с нуля)")
    table.add_row("[0]", "❌ Выход")

    console.print(table)
    console.print("[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]")


def main_loop():
    while True:
        display_dashboard()
        try:
            choice = input("Выберите пункт меню (0-7) и нажмите Enter: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\nДо встречи!")
            break

        if choice == "1":
            os.system("cls" if os.name == "nt" else "clear")
            console.print("[bold cyan]Запуск поиска и квалификации рекламодателей...[/bold cyan]\n")
            run_command(["scrape"])
            input("\nНажмите Enter для возврата в меню...")

        elif choice == "2":
            os.system("cls" if os.name == "nt" else "clear")
            console.print(Panel.fit(
                "[bold cyan]ВЫБОР КАНАЛА РАССЫЛКИ (STEP 1)[/bold cyan]\n\n"
                "  [1] 📸 Только INSTAGRAM Direct\n"
                "  [2] 📘 Только FACEBOOK Messenger\n"
                "  [3] 🚀 В обе сети по очереди (сначала FB, потом IG)\n"
                "  [0] ↩ Назад в главное меню",
                title="[bold white]Параметры отправки[/bold white]",
                border_style="cyan"
            ))
            sub = input("Выберите вариант (0-3): ").strip()
            if sub == "1":
                os.system("cls" if os.name == "nt" else "clear")
                console.print("[bold yellow]Запуск рассылки ТОЛЬКО в Instagram Direct...[/bold yellow]\n")
                run_command(["send", "ig"])
                input("\nНажмите Enter для возврата в меню...")
            elif sub == "2":
                os.system("cls" if os.name == "nt" else "clear")
                console.print("[bold blue]Запуск рассылки ТОЛЬКО в Facebook Messenger...[/bold blue]\n")
                run_command(["send", "fb"])
                input("\nНажмите Enter для возврата в меню...")
            elif sub == "3":
                os.system("cls" if os.name == "nt" else "clear")
                console.print("[bold green]Запуск рассылки в обе сети (FB + IG)...[/bold green]\n")
                run_command(["send"])
                input("\nНажмите Enter для возврата в меню...")

        elif choice == "3":
            os.system("cls" if os.name == "nt" else "clear")
            run_command(["stats"])
            input("\nНажмите Enter для возврата в меню...")

        elif choice == "4":
            csv_path = BASE_DIR / "data" / "leads.csv"
            if not csv_path.exists():
                db = DatabaseManager(
                    db_path=str(BASE_DIR / "data" / "leads.db"),
                    csv_path=str(BASE_DIR / "data" / "leads.csv")
                )
                db.export_to_csv()

            console.print(f"[green]Открываю таблицу: {csv_path}...[/green]")
            if sys.platform == "win32":
                os.startfile(str(csv_path))
            else:
                subprocess.run(["open", str(csv_path)])

        elif choice == "5":
            bat_path = BASE_DIR / "OPEN_BROWSER_LOGIN.bat"
            console.print("\n[bold cyan]Открываю обычный Google Chrome для входа в аккаунты...[/bold cyan]")
            if sys.platform == "win32":
                subprocess.run([str(bat_path)], shell=True)
            else:
                run_command(["login-fb"])
            input("\nНажмите Enter для возврата в меню...")

        elif choice == "6":
            cfg_path = BASE_DIR / "config.json"
            console.print(f"[green]Открываю {cfg_path} в Блокноте...[/green]")
            if sys.platform == "win32":
                os.system(f'notepad.exe "{cfg_path}"')
            else:
                os.system(f'open -e "{cfg_path}"')

        elif choice == "7":
            os.system("cls" if os.name == "nt" else "clear")
            console.print("[bold red]ВНИМАНИЕ: Очистка базы данных контактов[/bold red]")
            console.print("Это действие полностью удалит всех сохранённых лидов из базы leads.db и таблицы leads.csv.\n")
            confirm = input("Вы действительно хотите удалить ВСЕХ лидов? (д/н): ").strip().lower()
            if confirm in ["д", "да", "y", "yes"]:
                db = DatabaseManager(
                    db_path=str(BASE_DIR / "data" / "leads.db"),
                    csv_path=str(BASE_DIR / "data" / "leads.csv")
                )
                db.clear_all_leads()
                console.print("\n[bold green]✔ База данных и файл leads.csv успешно очищены![/bold green]")
            else:
                console.print("\n[yellow]Очистка отменена.[/yellow]")
            input("\nНажмите Enter для возврата в меню...")

        elif choice == "0":
            console.print("\n[dim]До встречи![/dim]")
            break


if __name__ == "__main__":
    main_loop()
