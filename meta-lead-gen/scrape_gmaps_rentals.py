"""
Парсер активных автопрокатов в Дубае из Google Maps с жесткими фильтрами качества.
Фильтры:
1. Только Car/Van Rental (без детейлинга, ремонта, недвижимости и моек).
2. Только живые компании (не Closed, с отзывами).
3. Только с прямым официальным сайтом (без агрегаторов).
4. Дедупликация по домену.
"""

import argparse
import csv
import io
import logging
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Безопасный вывод Unicode в Windows консоль
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gmaps_rental_scraper")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_CSV = DATA_DIR / "gmaps_rentals.csv"

DEFAULT_QUERIES = [
    "car rental dubai",
    "luxury car rental dubai",
    "rent a car al barsha dubai",
    "car rental deira dubai",
    "rent a car business bay dubai",
    "car rental dubai marina",
    "sports car rental dubai",
    "monthly car rental dubai",
]

STOP_WORDS = [
    "repair",
    "mechanic",
    "body shop",
    "garage",
    "real estate",
    "property",
    "hotel",
    "wash",
    "detailing",
    "tint",
    "towing",
    "recovery",
    "spare parts",
    "accessories",
]

EXCLUDED_DOMAINS = [
    "google.com",
    "google.ae",
    "whatsapp.com",
    "api.whatsapp.com",
    "wa.me",
    "facebook.com",
    "instagram.com",
    "booking.com",
    "kayak.com",
    "rentalcars.com",
    "oneclickdrive.com",
    "tripadvisor.com",
    "skyscanner.net",
    "expedia.com",
    "yandex.com",
    "apple.com",
    "waze.com",
    "yellowpages.ae",
]


def clean_domain(url: str) -> str:
    if not url:
        return ""
    u = url.strip().lower()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        parsed = urlparse(u)
        netloc = parsed.netloc or parsed.path
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.split("/")[0].split(":")[0]
    except Exception:
        return ""


def parse_phone(text: str) -> str:
    m = re.search(r"(\+971[\d\s\-]{7,13}|04[\d\s\-]{6,9}|05[\d\s\-]{7,9}|800[\d\s\-]{4,8})", text)
    return m.group(0).strip() if m else ""


def parse_rating_and_reviews(text: str):
    rating = ""
    reviews_count = 0
    # Ищем рейтинг вида 4.8
    m_rating = re.search(r"\b([3-5]\.[0-9])\b", text)
    if m_rating:
        rating = m_rating.group(1)
    # Ищем количество отзывов вида (125) или (1,240)
    m_reviews = re.search(r"\(([0-9,]+)\)", text)
    if m_reviews:
        try:
            reviews_count = int(m_reviews.group(1).replace(",", ""))
        except ValueError:
            reviews_count = 0
    return rating, reviews_count


