"""
Полный прогон pipeline без сети: подменяем геокодер и роутер стабами,
проверяем, что cmd_plan строит все четыре артефакта из sample.xlsx.

Держит deterministic-ядро (parser -> OR-Tools -> renderer -> reports) под тестом,
пока живые DaData/ORS недоступны.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from PIL import Image

from courier_router import cli
from courier_router.domain import GeoPoint
from courier_router.storage import Storage

# Реалистичные координаты для адресов sample.xlsx (СПб / Ленобласть).
COORDS = {
    "костюшко": (59.849354, 30.294428),          # depot
    "ленинский пр-кт, д 72": (59.862092, 30.181432),
    "танкиста хрустицкого": (59.835949, 30.257144),
    "деревня пески": (59.776715, 30.080067),
    "красное село": (59.735857, 30.078909),
}


def _match(address: str) -> tuple[float, float]:
    low = address.lower()
    for key, xy in COORDS.items():
        if key in low:
            return xy
    raise AssertionError(f"stub geocoder: неизвестный адрес {address!r}")


class StubGeocoder:
    def geocode(self, address: str, district: str = "") -> GeoPoint:
        lat, lon = _match(address)
        return GeoPoint(lat=lat, lon=lon, provider="stub",
                        normalized_address=address, precision="nearest_house",
                        confidence=0.92)


def _haversine_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class StubRouter:
    name = "stub"

    def matrix(self, coords):
        n = len(coords)
        dist = [[0.0] * n for _ in range(n)]
        dur = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                m = _haversine_m(coords[i], coords[j]) * 1.35  # дорожный коэффициент
                dist[i][j] = m
                dur[i][j] = m / 8.33  # ~30 км/ч
        return dur, dist

    def geometry(self, coords):
        # Ломаная прямыми отрезками между точками маршрута.
        out = []
        for (la1, lo1), (la2, lo2) in zip(coords, coords[1:]):
            for t in (0.0, 0.25, 0.5, 0.75):
                out.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
        out.append(coords[-1])
        return out


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_geocoder", lambda c: StubGeocoder())
    monkeypatch.setattr(cli, "get_router", lambda c: StubRouter())
    # изолируем кэш геокодера — тест не должен трогать data/cache/courier-router.db
    db = str(tmp_path / "cache.db")
    monkeypatch.setattr(cli, "Storage", lambda _path: Storage(db))


def _args(xlsx, out):
    import argparse
    return argparse.Namespace(
        xlsx=str(xlsx), date="2026-09-08", depart="10:00", end="depot",
        output=str(out), allow_low_confidence=False,
    )


def test_full_plan_offline(tmp_path, stubbed):
    sample = Path("data/input/sample.xlsx")
    if not sample.exists():
        import scripts.make_sample_xlsx  # noqa: F401  (генерирует файл как побочный эффект)
    assert sample.exists(), "нет data/input/sample.xlsx — запусти scripts/make_sample_xlsx.py"

    out = tmp_path / "run"
    rc = cli.cmd_plan(_args(sample, out))
    assert rc == 0

    for name in ("itinerary.txt", "route.json", "route.png", "geocoding-report.json"):
        f = out / name
        assert f.exists() and f.stat().st_size > 0, f"нет артефакта {name}"

    data = json.loads((out / "route.json").read_text("utf-8"))
    assert data["feasible"] is True
    seq = [v["stop"]["order_no"] for v in data["visits"]]
    assert len(seq) == 4 and set(seq) == {18452, 18477, 18491, 18500}

    # Порядок должен уважать временные окна: 18452 (10-12) раньше 18500 (15-21).
    assert seq.index(18452) < seq.index(18500)

    img = Image.open(out / "route.png")
    assert img.size == (1600, 1200)
    assert len(img.getcolors(maxcolors=100000) or [(0, 0)]) > 5  # не одноцветная заглушка


def test_report_has_no_low_confidence_warnings(tmp_path, stubbed):
    out = tmp_path / "run"
    cli.cmd_plan(_args(Path("data/input/sample.xlsx"), out))
    report = json.loads((out / "geocoding-report.json").read_text("utf-8"))
    assert all(r["confidence"] >= 0.80 for r in report)
