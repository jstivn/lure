import asyncio
import logging
from typing import Optional, Tuple
import aiohttp
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger("DolphinManager")


class DolphinConnectionError(Exception):
    """Raised when communication with Dolphin{anty} Local API fails."""
    pass


class DolphinBrowserManager:
    """
    Manages connection to Dolphin{anty} browser profiles via Local API and Playwright CDP.
    """

    def __init__(self, api_base: str, profile_id: str, auto_stop: bool = False):
        self.api_base = api_base.rstrip("/")
        self.profile_id = str(profile_id).strip()
        self.auto_stop = auto_stop
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._cdp_port: Optional[str] = None

    async def check_api_alive(self) -> bool:
        """Verify that Dolphin{anty} Local API is listening on port 3001."""
        try:
            async with aiohttp.ClientSession() as session:
                # Dolphin{anty} provides /browser_profiles or similar endpoint
                async with session.get(f"{self.api_base}/browser_profiles", timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    return resp.status in (200, 401, 403, 404)
        except Exception:
            return False

    async def start(self) -> Tuple[BrowserContext, Page]:
        """
        Starts the profile via Dolphin{anty} Local API and attaches Playwright via CDP.
        Returns: (BrowserContext, Page)
        """
        if not self.profile_id or self.profile_id == "REPLACE_WITH_YOUR_DOLPHIN_PROFILE_ID":
            raise DolphinConnectionError(
                "Dolphin Profile ID is not configured! Please set 'profile_id' in config.json or .env."
            )

        start_url = f"{self.api_base}/browser_profiles/{self.profile_id}/start?automation=1"
        logger.info(f"Requesting Dolphin Local API to launch profile {self.profile_id}...")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(start_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json(content_type=None)
        except aiohttp.ClientConnectorError as e:
            raise DolphinConnectionError(
                f"Cannot connect to Dolphin Local API at {self.api_base}.\n"
                "Please verify:\n"
                "1. Dolphin{anty} desktop app is running.\n"
                "2. Local API is enabled in Dolphin: Settings -> API (default port 3001).\n"
                f"Underlying error: {e}"
            )
        except Exception as e:
            raise DolphinConnectionError(f"Error calling Dolphin start API: {e}")

        logger.debug(f"Dolphin API response: {data}")

        # Check response success
        if not data.get("success", False):
            msg = data.get("msg") or data.get("error") or str(data)
            # If already running, Dolphin may return a message or we can attempt to stop then restart
            if "already" in msg.lower():
                logger.warning(f"Profile {self.profile_id} already marked active. Attempting stop & restart...")
                await self.stop()
                await asyncio.sleep(2)
                return await self.start()
            raise DolphinConnectionError(f"Dolphin failed to start profile {self.profile_id}: {msg}")

        automation_info = data.get("automation", {})
        port = automation_info.get("port")
        ws_endpoint = automation_info.get("wsEndpoint")

        if not port and not ws_endpoint:
            raise DolphinConnectionError(
                f"Dolphin response did not provide CDP port or wsEndpoint: {data}"
            )

        self._cdp_port = str(port) if port else None

        # Build CDP URL
        if ws_endpoint and ws_endpoint.startswith("ws://"):
            cdp_url = ws_endpoint
        elif ws_endpoint and port:
            cdp_url = f"ws://127.0.0.1:{port}{ws_endpoint}"
        else:
            cdp_url = f"http://127.0.0.1:{port}"

        logger.info(f"Connecting Playwright over CDP to {cdp_url}...")
        self._playwright = await async_playwright().start()

        # Connect with retry logic
        for attempt in range(5):
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    endpoint_url=cdp_url,
                    timeout=20000
                )
                break
            except Exception as err:
                if attempt == 4:
                    raise DolphinConnectionError(f"Failed to connect Playwright to CDP after 5 attempts: {err}")
                logger.warning(f"CDP connection attempt {attempt + 1} failed ({err}). Retrying in 2s...")
                await asyncio.sleep(2)

        # Retrieve browser context and active page
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = await self._browser.new_context(
                viewport={"width": 1366, "height": 850},
                user_agent=None  # Preserve Dolphin's antidetect fingerprint
            )

        # Set default navigation timeout
        self._context.set_default_navigation_timeout(45000)
        self._context.set_default_timeout(20000)

        # Find or create a page
        pages = self._context.pages
        if pages:
            self._page = pages[0]
        else:
            self._page = await self._context.new_page()

        logger.info("Successfully hooked into Dolphin{anty} profile via Playwright CDP!")
        return self._context, self._page

    async def stop(self):
        """Stops the Dolphin{anty} browser profile and closes Playwright connection."""
        try:
            if self._browser:
                await self._browser.close()
                self._browser = None
        except Exception as e:
            logger.debug(f"Error closing Playwright browser: {e}")

        try:
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
        except Exception as e:
            logger.debug(f"Error stopping Playwright instance: {e}")

        if self.auto_stop and self.profile_id:
            stop_url = f"{self.api_base}/browser_profiles/{self.profile_id}/stop"
            logger.info(f"Requesting Dolphin to stop profile {self.profile_id}...")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(stop_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        logger.debug(f"Stop profile response code: {resp.status}")
            except Exception as e:
                logger.warning(f"Could not cleanly send stop request to Dolphin: {e}")

    async def __aenter__(self):
        _, page = await self.start()
        return page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