def scrape_query(page, query: str, min_reviews: int = 10, max_cards_per_query: int = 60) -> list:
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}/?hl=en"
    logger.info(f"Загрузка поиска Google Maps: '{query}'...")

    try:
        page.goto(url, timeout=35000)
    except Exception as e:
        logger.error(f"Таймаут загрузки страницы: {e}")
        return []

    page.wait_for_timeout(3000)

    # Принятие соглашений Google при первом входе
    for btn_text in ["Accept all", "Agree", "I agree"]:
        try:
            loc = page.locator(f"button:has-text('{btn_text}')")
            if loc.count() > 0:
                loc.first.click()
                page.wait_for_timeout(1500)
                break
        except Exception:
            pass

    feed_loc = page.locator('div[role="feed"]')
    if feed_loc.count() == 0:
        logger.warning(f"Не найден контейнер выдачи для '{query}'")
        return []

    logger.info("Прокрутка ленты результатов...")
    collected_cards = []
    seen_card_hashes = set()
    scroll_attempts = 0
    last_count = 0
    consecutive_no_new = 0

    while scroll_attempts < 20 and len(seen_card_hashes) < max_cards_per_query:
        scroll_attempts += 1
        # Прокручиваем контейнер feed
        feed_loc.evaluate("el => el.scrollBy(0, 1200)")
        page.wait_for_timeout(random.uniform(1200, 1800))

        articles = page.locator('div[role="article"]').all()
        current_count = len(articles)

        if current_count == last_count:
            consecutive_no_new += 1
            if consecutive_no_new >= 3:
                logger.info("Конец ленты результатов достигнут.")
                break
        else:
            consecutive_no_new = 0
            last_count = current_count

    articles = page.locator('div[role="article"]').all()
    logger.info(f"Найдено карточек на экране: {len(articles)}")

    leads = []
    for art in articles:
        text = art.inner_text()
        text_lower = text.lower()

        # 1. Фильтр: исключаем закрытые филиалы
        if "permanently closed" in text_lower or "temporarily closed" in text_lower:
            continue

        # 2. Фильтр: стоп-слова
        if any(sw in text_lower for sw in STOP_WORDS):
            continue

        # 3. Фильтр: категория (должен быть прокат авто)
        is_rental = (
            "car rental" in text_lower
            or "van rental" in text_lower
            or "rent a car" in text_lower
            or "chauffeur" in text_lower
            or "luxury car" in text_lower
        )
        if not is_rental:
            continue

        # 4. Название компании (первая строка карточки)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            continue
        name = lines[0]

        # 5. Ищем сайт карточки
        website = ""
        for a in art.locator("a").all():
            href = a.get_attribute("href") or ""
            if href.startswith("http") and not href.startswith("https://www.google.com/maps"):
                dom = clean_domain(href)
                if dom and not any(bad in dom for bad in EXCLUDED_DOMAINS):
                    website = f"https://{dom}"
                    break

        if not website:
            continue

        # 6. Отзывы и активность
        rating, reviews_count = parse_rating_and_reviews(text)
        if reviews_count < min_reviews:
            continue

        phone = parse_phone(text)

        leads.append(
            {
                "business_name": name,
                "website": website,
                "website_found": clean_domain(website),
                "phone": phone,
                "rating": rating,
                "reviews_count": reviews_count,
                "search_query": query,
            }
        )

    logger.info(f"Из запроса '{query}' отобрано качественных прокатов с сайтами: {len(leads)}")
    return leads


def main():
    parser = argparse.ArgumentParser(description="Сборщик автопрокатов Дубая из Google Maps.")
    parser.add_argument("--min-reviews", type=int, default=10, help="Мин. количество отзывов (по умолч. 10)")
    parser.add_argument("--max-per-query", type=int, default=50, help="Макс. карточек на 1 поисковый запрос")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing_domains = set()
    all_leads = []

    # Если файл уже существует, читаем домены для дедупликации
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                dom = clean_domain(r.get("website", ""))
                if dom:
                    existing_domains.add(dom)
                    all_leads.append(r)
        logger.info(f"Загружено существующих компаний из CSV: {len(all_leads)}")

    logger.info("Запуск браузера Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()

        new_added = 0
        for q in DEFAULT_QUERIES:
            results = scrape_query(
                page,
                query=q,
                min_reviews=args.min_reviews,
                max_cards_per_query=args.max_per_query,
            )

            for item in results:
                dom = item["website_found"]
                if dom not in existing_domains:
                    existing_domains.add(dom)
                    all_leads.append(item)
                    new_added += 1

            # Пауза между запросами
            time.sleep(random.uniform(2.0, 4.0))

        browser.close()

    # Сохраняем обновленный CSV
    fieldnames = [
        "business_name",
        "website",
        "website_found",
        "phone",
        "rating",
        "reviews_count",
        "search_query",
    ]

    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_leads)

    logger.info(
        f"Сбор завершен! Добавлено новых: {new_added}. Всего уникальных автопрокатов в базе: {len(all_leads)}"
    )
    logger.info(f"Файл сохранен: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
