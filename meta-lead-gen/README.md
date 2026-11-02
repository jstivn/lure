# Meta Ad Library Lead Generation & Safe Outreach Tool

A modular, production-ready Python automation tool built with **Playwright (async)** connected via CDP to **Dolphin{anty}** anti-detect browser profiles.

The tool identifies US local service businesses running active ads on Meta that **do not have a website**, compares their Facebook vs. Instagram follower metrics, stores qualified leads locally with instant Excel synchronization, and performs human-simulated Step 1 outreach.

---

## Key Features

1. **Meta Ad Library Scraper & Qualifier (`scrape`)**:
   - Searches active US ads by keywords (e.g., *mobile detailing, window tint, house washing*).
   - Deduplicates advertisers against SQLite database in real-time so ads are never re-evaluated.
   - Deep website analysis: checks Page Intro/About external links and scans raw bio text with regex for custom domains.
   - Filters out non-website tools (link-in-bio like `linktr.ee`, `bio.site`, social profiles `instagram.com`, booking links `square.site`, messaging tools `wa.me`, `forms.gle`).
   - If qualified, compares Facebook vs. Instagram followers and sets `preferred_platform` to the higher-traction platform.

2. **Safe Outreach Sender (`send`)**:
   - Reads qualified leads (`status == 'READY_TO_SEND'`).
   - **Chat History Safety Guard:** Checks if there are already any messages in the DM (Facebook Messenger or Instagram Direct). If any prior messages exist, **Step 1 is skipped immediately** (marked as `EXISTING_CHAT`), avoiding spamming existing contacts.
   - **Daily Limit:** Stops automatically after `MAX_DAILY_MESSAGES` (default: 10/day).
   - **Human Warmup:** Lingers on profile for 5–12s and smoothly scrolls 1–2 posts.
   - **Realistic Typo & Backspace Simulation:** Natural typing rhythm (45–125ms), natural word bursts, cognitive pauses, and occasional realistic typos on adjacent QWERTY keys (4% chance) followed by a human reaction delay (180–420ms), `Backspace` press, and correction!
   - **Long Delays Between Leads:** 4 to 9 minutes between businesses with live terminal countdown.
   - **Spintax Link-Bait Hooks:** Casual American English, 1–2 short sentences, zero links, zero dashes.

3. **Dual Storage Engine (`./data/`)**:
   - `leads.db`: SQLite database for ACID transactions, indexed deduplication, and state tracking.
   - `leads.csv`: UTF-8-BOM encoded CSV automatically synchronized after every change, ready for viewing and editing in Microsoft Excel on Windows or Mac.

---

## Project Structure

```
meta-lead-gen/
├── config.json              # Main configuration (queries, delays, limits, niche)
├── .env.example             # Environment variable template
├── requirements.txt         # Python package dependencies
├── README.md                # Documentation and setup instructions
├── main.py                  # CLI entry point (scrape, send, stats, export)
├── core/
│   ├── __init__.py
│   ├── dolphin.py           # Dolphin{anty} Local API manager & CDP connector
│   ├── database.py          # SQLite engine with auto-synced CSV export
│   ├── spintax.py           # Spintax engine & randomized Step 1 hook generator
│   └── utils.py             # Human delays, typing simulation, typos, smooth scroll
├── modules/
│   ├── __init__.py
│   ├── scraper.py           # Module 1: Meta Ad Library scraper & qualifier
│   └── sender.py            # Module 2: Safe outreach sender with DM guard
└── data/
    ├── .gitkeep
    ├── leads.db             # Auto-generated SQLite database
    └── leads.csv            # Auto-generated Excel-compatible CSV
```

---

## 🚀 Быстрый запуск в 1 клик на Windows (Без консоли)

