"""
BitBrowser Local API Client.
Communicates with BitBrowser on http://127.0.0.1:54345 to control browser profiles.
"""

import aiohttp
from typing import Dict, Any, Optional, List


class BitBrowserClient:
    def __init__(self, api_url: str = "http://127.0.0.1:54345"):
        self.api_url = api_url.rstrip("/")

    async def check_api(self) -> bool:
        """Checks if BitBrowser desktop application and its Local API are running."""
        # 1. Primary check via local /health endpoint (does not depend on cloud backend)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/health",
                    json={},
                    timeout=aiohttp.ClientTimeout(total=3.0)
                ) as resp:
                    data = await resp.json()
                    if data.get("success", False):
                        return True
        except Exception:
            pass

        # 2. Secondary fallback via local /browser/pids/all
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/browser/pids/all",
                    json={},
                    timeout=aiohttp.ClientTimeout(total=3.0)
                ) as resp:
                    data = await resp.json()
                    return data.get("success", False)
        except Exception:
            return False

    async def list_profiles(self, page: int = 0, page_size: int = 50) -> List[Dict[str, Any]]:
        """Retrieves list of all profiles configured in BitBrowser."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/browser/list",
                    json={"page": page, "pageSize": page_size},
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as resp:
                    data = await resp.json()
                    if data.get("success"):
                        return data.get("data", {}).get("list", [])
                    return []
        except Exception:
            return []

    async def open_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """
        Launches a profile or retrieves connection details if already running.
        Returns dict with keys: 'http' (e.g. '127.0.0.1:58164'), 'ws', 'pid', 'name'.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/browser/open",
                    json={"id": profile_id},
                    timeout=aiohttp.ClientTimeout(total=20.0)
                ) as resp:
                    data = await resp.json()
                    if data.get("success"):
                        return data.get("data")
                    return None
        except Exception:
            return None

    async def close_profile(self, profile_id: str) -> bool:
        """Closes a running browser profile in BitBrowser."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/browser/close",
                    json={"id": profile_id},
                    timeout=aiohttp.ClientTimeout(total=10.0)
                ) as resp:
                    data = await resp.json()
                    return data.get("success", False)
        except Exception:
            return False
