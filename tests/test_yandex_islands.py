"""Island addresses from route sheets must resolve to the requested house."""
from types import SimpleNamespace

import pytest

from courier_router import cli, yandex_maps as ym
from courier_router.domain import GeoPoint
from courier_router.geocode import normalize_address_input


ADDRESS = "г Санкт-Петербург, Канонерский ост-в, д 7, кв. 243"


@pytest.mark.parametrize("marker", ["ост-в", "ост-в.", "ОСТ-В", "ост - в"])
def test_island_abbreviation_is_expanded_before_search(marker):
    source = ADDRESS.replace("ост-в", marker)
    assert normalize_address_input(source, "Кировский") == (
        "г Санкт-Петербург, Канонерский остров, д 7"
    )


def test_island_abbreviation_does_not_rewrite_building_suffix():
    source = "Санкт-Петербург, Канонерский ост-в, д 7, литера ОСТ-В"
    assert normalize_address_input(source) == (
        "Санкт-Петербург, Канонерский остров, д 7, литера ОСТ-В"
    )


def _card(title="Канонерский остров, 7", subtitle="Санкт-Петербург"):
    return ym.YResult(59.9108, 30.2199, title, subtitle,
                      "https://yandex.ru/maps/house/example/", True)


@pytest.mark.parametrize("title,subtitle,status", [
    ("Канонерский остров, 7", "Санкт-Петербург", "verified"),
    ("Канонерский тоннель, 12к2", "Санкт-Петербург", "strong_mismatch"),
    ("Канонерский остров, 8", "Санкт-Петербург", "strong_mismatch"),
    ("Белый остров, 7", "Санкт-Петербург", "strong_mismatch"),
    ("Канонерский остров, 7", "Москва", "strong_mismatch"),
])
def test_island_house_is_verified_but_other_addresses_are_rejected(
    monkeypatch, title, subtitle, status
):
    card = _card(title, subtitle)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    geo = cli.YandexMapsGeocoder().geocode(ADDRESS, "Кировский")
    assert geo.raw["_resolver"]["status"] == status
    assert geo.normalized_address == ADDRESS


def test_old_island_result_is_refreshed_and_original_delivery_details_survive(
    monkeypatch, tmp_path
):
    store = cli.Storage(str(tmp_path / "cache.db"))
    config = SimpleNamespace(geocoder="yandex", llm_provider="none")
    stop = SimpleNamespace(source_row=13, order_no=529, address_raw=ADDRESS,
                           district="Кировский", warnings=[], geo=None)
    # A previous result accepted with the old normalizer must not bypass the fix.
    store.put_geocode(ADDRESS, stop.district, GeoPoint(
        59.91, 30.22, "yandex_maps", ADDRESS, "house", 0.65,
        raw={"_resolver": {"primary": True, "version": cli.RESOLVER_VERSION,
                           "normalizer_version": 2, "dictionary_version": None,
                           "status": "review"}},
    ))
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {
        key: _card() for key, query, _ in jobs
        if query == "Санкт-Петербург, Канонерский остров, д 7"
    })
    try:
        report = cli.geocode_stops(config, [stop], store)
        assert report[0]["resolver_status"] == "verified"
        assert report[0]["source"] == "yandex:cache-refresh"
        assert stop.address_raw == ADDRESS
        assert stop.geo.normalized_address == ADDRESS
        assert cli.geocode_stops(config, [stop], store)[0]["source"] == "cache"
    finally:
        store.con.close()
