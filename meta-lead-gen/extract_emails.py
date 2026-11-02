"""
Скрипт извлечения контактных email-адресов с сайтов.
Поддерживает CSV с колонкой 'website' или 'website_found'.
"""

import argparse
import csv
import io
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

# Безопасный вывод Unicode в Windows консоль
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup
import urllib3

# Отключаем предупреждения о невалидных SSL-сертификатах при fallback-запросах
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("email_extractor")

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
)

# Расширения файлов и технический мусор, похожий на email
INVALID_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".mov",
)

# Шаблонные и сервисные домены/ключи, которые не являются реальными контактами
JUNK_PATTERNS = [
    "sentry.io",
    "example.com",
    "wixpress.com",
    "domain.com",
    "wordpress.org",
    "cloudflare.com",
    "schema.org",
    "w3.org",
    "yourname",
    "test@test",
    "user@domain",
    "email@email",
    "info@domain",
    "contact@domain",
    "placeholder",
    "dummy",
]

COMMON_CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contacts",
    "/about",
    "/about-us",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
]


def clean_domain(url_or_domain: str) -> str:
    """Извлекает чистый домен из URL или строки."""
    url = url_or_domain.strip().lower()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.split("/")[0].split(":")[0]


def is_valid_email(email: str) -> bool:
    """Проверка email на технический мусор."""
    email_clean = email.strip().lower()
    if len(email_clean) > 80 or len(email_clean) < 6:
        return False
    if any(email_clean.endswith(ext) for ext in INVALID_EXTENSIONS):
        return False
    if any(pattern in email_clean for pattern in JUNK_PATTERNS):
        return False
    # Проверка формата
    parts = email_clean.split("@")
    if len(parts) != 2:
        return False
    user, domain = parts
    if "." not in domain or len(user) == 0:
        return False
    return True


def fetch_page(
    session: requests.Session, url: str, timeout: int = 10
) -> Optional[str]:
    """Безопасно запрашивает HTML страницы с обработкой ошибок и SSL."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    try:
        response = session.get(
            url, headers=headers, timeout=timeout, allow_redirects=True
        )
        if response.status_code == 200:
            return response.text
        return None
    except requests.exceptions.SSLError:
        try:
            # Fallback на небезопасный SSL, если сертификат просрочен/самоподписан
            response = session.get(
                url,
                headers=headers,
                timeout=timeout,
                verify=False,
                allow_redirects=True,
            )
            if response.status_code == 200:
                return response.text
        except Exception:
            return None
    except Exception:
        return None
    return None


def extract_emails_from_html(
    html: str,
) -> Tuple[Set[str], Set[str]]:
    """
    Возвращает (emails_from_mailto, emails_from_text).
    """
    mailto_emails: Set[str] = set()
    text_emails: Set[str] = set()

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        soup = None

    if soup:
        # 1. Ссылки mailto:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                raw_mail = href[7:].split("?")[0].strip()
                # Если несколько адресов через запятую
                for m in raw_mail.split(","):
                    m_clean = m.strip().lower()
                    if is_valid_email(m_clean):
                        mailto_emails.add(m_clean)

        # Удаляем теги скриптов и стилей перед текстовым поиском
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        page_text = soup.get_text(separator=" ")
    else:
        page_text = html

    # 2. Поиск регулярным выражением
    for match in EMAIL_REGEX.findall(page_text):
        m_clean = match.strip().lower()
        if is_valid_email(m_clean):
            text_emails.add(m_clean)

    return mailto_emails, text_emails


def discover_contact_links(base_url: str, html: str) -> List[str]:
    """Ищет на главной ссылки на страницы контактов и о компании."""
    discovered: Set[str] = set()
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    parsed_base = urlparse(base_url)
    base_netloc = parsed_base.netloc.lower()

    keywords = ["contact", "about", "reach", "support", "контакт", "связ"]

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text().strip().lower()
        href_lower = href.lower()

        is_candidate = any(kw in href_lower for kw in keywords) or any(
            kw in text for kw in keywords
        )
        if not is_candidate:
            continue

        full_url = urljoin(base_url, href)
        parsed_target = urlparse(full_url)

        # Только внутри того же домена и протокол http/https
        if parsed_target.scheme in (
            "http",
            "https",
        ) and parsed_target.netloc.lower().endswith(base_netloc):
            clean_target = (
                f"{parsed_target.scheme}://{parsed_target.netloc}{parsed_target.path}"
            )
            if clean_target != base_url and clean_target != base_url + "/":
                discovered.add(clean_target)

    return list(discovered)[:4]  # Ограничиваем до 4 самых релевантных ссылок


def score_email(email: str, site_domain: str, is_mailto: bool) -> int:
    """Оценивает релевантность email для сортировки в приоритет."""
    score = 0
    email_domain = email.split("@")[-1].lower()

    # 1. Приоритет: домен email совпадает с доменом сайта
    if site_domain in email_domain or email_domain in site_domain:
        score += 50

    # 2. Почтовая ссылка в mailto обычно более актуальна, чем случайное упоминание
    if is_mailto:
        score += 20

    # 3. Префиксы корпоративных ящиков
    user_part = email.split("@")[0].lower()
    high_value_users = (
        "info",
        "contact",
        "support",
        "booking",
        "rent",
        "sales",
        "reservations",
    )
    if any(user_part.startswith(prefix) for prefix in high_value_users):
        score += 15

    # 4. Общие бесплатные почты получают меньший приоритет
    common_free_domains = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com")
    if email_domain in common_free_domains:
        score += 5

    return score


def process_website(
    session: requests.Session, website: str
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Обходит сайт и возвращает:
    (лучший_email, страница_где_найден, список_всех_найденных_email)
    """
    clean_dom = clean_domain(website)
    if not clean_dom:
        return None, None, []

    base_url = f"https://{clean_dom}"
    candidate_urls = [base_url]

    # Результаты: email -> (score, url_where_found)
    found_emails: dict[str, Tuple[int, str]] = {}

    visited: Set[str] = set()

    # 1. Загрузка главной страницы
    home_html = fetch_page(session, base_url)
    visited.add(base_url)

    if not home_html:
        # Попробуем через http:// если https не ответил
        http_base = f"http://{clean_dom}"
        home_html = fetch_page(session, http_base)
        visited.add(http_base)
        if home_html:
            base_url = http_base

    if home_html:
        mailto_list, text_list = extract_emails_from_html(home_html)
        for em in mailto_list:
            sc = score_email(em, clean_dom, is_mailto=True)
            found_emails[em] = max(found_emails.get(em, (0, ""))[0], sc), base_url
        for em in text_list:
            if em not in found_emails:
                sc = score_email(em, clean_dom, is_mailto=False)
                found_emails[em] = sc, base_url

        # Ищем ссылки на страницы контактов из меню/футера
        extra_pages = discover_contact_links(base_url, home_html)
        candidate_urls.extend(extra_pages)

    # 2. Если прямых ссылок не найдено, добавляем стандартные эвристические пути
    if len(candidate_urls) == 1:
        for path in COMMON_CONTACT_PATHS:
            candidate_urls.append(urljoin(base_url, path))

    # 3. Обход контактных страниц (если на главной еще не нашли доменный email с высоким скором)
    for target_url in candidate_urls:
        if target_url in visited:
            continue
        visited.add(target_url)

        # Пауза между запросами для вежливости
        time.sleep(random.uniform(1.0, 2.0))

        html = fetch_page(session, target_url)
        if not html:
            continue

        mailto_list, text_list = extract_emails_from_html(html)
        for em in mailto_list:
            sc = score_email(em, clean_dom, is_mailto=True)
            if em not in found_emails or sc > found_emails[em][0]:
                found_emails[em] = sc, target_url
        for em in text_list:
            if em not in found_emails:
                sc = score_email(em, clean_dom, is_mailto=False)
                found_emails[em] = sc, target_url

        # Если уже найден доменный email, дальше можно не спамить сайт запросами
        if any(v[0] >= 50 for v in found_emails.values()):
            break

    if not found_emails:
        return None, None, []

    # Сортируем найденные адреса по оценке (лучший первый)
    sorted_emails = sorted(
        found_emails.items(), key=lambda item: item[1][0], reverse=True
    )
    best_email = sorted_emails[0][0]
    best_source_url = sorted_emails[0][1][1]
    all_found = [e[0] for e in sorted_emails]

    return best_email, best_source_url, all_found


