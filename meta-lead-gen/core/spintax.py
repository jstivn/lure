import random
import re
from typing import Optional


class SpintaxEngine:
    """
    Первое сообщение (шаг 1) для FB Messenger и Instagram Direct.

    Одна схема для всех ниш:
    {Hi|Hey|Hello}! Saw your [ЧТО] ad on [SOURCE]. Do people [ДЕЙСТВИЕ] directly [HERE]?
    Меняются только [ЧТО] рекламируют и [ДЕЙСТВИЕ] клиентов, остальное одинаково.
    """

    OPENER = "{Hi|Hey|Hello}! Saw your ad on SOURCE."

    ENDINGS = [
        "Do people ACTION directly HERE?",
        "Can people ACTION directly HERE?",
        "Do {people|clients|customers} {usually|mostly} ACTION directly HERE?",
    ]

    # hook_niche из catalog.json -> (WHAT, ACTION)
    # Один универсальный текст для всех ниш: ничего не говорит о том, что именно рекламируют
    NICHES = {
        "generic": ("", "{get in touch with you|reach out to you|message you}"),
    }

    @staticmethod
    def parse(template: str) -> str:
        """Раскрывает спинтакс {a|b|c}, в том числе вложенный."""
        pattern = re.compile(r"\{([^{}]+)\}")
        while True:
            match = pattern.search(template)
            if not match:
                break
            chosen = random.choice(match.group(1).split("|"))
            template = template[:match.start()] + chosen + template[match.end():]
        return template.strip()

    @classmethod
    def generate_hook(cls, niche_type: Optional[str] = None, platform: Optional[str] = None) -> str:
        """Собирает шаг 1 для ниши (hook_niche) и платформы ("IG" или Facebook)."""
        what, action = cls.NICHES["generic"]
        is_ig = platform == "IG"

        template = (cls.OPENER + " " + random.choice(cls.ENDINGS)) \
            .replace("WHAT", what) \
            .replace("ACTION", action) \
            .replace("SOURCE", "Instagram" if is_ig else "Facebook") \
            .replace("HERE", "in DMs" if is_ig else "in Messenger")

        return re.sub(r"\s+", " ", cls.parse(template)).strip()
