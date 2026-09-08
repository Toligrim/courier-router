from types import SimpleNamespace

import pytest

from courier_router.address_verification import VERIFICATION_VERSION, VERIFIED, REVIEW, REJECTED
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


def config():
    return SimpleNamespace(geocoder="dadata", llm_provider="none")


def verified_geo(name="г Санкт-Петербург, Комендантский пр-кт, д 53 к 1"):
    return GeoPoint(
        60.015, 30.245, "dadata_verified", name, "verified_house", 0.99,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": VERIFIED, "score": 0.99}},
    )


def review_geo(name="г Санкт-Петербург, Комендантский пр-кт, д 53 к 1"):
    return GeoPoint(
        60.015, 30.245, "dadata_verified", name, "review", 0.72,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": REVIEW, "score": 0.72}},
    )


def rejected_geo():
    return GeoPoint(
        59.9, 29.1, "dadata_verified",
        "Ленинградская обл, г Сосновый Бор, СНТ Приморский, д 53 к 1",
        "review", 0.20,
        raw={"_resolver": {"version": VERIFICATION_VERSION, "status": REJECTED, "candidates": []}},
    )


def test_rejected_address_blocks_without_override(monkeypatch):
    fake = FakeGeocoder(rejected_geo())
    store = FakeStore()
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)

    with pytest.raises(RuntimeError, match="адрес отклонён"):
        geocode_stops(config(), [make_stop()], store, allow_low_confidence=False)

    assert store.saved is None


def test_rejected_address_cannot_be_overridden(monkeypatch):
    fake = FakeGeocoder(rejected_geo())
    store = FakeStore()
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)

    with pytest.raises(RuntimeError, match="не может использоваться в маршруте"):
        geocode_stops(config(), [make_stop()], store, allow_low_confidence=True)

    assert store.saved is None


def test_review_requires_explicit_override(monkeypatch):
    fake = FakeGeocoder(review_geo())
    store = FakeStore()
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)

    with pytest.raises(RuntimeError, match="статус REVIEW"):
        geocode_stops(config(), [make_stop()], store, allow_low_confidence=False)

    assert store.saved is None


def test_review_override_is_allowed_but_not_cached(monkeypatch):
    fresh = review_geo()
    fake = FakeGeocoder(fresh)
    store = FakeStore()
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)

    report = geocode_stops(config(), [make_stop()], store, allow_low_confidence=True)

    assert report[0]["resolver_status"] == REVIEW
    assert report[0]["requires_review"] is True
    assert store.saved is None


def test_cached_review_is_refreshed(monkeypatch):
    cached_review = review_geo("stale review")
    fresh = verified_geo("fresh verified")
    fake = FakeGeocoder(fresh)
    store = FakeStore(cached_review)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)

    report = geocode_stops(config(), [make_stop()], store)

    assert fake.calls == 1
    assert store.saved is fresh
    assert report[0]["normalized"] == "fresh verified"
    assert report[0]["source"] == "dadata:cache-refresh"


def test_legacy_dadata_cache_is_refreshed(monkeypatch):
    legacy = GeoPoint(59.9, 29.1, "dadata", "old wrong", "settlement", 0.55, raw={})
    fresh = verified_geo()
    fake = FakeGeocoder(fresh)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    store = FakeStore(legacy)

    report = geocode_stops(config(), [make_stop()], store)

    assert fake.calls == 1
    assert store.saved is fresh
    assert report[0]["normalized"].startswith("г Санкт-Петербург")
    assert report[0]["source"] == "dadata:cache-refresh"


def test_old_verification_version_is_refreshed(monkeypatch):
    stale = GeoPoint(
        60.015, 30.245, "dadata_verified", "stale", "verified_house", 0.99,
        raw={"_resolver": {"version": VERIFICATION_VERSION - 1, "status": VERIFIED}},
    )
    fresh = verified_geo("fresh")
    fake = FakeGeocoder(fresh)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)
    store = FakeStore(stale)

    report = geocode_stops(config(), [make_stop()], store)

    assert fake.calls == 1
    assert store.saved is fresh
    assert report[0]["normalized"] == "fresh"


def test_verified_cache_is_reused(monkeypatch):
    cached = verified_geo("cached")
    fake = FakeGeocoder(verified_geo("should not be used"))
    store = FakeStore(cached)
    monkeypatch.setattr("courier_router.cli.get_geocoder", lambda c: fake)

    report = geocode_stops(config(), [make_stop()], store)

    assert fake.calls == 0
    assert report[0]["normalized"] == "cached"
    assert report[0]["source"] == "cache"
    assert store.saved is None