Вам **не нужно** вводить никаких команд в терминале. В папке [`apps/meta-lead-gen`](file:///e:/billion/apps/meta-lead-gen) подготовлены готовые файлы запуска:

1. **`START.bat` (или `СТАРТ.bat`)** — **Главное интерактивное меню**.
   - При первом запуске **сам автоматически** проверит наличие Python, создаст виртуальное окружение и установит все библиотеки (`playwright`, `chromium`, `rich`, `aiohttp`).
   - Если ID профиля Dolphin еще не введен, предложит вставить его прямо в окне и сохранит в `config.json`.
   - Позволяет цифрами от 1 до 6 запускать поиск, рассылку, просмотр статистики или открывать Excel.

2. **Отдельные файлы запуска (можно вынести ярлыки на Рабочий стол):**
   - **`1_SCRAPE_LEADS.bat`** (или `1_ПОИСК_ЛИДОВ.bat`) — двойной клик: запускает поиск рекламодателей без сайтов в Meta Ad Library.
   - **`2_SEND_MESSAGES.bat`** (или `2_ОТПРАВКА_СООБЩЕНИЙ.bat`) — двойной клик: запускает безопасную отправку сообщений (с проверкой истории ДМ и опечатками).
   - **`3_OPEN_EXCEL.bat`** (или `3_ОТКРЫТЬ_EXCEL.bat`) — двойной клик: мгновенно открывает таблицу лидов `leads.csv` в Microsoft Excel.
   - **`4_VIEW_STATS.bat`** (или `4_СТАТИСТИКА.bat`) — двойной клик: показывает количество лидов по статусам и сколько отправлено сегодня.

---

## Windows Setup Instructions (PowerShell / CMD)

### 1. Prerequisites
Ensure Python 3.10+ is installed on Windows:
```powershell
# Check Python version
python --version

# If python is not recognized, install Python 3.10+ from python.org (ensure "Add Python to PATH" is checked)
```

### 2. Create and Activate Virtual Environment
Open PowerShell inside the `meta-lead-gen` folder of the repository:
```powershell
# Navigate to project folder (adjust the path to where you cloned the repo)
cd path\to\lure\meta-lead-gen

# Create virtual environment
python -m venv venv

# If PowerShell blocks script execution, run this once:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Activate virtual environment
.\venv\Scripts\Activate.ps1
```

*(If using Command Prompt `cmd.exe`: `venv\Scripts\activate.bat`)*

### 3. Install Dependencies & Playwright
```powershell
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

---

## Dolphin{anty} Configuration (Windows)

1. **Launch Dolphin{anty} Desktop App** on your PC.
2. **Enable Local API**:
   - Open Dolphin Settings (`⚙ Settings` at the bottom left).
   - Go to the **API** tab.
   - Ensure the Local API is enabled and listening on port `3001` (`http://localhost:3001`).
3. **Find your Browser Profile ID**:
   - In Dolphin's profile list, find the profile you want to use (must be logged into your Facebook and Instagram accounts).
   - Right-click the profile name or click the three dots `...` and select **Copy ID**.
4. **Update `config.json`**:
   Open `config.json` and paste your profile ID:
   ```json
   {
     "dolphin": {
       "api_base": "http://localhost:3001/v1.0",
       "profile_id": "12345678",
       "auto_stop_profile": false
     },
     "limits": {
       "max_daily_messages": 10
     }
   }
   ```
   *(Or set `DOLPHIN_PROFILE_ID=12345678` in `.env`).*

---

## Usage Guide (Windows CLI)

### 1. Run Scraper & Qualifier (`scrape`)
Discovers active advertisers, checks for websites, extracts follower metrics, and saves qualified leads:

```powershell
python main.py scrape
```

Custom search query or niche via CLI flags:
```powershell
# Scrape a specific query
python main.py scrape --query "window tint"

# Run for auto shop niche
python main.py scrape --niche shop
```

### 2. Run Safe Outreach Sender (`send`)
Sends Step 1 message hooks to leads with status `READY_TO_SEND`:

```powershell
python main.py send
```

**Outreach Behavior:**
- Checks daily message limit (default 10/day).
- Opens the business's profile on Facebook or Instagram.
- Runs human warmup (5–12s dwell time, smooth scroll 1–2 posts).
- Opens the message dialog and **inspects existing chat history**.
- **If existing messages are found in DM**: aborts Step 1, marks lead as `EXISTING_CHAT`, and moves to the next lead without consuming your daily quota or wasting 9 minutes.
- **If chat is fresh**: types the hook character-by-character with realistic key latency and natural typos on adjacent keys followed by backspacing and correction.
- Dispatches message, marks lead as `STEP_1_SENT`, and starts a live terminal countdown for 4 to 9 minutes before the next lead.

### 3. View Database Statistics (`stats`)
Displays lead counts by status, total leads, and messages sent today:

```powershell
python main.py stats
```

### 4. Force CSV Export (`export`)
Refreshes `./data/leads.csv` directly from SQLite:

```powershell
python main.py export
```

---

## Lead Status Lifecycle

| Status | Description |
|---|---|
| `READY_TO_SEND` | Qualified lead (no website found, only social/bio tools). Ready for outreach. |
| `STEP_1_SENT` | Step 1 link-bait hook sent successfully with timestamp and message text recorded. |
| `EXISTING_CHAT` | Existing messages detected in DM; Step 1 skipped to prevent duplicate messaging. |
| `DISQUALIFIED_HAS_WEBSITE` | Real custom domain detected in FB/IG links or bio. Skipped. |
| `FAILED_SEND` | Message button or input was inaccessible on FB/IG. |

---

## Ultra-Realistic Typing & Anti-Ban Architecture

1. **Typo Simulation (`enable_typos=True`):**
   - 4% probability on letters to hit an adjacent key on a standard US QWERTY keyboard (`a` -> `q`/`w`/`s`/`z`).
   - Human reaction latency (180–420ms) simulating visual recognition of the typo.
   - `Backspace` keypress event dispatched to delete the error.
   - Short pause (90–240ms) before typing the correct character.
   - Keystroke event intervals vary randomly between 45ms and 125ms with pauses on spaces and punctuation.
   - Meta's keystroke telemetry tracks `Backspace` events and typing variance; this ensures the typing profile is indistinguishable from a real human.

2. **Daily Pacing:** Set to 10 messages/day default for maximum safety on newer or valuable accounts.
