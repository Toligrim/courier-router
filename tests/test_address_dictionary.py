import json
from types import SimpleNamespace

import pytest

from courier_router.address_dictionary import AddressDictionary, street_conflict, street_part
from courier_router.geocode import YandexMapsGeocoder, normalize_address_input
from courier_router import cli, yandex_maps as ym
from courier_router.storage import Storage


@pytest.mark.parametrize("query, expected, status", [
    ("СПб, ул Горохвая, д 68 кв 20", "Санкт-Петербург, улица Гороховая, д 68", "corrected"),
    ("Санкт-Петербург, Горохвая улица, д 68 к 2 литера А", "Санкт-Петербург, улица Гороховая, д 68 к 2 литера А", "corrected"),
    ("Санкт-Петербург, ул Гороховая, д 68", None, "exact"),
    ("Ленинградская обл, г Всеволожск, ул Совесткая, д 1", "Ленинградская обл, г Всеволожск, улица Советская, д 1", "corrected"),
    ("Ленинградская обл, ул Совесткая, д 1", None, "ambiguous"),
    ("Московская область, ул Горохвая, д 68", None, "unknown_locality"),
    ("Санкт-Петербург, деревня Неизвестная, ул Горохвая, д 68", None, "unknown_locality"),
    ("Санкт-Петербург, д Неизвестная, ул Горохвая, д 68", None, "unknown_locality"),
    ("Санкт-Петербург, 1-е Новое, ул Горохвая, д 68", None, "unknown_locality"),
    ("ул Горохвая, д 68", None, "missing_locality"),
    ("Санкт-Петербург, линия 16-я Васильевского острова, д 68", None, "not_found"),
    ("Санкт-Петербург, Малая Морская улица, д 68", None, "exact"),
    ("Санкт-Петербург, неизвестный адрес без улицы", None, "unparsed"),
    ("Санкт-Петербург, проспект Гороховая, д 68", None, "not_found"),
])
def test_contextual_normalization(dictionary_root, query, expected, status):
    dictionary = AddressDictionary.open_current(dictionary_root)
    prepared = normalize_address_input(query)
    result = dictionary.normalize(prepared)
    assert result.status == status
    assert result.query == (expected or prepared)
    assert result.original == prepared
    assert result.dictionary_version == "20261020"


@pytest.mark.parametrize("query", [
    "Санкт-Петербург, ул Горохвая 68к2", "Санкт-Петербург, ул Горохвая д 68/3 к 2 стр 1",
    "Санкт-Петербург, Горохвая улица, 68а",
])
def test_correction_preserves_house_suffix(dictionary_root, query):
    result = AddressDictionary.open_current(dictionary_root).normalize(query)
    assert result.status == "corrected"
    assert result.query == query.replace("ул Горохвая", "улица Гороховая").replace("Горохвая улица", "улица Гороховая")


@pytest.mark.parametrize("a,b", [
    ("ул Ленина, д 10", "проспект Ленина, 10"),
    ("15-я линия Васильевского острова, д 80", "16-я линия Васильевского острова, 80"),
    ("Большая Морская улица, д 1", "Малая Морская улица, 1"),
])
def test_hard_street_distinctions(a, b):
    assert street_conflict(a, b)
    assert ym._match_score(a, b, "Санкт-Петербург") < 0


def _stop(address):
    return SimpleNamespace(address_raw=address, district="Приморский", source_row=1,
                           order_no=1, warnings=[], geo=None)


def test_dictionary_yandex_and_cache_generation(dictionary_root, gar_factory, tmp_path, monkeypatch):
    from courier_router.fns_update import update
    config = SimpleNamespace(geocoder="yandex", llm_provider="none", address_dictionary_dir=str(dictionary_root))
    calls = []
    def lookup(jobs):
        calls.extend(jobs)
        return {key: ym.YResult(59.9, 30.3, "Гороховая улица, 68", "Санкт-Петербург", "https://yandex.ru/maps/house/1/", True)
                for key, _, _ in jobs}
    monkeypatch.setattr(ym, "lookup_batch", lookup)
    address = "Санкт-Петербург, ул Горохвая, д 68 кв 20"
    store = Storage(str(tmp_path / "cache.db"))
    try:
        report = cli.geocode_stops(config, [_stop(address)], store)
        assert "Гороховая" in calls[0][1] and "кв" not in calls[0][1]
        assert report[0]["resolver_status"] == "verified"
        assert report[0]["normalized"] == address
        assert report[0]["normalization"]["status"] == "corrected"
        assert cli.geocode_stops(config, [_stop(address)], store)[0]["source"] == "cache"
        update(dictionary_root, source=gar_factory("20261021"), version="20261021", min_streets=1)
        refreshed = cli.geocode_stops(config, [_stop(address)], store)
        assert refreshed[0]["source"] == "yandex:cache-refresh"
        assert refreshed[0]["dictionary_version"] == "20261021"
        assert len(calls) == 1  # Same search query can reuse the Yandex card.
    finally:
        store.con.close()


def test_manual_pin_wins_over_dictionary(dictionary_root, tmp_path, monkeypatch):
    config = SimpleNamespace(geocoder="yandex", llm_provider="none", address_dictionary_dir=str(dictionary_root))
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: pytest.fail("Pinned coordinates must not trigger Yandex"))
    store = Storage(str(tmp_path / "cache.db"))
    try:
        address = "Санкт-Петербург, ул Горохвая, д 68"
        store.put_alias(address, "Приморский", 59.92, 30.31)
        result = cli.geocode_stops(config, [_stop(address)], store)[0]
        assert result["source"] == "alias"
        assert result["lat"] == 59.92
    finally:
        store.con.close()


def test_missing_dictionary_keeps_yandex(tmp_path):
    config = SimpleNamespace(geocoder="yandex", address_dictionary_dir=str(tmp_path / "missing"))
    geocoder = cli.get_geocoder(config)
    assert isinstance(geocoder, YandexMapsGeocoder)
    assert geocoder.dictionary_version is None
    assert "Горохвая" in geocoder.query("СПб, ул Горохвая, д 68")


def test_dictionary_cli_status_and_normalize(dictionary_root, capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["dictionary", "normalize", "--root", str(dictionary_root),
                              "СПб, ул Горохвая, д 68"])
    cli.cmd_dictionary(args)
    assert json.loads(capsys.readouterr().out)["status"] == "corrected"
    args = parser.parse_args(["dictionary", "status", "--root", str(dictionary_root)])
    cli.cmd_dictionary(args)
    assert json.loads(capsys.readouterr().out)["active"]["version"] == "20261020"
