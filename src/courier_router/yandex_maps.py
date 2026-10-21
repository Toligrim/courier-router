"""Headless Yandex Maps toponym lookup (sync Playwright).
Used by the primary geocoder and the optional coordinate cross-check / fallback.

lookup_batch([(key, address, (near_lat, near_lon)), ...]) -> {key: YResult | None}

When Yandex returns a disambiguation list (several candidates), the matching
item is chosen by street name + house number from the query — not the first row.
"""
from __future__ import annotations

import os
import math
import re
import urllib.parse
from dataclasses import dataclass
from .address_dictionary import street_conflict

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
_GENERIC = {"улица", "ул", "проспект", "пр", "пр-т", "пркт", "пр-кт", "переулок", "пер",
            "шоссе", "ш", "набережная", "наб", "бульвар", "б-р", "линия", "проезд",
            "пл", "площадь",
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
    s = re.sub(r"(?<=[0-9a-zа-яё])-(?=[a-zа-яё])", "", (s or "").lower())
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", s.replace("ё", "е")).split())


_MARK = (r"ул|улица|пр-т|пр|пр-?кт|проспект|пл|площадь|пер|переулок|ш|шоссе|наб|набережная|"
         r"б-р|бульвар|линия|проезд|аллея|туп|тупик")
_STREET_AFTER = re.compile(rf"(?:^|,)\s*(?:{_MARK})\.?\s+([^,]+)", re.I)
_STREET_AFTER_ANY = re.compile(rf"(?<!\w)(?P<marker>{_MARK})\.?\s+(?P<name>[^,]+)", re.I)
_STREET_BEFORE = re.compile(rf"(?:^|,)\s*([^,]+?)\s+(?:{_MARK})\b", re.I)


def _clean_street_part(part: str) -> set[str]:
    # Building letters may contain more than one character (for example, "литера
    # ПЛА"). Remove the complete suffix before tokenizing the remaining street.
    part = re.sub(r"\bлит(?:ера)?(?=\.|\s|№)\.?\s*№?\s*[0-9а-яa-z]+\b", " ", part, flags=re.I)
    part = re.sub(r"\b(?:д|дом|кв|литера?|корп(?:ус)?|стр(?:оение)?)\.?\s*(?:[0-9]+[а-яa-z/]*|[а-яa-z]\b)",
                  " ", part, flags=re.I)
    return {t for t in _norm(part).split() if t not in _GENERIC and not t.isdigit()
            and (len(t) >= 3 or re.fullmatch(r"\d+[а-яa-z]+", t))}


def _street_tokens(s: str) -> set[str]:
    s = s or ""
    # Prefer the explicit prefix form. It can follow a city without a comma,
    # as in "Ломоносов ул Красного Флота". If that marker is actually a suffix
    # ("Театральная площадь д 16"), its remainder has no street-name tokens and
    # the suffix parser below gets the address instead.
    m = _STREET_AFTER.search(s)
    if m:
        part = m.group(1)
        return _clean_street_part(part)

    middle = _STREET_AFTER_ANY.search(s)
    if middle:
        part = middle.group("name")
        if middle.group("marker").lower() == "линия":
            ordinal = re.search(r"(?<!\w)(\d+[- ]?[а-я])\s*$", s[:middle.start()], re.I)
            if ordinal:
                part = ordinal.group(1) + " " + part
        tokens = _clean_street_part(part)
        if tokens:
            return tokens

    m = _STREET_BEFORE.search(s)
    if not m:
        return _clean_street_part(s)
    part = m.group(1)
    # In "1-я линия Васильевского острова" the name continues after "линия".
    if re.search(r"\bлиния\b", m.group(0), re.I):
        part += " " + s[m.end():].split(",", 1)[0]
    return _clean_street_part(part)


def _prefix_locality_before_street(s: str) -> str:
    """Return a locality preceding an uncommaed prefix street marker, if clear."""
    s = s or ""
    middle = _STREET_AFTER_ANY.search(s)
    if not middle or not _clean_street_part(middle.group("name")):
        return ""
    # Only inspect the text immediately before the street marker. Earlier comma
    # components may be a region or territory and have their own review rules.
    prefix = s[:middle.start()].rsplit(",", 1)[-1].strip()
    if not prefix:
        return ""
    if middle.group("marker").lower() == "линия":
        prefix = re.sub(r"(?:^|\s)\d+[- ]?[а-я]\s*$", "", prefix, flags=re.I)
    return prefix


def _house_num(s: str) -> str | None:
    # A compact block (17к2) is not a house letter (17а). Ignore postal codes
    # and the coordinate/action text appended by the Yandex card subtitle.
    s = re.split(r"координаты\s*:", s or "", maxsplit=1, flags=re.I)[0]
    s = re.sub(r"(?:\b|(?<=\d))\s*(?:к|корп(?:ус)?|стр(?:оение)?)\.?\s*\d+[а-яa-z]?\b",
               " ", s, flags=re.I)
    number = r"[0-9]{1,4}(?:/[0-9]+)?(?:[а-яa-z](?![а-яa-z0-9]))?"
    m = re.search(rf"(?:^|[ ,])(?:д|дом)\.?\s*({number})", s, re.I)
    if m:
        return m.group(1).lower()
    nums = re.findall(rf"\b({number})(?=\b|к[0-9])", s, re.I)
    return nums[-1].lower() if nums else None


def _match_score(query: str, title: str, subtitle: str) -> float:
    if street_conflict(query, title):
        return -1.0
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
            lat, lon = float(m.group(1)), float(m.group(2))
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon  # Яндекс печатает lat, lon
    except Exception:
        pass
    # Coordinates elsewhere in the HTML may belong to the map centre or another result.
    return None


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
    page.wait_for_selector(f"{_TITLE_SEL}, {_SNIPPET_SEL}", state="visible", timeout=15000)

    # 1) Яндекс сразу открыл карточку одного дома
    if "/house/" in page.url or "/geo/" in page.url:
        coords = _coords_from_card(page)
        if coords:
            t, sub = _card_text(page)
            return YResult(coords[0], coords[1], t, sub, page.url, _house_num(t) is not None)

    # 2) список кандидатов — выбираем по совпадению улицы и номера дома
    try:
        snippets = page.query_selector_all(_SNIPPET_SEL)
    except Exception:
        snippets = []
    scored = []
    for sn in snippets[:12]:
        try:
            txt = sn.inner_text().strip()
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
            page.wait_for_selector(_COORD_SEL, state="visible", timeout=15000)
        except Exception:
            return None
        coords = _coords_from_card(page)
        if coords:
            ct, csub = _card_text(page)
            is_house = (("/house/" in page.url) or ("/geo/" in page.url)) and _house_num(ct) is not None
            return YResult(coords[0], coords[1], ct or title, csub or subtitle, page.url, is_house)

    # 3) Only a visible card can provide coordinates; unknown precision requires review.
    coords = _coords_from_card(page)
    if coords:
        t, sub = _card_text(page)
        is_house = (("/house/" in page.url) or ("/geo/" in page.url)) and _house_num(t) is not None
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
