from types import SimpleNamespace

import pytest

from courier_router.cli import geocode_stops
from courier_router.domain import GeoPoint, Operation, Payment, Stop


class FakeStore:
    def __init__(self, cached=None):
        self.cached = cached
        self.saved = None

    def get_geocode(self, address, district=""):
        return self.cached

    def put_geocode(self, address, district, geo):
        self.saved = geo


class FakeGeocoder:
    def __init__(self, geo):
        self.geo = geo
        self.calls = 0

    def geocode(self, address, district=""):
        self.calls += 1
        return self.geo


def make_stop():
    return Stop(
        source_row=2,
        operation=Operation.DELIVERY,
        order_no=383,
        phone="",
        district="",
        address_raw="ПБ,Коменданский пр-т, д 53, к 1, кв 231",
        access="",
        window=None,
        payment=Payment(),
    )


def test_strong_mismatch_blocks_without_override(monkeypatch):
    geo = GeoPoint(
        59.9, 29.1, "dadata_clean",
        "Ленинградская обл, г Сосновый Бор, СНТ Приморский, д 53 к 1",
        "settlement", 0.30,
        raw={"_resolver": {"status": "strong_mismatch", "candidates": []}},
    )
    fake = FakeGeocoder(geo)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    c = SimpleNamespace(geocoder="dadata", llm_provider="none")

    with pytest.raises(RuntimeError, match="сильно расходится"):
        geocode_stops(c, [make_stop()], FakeStore(), allow_low_confidence=False)


def test_legacy_dadata_cache_is_refreshed(monkeypatch):
    legacy = GeoPoint(59.9, 29.1, "dadata", "old wrong", "settlement", 0.55, raw={})
    fresh = GeoPoint(
        60.015, 30.245, "dadata_suggest",
        "г Санкт-Петербург, Комендантский пр-кт, д 53 к 1",
        "house", 0.99,
        raw={"_resolver": {"status": "resolved", "score": 0.99}},
    )
    fake = FakeGeocoder(fresh)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    c = SimpleNamespace(geocoder="dadata", llm_provider="none")
    store = FakeStore(legacy)

    report = geocode_stops(c, [make_stop()], store)

    assert fake.calls == 1
    assert store.saved is fresh
    assert report[0]["normalized"].startswith("г Санкт-Петербург")
    assert report[0]["source"] == "dadata:cache-refresh"
