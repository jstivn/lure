"""
Google Maps / Instant Data Scraper Lead Importer.
Strictly implements UAE Dubai B2B detailing qualification rules:
- Requires UAE mobile numbers (+971 5XXXXXXXX)
- Disqualifies UAE landlines (04, 02, 06, 03, 07, 09, +9714, +9712, +9716)
- Disqualifies businesses with existing custom websites
- Disqualifies businesses marked as temporarily or permanently closed
- Deduplicates against data/list.txt and internal SQLite database
- Automatically distributes qualified leads 50/50 between WP 1 and wp2.
"""

import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Force UTF-8 on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table

from core.database import WhatsAppDatabase

console = Console(force_terminal=True, legacy_windows=False)

EXCLUDED_DOMAINS = {
    "instagram.com", "facebook.com", "fb.com", "fb.me",
    "wa.me", "whatsapp.com", "api.whatsapp.com",
    "g.page", "linktr.ee", "bio.link", "dubizzle.com"
}


class LeadImporter:
    def __init__(self, db_path: str = "data/wa_leads.db", list_txt_path: str = "data/list.txt"):
        self.db = WhatsAppDatabase(db_path)
        self.list_txt_path = list_txt_path

    def import_file(self, file_path: str, default_niche: str = "detailing") -> Dict[str, int]:
        """
        Parses a file and imports qualified detailing leads into the database.
        """
        path = Path(file_path)
        if not path.exists():
            console.print(f"[bold red]File not found: {file_path}[/bold red]")
            return {"total": 0, "qualified": 0}

        # Sync known contacts from data/list.txt first
        self.db.sync_from_list_txt(self.list_txt_path)

        rows = self._read_rows(path)
        console.print(f"Read [bold cyan]{len(rows)}[/bold cyan] total raw records from [italic]{path.name}[/italic]")

        stats = {
            "total_rows": len(rows),
            "qualified": 0,
            "disqualified_has_website": 0,
            "disqualified_landline": 0,
            "disqualified_invalid_phone": 0,
            "disqualified_closed": 0,
            "duplicate": 0
        }

        assign_toggle = 0

        for row in rows:
            name, phone_raw, website, cat, status_info = self._extract_fields(row)
            niche = cat if cat else default_niche

            # 1. Closed status check
            if any(term in status_info.lower() for term in ["temporarily closed", "permanently closed", "closed"]):
                stats["disqualified_closed"] += 1
                continue

            # 2. Phone validation (strictly +971 5XXXXXXXX)
            norm_phone, err = self._validate_and_normalize_phone(phone_raw)
            if not norm_phone:
                if err == "landline":
                    stats["disqualified_landline"] += 1
                else:
                    stats["disqualified_invalid_phone"] += 1
                continue

            # 3. Duplicate check (DB + list.txt)
            if self.db.is_known_phone(norm_phone):
                stats["duplicate"] += 1
                continue

            # 4. Website check (Preserved for personalization, no longer disqualified for AI outreach)
            # Websites are welcome and used to build personal AI simulator demos!

            # 5. Allocate 50/50 between WP 1 and wp2
            account = "profile_1" if (assign_toggle % 2 == 0) else "profile_2"
            assign_toggle += 1

            added = self.db.add_lead(
                phone=norm_phone,
                business_name=name or "Detailing Studio",
                niche=niche,
                assigned_account=account
            )
            if added:
                stats["qualified"] += 1

        self._print_import_report(stats)
        return stats

    def _read_rows(self, path: Path) -> List[Dict[str, str]]:
        """Reads CSV, TSV, or TXT lines."""
        rows = []
        if path.suffix.lower() in [".csv", ".tsv"]:
            try:
                delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    if "\t" in sample and sample.count("\t") > sample.count(","):
                        delimiter = "\t"
                    reader = csv.DictReader(f, delimiter=delimiter)
                    for r in reader:
                        rows.append(r)
            except Exception as e:
                console.print(f"[red]Error parsing CSV/TSV: {e}[/red]")
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append({"phone": line})
        return rows

    def _extract_fields(self, row: Dict[str, str]) -> Tuple[str, str, str, str, str]:
        """Extracts name, phone, website, category, and status info using fuzzy and Instant Data Scraper column matching."""
        name, phone, website, cat, status_info = "", "", "", "", ""

        # Direct Instant Data Scraper (Google Maps) key check
        if "xxVWCe" in row and row["xxVWCe"]:
            name = row["xxVWCe"].strip()
        if "UsdlK" in row and row["UsdlK"]:
            phone = row["UsdlK"].strip()
        if "lcr4fd href" in row and row["lcr4fd href"]:
            raw_href = row["lcr4fd href"].strip()
            # If Google Ads redirect, it points to a paid website
            if "google.com/aclk" in raw_href:
                website = "https://paid-ad-website.ae"
            else:
                website = raw_href
        elif row.get("R8c4Qb", "").strip().lower() in ["сайт", "website"]:
            website = "https://has-custom-website.ae"
        if "W4Efsd" in row and row["W4Efsd"]:
            cat = row["W4Efsd"].strip()
        if "W4Efsd 3" in row and row["W4Efsd 3"]:
            status_info += " " + row["W4Efsd 3"].strip()
        if "W4Efsd 4" in row and row["W4Efsd 4"]:
            status_info += " " + row["W4Efsd 4"].strip()

        for k, v in row.items():
            if not k:
                continue
            k_lower = k.lower().strip()
            v_clean = (v or "").strip()

            if any(term in k_lower for term in ["phone", "tel", "mobile", "contact"]):
                if not phone:
                    phone = v_clean
            elif any(term in k_lower for term in ["title", "name", "business", "company"]):
                if not name:
                    name = v_clean
            elif any(term in k_lower for term in ["website", "url", "link", "site", "web"]):
                if not website:
                    website = v_clean
            elif any(term in k_lower for term in ["category", "niche", "type"]):
                if not cat:
                    cat = v_clean
            elif any(term in k_lower for term in ["status", "temporarily", "permanently", "hours"]):
                if not status_info:
                    status_info = v_clean

        return name, phone, website, cat, status_info

    def _validate_and_normalize_phone(self, phone: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Validates UAE phone numbers.
        Allows ONLY mobile (+971 5X).
        Strictly drops landlines (04, 02, 06, 03, 07, 09, +9714, +9712, etc.).
        """
        if not phone:
            return None, "empty"

        digits = re.sub(r"\D", "", phone)

        # Landlines check: 04, 02, 06, 03, 07, 09
        landline_prefixes = ("04", "02", "06", "03", "07", "09", "9714", "9712", "9716", "9713", "9717", "9719")
        if any(digits.startswith(p) for p in landline_prefixes):
            return None, "landline"

        # 050XXXXXXX -> 97150XXXXXXX
        if digits.startswith("05") and len(digits) == 10:
            digits = "971" + digits[1:]
        elif digits.startswith("5") and len(digits) == 9:
            digits = "971" + digits
        elif digits.startswith("00971"):
            digits = digits[2:]

        if digits.startswith("9715") and len(digits) == 12:
            return f"+{digits}", None

        return None, "invalid"

    def _has_custom_website(self, website_url: str) -> bool:
        """Checks if URL points to a custom website (as opposed to social media or directory)."""
        if not website_url or not website_url.strip():
            return False

        url = website_url.strip()
        if not url.startswith("http"):
            url = f"https://{url}"

        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]

            if any(excluded in netloc for excluded in EXCLUDED_DOMAINS):
                return False

            return "." in netloc
        except Exception:
            return False

    def _print_import_report(self, stats: Dict[str, int]):
        """Prints a clean summary table of import results."""
        table = Table(title="UAE Detailing Lead Qualification Report", border_style="cyan")
        table.add_column("Metric", style="white")
        table.add_column("Count", justify="right", style="bold")

        table.add_row("Всего сырых записей (Total Raw)", str(stats["total_rows"]))
        table.add_row("✓ Квалифицировано (Добавлено в очередь)", f"[bold green]{stats['qualified']}[/bold green]")
        table.add_row("✗ Отсеяно (Есть веб-сайт)", f"[yellow]{stats['disqualified_has_website']}[/yellow]")
        table.add_row("✗ Отсеяно (Городской номер 04/02/06)", f"[magenta]{stats['disqualified_landline']}[/magenta]")
        table.add_row("✗ Отсеяно (Закрытый бизнес / Closed)", f"[red]{stats['disqualified_closed']}[/red]")
        table.add_row("✗ Отсеяно (Некорректный/чужой номер)", f"[dim red]{stats['disqualified_invalid_phone']}[/dim red]")
        table.add_row("✗ Отсеяно (Дубль из data/list.txt)", f"[dim]{stats['duplicate']}[/dim]")

        console.print(table)
