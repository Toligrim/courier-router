from types import SimpleNamespace

import pytest

from courier_router.address_verification import VERIFICATION_VERSION, VERIFIED, REJECTED
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


def test_rejected_address_blocks_without_override(monkeypatch):
    geo = GeoPoint(
        59.9, 29.1, "dadata_verified",
        "Ленинградская обл, г Сосновый Бор, СНТ Приморский, д 53 к 1",
        "review", 0.20,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": REJECTED, "candidates": []}},
    )
    fake = FakeGeocoder(geo)
    store = FakeStore()
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    c = SimpleNamespace(geocoder="dadata", llm_provider="none")

    with pytest.raises(RuntimeError, match="не прошёл проверку"):
        geocode_stops(c, [make_stop()], store, allow_low_confidence=False)

    assert store.saved is None


def test_legacy_dadata_cache_is_refreshed(monkeypatch):
    legacy = GeoPoint(59.9, 29.1, "dadata", "old wrong", "settlement", 0.55, raw={})
    fresh = GeoPoint(
        60.015, 30.245, "dadata_verified",
        "г Санкт-Петербург, Комендантский пр-кт, д 53 к 1",
        "verified_house", 0.99,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": VERIFIED, "score": 0.99}},
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


def test_old_verification_version_is_refreshed(monkeypatch):
    stale = GeoPoint(
        60.015, 30.245, "dadata_verified", "stale", "verified_house", 0.99,
        raw={"_resolver": {"version": VERIFICATION_VERSION - 1, "status": VERIFIED}},
    )
    fresh = GeoPoint(
        60.016, 30.246, "dadata_verified", "fresh", "verified_house", 0.99,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": VERIFIED, "score": 0.99}},
    )
    fake = FakeGeocoder(fresh)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    c = SimpleNamespace(geocoder="dadata", llm_provider="none")
    store = FakeStore(stale)

    report = geocode_stops(c, [make_stop()], store)

    assert fake.calls == 1
    assert store.saved is fresh
    assert report[0]["normalized"] == "fresh"


def test_rejected_override_is_not_cached(monkeypatch):
    rejected = GeoPoint(
        60.015, 30.245, "dadata_verified", "candidate", "review", 0.20,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": REJECTED}},
    )
    fake = FakeGeocoder(rejected)
    store = FakeStore()
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    c = SimpleNamespace(geocoder="dadata", llm_provider="none")

    report = geocode_stops(c, [make_stop()], store, allow_low_confidence=True)

    assert report[0]["resolver_status"] == REJECTED
    assert store.saved is None
