import asyncio
import hashlib
import logging
import random
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.async_api import Page, async_playwright
from rich.console import Console

from core.catalog import BASE_STOP_WORDS, NAME_STOP_WORDS, find_stop_word, has_signal
from core.database import DatabaseManager
from core.email_queue import find_emails, queue_email_lead
from core.wa_queue import normalize_phone, queue_whatsapp_lead

logger = logging.getLogger("GMapsModule")
console = Console()

# Порядок выбора канала для лида из Google Maps
CHANNEL_ORDER = ("WA", "IG", "FB")

SOCIAL_SKIP = {"sharer", "share", "p", "reel", "reels", "explore", "stories", "tr", "plugins", "dialog", "login", "policies", "help"}


class GMapsScraper:
    """
    Сбор лидов из Google Maps: запрос -> лента карточек -> карточка места.
    Подходят компании без сайта в карточке. Сохраняем телефон, Instagram и Facebook, если они есть.
    Канал: любой телефон -> WhatsApp (номер это просто идентификатор чата), иначе Instagram, иначе Facebook, иначе лид пропускаем.
    """

    def __init__(self, db: DatabaseManager, config: Dict[str, Any]):
        self.db = db
        cfg = config.get("gmaps", {})
        self.plan: List[Dict[str, Any]] = config.get("plan") or []
        self.max_places = int(cfg.get("max_places_per_query", 40))
        self.min_reviews = int(cfg.get("min_reviews", 3))
        self.headless = bool(config.get("headless", False))
        self.current: Dict[str, Any] = {}
        self.stats = {"checked": 0, "saved": 0, "has_website": 0, "no_contact": 0, "filtered": 0}

    async def run(self):
        console.print(f"[bold cyan]🗺 Google Maps: {len(self.plan)} запросов, до {self.max_places} мест на запрос[/bold cyan]")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                locale="en-US",
                viewport={"width": 1400, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            feed_page = await context.new_page()
            place_page = await context.new_page()
            try:
                for idx, item in enumerate(self.plan, 1):
                    self.current = item
                    console.print(f"\n[cyan]▶ [{idx}/{len(self.plan)}] {item['query']}[/cyan]")
                    places = await self._collect_places(feed_page, item["query"])
                    console.print(f"  Мест в выдаче: {len(places)}")
                    for place in places:
                        await self._process_place(place_page, place)
                        await asyncio.sleep(random.uniform(1.0, 2.2))
            finally:
                await browser.close()

        s = self.stats
        console.print(
            f"\n[bold green]✔ Google Maps готов. Проверено: {s['checked']} | Сохранено лидов: {s['saved']} | "
            f"С сайтом: {s['has_website']} | Без контактов: {s['no_contact']} | Отсеяно фильтрами: {s['filtered']}[/bold green]"
        )

    async def _collect_places(self, page: Page, query: str) -> List[Dict[str, str]]:
        url = f"https://www.google.com/maps/search/{quote_plus(query)}/?hl=en"
        try:
            await page.goto(url, timeout=40000)
        except Exception as e:
            console.print(f"  [red]Не загрузилась выдача: {e}[/red]")
            return []
        await page.wait_for_timeout(3000)
        await self._accept_consent(page)

        feed = page.locator('div[role="feed"]')
        if await feed.count() == 0:
            console.print("  [yellow]⚠ Нет ленты результатов (возможна капча или одно место в выдаче)[/yellow]")
            return []

        last, stale = 0, 0
        for _ in range(30):
            links = await page.locator('div[role="feed"] a.hfpxzc').count()
            if links >= self.max_places:
                break
            await feed.evaluate("el => el.scrollBy(0, 1500)")
            await page.wait_for_timeout(random.uniform(1100, 1700))
            if links == last:
                stale += 1
                if stale >= 3:
                    break
            else:
                stale, last = 0, links

        places, seen = [], set()
        for a in await page.locator('div[role="feed"] a.hfpxzc').all():
            href = await a.get_attribute("href") or ""
            name = (await a.get_attribute("aria-label") or "").strip()
            if href and href not in seen:
                seen.add(href)
                places.append({"href": href, "name": name})
        return places[: self.max_places]

    @staticmethod
    async def _accept_consent(page: Page):
        for text in ("Accept all", "Agree", "I agree"):
            try:
                btn = page.locator(f"button:has-text('{text}')")
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(1200)
                    return
            except Exception:
                pass

    async def _process_place(self, page: Page, place: Dict[str, str]):
        place_id = self._place_id(place["href"], place["name"])
        adv_id = f"gmaps:{place_id}"
        if self.db.lead_exists(adv_id):
            return
        self.stats["checked"] += 1

        try:
            await page.goto(place["href"], timeout=40000)
            await page.wait_for_selector("h1", timeout=15000)
            await page.wait_for_timeout(1200)
            info = await self._read_place(page)
        except Exception as e:
            logger.debug(f"Карточка не открылась {place['name']}: {e}")
            return

        name = info["name"] or place["name"]
        base = {
            "advertiser_id": adv_id,
            "business_name": name,
            "search_query": f"gmaps: {self.current.get('query', '')}",
        }

        if info["closed"]:
            self.stats["filtered"] += 1
            return

        if info["website"]:
            self.stats["has_website"] += 1
            console.print(f"  [yellow]⚠ Есть сайт {info['website']}:[/yellow] {name}")
            self._save({**base, "website_found": info["website"], "has_website": 1,
                        "qualification_reason": f"Website in Google Maps card: {info['website']}",
                        "status": "DISQUALIFIED_HAS_WEBSITE"})
            return

        text_blob = f"{name} {info['category']}".lower()
        allow = self.current.get("allow_words", [])
        stop = find_stop_word(name.lower(), NAME_STOP_WORDS, allow) or find_stop_word(info["category"].lower(), BASE_STOP_WORDS, allow)
        if stop or not has_signal(text_blob, self.current.get("signals", [])) or info["reviews"] < self.min_reviews:
            self.stats["filtered"] += 1
            reason = f"стоп-слово '{stop}'" if stop else ("нет сигналов ниши" if not has_signal(text_blob, self.current.get("signals", [])) else f"отзывов {info['reviews']}")
            console.print(f"  [dim]– Отсеян ({reason}): {name} [{info['category']}][/dim]")
            self._save({**base, "qualification_reason": f"Maps filter: {reason}", "status": "DISQUALIFIED_NICHE_MISMATCH"})
            return

        self._route(base, name, info)

    def _route(self, base: Dict[str, Any], name: str, info: Dict[str, Any]):
        phone, ig, fb = info["phone"], info["instagram"], info["facebook"]
        wa_phone = normalize_phone(phone, self.current.get("country") or "AE") if phone else None

        lead = {
            **base,
            "fb_page_url": fb or "",
            "ig_username": ig or "",
            "ig_profile_url": f"https://www.instagram.com/{ig}/" if ig else "",
            "has_website": 0,
            "ad_cta_type": "Google Maps",
        }
        contacts = f"тел: {phone or '-'} | IG: {ig or '-'} | FB: {fb or '-'}"

        for channel in CHANNEL_ORDER:
            if channel == "WA" and wa_phone and queue_whatsapp_lead(wa_phone, name, self.current.get("niche", "")):
                lead.update({
                    "preferred_platform": "WA",
                    "preferred_profile_url": f"https://wa.me/{wa_phone.lstrip('+')}",
                    "preferred_message_url": f"https://wa.me/{wa_phone.lstrip('+')}",
                    "qualification_reason": f"Google Maps, no website. {contacts}",
                    "status": "ROUTED_WA",
                })
                phone = wa_phone
                break
            if channel == "IG" and ig:
                lead.update({
                    "preferred_platform": "IG",
                    "preferred_profile_url": f"https://www.instagram.com/{ig}/",
                    "preferred_message_url": f"https://www.instagram.com/direct/t/{ig}/",
                    "qualification_reason": f"Google Maps, no website. {contacts}",
                    "status": "READY_TO_SEND",
                })
                break
            if channel == "FB" and fb:
                lead.update({
                    "preferred_platform": "FB",
                    "preferred_profile_url": fb,
                    "preferred_message_url": fb,
                    "qualification_reason": f"Google Maps, no website. {contacts}",
                    "status": "READY_TO_SEND",
                })
                break
        else:
            self.stats["no_contact"] += 1
            console.print(f"  [dim]– Нет подходящего контакта ({contacts}): {name}[/dim]")
            self._save({**lead, "qualification_reason": f"No usable contact. {contacts}", "status": "DISQUALIFIED_NO_CONTACT"})
            return

        self._save(lead)
        if phone:
            self.db.set_lead_phone(lead["advertiser_id"], phone)
        if info.get("email"):
            self.db.set_lead_email(lead["advertiser_id"], info["email"])
            if queue_email_lead(info["email"], name, "gmaps"):
                console.print(f"  [blue]✉ Почта {info['email']} добавлена в очередь рассылки[/blue]")
        self.stats["saved"] += 1
        console.print(f"  [green]✔ {name}[/green] → [bold]{lead['preferred_platform']}[/bold] ({contacts})")

    def _save(self, data: Dict[str, Any]):
        cur = self.current
        for key in ("niche", "geo", "market", "hook_niche", "demo_niche"):
            if cur.get(key) and key not in data:
                data[key] = cur[key]
        self.db.upsert_lead(data)

    @staticmethod
    def _place_id(href: str, name: str) -> str:
        m = re.search(r"!1s([0-9a-zA-Zx:_-]+)", href)
        if m:
            return m.group(1)
        return hashlib.md5(f"{name}|{href}".encode("utf-8")).hexdigest()[:16]

    async def _read_place(self, page: Page) -> Dict[str, Any]:
        data = await page.evaluate('''() => {
            const q = s => document.querySelector(s);
            const phoneBtn = q('button[data-item-id^="phone:tel:"]');
            const site = q('a[data-item-id="authority"]');
            const cat = q('button[jsaction*="category"]');
            const main = q('div[role="main"]');
            return {
                name: (q('h1') || {}).innerText || "",
                category: cat ? cat.innerText : "",
                phone: phoneBtn ? phoneBtn.getAttribute('data-item-id').replace('phone:tel:', '') : "",
                website: site ? site.href : "",
                text: main ? main.innerText : document.body.innerText,
                links: Array.from(document.querySelectorAll('div[role="main"] a[href]')).map(a => a.href),
                mailto: Array.from(document.querySelectorAll('a[href^="mailto:"]')).map(a => a.href.replace('mailto:', ''))
            };
        }''')
        text = data["text"] or ""
        low = text.lower()
        reviews = 0
        m = re.search(r"\(([\d,\.]+)\)", text) or re.search(r"([\d,\.]+)\s+reviews?", text, re.IGNORECASE)
        if m:
            try:
                reviews = int(re.sub(r"[^\d]", "", m.group(1)))
            except ValueError:
                reviews = 0

        instagram, facebook = None, None
        for href in data["links"]:
            target = self._unwrap_google(href)
            ig = self._instagram_handle(target)
            fb = self._facebook_page(target)
            instagram = instagram or ig
            facebook = facebook or fb

        return {
            "name": data["name"].strip(),
            "category": data["category"].strip(),
            "phone": data["phone"].strip(),
            "website": self._clean_site(data["website"]),
            "reviews": reviews,
            "closed": "permanently closed" in low or "temporarily closed" in low,
            "instagram": instagram,
            "facebook": facebook,
            "email": (find_emails(" ".join(data["mailto"]) + " " + text) or [""])[0],
        }

    @staticmethod
    def _unwrap_google(href: str) -> str:
        if "google.com/url" in href:
            q = parse_qs(urlparse(href).query).get("q")
            if q:
                return unquote(q[0])
        return href

    @staticmethod
    def _clean_site(href: str) -> str:
        """Сайт из кнопки Website; пустая строка, если это не собственный сайт (соцсети и мессенджеры не считаются)."""
        if not href:
            return ""
        target = GMapsScraper._unwrap_google(href)
        host = (urlparse(target).hostname or "").lower().removeprefix("www.")
        if not host or any(host == d or host.endswith("." + d) for d in
                           ("facebook.com", "instagram.com", "wa.me", "whatsapp.com", "linktr.ee", "google.com")):
            return ""
        return host

    @staticmethod
    def _instagram_handle(url: str) -> Optional[str]:
        m = re.match(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?", url)
        if m and m.group(1).lower() not in SOCIAL_SKIP:
            return m.group(1)
        return None

    @staticmethod
    def _facebook_page(url: str) -> Optional[str]:
        m = re.match(r"https?://(?:www\.|m\.|web\.)?facebook\.com/(?:profile\.php\?id=)?([A-Za-z0-9_.\-]{3,})/?", url)
        if m and m.group(1).lower() not in SOCIAL_SKIP:
            return f"https://www.facebook.com/{m.group(1)}"
        return None
