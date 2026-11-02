import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from playwright.async_api import Page
from rich.console import Console

from core.catalog import BASE_STOP_WORDS, ECOMMERCE_STOP_WORDS, NAME_STOP_WORDS, find_stop_word, has_signal
from core.database import DatabaseManager
from core.email_queue import find_emails, queue_email_lead
from core.wa_queue import find_phones, queue_whatsapp_lead
from core.utils import (
    human_delay,
    is_custom_website,
    parse_follower_count,
    smooth_scroll,
    unwrap_meta_redirect
)

logger = logging.getLogger("ScraperModule")


console = Console()


class MetaScraperQualifier:
    """
    Module 1: Scrapes active ads from Meta Ad Library, opens 'See ad details'
    modal to inspect ad creative CTA links and 'About the advertiser' card
    (including linked Instagram profile, exact follower counts for FB & IG),
    instantly disqualifies ads with custom websites, and performs final FB Intro
    checks for qualified candidates.
    """

    def __init__(self, page: Page, db: DatabaseManager, config: Dict[str, Any]):
        self.page = page
        self.db = db
        self.config = config
        self.search_config = config.get("search", {})
        self.excluded_domains = config.get("excluded_domains", [])
        self.country = self.search_config.get("country", "US")
        self.active_status = self.search_config.get("active_status", "active")
        self.max_ads = self.search_config.get("max_ads_per_query", 10)
        self.platform_filter = self.search_config.get("platform", "").lower()
        self.only_ig_dominant = self.search_config.get("only_ig_dominant", False)
        # no_website_only: лид с сайтом отсеивается; any: сайт не мешает (старый режим «высокий приоритет»)
        self.no_website_only = config.get("campaign", {}).get("website_policy", "no_website_only") != "any"
        # контекст текущего запроса (страна, ниша, сигналы), выставляется в run()
        self.current: Dict[str, Any] = {}
        # почта, найденная на странице Facebook: advertiser_id -> email (уходит в базу почт только для квалифицированных лидов)
        self._page_emails: Dict[str, str] = {}

    def _build_plan(self) -> List[Dict[str, Any]]:
        """План запросов из выбора «ниши × гео»; без него работает старый список search.queries."""
        plan = self.config.get("plan")
        if plan:
            return plan
        queries = self.search_config.get("queries", ["epoxy floor", "garage floor coating"])
        return [{"query": q, "country": self.country, "signals": [], "allow_words": []} for q in queries]

    def _save_lead(self, data: Dict[str, Any]) -> int:
        """upsert_lead с меткой ниши/гео текущего запроса, чтобы рассылка и демо-ссылки знали контекст."""
        cur = self.current
        for key in ("niche", "geo", "market", "hook_niche", "demo_niche"):
            if cur.get(key) and key not in data:
                data[key] = cur[key]
        lead_id = self.db.upsert_lead(data)
        email = self._page_emails.get(data.get("advertiser_id"))
        if email and data.get("status") in ("READY_TO_SEND", "ROUTED_WA"):
            self.db.set_lead_email(data["advertiser_id"], email)
            if queue_email_lead(email, data.get("business_name", ""), "facebook"):
                console.print(f"  [blue]✉ Почта {email} добавлена в очередь рассылки[/blue]")
        return lead_id

    def _stop_word(self, text: str, name_only: bool = False) -> str:
        words = NAME_STOP_WORDS if name_only else BASE_STOP_WORDS
        return find_stop_word(text, words, self.current.get("allow_words", []))

    async def run(self):
        """Main execution flow for scraping and qualifying."""
        plan = self._build_plan()
        total_queries = len(plan)
        policy = "только без сайта" if self.no_website_only else "любые (сайт не отсеивает)"
        console.print(f"[bold cyan]🔍 Запуск Meta Ad Library Scraper: {total_queries} запросов (по {self.max_ads} объявлений), сайт: {policy}...[/bold cyan]")

        total_scraped = 0
        total_qualified = 0

        for q_idx, item in enumerate(plan, 1):
            self.current = item
            self.country = item.get("country") or self.country
            query = item["query"]
            geo_label = item.get("geo_label")
            ctx = f" [{item.get('niche_label', '')} / {geo_label}]" if geo_label else ""
            console.print(f"\n[bold yellow]━━━ [{q_idx}/{total_queries}] Поиск в Ad Library: '{query}' (Страна: {self.country}, Лимит: {self.max_ads}){ctx} ━━━[/bold yellow]")
            candidates, disqualified_count = await self.scrape_and_qualify_ad_library(query)
            total_scraped += len(candidates) + disqualified_count

            if candidates:
                console.print(f"\n[cyan]Проверка {len(candidates)} кандидатов на страницах Facebook на скрытые сайты...[/cyan]")
                for cand in candidates:
                    qualified = await self.verify_facebook_page(cand, query)
                    if qualified:
                        total_qualified += 1
                    await human_delay(2.5, 4.5)
            else:
                console.print(f"[dim]По запросу '{query}' не найдено подходящих кандидатов.[/dim]")

        console.print(f"\n[bold green]✔ Полный цикл скрапинга завершён! Проверено: {total_scraped} | Отобрано лидов: {total_qualified}[/bold green]")
        console.print("[bold blue]📁 Все результаты сохранены в SQLite (leads.db) и leads.csv[/bold blue]")
    async def scrape_and_qualify_ad_library(self, query: str) -> Tuple[List[Dict[str, Any]], int]:
        """
        Navigates to Meta Ad Library, opens ad modals, pre-filters websites directly
        from the ad creative, and extracts IG username & followers without touching instagram.com.
        """
        url = (
            f"https://www.facebook.com/ads/library/?"
            f"active_status={self.active_status}&ad_type=all&country={self.country}&q={quote(query)}"
            f"&sort_data[direction]=desc&sort_data[mode]=relevancy_monthly_grouped&search_type=keyword_unordered&media_type=all"
        )
        if self.platform_filter in ("instagram", "ig"):
            url += "&publisher_platforms[0]=instagram" 
        logger.info(f"Opening Ad Library URL: {url}")

        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await human_delay(4.0, 7.0)
            await self._dismiss_popups()
        except Exception as e:
            console.print(f"[red]Error loading Ad Library page: {e}[/red]")
            return [], 0

        candidates: List[Dict[str, Any]] = []
        processed_adv_ids = set()
        processed_names = set()
        disqualified_count = 0
        scroll_attempts = 0
        no_new_ads_count = 0
        max_scrolls = max(8, self.max_ads // 4)

        with console.status(f"[bold green]Inspecting ads directly in Ad Library for '{query}'...") as status:
            while len(candidates) + disqualified_count < self.max_ads and scroll_attempts < max_scrolls:
                # Find all "See ad details" buttons
                btns = await self.page.query_selector_all(
                    'div[role="button"]:has-text("See ad details"), button:has-text("See ad details"), '
                    'div[role="button"]:has-text("Сведения о рекламе"), div[role="button"]:has-text("Информация о рекламе")'
                )

                for btn in btns:
                    if len(candidates) + disqualified_count >= self.max_ads:
                        break

                    try:
                        # Pre-check: Inspect parent card header BEFORE clicking to skip duplicates instantly
                        card_meta = await btn.evaluate(r'''el => {
                            let parent = el;
                            for (let step = 0; step < 15; step++) {
                                if (!parent.parentElement) break;
                                parent = parent.parentElement;
                                const link = parent.querySelector('a[href*="facebook.com/"], a[href*="view_all_page_id="]');
                                if (link && (link.href || link.innerText)) {
                                    return {
                                        name: (link.innerText || '').trim(),
                                        href: link.href || ''
                                    };
                                }
                            }
                            return null;
                        }''')

                        if card_meta:
                            card_name = card_meta.get("name", "").strip()
                            card_href = card_meta.get("href", "")
                            adv_slug = None
                            if card_href:
                                m = re.search(r'(?:view_all_page_id=|facebook\.com/(?:profile\.php\?id=)?)([a-zA-Z0-9\._\-]+)', card_href)
                                if m:
                                    adv_slug = m.group(1).rstrip('/')

                            if (adv_slug and (adv_slug in processed_adv_ids or self.db.lead_exists(adv_slug))) or (card_name and card_name.lower() in processed_names):
                                # Skip instantly without clicking!
                                continue

                        # Scroll button into view and click
                        await btn.scroll_into_view_if_needed()
                        await human_delay(0.5, 1.0)
                        await btn.click()
                        await human_delay(1.5, 2.5)

                        # Expand "About the advertiser" accordion if present
                        about_loc = self.page.locator(
                            'text=/About the advertiser|Об этом рекламодателе|Информация о рекламодателе|О рекламодателе/i'
                        ).first
                        if await about_loc.is_visible():
                            await about_loc.click()
                            await human_delay(1.0, 1.5)

                        # Extract dialog content including CTA buttons
                        dialog_data = await self.page.evaluate('''() => {
                            const dialogs = Array.from(document.querySelectorAll('div[role="dialog"]'));
                            const adDialog = dialogs.find(d => 
                                d.innerText.includes('Ad Details') || 
                                d.innerText.includes('About the advertiser') ||
                                d.innerText.includes('Сведения о рекламе') ||
                                d.innerText.includes('Об этом рекламодателе')
                            );
                            if (!adDialog) return null;
                            return {
                                text: adDialog.innerText,
                                links: Array.from(adDialog.querySelectorAll('a[href]')).map(a => ({
                                    href: a.href,
                                    text: a.innerText.trim()
                                })),
                                buttons: Array.from(adDialog.querySelectorAll('button, [role="button"], a[role="button"]')).map(b => b.innerText.trim()).filter(Boolean)
                            };
                        }''')

                        # Close dialog with Escape
                        await self.page.keyboard.press("Escape")
                        await human_delay(0.8, 1.4)

                        if not dialog_data:
                            continue

                        parsed = self._parse_modal_data(dialog_data["text"], dialog_data["links"], dialog_data.get("buttons", []))
                        adv_id = parsed["advertiser_id"]
                        biz_name = parsed["business_name"] or f"Business_{adv_id}"

                        if not adv_id:
                            continue

                        # Strict deduplication against SQLite & current batch
                        if adv_id in processed_adv_ids or self.db.lead_exists(adv_id):
                            continue
                        processed_adv_ids.add(adv_id)
                        if biz_name:
                            processed_names.add(biz_name.lower().strip())

                        # Check Blacklist Stop-Words (E-commerce / nationwide stores)
                        dialog_lower = (dialog_data.get("text", "") + " " + biz_name).lower()
                        matched_ecom = find_stop_word(dialog_lower, ECOMMERCE_STOP_WORDS)
                        if matched_ecom:
                            matched_sw = matched_ecom
                            console.print(f"  [yellow]⚠ DISQUALIFIED (E-commerce Blacklist '{matched_sw}'):[/yellow] {biz_name}")
                            self._save_lead({
                                "advertiser_id": adv_id,
                                "business_name": biz_name,
                                "search_query": query,
                                "fb_page_url": parsed["fb_page_url"],
                                "fb_followers": parsed["fb_followers"],
                                "ig_username": parsed["ig_username"] or "",
                                "ig_profile_url": f"https://www.instagram.com/{parsed['ig_username']}/" if parsed["ig_username"] else "",
                                "ig_followers": parsed["ig_followers"],
                                "website_found": parsed.get("website_found"),
                                "has_website": 1 if parsed["has_website"] else 0,
                                "ad_cta_type": parsed.get("ad_cta_type", ""),
                                "qualification_reason": f"E-commerce / nationwide stop-word detected: {matched_sw}",
                                "status": "DISQUALIFIED_ECOMMERCE_BLACKLIST"
                            })
                            disqualified_count += 1
                            continue

                        # Check Niche Mismatch Stop-Words (drama, film, theatre, etc.)
                        matched_niche_sw = self._stop_word(dialog_lower) or self._stop_word(biz_name, name_only=True)
                        if matched_niche_sw:
                            matched_sw = matched_niche_sw
                            console.print(f"  [yellow]⚠ DISQUALIFIED (Niche Mismatch '{matched_sw}'):[/yellow] {biz_name}")
                            self._save_lead({
                                "advertiser_id": adv_id,
                                "business_name": biz_name,
                                "search_query": query,
                                "fb_page_url": parsed["fb_page_url"],
                                "fb_followers": parsed["fb_followers"],
                                "ig_username": parsed["ig_username"] or "",
                                "ig_profile_url": f"https://www.instagram.com/{parsed['ig_username']}/" if parsed["ig_username"] else "",
                                "ig_followers": parsed["ig_followers"],
                                "website_found": parsed.get("website_found"),
                                "has_website": 1 if parsed["has_website"] else 0,
                                "ad_cta_type": parsed.get("ad_cta_type", ""),
                                "qualification_reason": f"Niche mismatch stop-word detected: {matched_sw}",
                                "status": "DISQUALIFIED_NICHE_MISMATCH"
                            })
                            disqualified_count += 1
                            continue


                        # Сигналы выбранной ниши: хотя бы одно слово в объявлении или названии (пустой список = проверка выключена)
                        if not has_signal(dialog_lower, self.current.get("signals", [])):
                            console.print(f"  [yellow]⚠ DISQUALIFIED (No Niche Signals in ad/name):[/yellow] {biz_name}")
                            self._save_lead({
                                "advertiser_id": adv_id,
                                "business_name": biz_name,
                                "search_query": query,
                                "fb_page_url": parsed["fb_page_url"],
                                "fb_followers": parsed["fb_followers"],
                                "ig_username": parsed["ig_username"] or "",
                                "ig_profile_url": f"https://www.instagram.com/{parsed['ig_username']}/" if parsed["ig_username"] else "",
                                "ig_followers": parsed["ig_followers"],
                                "website_found": parsed.get("website_found"),
                                "has_website": 1 if parsed["has_website"] else 0,
                                "ad_cta_type": parsed.get("ad_cta_type", ""),
                                "qualification_reason": f"No signals of niche '{self.current.get('niche_label', '-')}' found in ad creative or name",
                                "status": "DISQUALIFIED_NO_NICHE_SIGNAL"
                            })
                            disqualified_count += 1
                            continue
                        if parsed["has_website"] and self.no_website_only:
                            domain = parsed["website_found"]
                            console.print(f"  [yellow]⚠ DISQUALIFIED (Has Website '{domain}'):[/yellow] {biz_name}")
                            self._save_lead({
                                "advertiser_id": adv_id,
                                "business_name": biz_name,
                                "search_query": query,
                                "fb_page_url": parsed["fb_page_url"],
                                "fb_followers": parsed["fb_followers"],
                                "ig_username": parsed["ig_username"] or "",
                                "ig_profile_url": f"https://www.instagram.com/{parsed['ig_username']}/" if parsed["ig_username"] else "",
                                "ig_followers": parsed["ig_followers"],
                                "website_found": domain,
                                "has_website": 1,
                                "ad_cta_type": parsed.get("ad_cta_type", ""),
                                "qualification_reason": f"Website found in ad: {domain}",
                                "status": "DISQUALIFIED_HAS_WEBSITE"
                            })
                            disqualified_count += 1
                            continue

                        if parsed["has_website"]:
                            domain = parsed["website_found"]
                            console.print(f"  [bold green]★ Candidate found (Has Website — HIGH PRIORITY):[/bold green] [bold]{biz_name}[/bold] -> '{domain}' (FB: {parsed['fb_followers']:,} | IG: @{parsed['ig_username']} | CTA: {parsed.get('ad_cta_type', 'Direct')})")
                        else:
                            console.print(f"  [green]✔ Candidate found (No site in ad):[/green] [bold]{biz_name}[/bold] (FB: {parsed['fb_followers']:,} | IG: @{parsed['ig_username']} [{parsed['ig_followers']:,}])")

                        candidates.append({
                            "advertiser_id": adv_id,
                            "business_name": biz_name,
                            "search_query": query,
                            "fb_page_url": parsed["fb_page_url"],
                            "fb_followers": parsed["fb_followers"],
                            "ig_username": parsed["ig_username"] or "",
                            "ig_profile_url": f"https://www.instagram.com/{parsed['ig_username']}/" if parsed["ig_username"] else "",
                            "ig_followers": parsed["ig_followers"],
                            "has_website": 1 if parsed["has_website"] else 0,
                            "website_found": parsed.get("website_found"),
                            "ad_cta_type": parsed.get("ad_cta_type", ""),
                            "wa_phone": parsed.get("wa_phone"),
                            "ad_platforms": parsed.get("ad_platforms", set()),
                        })

                        status.update(f"Inspected: {len(candidates) + disqualified_count}/{self.max_ads} (Candidates: {len(candidates)}, Disqualified: {disqualified_count})...")

                    except Exception as e:
                        logger.debug(f"Error inspecting ad card: {e}")
                        try:
                            await self.page.keyboard.press("Escape")
                        except Exception:
                            pass

                if len(candidates) + disqualified_count >= self.max_ads:
                    break

                # Scroll down smoothly to trigger infinite loading
                prev_count = len(btns)
                await smooth_scroll(self.page, distance=800, direction="down")
                await human_delay(2.5, 3.5)
                scroll_attempts += 1

                # Check if new buttons loaded
                new_btns = await self.page.query_selector_all(
                    'div[role="button"]:has-text("See ad details"), button:has-text("See ad details"), '
                    'div[role="button"]:has-text("Сведения о рекламе"), div[role="button"]:has-text("Информация о рекламе")'
                )
                if len(new_btns) <= prev_count:
                    no_new_ads_count += 1
                    if no_new_ads_count >= 2:
                        console.print(f"  [dim]ℹ Все доступные объявления по запросу '{query}' просмотрены. Идем дальше.[/dim]")
                        break
                else:
                    no_new_ads_count = 0

        return candidates, disqualified_count

    @staticmethod
    def _extract_wa_phone(url: str) -> Optional[str]:
        """Номер из wa.me/<номер>, api.whatsapp.com/send?phone=<номер> или whatsapp.com/send?phone=."""
        m = re.search(r'(?:wa\.me/|whatsapp\.com/send/?\?(?:[^#]*&)?phone=)(?:\+|%2B)?(\d{8,15})', url or "", re.IGNORECASE)
        return f"+{m.group(1)}" if m else None

    @staticmethod
    def _detect_ad_platforms(dialog_text: str) -> set:
        """Платформы показа из блока «Platforms» окна объявления; пустое множество = не удалось определить."""
        m = re.search(r'(?:Platforms|Платформы)\s*\n?(.{0,80})', dialog_text or "", re.IGNORECASE | re.DOTALL)
        chunk = m.group(1).lower() if m else ""
        found = set()
        if "instagram" in chunk:
            found.add("instagram")
        if "facebook" in chunk or "messenger" in chunk:
            found.add("facebook")
        return found

    def _handoff_to_whatsapp(self, phone: str, biz_name: str) -> bool:
        return queue_whatsapp_lead(phone, biz_name, self.current.get("niche") or self.current.get("hook_niche") or "")
    def _parse_modal_data(self, dialog_text: str, dialog_links: List[Dict[str, str]], dialog_buttons: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Parses 'About the advertiser' and ad creative links directly from the modal.
        """
        res = {
            "advertiser_id": None,
            "business_name": None,
            "fb_page_url": None,
            "fb_followers": 0,
            "ig_username": None,
            "ig_followers": 0,
            "has_website": False,
            "website_found": None,
            "ad_cta_type": "Send Message",
            "wa_phone": None,
            "ad_platforms": self._detect_ad_platforms(dialog_text),
        }

        # Кнопка рекламы ведёт в WhatsApp: номер лежит в ссылке wa.me / api.whatsapp.com
        for l in dialog_links:
            phone = self._extract_wa_phone(unwrap_meta_redirect(l.get("href", "")))
            if phone:
                res["wa_phone"] = phone
                res["ad_cta_type"] = "Send WhatsApp Message"
                break

        # Extract ad CTA button type
        all_cta_candidates = []
        if dialog_buttons:
            all_cta_candidates.extend(dialog_buttons)
        for l in dialog_links:
            if l.get("text"):
                all_cta_candidates.append(l.get("text"))

        for text in all_cta_candidates:
            t_low = text.lower()
            if "whatsapp" in t_low:
                res["ad_cta_type"] = "Send WhatsApp Message"
                break
            elif "message" in t_low or "сообщение" in t_low:
                res["ad_cta_type"] = "Send Message"
                break
            elif "learn more" in t_low or "подробнее" in t_low:
                res["ad_cta_type"] = "Learn More"
                break
            elif "quote" in t_low or "расчет" in t_low:
                res["ad_cta_type"] = "Get Quote"
                break
            elif "contact" in t_low or "связаться" in t_low:
                res["ad_cta_type"] = "Contact Us"
                break
            elif "book" in t_low or "забронировать" in t_low:
                res["ad_cta_type"] = "Book Now"
                break
            elif "call" in t_low or "позвонить" in t_low:
                res["ad_cta_type"] = "Call Now"
                break

        # 1. Check all links in dialog for custom websites (CTA button, display link, etc.)
        for l in dialog_links:
            href = l.get("href", "")
            unwrapped = unwrap_meta_redirect(href)
            if re.search(r'(?:^|//|\.)(?:wa\.me|whatsapp\.com)/', unwrapped, re.IGNORECASE):
                continue  # ссылка на WhatsApp это канал связи, не сайт компании
            has_site, domain = is_custom_website(unwrapped, self.excluded_domains)
            if has_site:
                res["has_website"] = True
                res["website_found"] = domain
                break

        # 2. Extract FB link / username from links if present
        for l in dialog_links:
            href = l.get("href", "")
            if "facebook.com/" in href and not any(k in href for k in ["/ads/", "/legal/", "/policies/", "/about/", "/privacy/"]):
                m_page = re.search(r'facebook\.com/(?:profile\.php\?id=)?([a-zA-Z0-9\._\-]+)', href)
                if m_page and not res["advertiser_id"]:
                    clean_id = m_page.group(1).rstrip('/')
                    res["advertiser_id"] = clean_id
                    res["fb_page_url"] = f"https://www.facebook.com/{clean_id}"
                    if l.get("text"):
                        res["business_name"] = l.get("text").split('\n')[0].strip()

        # 3. Extract info from "About the advertiser" section
        for kw in ["About the advertiser", "Об этом рекламодателе", "Информация о рекламодателе", "О рекламодателе"]:
            if kw in dialog_text:
                about_part = dialog_text.split(kw, 1)[1]
                for stop_word in ["About ads and data use", "Сведения о рекламе", "Close", "Закрыть"]:
                    if stop_word in about_part:
                        about_part = about_part.split(stop_word, 1)[0]

                # Check bio text for website
                if not res["has_website"]:
                    has_bio_site, bio_domain = is_custom_website(about_part[:1000], self.excluded_domains)
                    if has_bio_site:
                        res["has_website"] = True
                        res["website_found"] = bio_domain

                # ID
                m_id = re.search(r'ID:\s*(\d+)', about_part)
                if m_id and not res["advertiser_id"]:
                    res["advertiser_id"] = m_id.group(1)
                    res["fb_page_url"] = f"https://www.facebook.com/{m_id.group(1)}"

                # Instagram handle & IG followers
                m_ig = re.search(r'@([a-zA-Z0-9_\.]+)\s*\n\s*([\d\.,]+[KkMm]?)\s*(?:followers?|подписчик\w*)', about_part, re.IGNORECASE)
                if m_ig:
                    res["ig_username"] = m_ig.group(1)
                    res["ig_followers"] = parse_follower_count(m_ig.group(2))
                else:
                    m_handle = re.search(r'@([a-zA-Z0-9_\.]{3,30})', about_part)
                    if m_handle:
                        res["ig_username"] = m_handle.group(1)

                # FB followers
                before_ig = about_part.split("@")[0] if "@" in about_part else about_part
                m_fb = re.search(r'([\d\.,]+[KkMm]?)\s*(?:followers?|likes?|подписчик\w*|отметок)', before_ig, re.IGNORECASE)
                if m_fb:
                    res["fb_followers"] = parse_follower_count(m_fb.group(1))

                # Business Name from first line of about_part
                lines = [line.strip() for line in about_part.split('\n') if line.strip()]
                if lines and not res["business_name"]:
                    res["business_name"] = lines[0]

                break

        return res

    async def verify_facebook_page(self, cand: Dict[str, Any], query: str) -> bool:
        """
        Final step for candidates: visits Facebook Page to verify that Intro section
        doesn't have a hidden website link, and sets preferred outreach platform.
        """
        adv_id = cand["advertiser_id"]
        biz_name = cand["business_name"]
        fb_url = cand["fb_page_url"]
        fb_followers = cand["fb_followers"]
        ig_username = cand["ig_username"]
        ig_followers = cand["ig_followers"]

        if not fb_url:
            fb_url = f"https://www.facebook.com/{adv_id}"

        console.print(f"\n[cyan]▶ Checking FB Page: [bold]{biz_name}[/bold] ({fb_url})[/cyan]")

        try:
            await self.page.goto(fb_url, wait_until="domcontentloaded", timeout=35000)
            await human_delay(2.5, 4.5)
            await self._dismiss_popups()
        except Exception as e:
            logger.warning(f"Failed to navigate to FB page {fb_url}: {e}")
            return False

        # Check for website on Facebook Page (Intro / Links)
        has_site, website_val = await self._check_facebook_website()

        final_has_website = 1 if (has_site or cand.get("has_website")) else 0
        final_website = website_val if has_site else cand.get("website_found")

        if final_has_website and self.no_website_only:
            console.print(f"  [yellow]⚠ DISQUALIFIED (Has Website '{final_website}' on FB page):[/yellow] {biz_name}")
            self._save_lead({
                "advertiser_id": adv_id,
                "business_name": biz_name,
                "search_query": query,
                "fb_page_url": fb_url,
                "fb_followers": fb_followers,
                "ig_username": ig_username or "",
                "ig_profile_url": cand.get("ig_profile_url", ""),
                "ig_followers": ig_followers,
                "website_found": final_website,
                "has_website": 1,
                "ad_cta_type": cand.get("ad_cta_type", ""),
                "qualification_reason": f"Website found on FB page: {final_website}",
                "status": "DISQUALIFIED_HAS_WEBSITE"
            })
            return False

        if final_has_website:
            qualification_reason = f"HAS_WEBSITE (HIGH_PRIORITY) — Verified Meta Advertiser: {final_website}"
            console.print(f"  [bold green]★ Facebook Page verified (Has Website — HIGH PRIORITY):[/bold green] '{final_website}'")
        else:
            qualification_reason = "No website found on FB or in Ad (only social/link-in-bio if any)"
            console.print("  [green]✔ Facebook Page verified (NO custom website)[/green]")

        page_email = await self._extract_fb_email()
        if page_email:
            self._page_emails[adv_id] = page_email

        # If fb_followers was 0 in ad modal, attempt extracting from FB page directly
        if fb_followers == 0:
            fb_page_followers = await self._extract_fb_followers()
            if fb_page_followers > 0:
                fb_followers = fb_page_followers

        # If ig_username was not in ad modal, check FB page for linked IG
        if not ig_username:
            extracted_ig_handle, extracted_ig_url = await self._extract_ig_link()
            if extracted_ig_handle:
                ig_username = extracted_ig_handle
                cand["ig_profile_url"] = extracted_ig_url

        # If ig_username is present but follower count is 0, fetch directly from IG profile
        if ig_username and ig_followers == 0:
            ig_followers = await self._extract_ig_followers_from_profile(ig_username)
            cand["ig_followers"] = ig_followers

        # Minimum Audience filter: require at least min_followers (5) on at least one platform
                # Strict check for niche mismatch on FB Page
        matched_fb_sw = self._stop_word(biz_name, name_only=True)
        if matched_fb_sw:
            matched_sw = matched_fb_sw
            console.print(f"  [yellow]⚠ DISQUALIFIED on FB Page (Niche Mismatch '{matched_sw}'):[/yellow] {biz_name}")
            self._save_lead({
                "advertiser_id": adv_id,
                "business_name": biz_name,
                "search_query": query,
                "fb_page_url": fb_url,
                "fb_followers": fb_followers,
                "ig_username": ig_username or "",
                "ig_profile_url": cand.get("ig_profile_url", ""),
                "ig_followers": ig_followers,
                "website_found": None,
                "qualification_reason": f"Niche mismatch stop-word: {matched_sw}",
                "status": "DISQUALIFIED_NICHE_MISMATCH"
            })
            return False

        # Канал 1: кнопка рекламы ведёт в WhatsApp, пишем туда (аудитория в соцсетях не важна)
        wa_phone = cand.get("wa_phone")
        if not wa_phone and "whatsapp" in (cand.get("ad_cta_type") or "").lower():
            # Ad Library не показывает номер за кнопкой WhatsApp, но он обычно указан в шапке страницы Facebook
            wa_phone = await self._extract_fb_phone()
            if wa_phone:
                console.print(f"  [blue]ℹ Номер для WhatsApp взят со страницы Facebook: {wa_phone}[/blue]")
        if wa_phone and self._handoff_to_whatsapp(wa_phone, biz_name):
            console.print(f"  [bold green]★ Канал: WHATSAPP[/bold green] (кнопка рекламы ведёт на {wa_phone}), лид передан в очередь WhatsApp")
            self._save_lead({
                "advertiser_id": adv_id,
                "business_name": biz_name,
                "search_query": query,
                "fb_page_url": fb_url,
                "fb_followers": fb_followers,
                "ig_username": ig_username or "",
                "ig_profile_url": cand.get("ig_profile_url", ""),
                "ig_followers": ig_followers,
                "preferred_platform": "WA",
                "preferred_profile_url": f"https://wa.me/{wa_phone.lstrip('+')}",
                "preferred_message_url": f"https://wa.me/{wa_phone.lstrip('+')}",
                "website_found": final_website,
                "has_website": final_has_website,
                "ad_cta_type": cand.get("ad_cta_type", ""),
                "qualification_reason": f"Ad CTA leads to WhatsApp {wa_phone}",
                "status": "ROUTED_WA"
            })
            self.db.set_lead_phone(adv_id, wa_phone)
            return True

        min_followers = self.search_config.get("min_followers", 100)
        if fb_followers < min_followers and (not ig_username or ig_followers < min_followers):
            console.print(f"  [yellow]⚠ DISQUALIFIED (Low Audience):[/yellow] FB has {fb_followers} and IG has {ig_followers} (min {min_followers} required)")
            self._save_lead({
                "advertiser_id": adv_id,
                "business_name": biz_name,
                "search_query": query,
                "fb_page_url": fb_url,
                "fb_followers": fb_followers,
                "ig_username": ig_username or "",
                "ig_profile_url": cand.get("ig_profile_url", ""),
                "ig_followers": ig_followers,
                "website_found": None,
                "qualification_reason": f"Low audience: FB ({fb_followers}) and IG ({ig_followers}) both under {min_followers}",
                "status": "DISQUALIFIED_LOW_AUDIENCE"
            })
            return False

        console.print(f"  [blue]ℹ Final Audience Comparison:[/blue] FB: {fb_followers:,} | IG: @{ig_username} ({ig_followers:,})")

        # Канал: реклама только в IG -> Instagram, только в FB -> Messenger, в обеих -> где больше подписчиков
        ad_platforms = cand.get("ad_platforms") or set()
        if ig_username and ad_platforms == {"instagram"}:
            use_ig, reason = True, "реклама только в Instagram"
        elif ad_platforms == {"facebook"}:
            use_ig, reason = False, "реклама только в Facebook"
        else:
            use_ig = bool(ig_username) and ig_followers > fb_followers
            reason = f"по подписчикам, IG: {ig_followers:,} / FB: {fb_followers:,}"

        if use_ig:
            preferred_platform = "IG"
            preferred_profile = f"https://www.instagram.com/{ig_username}/"
            preferred_message = f"https://www.instagram.com/direct/t/{ig_username}/"
            console.print(f"  [bold yellow]★ Канал: INSTAGRAM[/bold yellow] ({reason})")
        else:
            preferred_platform = "FB"
            preferred_profile = fb_url
            preferred_message = fb_url
            console.print(f"  [bold blue]★ Канал: FACEBOOK[/bold blue] ({reason})")

        # If user explicitly configured only IG-dominant leads, filter out FB-preferred leads
        if self.only_ig_dominant and preferred_platform != "IG":
            console.print(f"  [yellow]⚠ DISQUALIFIED (Not IG-dominant: FB {fb_followers:,} >= IG {ig_followers:,}):[/yellow] {biz_name}")
            self._save_lead({
                "advertiser_id": adv_id,
                "business_name": biz_name,
                "search_query": query,
                "fb_page_url": fb_url,
                "fb_followers": fb_followers,
                "ig_username": ig_username or "",
                "ig_profile_url": cand.get("ig_profile_url", ""),
                "ig_followers": ig_followers,
                "preferred_platform": "FB",
                "preferred_profile_url": fb_url,
                "preferred_message_url": fb_url,
                "website_found": final_website,
                "has_website": final_has_website,
                "ad_cta_type": cand.get("ad_cta_type", ""),
                "qualification_reason": f"Only IG-dominant enabled: FB ({fb_followers:,}) >= IG ({ig_followers:,})",
                "status": "DISQUALIFIED_NOT_IG_DOMINANT"
            })
            return False

        # Save qualified lead ready for outreach
        self._save_lead({
            "advertiser_id": adv_id,
            "business_name": biz_name,
            "search_query": query,
            "fb_page_url": fb_url,
            "fb_followers": fb_followers,
            "ig_username": ig_username or "",
            "ig_profile_url": cand.get("ig_profile_url", ""),
            "ig_followers": ig_followers,
            "preferred_platform": preferred_platform,
            "preferred_profile_url": preferred_profile,
            "preferred_message_url": preferred_message,
            "website_found": final_website,
            "has_website": final_has_website,
            "ad_cta_type": cand.get("ad_cta_type", ""),
            "qualification_reason": qualification_reason,
            "status": "READY_TO_SEND"
        })

        console.print("  [bold green]✔ Lead QUALIFIED and saved ready for outreach![/bold green]")
        return True

    async def _extract_fb_phone(self) -> Optional[str]:
        """Первый номер телефона (+код страны) из шапки открытой страницы Facebook: ссылки tel:/wa.me и текст."""
        try:
            raw = await self.page.evaluate(r"""() => {
                const hrefs = Array.from(document.querySelectorAll('a[href^="tel:"], a[href*="wa.me"], a[href*="whatsapp"]')).map(a => a.href);
                return hrefs.join(' ') + ' ' + document.body.innerText;
            }""")
        except Exception:
            return None
        phones = find_phones(raw or "", self.country or "AE")
        return phones[0] if phones else None

    async def _extract_fb_email(self) -> Optional[str]:
        """Первая почта из шапки открытой страницы Facebook: ссылки mailto: и текст."""
        try:
            raw = await self.page.evaluate(r"""() => {
                const hrefs = Array.from(document.querySelectorAll('a[href^="mailto:"]')).map(a => a.href.replace('mailto:', ''));
                return hrefs.join(' ') + ' ' + document.body.innerText;
            }""")
        except Exception:
            return None
        emails = find_emails(raw or "")
        return emails[0] if emails else None

    async def _extract_ig_followers_from_profile(self, ig_username: str) -> int:
        """Extracts follower count directly from Instagram profile if omitted in ad modal."""
        if not ig_username:
            return 0
        try:
            url = f"https://www.instagram.com/{ig_username}/"
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await human_delay(1.5, 2.5)
            meta_desc = await self.page.evaluate('''() => {
                const el = document.querySelector('meta[property="og:description"]');
                return el ? el.getAttribute('content') : '';
            }''')
            if meta_desc:
                m = re.search(r'([\d\.,]+[KkMm]?)\s*followers', meta_desc, re.IGNORECASE)
                if m:
                    cnt = parse_follower_count(m.group(1))
                    logger.info(f"Extracted IG followers for @{ig_username}: {cnt}")
                    return cnt
        except Exception as e:
            logger.debug(f"Could not fetch IG followers for {ig_username}: {e}")
        return 0

    async def _dismiss_popups(self):
        """Dismisses cookie consent or login popups if displayed."""
        popups = [
            'button:has-text("Allow all cookies")',
            'button:has-text("Decline optional cookies")',
            'button:has-text("Only allow essential cookies")',
            'div[aria-label="Decline optional cookies"]',
            'div[aria-label="Close"]',
            '[aria-label="Dismiss"]'
        ]
        for sel in popups:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await human_delay(1.0, 2.0)
                    break
            except Exception:
                pass

    async def _check_facebook_website(self) -> Tuple[bool, Optional[str]]:
        """
        Scans Facebook Page links and Intro text for real custom websites.
        Returns: (has_custom_website, domain_found)
        """
        try:
            # 1. Extract candidate external links from the page
            links = await self.page.evaluate(r'''() => {
                const list = [];
                const anchors = document.querySelectorAll('a[href]');
                for (const a of anchors) {
                    const href = (a.href || '').trim();
                    const text = (a.innerText || '').trim();

                    if (!href || href.startsWith('#') || href.startsWith('javascript:')) {
                        continue;
                    }

                    // Skip internal Facebook navigation (keep only external /l.php redirect)
                    if (href.includes('facebook.com') && !href.includes('/l.php')) {
                        continue;
                    }

                    // Skip internal FB CDN / static assets
                    if (href.includes('fbcdn.net') || href.includes('fbsbx.com')) {
                        continue;
                    }

                    // Only consider anchor text if short and single-line (e.g. "myshop.com")
                    const cleanText = (text.length <= 60 && text.indexOf('\n') === -1) ? text : '';
                    list.push({ href, text: cleanText });
                }
                return list;
            }''')

            for item in links:
                href = item["href"]
                text = item["text"]

                # Unquote Meta redirect wrapper
                unwrapped = unwrap_meta_redirect(href)

                # Check href for custom website
                is_custom, domain = is_custom_website(unwrapped, self.excluded_domains)
                if is_custom:
                    return True, domain

                # Check visible anchor text if present and looks like a domain
                if text and "." in text:
                    is_custom_t, domain_t = is_custom_website(text, self.excluded_domains)
                    if is_custom_t:
                        return True, domain_t

            # 2. Extract Intro / About card text ONLY (never the full post feed)
            intro_text = await self.page.evaluate(r'''() => {
                const headings = Array.from(document.querySelectorAll('h2, h3, span, div'));
                const introHeading = headings.find(el => {
                    const t = (el.innerText || '').trim().toLowerCase();
                    return t === 'intro' || t === 'краткая информация' || t === 'about';
                });

                if (introHeading) {
                    const card = introHeading.closest('div[class*="x"]') || introHeading.parentElement;
                    if (card && card.parentElement) {
                        return (card.parentElement.innerText || '').slice(0, 1500);
                    }
                }

                const bioEl = document.querySelector('div[data-testid="profile_intro_card"]') ||
                              document.querySelector('div[aria-label*="Intro"]');
                if (bioEl) return (bioEl.innerText || '').slice(0, 1500);

                return '';
            }''')

            if intro_text:
                is_custom_bio, domain_bio = is_custom_website(intro_text, self.excluded_domains)
                if is_custom_bio:
                    return True, domain_bio

        except Exception as e:
            logger.debug(f"Error checking FB website: {e}")

        return False, None

    async def _extract_fb_followers(self) -> int:
        """Extracts Facebook followers or likes count from page text (supports English & Russian)."""
        try:
            body_text = await self.page.evaluate('() => document.body.innerText')
            patterns = [
                r'([\d\.,]+[KkMm]?)\s*(?:followers?|подписчик\w*)',
                r'([\d\.,]+[KkMm]?)\s*(?:likes?|отметок|человек\w*\s+отметил\w*)',
                r'([\d\.,]+[KkMm]?)\s*(?:people follow this|человек\w*\s+подписан\w*)',
                r'Followed by\s+([\d\.,]+[KkMm]?)'
            ]
            for pat in patterns:
                m = re.search(pat, body_text, re.IGNORECASE)
                if m:
                    return parse_follower_count(m.group(1))
        except Exception as e:
            logger.debug(f"Error extracting FB followers: {e}")
        return 0

    async def _extract_ig_link(self) -> Tuple[Optional[str], Optional[str]]:
        """Looks for an Instagram profile link or @handle on the Facebook page as fallback."""
        try:
            ig_href = await self.page.evaluate(r'''() => {
                const a = document.querySelector('a[href*="instagram.com"]');
                return a ? a.href : null;
            }''')

            if ig_href:
                unwrapped = unwrap_meta_redirect(ig_href)
                parsed = urlparse(unwrapped)
                slug = parsed.path.strip("/").split("/")[0]
                if slug and slug not in ("p", "explore", "reels", "direct", "stories"):
                    return slug, f"https://www.instagram.com/{slug}/"

            body_text = await self.page.evaluate('() => document.body.innerText')
            patterns = [
                r'Instagram:\s*@?([A-Za-z0-9._]{3,30})',
                r'@([A-Za-z0-9._]{3,30})\s*(?:on Instagram|в Instagram)?'
            ]
            for pat in patterns:
                m = re.search(pat, body_text, re.IGNORECASE)
                if m:
                    handle = m.group(1)
                    if handle.lower() not in ("gmail", "yahoo", "hotmail", "outlook"):
                        return handle, f"https://www.instagram.com/{handle}/"

        except Exception as e:
            logger.debug(f"Error looking for IG link on FB: {e}")

        return None, None