def main():
    parser = argparse.ArgumentParser(
        description="Парсер email-адресов с сайтов из CSV."
    )
    parser.add_argument(
        "--input",
        "-i",
        default=str(Path(__file__).resolve().parent / "data" / "leads.csv"),
        help="Путь к исходному CSV файлу",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(Path(__file__).resolve().parent / "data" / "leads_emails.csv"),
        help="Путь для сохранения результатов",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        logger.error(f"Файл не найден: {args.input}")
        sys.exit(1)

    # Чтение входного файла
    rows_to_process = []
    with open(args.input, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        # Ищем подходящую колонку с сайтом
        website_col = None
        for candidate in ["website", "website_found", "site", "url", "domain"]:
            if candidate in fieldnames:
                website_col = candidate
                break

        if not website_col:
            logger.error(
                f"Не найдена колонка с сайтом. Доступные колонки: {fieldnames}"
            )
            sys.exit(1)

        logger.info(f"Используем колонку сайта: '{website_col}'")

        for row in reader:
            raw_site = (row.get(website_col) or "").strip()
            if raw_site:
                rows_to_process.append(
                    {
                        "business_name": row.get("business_name", ""),
                        "website": raw_site,
                        "original_row": row,
                    }
                )

    total_leads = len(rows_to_process)
    logger.info(f"Найдено сайтов для проверки: {total_leads}")

    if total_leads == 0:
        logger.warning("Нет записей с заполненными сайтами для обработки.")
        return

    # Подготовка результирующего CSV
    output_fields = [
        "business_name",
        "website",
        "email",
        "found_on_page",
        "all_emails",
    ]
    file_exists = os.path.exists(args.output)

    with open(
        args.output,
        mode="w",
        encoding="utf-8-sig",
        newline="",
    ) as out_f:
        writer = csv.DictWriter(out_f, fieldnames=output_fields)
        writer.writeheader()

        session = requests.Session()

        found_count = 0
        for idx, item in enumerate(rows_to_process, start=1):
            name = item["business_name"]
            site = item["website"]

            logger.info(
                f"[{idx}/{total_leads}] Обработка: {name or site} ({site})..."
            )

            try:
                best_email, source_page, all_emails = process_website(
                    session, site
                )
            except Exception as e:
                logger.error(f"Ошибка при обработке {site}: {e}")
                best_email, source_page, all_emails = None, None, []

            if best_email:
                found_count += 1
                logger.info(f"  -> Найдено: {best_email} (на {source_page})")
            else:
                logger.info("  -> Email не найден")

            writer.writerow(
                {
                    "business_name": name,
                    "website": site,
                    "email": best_email or "",
                    "found_on_page": source_page or "",
                    "all_emails": "; ".join(all_emails),
                }
            )
            out_f.flush()

            # Задержка между сайтами
            time.sleep(random.uniform(1.0, 2.0))

    logger.info(
        f"Готово. Проверено: {total_leads}, найдено email: {found_count}. Результат сохранен в {args.output}"
    )


if __name__ == "__main__":
    main()
