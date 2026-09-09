"""Headless Yandex Maps toponym lookup (sync Playwright). Used by build_route.py for
(a) escalating addresses DaData can't pin to a building, and (b) an optional
second-source cross-check. NOT the primary geocoder — see pipeline/README.md.

lookup_batch([(key, address, (near_lat, near_lon)), ...]) -> {key: YResult | None}
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


@dataclass
class YResult:
    lat: float
    lon: float
    title: str
    subtitle: str
    url: str
    is_house: bool           # resolved to a /house/ or /geo/ toponym (not an org / street)


class Unavailable(RuntimeError):
    pass


def _one(page, address: str, near: tuple[float, float] | None) -> YResult | None:
    lat0, lon0 = near or (60.0, 30.3)
    url = (f"https://yandex.ru/maps/?ll={lon0}%2C{lat0}&z=16&mode=search&text="
           + urllib.parse.quote(address))
    page.goto(url, wait_until="domcontentloaded", timeout=40000)
    coords = None
    try:
        el = page.wait_for_selector(_COORD_SEL, timeout=15000)
        m = re.search(r"(-?\d{1,2}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})", el.inner_text())
        if m:
            coords = (float(m.group(1)), float(m.group(2)))   # Yandex prints lat, lon
    except Exception:
        page.wait_for_timeout(3500)
        m = re.search(r'"coordinates"\s*:\s*\[\s*(3[01]\.\d{3,})\s*,\s*(6[01]\.\d{3,})\s*\]',
                      page.content())
        if m:
            coords = (float(m.group(2)), float(m.group(1)))
    if not coords:
        return None
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
    final = page.url
    is_house = ("/house/" in final) or ("/geo/" in final)
    return YResult(coords[0], coords[1], title, subtitle, final, is_house)


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
