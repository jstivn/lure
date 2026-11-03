"""
Первое сообщение (шаг 1) для WhatsApp.

Одна схема для всех ниш:
{Hi|Hey|Hello}! Saw your [ЧТО] ad on Instagram. Do people [ДЕЙСТВИЕ] directly in WhatsApp?
Меняются только [ЧТО] рекламируют и [ДЕЙСТВИЕ] клиентов, остальное одинаково.
"""

import random
import re
from typing import Optional


class SpintaxGenerator:
    OPENER = "{Hi|Hey|Hello}! Saw your ad on Instagram."

    ENDINGS = [
        "Do people ACTION directly in WhatsApp?",
        "Can people ACTION directly in WhatsApp?",
        "Do {people|clients|customers} {usually|mostly} ACTION directly in WhatsApp?",
    ]

    # Один универсальный текст для всех ниш: ничего не говорит о том, что именно рекламируют
    NICHES = {
        "generic": ("", "{get in touch with you|reach out to you|message you}"),
    }

    def __init__(self):
        self._last_generated = ""

    @staticmethod
    def spin(text: str) -> str:
        """Раскрывает спинтакс {a|b|c}, в том числе вложенный."""
        pattern = re.compile(r"\{([^{}]+)\}")
        while True:
            match = pattern.search(text)
            if not match:
                break
            text = text[:match.start()] + random.choice(match.group(1).split("|")) + text[match.end():]
        return text.strip()

    def generate_hook(self, niche: str = "", business_name: Optional[str] = None) -> str:
        """Шаг 1 для ниши; подряд два одинаковых текста не отдаёт."""
        what, action = self.NICHES["generic"]
        result = ""
        for _ in range(15):
            template = (self.OPENER + " " + random.choice(self.ENDINGS)) \
                .replace("WHAT", what).replace("ACTION", action)
            result = re.sub(r"\s+", " ", self.spin(template)).strip()
            if result != self._last_generated:
                break
        self._last_generated = result
        return result
