"""Сверка координат со вторым источником (Яндекс Карты headless).

Правило: расхождение больше порога и Яндекс дал дом → берём координаты Яндекса,
помечаем на сверку, но маршрут строится (raise нет).
"""
from __future__ import annotations

from types import SimpleNamespace

from courier_router import cli
from courier_router import yandex_maps as ym
from courier_router.domain import GeoPoint
from courier_router.storage import Storage


def _stop(row, addr, lat, lon):
    return SimpleNamespace(
        source_row=row, address_raw=addr, district="", warnings=[],
        coord_status="ok", coord_note="",
        geo=GeoPoint(lat=lat, lon=lon, provider="dadata_suggest",
                     normalized_address=addr, precision="nearest_house", confidence=0.99),
    )


def _report_row(s):
    return {"order_no": s.source_row, "raw": s.address_raw, "normalized": s.geo.normalized_address,
            "lat": s.geo.lat, "lon": s.geo.lon, "requires_review": False,
            "dadata_lat": s.geo.lat, "dadata_lon": s.geo.lon,
            "coord_source": "dadata", "yandex_lat": None, "yandex_lon": None, "delta_m": None}


def _run(monkeypatch, tmp_path, lookup_impl, warn_m=75.0):
    monkeypatch.setattr(ym, "lookup_batch", lookup_impl)
    store = Storage(str(tmp_path / "t.db"))
    c = SimpleNamespace(yandex_xcheck_warn_m=warn_m)
    stops = [
        _stop(1, "Санкт-Петербург, Ленинский пр-кт, д 72 к 1", 59.86156, 30.18205),
        _stop(2, "Санкт-Петербург, ул Танкиста Хрустицкого, д 102", 59.83593, 30.25701),
    ]
    report = [_report_row(s) for s in stops]
    cli._yandex_crosscheck(c, stops, report, store)
    return stops, report


def test_small_delta_keeps_dadata(monkeypatch, tmp_path):
    def lookup(items):
        # ~10 м в стороне от DaData
        return {items[0][0]: ym.YResult(59.86165, 30.18205, "дом 72к1", "Ленинский пр-кт", "u", True),
                items[1][0]: ym.YResult(59.83593, 30.25701, "дом 102", "Танкиста Хрустицкого", "u", True)}
    stops, report = _run(monkeypatch, tmp_path, lookup)
    assert report[0]["coord_source"] == "dadata"
    assert (stops[0].geo.lat, stops[0].geo.lon) == (59.86156, 30.18205)
    assert report[0]["delta_m"] is not None and report[0]["delta_m"] < 75
    assert report[0]["requires_review"] is False


def test_large_delta_takes_yandex_and_flags_without_raising(monkeypatch, tmp_path):
    def lookup(items):
        return {items[0][0]: ym.YResult(59.8630, 30.1850, "дом 72к1", "Ленинский пр-кт, 72к1", "u", True),
                items[1][0]: ym.YResult(59.83593, 30.25701, "дом 102", "", "u", True)}
    stops, report = _run(monkeypatch, tmp_path, lookup)
    # координаты первой точки заменены на яндексовы
    assert (round(stops[0].geo.lat, 4), round(stops[0].geo.lon, 4)) == (59.8630, 30.1850)
    assert report[0]["coord_source"] == "yandex_crosscheck"
    assert report[0]["requires_review"] is True
    assert report[0]["delta_m"] > 75
    assert stops[0].coord_status == "review"
    assert "Яндекс Карт" in report[0]["coord_note"]
    assert stops[0].warnings == []
    # вторая точка не тронута
    assert report[1]["coord_source"] == "dadata"


def test_large_delta_but_not_house_keeps_dadata(monkeypatch, tmp_path):
    def lookup(items):
        return {items[0][0]: ym.YResult(59.8630, 30.1850, "Ленинский проспект", "улица", "u", False),
                items[1][0]: None}
    stops, report = _run(monkeypatch, tmp_path, lookup)
    assert report[0]["coord_source"] == "dadata"
    assert (stops[0].geo.lat, stops[0].geo.lon) == (59.86156, 30.18205)
    assert report[0]["requires_review"] is True
    assert stops[0].coord_status == "review" and "расход" in stops[0].coord_note.lower()
    assert stops[1].coord_status == "ok" and not stops[1].coord_note


def test_headless_unavailable_is_graceful(monkeypatch, tmp_path):
    def lookup(items):
        raise ym.Unavailable("playwright не установлен")
    stops, report = _run(monkeypatch, tmp_path, lookup)
    assert all(r["coord_source"] == "dadata" for r in report)
    assert all(r["requires_review"] is False for r in report)
    assert all(s.coord_status == "ok" for s in stops) and stops[0].warnings == []


# ---- DaData вообще не нашла адрес → координата от Яндекс Карт как основной источник ----

class _RaisingGeocoder:
    def geocode(self, address, district=""):
        raise ValueError("DaData не вернула адрес (clean_disabled)")


def _cfg():
    return SimpleNamespace(llm_provider="none", geocoder="dadata",
                           yandex_crosscheck=True, yandex_xcheck_warn_m=75.0)


def _plain_stop(row, addr):
    return SimpleNamespace(source_row=row, order_no=row, address_raw=addr, district="",
                           warnings=[], coord_status="ok", coord_note="", geo=None)


def test_dadata_failure_falls_back_to_yandex(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_geocoder", lambda c: _RaisingGeocoder())
    monkeypatch.setattr(ym, "lookup_batch", lambda items: {
        items[0][0]: ym.YResult(59.78306, 30.13506, "Колхозная улица, 12",
                                "Колхозная улица, 12, территория Горелово", "u", True)})
    store = Storage(str(tmp_path / "t.db"))
    s = _plain_stop(7, "Санкт-Петербург, тер Горелово, ул Колхозная, д 12 литера А / частный дом")

    report = cli.geocode_stops(_cfg(), [s], store)   # не должно бросить

    assert report[0]["coord_source"] == "yandex_fallback"
    assert (round(s.geo.lat, 5), round(s.geo.lon, 5)) == (59.78306, 30.13506)
    assert report[0]["requires_review"] is True
    assert s.coord_status == "review" and "Яндекс Карт" in s.coord_note
    assert s.warnings == []


def test_dadata_and_yandex_both_fail_still_raises(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setattr(cli, "get_geocoder", lambda c: _RaisingGeocoder())
    monkeypatch.setattr(ym, "lookup_batch", lambda items: {items[0][0]: None})
    store = Storage(str(tmp_path / "t.db"))
    s = _plain_stop(7, "мусорный адрес которого нет")
    with pytest.raises(RuntimeError, match="Строка 7"):
        cli.geocode_stops(_cfg(), [s], store)
