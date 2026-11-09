"""Hyphenated house numbers must survive lookup, matching and cached results."""
from types import SimpleNamespace

import pytest

from courier_router import cli, yandex_maps as ym
from courier_router.domain import GeoPoint
from courier_router.geocode import YandexMapsGeocoder, _same_house_number


ADDRESS = "г Санкт-Петербург, Московский пр-кт, д 183-185 литера А"


@pytest.mark.parametrize("text,expected", [
    ("Московский пр-кт, д 183-185 литера А", "183-185"),
    ("Московский проспект, 183-185", "183-185"),
    ("Московский проспект, 183 – 185, подъезд 2", "183-185"),
    ("Московский проспект, д 183—185 к 2", "183-185"),
    ("Московский проспект, 183‑185 стр 1", "183-185"),
    ("Московский проспект, 183−185А", "183-185а"),
    ("5-й Предпортовый проезд, 6к2", "6"),
    ("2-я Советская улица, 12/3", "12/3"),
])
def test_complete_house_number_is_extracted(text, expected):
    assert ym._house_num(text) == expected


@pytest.mark.parametrize("a,b,expected", [
    ("183", "183-185", False),
    ("183-185", "183", False),
    ("183-185", "185", False),
    ("183-185", "183-187", False),
    ("27", "27в", True),
    ("12", "12/3", True),
])
def test_house_suffix_tolerance_does_not_accept_partial_compound_numbers(a, b, expected):
    assert _same_house_number(a, b) is expected


def _card(title="Московский проспект, 183-185", subtitle="Санкт-Петербург"):
    return ym.YResult(59.85, 30.32, title, subtitle,
                      "https://yandex.ru/maps/house/example/", True)


@pytest.mark.parametrize("title,subtitle,status", [
    ("Московский проспект, 183-185", "Санкт-Петербург", "review"),
    ("Московский проспект, 183–185 литера А", "Санкт-Петербург", "verified"),
    ("Московский проспект, 183", "Санкт-Петербург", "strong_mismatch"),
    ("Московский проспект, 185", "Санкт-Петербург", "strong_mismatch"),
    ("Московский проспект, 183-187", "Санкт-Петербург", "strong_mismatch"),
    ("Ленинский проспект, 183-185", "Санкт-Петербург", "strong_mismatch"),
    ("Московский проспект, 183-185 литера Б", "Санкт-Петербург", "strong_mismatch"),
    ("Московский проспект, 183-185", "Москва", "strong_mismatch"),
])
def test_same_compound_house_is_accepted_and_conflicting_addresses_are_rejected(
    monkeypatch, title, subtitle, status
):
    card = _card(title, subtitle)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    result = YandexMapsGeocoder().geocode(ADDRESS, "Московский")
    assert result.raw["_resolver"]["status"] == status
    assert result.normalized_address == ADDRESS


def test_exact_compound_house_ranks_above_either_endpoint():
    query = "Санкт-Петербург, Московский пр-кт, д 183-185 литера А"
    exact = ym._match_score(query, "Московский проспект, 183-185", "Санкт-Петербург")
    assert exact > ym._match_score(query, "Московский проспект, 183", "Санкт-Петербург")
    assert exact > ym._match_score(query, "Московский проспект, 185", "Санкт-Петербург")


def test_pre_fix_final_and_raw_caches_are_refreshed_before_building(monkeypatch, tmp_path):
    config = SimpleNamespace(geocoder="yandex", llm_provider="none")
    stop = SimpleNamespace(source_row=8, order_no=587, address_raw=ADDRESS,
                           district="Московский", warnings=[], geo=None)
    store = cli.Storage(str(tmp_path / "cache.db"))
    query = "Санкт-Петербург, Московский пр-кт, д 183-185 литера А"
    store.put_geocode(ADDRESS, stop.district, GeoPoint(
        59.85, 30.32, "yandex_maps", ADDRESS, "house", 0.95,
        raw={"_resolver": {"primary": True, "version": 5,
                           "normalizer_version": 3, "dictionary_version": None,
                           "status": "verified"}},
    ))
    store.con.execute(cli._YANDEX_CACHE_SCHEMA)
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found,extractor_version) "
        "VALUES(?,?,?,?,?,?,?,1,2)",
        (cli.Storage.key(query), 59.85, 30.32, "Московский проспект, 185",
         "Санкт-Петербург", "old", 1),
    )
    store.con.commit()
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {
        key: _card() for key, _, _ in jobs
    })
    try:
        report = cli.geocode_stops(config, [stop], store)
        assert report[0]["resolver_status"] == "review"
        assert report[0]["resolver_candidates"] == [{"value": "Московский проспект, 183-185"}]
        assert report[0]["source"] == "yandex:cache-refresh"
        assert stop.address_raw == ADDRESS
        assert stop.geo.normalized_address == ADDRESS

        def no_network(_jobs):
            pytest.fail("The newly validated result should be cached")

        monkeypatch.setattr(ym, "lookup_batch", no_network)
        assert cli.geocode_stops(config, [stop], store)[0]["source"] == "cache"
    finally:
        store.con.close()
