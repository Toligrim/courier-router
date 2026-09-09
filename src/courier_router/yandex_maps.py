"""Headless Yandex Maps toponym lookup (sync Playwright). Optional second source
for the DaData cross-check / fallback — NOT the primary geocoder.

lookup_batch([(key, address, (near_lat, near_lon)), ...]) -> {key: YResult | None}

When Yandex returns a disambiguation list (several candidates), the matching
item is chosen by street name + house number from the query — not the first row.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
_COORD_SEL = ("[class*='toponym-card-title-view__coords'], "
              "[class*='card-title-view__coordinates']")
_TITLE_SEL = "h1.card-title-view__title, [class*='card-title-view__title']"
_SUB_SEL = ("[class*='card-title-view__subtitle'], "
            "[class*='toponym-card-title-view__subtitle']")
_SNIPPET_SEL = ("[class*='search-snippet-view'], [class*='search-business-snippet-view'], "
                "li[class*='search-snippet']")
_GENERIC = {"улица", "ул", "проспект", "пр", "пркт", "пр-кт", "переулок", "пер",
            "шоссе", "ш", "набережная", "наб", "бульвар", "б-р", "линия", "проезд",
            "дом", "д", "литера", "лит", "корпус", "корп", "к", "строение", "стр",
            "деревня", "дер", "поселок", "посёлок", "пос", "село", "снт", "тер",
            "территория", "область", "обл", "район", "р-н", "городское", "поселение"}


@dataclass
class YResult:
    lat: float
    lon: float
    title: str
    subtitle: str
    url: str
    is_house: bool


class Unavailable(RuntimeError):
    pass


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", (s or "").lower().replace("ё", "е")).split())


_MARK = (r"ул|улица|пр|пр-?кт|проспект|пер|переулок|ш|шоссе|наб|набережная|"
         r"б-р|бульвар|линия|проезд|аллея|туп|тупик")
_STREET_AFTER = re.compile(rf"(?:^|,)\s*(?:{_MARK})\.?\s+([^,]+)", re.I)
_STREET_BEFORE = re.compile(rf"(?:^|,)\s*([^,]+?)\s+(?:{_MARK})\b", re.I)


def _street_tokens(s: str) -> set[str]:
    m = _STREET_AFTER.search(s or "") or _STREET_BEFORE.search(s or "")
    part = m.group(1) if m else (s or "")
    part = re.sub(r"\b(?:д|дом|кв|литера?|корп(?:ус)?|стр(?:оение)?)\.?\s*[0-9а-яa-z/]*", " ", part, flags=re.I)
    return {t for t in _norm(part).split() if t not in _GENERIC and not t.isdigit() and len(t) >= 3}


def _house_num(s: str) -> str | None:
    m = re.search(r"(?:^|[ ,])(?:д|дом)\.?\s*([0-9]+[а-яa-z]?(?:/[0-9]+)?)", s or "", re.I)
    if m:
        return _norm(m.group(1))
    nums = re.findall(r"\b([0-9]{1,4}[а-яa-z]?(?:/[0-9]+)?)\b", s or "")
    return _norm(nums[-1]) if nums else None


def _match_score(query: str, title: str, subtitle: str) -> float:
    q_streets = _street_tokens(query)
    hay = _norm(f"{title} {subtitle}")
    hay_set = set(hay.split())
    if not q_streets:
        street_hit = 0.0
    else:
        street_hit = len(q_streets & hay_set) / len(q_streets)
    q_house = _house_num(query)
    t_house = _house_num(title) or _house_num(subtitle)
    house_hit = 1.0 if (q_house and t_house and q_house == t_house) else 0.0
    # штраф, если Яндекс дал СНТ/организацию вместо улицы из запроса
    org_penalty = 0.35 if ("снт" in _norm(title).split() and "снт" not in _norm(query).split()) else 0.0
    return street_hit * 0.6 + house_hit * 0.5 - org_penalty


def _coords_from_card(page) -> tuple[float, float] | None:
    try:
        el = page.wait_for_selector(_COORD_SEL, timeout=8000)
        m = re.search(r"(-?\d{1,2}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})", el.inner_text())
        if m:
            return float(m.group(1)), float(m.group(2))   # Яндекс печатает lat, lon
    except Exception:
        pass
    m = re.search(r'"coordinates"\s*:\s*\[\s*(3[01]\.\d{3,})\s*,\s*(6[01]\.\d{3,})\s*\]', page.content())
    return (float(m.group(2)), float(m.group(1))) if m else None


def _card_text(page):
    title = subtitle = ""
    try:
        e = page.query_selector(_TITLE_SEL)
        if e:
            title = " ".join(e.inner_text().split())
    except Exception:
        pass
    try:
        e = page.query_selector(_SUB_SEL)
        if e:
            subtitle = " ".join(e.inner_text().split())
    except Exception:
        pass
    return title, subtitle


def _one(page, address: str, near: tuple[float, float] | None) -> YResult | None:
    lat0, lon0 = near or (60.0, 30.3)
    url = (f"https://yandex.ru/maps/?ll={lon0}%2C{lat0}&z=15&mode=search&text="
           + urllib.parse.quote(address))
    page.goto(url, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(2500)

    # 1) Яндекс сразу открыл карточку одного дома
    if "/house/" in page.url or "/geo/" in page.url:
        coords = _coords_from_card(page)
        if coords:
            t, sub = _card_text(page)
            return YResult(coords[0], coords[1], t, sub, page.url, True)

    # 2) список кандидатов — выбираем по совпадению улицы и номера дома
    try:
        snippets = page.query_selector_all(_SNIPPET_SEL)
    except Exception:
        snippets = []
    scored = []
    for sn in snippets[:12]:
        try:
            txt = " ".join(sn.inner_text().split())
        except Exception:
            continue
        if not txt:
            continue
        lines = txt.split("\n") if "\n" in txt else [txt]
        title = lines[0]
        subtitle = " ".join(lines[1:])[:200]
        scored.append((_match_score(address, title, subtitle), title, subtitle, sn))
    scored.sort(key=lambda x: x[0], reverse=True)

    if scored and scored[0][0] >= 0.55:
        _, title, subtitle, sn = scored[0]
        try:
            sn.click(timeout=4000)
            page.wait_for_timeout(2200)
        except Exception:
            pass
        coords = _coords_from_card(page)
        if coords:
            ct, csub = _card_text(page)
            is_house = ("/house/" in page.url) or ("/geo/" in page.url) or (_house_num(ct or title) is not None)
            return YResult(coords[0], coords[1], ct or title, csub or subtitle, page.url, is_house)

    # 3) как было: первый попавшийся coordinates на странице (низкое доверие)
    coords = _coords_from_card(page)
    if coords:
        t, sub = _card_text(page)
        is_house = ("/house/" in page.url) or ("/geo/" in page.url)
        return YResult(coords[0], coords[1], t, sub, page.url, is_house)
    return None


def lookup_batch(items: list[tuple[str, str, tuple[float, float] | None]]
                 ) -> dict[str, YResult | None]:
    if not items:
        return {}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"playwright не установлен: {e}") from e

    out: dict[str, YResult | None] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                      user_agent=_UA, viewport={"width": 1366, "height": 900})
            for key, address, near in items:
                page = ctx.new_page()
                try:
                    out[key] = _one(page, address, near)
                except Exception:
                    out[key] = None
                finally:
                    page.close()
            browser.close()
    except Unavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"headless-браузер недоступен: {e}") from e
    return out
