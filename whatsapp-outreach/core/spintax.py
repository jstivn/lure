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
    OPENER = "{Hi|Hey|Hello}! Saw your WHAT ad on Instagram."

    ENDINGS = [
        "Do people ACTION directly in WhatsApp?",
        "Can people ACTION directly in WhatsApp?",
        "Do {people|clients|customers} {usually|mostly} ACTION directly in WhatsApp?",
    ]

    NICHES = {
        "rental": ("{rental|car rental}", "{book with you|reserve with you}"),
        "beauty": ("{clinic|treatment|skincare|beauty}", "{book with you|book appointments with you}"),
        "dental": ("{dental|clinic|smile}", "{book with you|book consultations with you}"),
        "real_estate": ("{property|real estate|project}", "{get in touch with you|reach out to you}"),
        "yacht": ("{yacht|tour|boat|charter}", "{book with you|reserve with you}"),
        "renovation": ("{remodeling|kitchen|renovation}", "{get quotes from you|book with you}"),
        # ниши вне таблицы
        "generic": ("{business|service}", "{book with you|get in touch with you}"),
    }

    # Ключевые слова в нише лида -> ключ в NICHES
    KEYWORDS = [
        ("rental", ("rental", "rent", "прокат", "лизинг")),
        ("yacht", ("yacht", "charter", "tour", "boat", "яхт", "тур")),
        ("beauty", ("beauty", "aesthetic", "skincare", "salon", "косметолог", "красот")),
        ("dental", ("dental", "dentist", "стоматолог")),
        ("real_estate", ("real estate", "property", "недвижим")),
        ("renovation", ("renovation", "remodel", "kitchen", "interior", "ремонт", "кухн")),
    ]

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

    @classmethod
    def _niche_key(cls, niche: Optional[str]) -> str:
        lowered = (niche or "").lower().replace("_", " ")
        for key, words in cls.KEYWORDS:
            if any(w in lowered for w in words):
                return key
        return "generic"

    def generate_hook(self, niche: str = "", business_name: Optional[str] = None) -> str:
        """Шаг 1 для ниши; подряд два одинаковых текста не отдаёт."""
        what, action = self.NICHES[self._niche_key(niche)]
        result = ""
        for _ in range(15):
            template = (self.OPENER + " " + random.choice(self.ENDINGS)) \
                .replace("WHAT", what).replace("ACTION", action)
            result = re.sub(r"\s+", " ", self.spin(template)).strip()
            if result != self._last_generated:
                break
        self._last_generated = result
        return result
