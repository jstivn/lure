import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CATALOG_PATH = Path(__file__).resolve().parent.parent / "catalog.json"

# Стоп-лист для текста объявления: заведомо не наши клиенты (кино, одежда, церкви, конкуренты).
# Совпадение только по целому слову, чтобы "seo" не срабатывало на "season".
BASE_STOP_WORDS = [
    "drama", "theatre", "theater", "movie", "actor", "actress",
    "musician", "podcast", "radio", "comedy", "comedian", "novel",
    "series", "entertainment", "reelshort", "dramabox", "shorttv", "shortmax",
    "webtoon", "manga", "comic", "fiction",
    "clothing", "apparel", "lingerie", "underwear", "fashion", "jewelry", "candle",
    "bakery", "restaurant", "cafe", "author",
    "church", "ministry", "politician", "nonprofit", "charity",
    "lead generation", "digital marketing", "seo", "media company", "social media manager",
]

# Дополнительно проверяются только в названии бизнеса: в тексте объявления они встречаются
# в любом нормальном объявлении ("Book now", "bar" в меню и т.п.).
NAME_ONLY_STOP_WORDS = [
    "film", "scene", "scenes", "band", "music", "notes", "episode", "episodes", "novels",
    "bra", "food", "bar", "pub", "book", "agency", "lead gen", "marketing", "web design",
    "software", "saas", "political",
]

NAME_STOP_WORDS = BASE_STOP_WORDS + NAME_ONLY_STOP_WORDS
ECOMMERCE_STOP_WORDS = [
    "shopify", "shipping worldwide", "nationwide shipping",
    "free worldwide shipping", "free shipping on orders", "order online at",
    "spray bottle", "16oz", "amazon.com", "walmart.com", "online course",
]

CUSTOM_NICHE_ID = "custom"


def load_catalog() -> Dict[str, Any]:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("niches", {})
    data.setdefault("geos", {})
    return data


def find_stop_word(text: str, stop_words: Iterable[str], allow: Iterable[str] = ()) -> str:
    """Возвращает первое найденное стоп-слово (целым словом) или пустую строку."""
    allowed = {a.lower() for a in allow}
    for sw in stop_words:
        if sw.lower() in allowed:
            continue
        if re.search(rf"\b{re.escape(sw)}\b", text, re.IGNORECASE):
            return sw
    return ""


def has_signal(text: str, signals: Iterable[str]) -> bool:
    """Есть ли в тексте хотя бы один сигнал ниши (слово или его начало: rent -> rental)."""
    sigs = [s for s in signals if s]
    if not sigs:
        return True
    return any(re.search(rf"\b{re.escape(s)}", text, re.IGNORECASE) for s in sigs)


def _split_csv(value: Optional[Any]) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def build_plan(
    catalog: Dict[str, Any],
    niche_ids: Iterable[str],
    geo_ids: Iterable[str],
    custom_keywords: Optional[Any] = None,
    queries_per_pair: int = 8,
) -> List[Dict[str, Any]]:
    """
    Превращает выбор «ниши × гео» в список поисковых запросов для Ad Library.
    Каждый элемент несёт свою страну, нишу, сигналы и стоп-слова, чтобы скрапер
    не зависел от глобального конфига.
    """
    niches = catalog.get("niches", {})
    geos = catalog.get("geos", {})
    custom = _split_csv(custom_keywords)

    selected: List[Dict[str, Any]] = []
    for nid in _split_csv(niche_ids):
        n = niches.get(nid)
        if not n:
            raise ValueError(f"Неизвестная ниша '{nid}'. Доступны: {', '.join(niches)}")
        selected.append({"id": nid, **n})
    if custom:
        selected.append({
            "id": CUSTOM_NICHE_ID, "label": "Свои запросы", "keywords": custom,
            "signals": [], "allow_words": [], "hook_niche": "generic", "demo_niche": "detailing",
        })
    if not selected:
        raise ValueError("Не выбрана ни одна ниша")

    geo_list: List[Dict[str, Any]] = []
    for gid in _split_csv(geo_ids):
        g = geos.get(gid)
        if not g:
            raise ValueError(f"Неизвестное гео '{gid}'. Доступны: {', '.join(geos)}")
        geo_list.append({"id": gid, **g})
    if not geo_list:
        raise ValueError("Не выбрано ни одно гео")

    plan: List[Dict[str, Any]] = []
    seen = set()
    for geo in geo_list:
        cities = geo.get("cities") or [""]
        for niche in selected:
            combos = []
            # сначала самые сильные ключевые слова по всем городам, лимит на пару режет хвост
            for kw in niche["keywords"]:
                for city in cities:
                    combos.append(f"{kw} {city}".strip())
            for query in combos[:max(1, int(queries_per_pair))]:
                key = (query.lower(), geo["country"])
                if key in seen:
                    continue
                seen.add(key)
                plan.append({
                    "query": query,
                    "country": geo["country"],
                    "geo": geo["id"],
                    "geo_label": geo.get("label", geo["id"]),
                    "market": geo.get("market", "usa"),
                    "niche": niche["id"],
                    "niche_label": niche.get("label", niche["id"]),
                    "hook_niche": niche.get("hook_niche", "generic"),
                    "demo_niche": niche.get("demo_niche", niche["id"]),
                    "signals": niche.get("signals", []),
                    "allow_words": niche.get("allow_words", []),
                })
    return plan


def niche_profile(catalog: Dict[str, Any], niche_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not niche_id:
        return None
    n = catalog.get("niches", {}).get(niche_id)
    return {"id": niche_id, **n} if n else None
