import base64
import html
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Dict, Optional

from core.database import clean_company_name

logger = logging.getLogger("Mockup")

BASE_DIR = Path(__file__).resolve().parent.parent
ASSET_DIR = BASE_DIR / "assets" / "mockups"
OUT_DIR = BASE_DIR / "data" / "mockups"

ACCENTS = ["#D9B98A", "#C9A27E", "#B8C4B0", "#E3B7A6"]

# country code -> (botox, hydrafacial, filler, country name for the no-city fallback)
PRICES = {
    "AE": ("AED 900", "AED 600", "AED 1,200", "UAE"),
    "SA": ("SAR 900", "SAR 600", "SAR 1,200", "Saudi Arabia"),
    "GB": ("£150", "£120", "£180", "United Kingdom"),
}

# hook_niche лида -> шаблон макета. Ниши вне таблицы макет не получают.
TEMPLATES = {
    "beauty": "aesthetics_combo_template.html",
    "renovation": "northline_template.html",
}

VIEWPORT = {"width": 1440, "height": 1080}


def _catalog_geo(geo: Optional[str]) -> Dict[str, Any]:
    try:
        with open(BASE_DIR / "catalog.json", "r", encoding="utf-8") as f:
            return json.load(f).get("geos", {}).get(geo or "", {})
    except Exception:
        return {}


def resolve_city(lead: Dict[str, Any], geo: Dict[str, Any]) -> str:
    """Город берём из поискового запроса лида (в лидах нет отдельного поля города); иначе страна."""
    query = (lead.get("search_query") or "").lower()
    for city in geo.get("cities", []):
        if city.lower() in query:
            return city
    return PRICES.get(geo.get("country", ""), (None, None, None, ""))[3]


def build_context(lead: Dict[str, Any], accent: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Значения меток для лида или None, если макет делать нельзя (нет названия, ниша без шаблона, страна без прайса)."""
    raw_name = (lead.get("business_name") or "").strip()
    if not raw_name:
        return None
    name = clean_company_name(raw_name)
    if not name or name == "Your Business":
        return None

    niche = lead.get("hook_niche") or lead.get("niche")
    template = TEMPLATES.get("beauty" if niche == "aesthetics" else niche)
    if not template:
        return None
    ctx = {"CLINIC_NAME": name, "_template": template}
    if template == "northline_template.html":
        return ctx

    geo = _catalog_geo(lead.get("geo"))
    prices = PRICES.get(geo.get("country", ""))
    if not prices:
        return None
    ctx.update({
        "CITY": resolve_city(lead, geo),
        "ACCENT": accent or random.choice(ACCENTS),
        "BOTOX_PRICE": prices[0],
        "FACIAL_PRICE": prices[1],
        "FILLER_PRICE": prices[2],
    })
    return ctx


def render_html(ctx: Dict[str, str]) -> str:
    page = (ASSET_DIR / ctx["_template"]).read_text(encoding="utf-8")
    if "hero.jpg" in page:
        hero = base64.b64encode((ASSET_DIR / "hero.jpg").read_bytes()).decode()
        page = page.replace('src="hero.jpg"', f'src="data:image/jpeg;base64,{hero}"')
    for key, value in ctx.items():
        if key != "_template":
            page = page.replace("{{" + key + "}}", html.escape(value) if key != "ACCENT" else value)
    return page


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")[:40]


async def render_png(browser, ctx: Dict[str, str], out_path: Path) -> Path:
    """Скриншот 1440x1080, deviceScaleFactor 2, после загрузки шрифтов."""
    context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
    try:
        page = await context.new_page()
        await page.set_content(render_html(ctx), wait_until="networkidle")
        await page.evaluate("document.fonts.ready")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, **VIEWPORT})
    finally:
        await context.close()
    return out_path


def _cli() -> None:
    """Запуск из Lure отдельным процессом: печатает JSON {"path": ...} или {"error": ...}."""
    import argparse
    import asyncio

    p = argparse.ArgumentParser()
    p.add_argument("--meta-id", type=int)
    p.add_argument("--name")
    p.add_argument("--geo", default="AE_DUBAI")
    p.add_argument("--niche", default="beauty", help="hook_niche для лида без записи в базе (WhatsApp, письма)")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    db = None
    if args.meta_id:
        from core.database import DatabaseManager
        db = DatabaseManager()
        lead = db.get_lead(args.meta_id)
        if not lead:
            print(json.dumps({"error": f"Лид #{args.meta_id} не найден"}))
            return
    else:
        lead = {"id": 0, "business_name": args.name, "geo": args.geo, "hook_niche": args.niche, "search_query": ""}

    ctx = build_context(lead)
    if not ctx:
        print(json.dumps({"error": "Нет названия, или у ниши нет макета (есть beauty и renovation), или страна бьюти-лида не из ОАЭ / Саудовской Аравии / Великобритании"}))
        return
    made = asyncio.run(generate_for_leads([lead], out_dir=Path(args.out), db=db))
    print(json.dumps({"path": str(next(iter(made.values())))}))


async def generate_for_leads(leads, out_dir: Optional[Path] = None, db=None) -> Dict[int, Path]:
    """Генерирует макеты для списка лидов. Если передан db, путь сохраняется в лиде."""
    from playwright.async_api import async_playwright

    result: Dict[int, Path] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for lead in leads:
                ctx = build_context(lead)
                if not ctx:
                    logger.info(f"Lead {lead.get('id')}: skipped (no name or unsupported country)")
                    continue
                path = (out_dir or OUT_DIR) / f"{lead['id']}_{_safe_name(ctx['CLINIC_NAME'])}.png"
                await render_png(browser, ctx, path)
                result[lead["id"]] = path
                if db:
                    db.set_lead_mockup(lead["id"], str(path))
        finally:
            await browser.close()
    return result


if __name__ == "__main__":
    _cli()

