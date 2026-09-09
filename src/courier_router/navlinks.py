"""Deterministic deep-link builder for Yandex Navigator, plus round-trip
validation. No external dependencies. Pure functions.

Docs verified 2026-09-08 (https://yandex.ru/dev/navigator/doc/ru/concepts/navigator-url-params):
  yandexnavi://build_route_on_map?lat_from&lon_from&lat_to&lon_to&lat_via_0&lon_via_0&...
  via index starts at 0 and must be contiguous. No maximum point count is stated.
"""
from __future__ import annotations

import math
import urllib.parse
from dataclasses import dataclass

SPB_LO_BBOX = (58.2, 61.7, 26.5, 36.5)    # lat_min, lat_max, lon_min, lon_max


@dataclass
class RoutePoint:
    role: str                 # "start" | "via" | "finish"
    label: str
    lat: float
    lon: float
    order_id: str = "-"
    two_gis_object_id: str | None = None   # kept for data compatibility; unused
    estimated: bool = False   # coordinate is an estimate, not independently verified


def _c(x: float) -> str:
    return f"{x:.6f}"


# --------------------------------------------------------------------------- Yandex
def build_yandex_url(points: list[RoutePoint]) -> str:
    if len(points) < 2:
        raise ValueError("need at least start and finish")
    vias = points[1:-1]
    parts = [
        ("lat_from", _c(points[0].lat)), ("lon_from", _c(points[0].lon)),
        ("lat_to", _c(points[-1].lat)),  ("lon_to", _c(points[-1].lon)),
    ]
    for i, p in enumerate(vias):
        parts.append((f"lat_via_{i}", _c(p.lat)))
        parts.append((f"lon_via_{i}", _c(p.lon)))
    return "yandexnavi://build_route_on_map?" + "&".join(f"{k}={v}" for k, v in parts)


def validate_yandex_url(url: str, points: list[RoutePoint]) -> list[str]:
    """Parse the URL back and check it matches `points`. Returns list of problems."""
    problems: list[str] = []
    try:
        q = urllib.parse.parse_qs(url.split("?", 1)[1], strict_parsing=True)
    except Exception as e:  # noqa: BLE001
        return [f"URL не парсится: {e}"]
    g = lambda k: q.get(k, [None])[0]  # noqa: E731

    if g("lat_from") != _c(points[0].lat) or g("lon_from") != _c(points[0].lon):
        problems.append("lat_from/lon_from не совпадает со стартом")
    if g("lat_to") != _c(points[-1].lat) or g("lon_to") != _c(points[-1].lon):
        problems.append("lat_to/lon_to не совпадает с финишем")
    if (g("lat_from"), g("lon_from")) != (g("lat_to"), g("lon_to")):
        problems.append("старт и финиш в URL различаются (ожидался кольцевой маршрут)")

    vias = points[1:-1]
    lat_idx = sorted(int(k.rsplit("_", 1)[1]) for k in q if k.startswith("lat_via_"))
    lon_idx = sorted(int(k.rsplit("_", 1)[1]) for k in q if k.startswith("lon_via_"))
    if lat_idx != list(range(len(vias))):
        problems.append(f"индексы lat_via_N не непрерывны 0..{len(vias)-1}: {lat_idx}")
    if lat_idx != lon_idx:
        problems.append("для каждого lat_via_N нет парного lon_via_N")
    for i, p in enumerate(vias):
        if g(f"lat_via_{i}") != _c(p.lat) or g(f"lon_via_{i}") != _c(p.lon):
            problems.append(f"via_{i} ({p.order_id}) координата в URL != таблице")

    for p in points:
        if not (SPB_LO_BBOX[0] <= p.lat <= SPB_LO_BBOX[1] and SPB_LO_BBOX[2] <= p.lon <= SPB_LO_BBOX[3]):
            problems.append(f"точка {p.order_id} вне зоны СПб/Ленобласти — возможна перестановка lat/lon")
    return problems


# --------------------------------------------------------------------------- misc
def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


YANDEX_RESTRICTIONS = [
    "Максимум точек: в актуальной документации Яндекс Навигатора (navigator-url-params, 2026-09-08) "
    "верхний предел не указан — «промежуточных точек на маршруте может быть несколько». «20 точек» из "
    "старых обсуждений в текущей документации не подтверждаются.",
    "Лимит переходов (документ «Ограничения»): переходы по URL со стороннего приложения/сайта считаются "
    "на устройстве за 24 часа; после 5 переходов дальнейшие открытия по URL ведут не в приложение, а на "
    "веб-страницу Навигатора в браузере.",
    "Переходы не учитываются, дословно: если «URL состоит только из URL-схемы yandexnavi» ИЛИ если URL "
    "подписан ключом доступа (client + signature).",
    "ОСТОРОЖНО: документация не уточняет, попадает ли составной маршрутный URL (в нём есть путь "
    "build_route_on_map и query-параметры) под формулировку «состоит только из URL-схемы yandexnavi». "
    "Поэтому нельзя утверждать, что этот URL не учитывается в лимите 5/24ч.",
    "Подпись client+signature требует RSA-ключ по заявке Яндекса. Ссылки формируются без подписи. "
    "Практически: первые несколько открытий за сутки, скорее всего, откроют приложение.",
]
