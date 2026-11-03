"""
Playwright-based WhatsApp Web Driver.
Connects to BitBrowser profile over Chrome DevTools Protocol (CDP),
opens chat by direct phone URL, verifies validity and prior history,
types human-like Spintax hook, and dispatches on confirmation.
"""

import asyncio
import random
from typing import Optional, Tuple
from playwright.async_api import async_playwright, Page


QWERTY_ADJACENT = {
    'a': 'qwsz', 'b': 'vghn', 'c': 'xdfv', 'd': 'serfcx', 'e': 'wsdr',
    'f': 'drtgvc', 'g': 'ftyhbv', 'h': 'gyujnb', 'i': 'ujko', 'j': 'huikmn',
    'k': 'jiolm', 'l': 'kop', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
    'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'wazxde', 't': 'rfgy',
    'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu',
    'z': 'asx'
}


class WhatsAppDriver:
    def __init__(self, cdp_http_url: str, min_typing_ms: int = 35, max_typing_ms: int = 95):
        self.cdp_url = f"http://{cdp_http_url}" if not cdp_http_url.startswith("http") else cdp_http_url
        self.min_typing_ms = min_typing_ms
        self.max_typing_ms = max_typing_ms
        self._playwright = None
        self._browser = None
        self.page: Optional[Page] = None

    async def connect(self) -> bool:
        """Connects Playwright to the running BitBrowser CDP instance."""
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
            
            contexts = self._browser.contexts
            if not contexts:
                return False

            context = contexts[0]
            # Locate WhatsApp Web page or create new if not present
            for page in context.pages:
                if "web.whatsapp.com" in page.url:
                    self.page = page
                    break

            if not self.page:
                self.page = await context.new_page()
                await self.page.goto("https://web.whatsapp.com/", timeout=45000)

            # Wait for WhatsApp Web UI to be ready
            await self._wait_for_wa_ready(timeout_sec=30)
            return True
        except Exception:
            return False

    async def _wait_for_wa_ready(self, timeout_sec: int = 30):
        """Waits until chat panel or QR screen is loaded."""
        if not self.page:
            return
        try:
            await self.page.wait_for_selector("#pane-side, canvas[aria-label*='QR'], div[role='textbox']", timeout=timeout_sec * 1000)
        except Exception:
            pass

    async def open_dialog_and_prepare(self, phone: str, hook_text: str) -> Tuple[str, Optional[str]]:
        """
        Navigates to direct WhatsApp dialog: https://web.whatsapp.com/send?phone=...
        Returns status:
        - 'READY_TO_CONFIRM': chat open, hook typed into box, ready for human Enter
        - 'INVALID_PHONE': phone not registered on WhatsApp
        - 'ALREADY_CONTACTED': prior messages already exist with this contact
        - 'TIMEOUT' / 'ERROR': failed to load chat
        """
        if not self.page:
            return "ERROR", "Page not connected"

        # Ensure any stale modals from prior requests are dismissed
        try:
            stale_popup = self.page.locator("div[data-animate-modal-popup='true'], div[role='dialog'], [data-testid='confirm-popup']")
            if await stale_popup.count() > 0:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
        except Exception:
            pass

        clean_digits = "".join(filter(str.isdigit, phone))
        direct_url = f"https://web.whatsapp.com/send?phone={clean_digits}"

        try:
            await self.page.goto(direct_url, wait_until="domcontentloaded", timeout=40000)
        except Exception:
            # Direct navigation can sometimes be slow; ensure page is responsive
            pass

        # Wait for either:
        # 1. Invalid phone popup
        # 2. Main message input box
        start_time = asyncio.get_event_loop().time()
        input_selector = "footer div[contenteditable='true'], div[contenteditable='true'][data-tab='10'], div[role='textbox'][aria-placeholder*='message'], div[role='textbox'][aria-placeholder*='сообщение']"
        
        invalid_keywords = [
            "shared via url is invalid", "phone number shared via url",
            "isn't on whatsapp", "is not on whatsapp", "not on whatsapp",
            "не зарегистрирован", "номер телефона, указанный в url",
            "недействительный номер", "недействителен",
            "no account found"
        ]

        while asyncio.get_event_loop().time() - start_time < 35:
            # 1. Check for Invalid Phone popup
            popup = self.page.locator("div[data-animate-modal-popup='true'], div[role='dialog'], [data-testid='confirm-popup']")
            if await popup.count() > 0:
                try:
                    text = (await popup.first.inner_text()).lower()
                except Exception:
                    text = ""

                # Только явное «номера нет в WhatsApp»; любой другой диалог (загрузка, обновление, лимиты) не списывает лид
                if any(k in text for k in invalid_keywords):
                    # Click OK button to dismiss popup
                    try:
                        btn = popup.locator("button:has-text('OK'), button:has-text('ОК'), div[role='button']:has-text('OK'), div[role='button']:has-text('ОК'), button, div[role='button']").first
                        if await btn.count() > 0:
                            await btn.click()
                            await asyncio.sleep(0.5)
                    except Exception:
                        pass

                    # Also press Escape as fallback to close any remaining modal overlay
                    try:
                        await self.page.keyboard.press("Escape")
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass

                    return "INVALID_PHONE", "Phone number is not registered on WhatsApp"

            # 2. Check for message input box
            input_box = self.page.locator(input_selector)
            if await input_box.count() > 0 and await input_box.first.is_visible():
                break

            await asyncio.sleep(1.0)
        else:
            return "TIMEOUT", "Timed out waiting for WhatsApp chat to open"

        # 3. Check for prior chat history (both outgoing and incoming messages)
        # Give WhatsApp Web 2.0s to pull conversation history from IndexedDB
        await asyncio.sleep(2.0)
        has_prior_chat = await self.page.evaluate("""() => {
            const pane = document.querySelector('#main, div[data-testid="conversation-panel-wrapper"]');
            if (!pane) return false;
            
            // Real messages in current chat (excluding encryption/system notices)
            const msgs = pane.querySelectorAll(
                'div[data-testid="msg-container"], ' +
                'span[data-icon="tail-out"], span[data-testid="tail-out"], ' +
                'span[data-icon="tail-in"], span[data-testid="tail-in"], ' +
                'div.copyable-text[data-pre-plain-text]'
            );
            return msgs.length > 0;
        }""")

        if has_prior_chat:
            return "ALREADY_CONTACTED", "Prior conversation already exists with this number"

        # 4. Human-like typing into input box with realistic typo simulation
        input_box = self.page.locator(input_selector).first
        await input_box.click()
        await asyncio.sleep(0.5)

        await self._human_type(hook_text)

        return "READY_TO_CONFIRM", None

    async def _human_type(self, text: str, max_typos: int = 2):
        """
        Simulates natural human typing:
        - 1-2 realistic QWERTY neighbor-key typos per message
        - Cognitive latency: user notices mistake on screen (200-420ms)
        - Backspace deletion and typing the correction
        - Rhythm pauses between words and punctuation marks
        """
        typos_count = 0
        target_typos = random.randint(1, max_typos)

        for idx, char in enumerate(text):
            char_lower = char.lower()

            # Decide if we make a realistic typo on this character
            should_typo = False
            if typos_count < target_typos and char_lower in QWERTY_ADJACENT:
                # 6% natural chance, or trigger if we are past the middle with 0 typos
                if random.random() < 0.06 or (typos_count == 0 and idx > (len(text) // 2) and random.random() < 0.25):
                    should_typo = True

            if should_typo:
                typo_char = random.choice(QWERTY_ADJACENT[char_lower])
                if char.isupper():
                    typo_char = typo_char.upper()

                # 1. Type the mistaken adjacent key
                await self.page.keyboard.type(typo_char, delay=random.randint(self.min_typing_ms, self.max_typing_ms))
                # 2. Cognitive latency: user realizes the typo
                await asyncio.sleep(random.uniform(0.20, 0.42))
                # 3. Hit Backspace to delete
                await self.page.keyboard.press("Backspace")
                # 4. Brief pause before continuing
                await asyncio.sleep(random.uniform(0.10, 0.25))
                typos_count += 1

            # Type the correct character
            await self.page.keyboard.type(char, delay=random.randint(self.min_typing_ms, self.max_typing_ms))

            # Natural rhythm pauses
            if char == " ":
                if random.random() < 0.40:
                    await asyncio.sleep(random.uniform(0.10, 0.28))
            elif char in ",.?!":
                await asyncio.sleep(random.uniform(0.25, 0.50))

    async def send_prepared_message(self, timeout_sec: int = 15) -> Tuple[bool, str]:
        """
        Sends the message currently typed in the input box by hitting Enter or clicking Send.
        Verifies actual delivery by checking WhatsApp tick marks (галочки доставки):
        - 'DELIVERED': 2 checkmarks (delivered to recipient)
        - 'SENT': 1 checkmark (sent to WhatsApp server)
        - 'PENDING_TIMEOUT': stuck on clock/pending icon
        - 'FAILED_TO_DISPATCH': message never left input box
        """
        if not self.page:
            return False, "PAGE_NOT_CONNECTED"

        try:
            input_selector = "footer div[contenteditable='true'], div[contenteditable='true'][data-tab='10'], div[role='textbox']"
            input_box = self.page.locator(input_selector).first

            # 1. Dispatch via Enter
            await input_box.press("Enter")
            await asyncio.sleep(1.0)

            # 2. Or click the Send button if Enter didn't clear input
            send_btn = self.page.locator("button[aria-label*='Send'], button[aria-label*='Отправить'], span[data-icon='send'], button:has(span[data-icon='send']), [data-testid='send']")
            if await send_btn.count() > 0 and await send_btn.first.is_visible():
                await send_btn.first.click()
                await asyncio.sleep(1.0)

            # 3. Delivery Verification Polling Loop
            start_poll = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_poll < timeout_sec:
                status = await self.page.evaluate("""() => {
                    const pane = document.querySelector('#main');
                    if (!pane) return { done: false, status: 'NO_PANE' };

                    // Verify input box cleared
                    const input = pane.querySelector('footer div[contenteditable="true"], div[role="textbox"]');
                    const textLen = input && input.innerText ? input.innerText.trim().length : 0;
                    if (textLen > 15) {
                        return { done: false, status: 'TEXT_STILL_IN_INPUT' };
                    }

                    // Locate outgoing messages
                    const outgoing = Array.from(pane.querySelectorAll('div[data-testid="msg-container"]')).filter(m => {
                        return m.querySelector('span[data-icon="tail-out"], span[data-testid="tail-out"]') !== null ||
                               (m.closest('div[data-testid^="conv-msg-"]') && m.closest('div[data-testid^="conv-msg-"]').querySelector('span[data-icon="tail-out"], span[data-testid="tail-out"]') !== null);
                    });

                    if (outgoing.length === 0) {
                        return { done: false, status: 'WAITING_OUTGOING_BUBBLE' };
                    }

                    // Check status meta on the latest outgoing message
                    const lastMsg = outgoing[outgoing.length - 1];
                    const meta = lastMsg.querySelector('div[data-testid="msg-meta"]');
                    const aria = meta ? (meta.querySelector('span[aria-label]') ? meta.querySelector('span[aria-label]').getAttribute('aria-label') : '') : '';
                    const svgTitle = meta ? (meta.querySelector('svg title') ? meta.querySelector('svg title').textContent : '') : '';
                    const dataIcon = meta ? (meta.querySelector('span[data-icon]') ? meta.querySelector('span[data-icon]').getAttribute('data-icon') : '') : '';

                    const combined = (aria + " " + svgTitle + " " + dataIcon).toLowerCase();

                    // Two ticks: delivered / read (Доставлено / Прочитано)
                    if (combined.includes('доставлено') || combined.includes('delivered') || 
                        combined.includes('прочитано') || combined.includes('read') || 
                        svgTitle === 'wds-ic-read' || svgTitle === 'wds-ic-delivered' || dataIcon === 'msg-dblcheck') {
                        return { done: true, status: 'DELIVERED' };
                    }

                    // One tick: sent to server (Отправлено)
                    if (combined.includes('отправлено') || combined.includes('sent') || 
                        svgTitle === 'wds-ic-check' || dataIcon === 'msg-check') {
                        return { done: true, status: 'SENT' };
                    }

                    // Clock: pending
                    if (combined.includes('ожидание') || combined.includes('pending') || 
                        svgTitle === 'wds-ic-time' || dataIcon === 'msg-time') {
                        return { done: false, status: 'PENDING' };
                    }

                    return { done: true, status: 'SENT' };
                }""")

                if status.get("done"):
                    return True, status.get("status", "SENT")

                await asyncio.sleep(1.0)

            # Check if input box was at least cleared
            box_cleared = await self.page.evaluate("""() => {
                const input = document.querySelector('footer div[contenteditable="true"], div[role="textbox"]');
                return !input || !input.innerText || input.innerText.trim().length === 0;
            }""")
            if box_cleared:
                return True, "SENT_UNCONFIRMED_TICKS"

            return False, "TIMEOUT_NOT_SENT"
        except Exception as e:
            return False, f"ERROR: {str(e)}"

    async def clear_input(self):
        """Clears text in input box if user skips the lead."""
        if not self.page:
            return
        try:
            input_selector = "footer div[contenteditable='true'], div[contenteditable='true'][data-tab='10']"
            box = self.page.locator(input_selector).first
            if await box.count() > 0:
                await box.click()
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")
        except Exception:
            pass

    async def close(self):
        """Disconnects Playwright CDP cleanly without closing the user's browser."""
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
