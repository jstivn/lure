import asyncio
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple

import aiohttp
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from rich.console import Console

logger = logging.getLogger("BrowserManager")
console = Console()

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = BASE_DIR / "data" / "profiles"

# Independent isolated profiles and ports for FB and IG to allow 100% parallel outreach
PLATFORM_SETTINGS = {
    "fb": {
        "port": 9333,
        "profile_dir_name": "chrome_fb",
        "display_name": "Facebook",
        "default_url": "https://www.facebook.com"
    },
    "ig": {
        "port": 9334,
        "profile_dir_name": "chrome_ig",
        "display_name": "Instagram",
        "default_url": "https://www.instagram.com"
    }
}


def get_chrome_executable() -> str:
    """Finds Chrome: LURE_CHROME_PATH, standard install folders, then PATH."""
    candidates = [os.environ.get("LURE_CHROME_PATH", "")]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(var)
        if root:
            candidates.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    raise FileNotFoundError("Google Chrome not found. Install it or set LURE_CHROME_PATH to chrome.exe.")


class BrowserSession:
    """
    Connects to Native Google Chrome via Chrome DevTools Protocol (CDP).
    - Facebook uses Port 9333 with isolated profile data/profiles/chrome_fb.
    - Instagram uses Port 9334 with isolated profile data/profiles/chrome_ig.
    This completely eliminates conflicts and allows 100% parallel outreach
    without either browser closing or interfering with the other.
    """

    def __init__(self, platform: str, config: Dict[str, Any], headless: Optional[bool] = None):
        plat = (platform or "fb").lower()
        if plat in ("instagram", "ig"):
            self.platform = "ig"
        else:
            self.platform = "fb"

        self.settings = PLATFORM_SETTINGS[self.platform]
        self.cdp_port = self.settings["port"]
        self.cdp_url = f"http://127.0.0.1:{self.cdp_port}"
        self.profile_dir = PROFILES_DIR / self.settings["profile_dir_name"]
        self.display_name = self.settings["display_name"]

        self.config = config
        self.headless = config.get("headless", False) if headless is None else headless

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._proc: Optional[subprocess.Popen] = None

    @classmethod
    def get_profile_path(cls, platform: str = "fb") -> Path:
        plat = (platform or "fb").lower()
        dir_name = "chrome_ig" if plat in ("ig", "instagram") else "chrome_fb"
        p = PROFILES_DIR / dir_name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def has_saved_session(cls, platform: str) -> bool:
        """Checks if Chrome user profile contains session cookies."""
        user_dir = cls.get_profile_path(platform)
        cookies_network = user_dir / "Default" / "Network" / "Cookies"
        cookies_legacy = user_dir / "Default" / "Cookies"
        return cookies_network.exists() or cookies_legacy.exists()

    async def _is_cdp_alive(self) -> bool:
        """Checks if Chrome CDP is already listening on this session's port."""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.cdp_url}/json/version", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    return r.status == 200
        except Exception:
            return False

    async def _ensure_chrome_running(self):
        """Ensures that native Google Chrome is running on its designated port and profile."""
        if await self._is_cdp_alive():
            logger.info(f"Connecting to existing running Google Chrome for {self.display_name} on port {self.cdp_port}...")
            return

        chrome_exe = get_chrome_executable()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = str(self.profile_dir)
        logger.info(f"Launching Native Google Chrome for {self.display_name} (CDP Port {self.cdp_port})...")

        args = [
            chrome_exe,
            f"--remote-debugging-port={self.cdp_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
        ]

        if self.headless:
            args.append("--headless=new")

        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait up to 10 seconds for CDP to wake up
        for _ in range(20):
            await asyncio.sleep(0.5)
            if await self._is_cdp_alive():
                logger.info(f"Chrome CDP ({self.display_name}, Port {self.cdp_port}) successfully initialized!")
                return

        raise RuntimeError(f"Could not connect to Chrome CDP for {self.display_name} at {self.cdp_url} after launching.")

    async def start(self) -> Tuple[BrowserContext, Page]:
        """Connects Playwright to Native Chrome via CDP."""
        await self._ensure_chrome_running()

        self._playwright = await async_playwright().start()

        # Connect over CDP to platform-specific port
        self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url, timeout=30000)

        # Grab default context
        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = await self._browser.new_context()

        # Set realistic timeouts
        self._context.set_default_navigation_timeout(45000)
        self._context.set_default_timeout(20000)

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        return self._context, self._page

    async def stop(self):
        """Closes Playwright connection cleanly (leaves Chrome profile intact)."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser connection: {e}")
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug(f"Error stopping Playwright: {e}")
            self._playwright = None

        # If we launched Chrome in headless mode specifically for this run, terminate it
        if self._proc and self.headless:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    async def __aenter__(self) -> Page:
        _, page = await self.start()
        return page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()


async def interactive_login(platform: str, config: Dict[str, Any]):
    """
    Opens native Chrome directly via Windows so the user can easily log in
    to Facebook and Instagram independently without any bot detection.
    """
    plat = (platform or "fb").lower()
    target_key = "ig" if plat in ("ig", "instagram") else "fb"
    settings = PLATFORM_SETTINGS[target_key]

    chrome_exe = get_chrome_executable()
    profile_dir = str(BrowserSession.get_profile_path(target_key))
    target_url = settings["default_url"]
    name = settings["display_name"]
    port = settings["port"]

    console.print(f"\n[bold cyan]━━━ Открытие официального Google Chrome для {name} ━━━[/bold cyan]")
    console.print(f"[yellow]Сейчас откроется настоящий Google Chrome для {name} (порт: {port}, профиль: {settings['profile_dir_name']}).[/yellow]")

    cmd = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        target_url
    ]

    subprocess.Popen(cmd)
    try:
        console.print(
            f"\n[bold green]✔ Окно Google Chrome для {name} открыто![/bold green]\n"
            f"1. Войдите в свой аккаунт {name} в открывшемся окне.\n"
            f"2. Когда вы войдете и увидите главную страницу {name}, вернитесь сюда."
        )
        input("\nКогда успешно вошли, нажмите [Enter] здесь для сохранения: ")
        console.print(f"[bold green]✔ Вход сохранен навсегда в профиле {settings['profile_dir_name']}![/bold green]")
    finally:
        pass
