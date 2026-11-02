import asyncio
import logging
import random
import re
import sys
import time
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from playwright.async_api import Page

logger = logging.getLogger("AutomationUtils")

# Comprehensive set of recognized Top-Level Domains (TLDs) for real websites
VALID_TLDS = {
    "com", "net", "org", "us", "co", "io", "biz", "info", "site", "online",
    "store", "pro", "tech", "co.uk", "ca", "me", "app", "dev", "agency",
    "services", "cleaning", "care", "auto", "cars", "club", "shop", "vip",
    "llc", "inc", "solutions", "group", "center", "expert", "guru", "ninja",
    "help", "link", "live", "mobi", "page", "space", "website", "zone",
    "design", "digital", "top", "xyz", "company", "work", "direct", "city",
    "today", "plus", "life", "world", "media", "fit", "global", "deals",
    "promo", "contact", "express",
    # национальные зоны: без них сайты вида *.ae не считались сайтами и такие компании попадали в «без сайта»
    "ae", "sa", "qa", "kw", "bh", "om", "eg", "jo", "lb", "tr", "ph", "uk", "au", "nz",
    "pk", "za", "ng", "ke", "ie", "de", "fr", "es", "nl", "ch", "se", "dk", "fi", "pl",
    "cz", "ro", "gr", "pt", "ru", "ua", "kz", "mx", "br", "sg", "th", "vn", "hk", "tw",
    "jp", "kr", "cn"
}

_TLD_REGEX_STR = "|".join(re.escape(tld) for tld in sorted(VALID_TLDS, key=len, reverse=True))

# Regex pattern for finding external domain mentions in raw text
DOMAIN_REGEX = re.compile(
    rf"\b(?:https?:\/\/)?(?:www\.)?([a-zA-Z0-9][a-zA-Z0-9-]{{1,61}}[a-zA-Z0-9]\.(?:{_TLD_REGEX_STR}))\b",
    re.IGNORECASE
)


def is_valid_tld(hostname: str) -> bool:
    """Checks if the hostname ends with a recognized Top-Level Domain."""
    if not hostname or "." not in hostname:
        return False
    parts = hostname.lower().split(".")
    if len(parts) >= 2:
        last = parts[-1]
        second_last = f"{parts[-2]}.{parts[-1]}"
        return last in VALID_TLDS or second_last in VALID_TLDS
    return False


# QWERTY adjacent keys map for realistic typo simulation
QWERTY_ADJACENT = {
    'a': 'qwsz', 'b': 'vghn', 'c': 'xdfv', 'd': 'serfcx', 'e': 'wsdr',
    'f': 'drtgvc', 'g': 'ftyhbv', 'h': 'gyujnb', 'i': 'ujko', 'j': 'huikmn',
    'k': 'jiolm', 'l': 'kop', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
    'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'wazxde', 't': 'rfgy',
    'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu',
    'z': 'asx'
}


async def human_delay(min_sec: float, max_sec: float):
    """Asynchronous sleep with randomized float duration."""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


async def human_type(
    page: Page,
    text: str,
    min_delay_ms: int = 45,
    max_delay_ms: int = 125,
    enable_typos: bool = True,
    typo_chance: float = 0.04
):
    """
    Simulates ultra-realistic human typing:
    - Variable keystroke intervals and natural word bursts.
    - Rare, authentic typos based on adjacent QWERTY keys (4% chance).
    - Human reaction latency (180-420ms) before realizing the typo.
    - Backspace deletion and typing the correct letter.
    - Thoughtful micro-pauses at spaces and punctuation marks.
    """
    for idx, char in enumerate(text):
        char_lower = char.lower()

        # Rare human typo simulation on letters
        if enable_typos and char_lower in QWERTY_ADJACENT and random.random() < typo_chance:
            typo_char = random.choice(QWERTY_ADJACENT[char_lower])
            if char.isupper():
                typo_char = typo_char.upper()

            # 1. Type the accidental mistake key
            await page.keyboard.type(typo_char, delay=random.randint(min_delay_ms, max_delay_ms))

            # 2. Human cognitive latency: "Oops, I made a typo" (180ms - 420ms)
            await asyncio.sleep(random.uniform(0.18, 0.42))

            # 3. Hit Backspace to delete
            await page.keyboard.press("Backspace")

            # 4. Small pause before resuming correction
            await asyncio.sleep(random.uniform(0.09, 0.24))

        # Type the correct character
        press_delay = random.randint(min_delay_ms, max_delay_ms)
        await page.keyboard.type(char, delay=press_delay)

        # Natural pauses: between words or after punctuation
        if char == " ":
            # Space between words: slight rhythm reset
            if random.random() < 0.45:
                await asyncio.sleep(random.uniform(0.10, 0.28))
        elif char in ",.?!":
            # Punctuation: natural cognitive hesitation
            await asyncio.sleep(random.uniform(0.25, 0.55))


async def smooth_scroll(page: Page, distance: int = 500, direction: str = "down"):
    """
    Emulates human scrolling using mouse wheel increments.
    """
    step = 60
    current = 0
    multiplier = 1 if direction == "down" else -1

    while current < distance:
        chunk = random.randint(40, 90)
        current += chunk
        await page.mouse.wheel(0, chunk * multiplier)
        await asyncio.sleep(random.uniform(0.04, 0.12))


async def terminal_countdown(seconds: int, prefix: str = "Anti-Ban Safe Delay"):
    """
    Displays an in-place live countdown timer in the terminal.
    """
    start_time = time.time()
    end_time = start_time + seconds

    while True:
        remaining = int(end_time - time.time())
        if remaining <= 0:
            sys.stdout.write(f"\r[green]✔ {prefix} complete. Resuming!{' ' * 20}\n[/green]")
            sys.stdout.flush()
            break

        mins, secs = divmod(remaining, 60)
        time_str = f"{mins:02d}:{secs:02d}"
        sys.stdout.write(f"\r⏱  [{prefix}] Next lead in: {time_str} ... (Press Ctrl+C to pause)")
        sys.stdout.flush()
        await asyncio.sleep(1)


def unwrap_meta_redirect(url: str) -> str:
    """
    Extracts destination URL from Meta link wrappers:
    e.g. https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com%2F&h=...
    """
    if not url:
        return ""
    if "/l.php" in url or "l.instagram.com" in url or "lm.facebook.com" in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "u" in params and params["u"]:
            return unquote(params["u"][0])
    return url


def clean_domain_name(url_or_domain: str) -> str:
    """
    Extracts the root hostname (without www. or protocol).
    """
    if not url_or_domain:
        return ""
    url = url_or_domain.strip().lower()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname.strip()
    except Exception:
        return url_or_domain.lower().strip()


# Ссылки кнопок «Написать в Messenger/WhatsApp» ведут в мессенджер, это не сайт компании (всегда исключаются)
MESSENGER_LINK_DOMAINS = {"fb.me", "m.me", "fb.com", "messenger.com", "whatsapp.com", "wa.me"}


def is_custom_website(url_or_text: str, excluded_domains: List[str]) -> Tuple[bool, Optional[str]]:
    """
    Evaluates whether a given URL or text snippet contains a REAL custom website.
    Returns (True, domain_name) if it has a custom domain.
    Returns (False, None) if NO website or only excluded link-in-bio/social platforms/maps.
    """
    if not url_or_text:
        return False, None

    raw = url_or_text.strip()
    raw_lower = raw.lower()

    # Skip non-web protocols, anchors, map links, and media assets
    if (
        raw_lower.startswith(("mailto:", "tel:", "javascript:", "#", "callto:", "sms:"))
        or "/maps" in raw_lower
        or "/directions" in raw_lower
        or "where1=" in raw_lower
        or raw_lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".js", ".pdf", ".mp4"))
    ):
        return False, None

    cleaned = unwrap_meta_redirect(raw)
    cleaned_lower = cleaned.lower()

    if (
        "/maps" in cleaned_lower
        or "/directions" in cleaned_lower
        or "where1=" in cleaned_lower
    ):
        return False, None

    excl_set = {d.strip().lower() for d in excluded_domains if d} | MESSENGER_LINK_DOMAINS

    def check_excluded(domain_str: str) -> bool:
        return any(domain_str == excl or domain_str.endswith("." + excl) for excl in excl_set)

    # 1. Single URL or clean domain string (no whitespace)
    if " " not in cleaned and ("http://" in cleaned or "https://" in cleaned or cleaned.startswith("www.") or "." in cleaned):
        try:
            h = clean_domain_name(cleaned)
            if h and "." in h and is_valid_tld(h):
                if check_excluded(h):
                    return False, None
                # Check root domain structure
                if re.match(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$', h):
                    return True, h
        except Exception:
            pass

    # 2. Regex scan for embedded domain patterns in short text (bio or anchor)
    if len(cleaned) <= 600:
        matches = DOMAIN_REGEX.findall(cleaned)
        for match in matches:
            candidate = match.lower().strip()
            if candidate.startswith("www."):
                candidate = candidate[4:]
            if candidate.startswith(("http://", "https://")):
                candidate = clean_domain_name(candidate)

            if is_valid_tld(candidate) and not check_excluded(candidate):
                return True, candidate

    return False, None


def parse_follower_count(raw_str: str) -> int:
    """
    Parses follower/like strings into integers:
    e.g. '1.2K followers' -> 1200, '3.4M' -> 3400000, '540 likes' -> 540
    """
    if not raw_str:
        return 0

    clean_str = raw_str.replace(",", "").strip()
    match = re.search(r"([\d\.]+)\s*([KkMm])?", clean_str)
    if not match:
        return 0

    number = float(match.group(1))
    unit = match.group(2)

    if unit:
        unit = unit.upper()
        if unit == "K":
            return int(number * 1000)
        elif unit == "M":
            return int(number * 1000000)
    return int(number)
