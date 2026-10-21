import pytest

from courier_router import cli, yandex_maps as ym
from courier_router.geocode import (
    RESOLVER_VERSION,
    YandexMapsGeocoder,
    _has_street_marker,
    _score_locality,
    _street_tokens,
    normalize_address_input,
)


def test_lomonosov_is_not_expanded_as_len_oblast():
    # Real route-sheet spelling has no city prefix or comma before the street.
    address = "Ломоносов ул Красного Флота д.23 к 1 кв68"
    assert normalize_address_input(address) == "Ломоносов ул Красного Флота д.23 к 1"
    assert normalize_address_input("ЛО, Ломоносовский р-н, д. Пески") == (
        "Ленинградская область, Ломоносовский р-н, д. Пески"
    )


def test_spaced_saint_petersburg_is_canonical_and_blocks_courier_district():
    source = "Санкт -Петербург б-р Новаторов д.68 кв 42"
    prepared = normalize_address_input(source, "Красносельский")
    assert prepared == "Санкт-Петербург б-р Новаторов д.68"
    assert "Красносельский" not in prepared


@pytest.mark.parametrize(
    ("address", "district", "expected"),
    [
        ("Петергофское шоссе д16", "Приморский", "Приморский, Петергофское шоссе д16"),
        ("Петергофский проспект д16", "Приморский", "Приморский, Петергофский проспект д16"),
        (
            "15-я линия Васильевского острова д80",
            "Приморский",
            "Приморский, 15-я линия Васильевского острова д80",
        ),
        (
            "Ломоносов ул Красного Флота д.23 к 1",
            "Ломоносовский",
            "Ломоносов ул Красного Флота д.23 к 1",
        ),
    ],
)
def test_courier_district_is_added_only_when_locality_is_not_in_street_prefix(
    address, district, expected
):
    assert normalize_address_input(address, district) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("г Санкт-Петербург, пр-т КИМа, д 4", "г Санкт-Петербург, проспект КИМа, д 4"),
        ("г Санкт-Петербург, Театральная пл, д 16", "г Санкт-Петербург, Театральная площадь, д 16"),
        ("г Санкт-Петербург, Театральная площадь, д 16", "г Санкт-Петербург, Театральная площадь, д 16"),
    ],
)
def test_street_shorthands_are_canonicalized(source, expected):
    assert normalize_address_input(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Санкт-Петербург, Театральная пл, д 16 литера ПЛ",
            "Санкт-Петербург, Театральная площадь, д 16 литера ПЛ",
        ),
        (
            "ул Тестовая, д1, лит ПЛ",
            "ул Тестовая, д1, лит ПЛ",
        ),
        (
            "Театральная пл д16 литера ПЛ",
            "Театральная площадь д16 литера ПЛ",
        ),
        (
            "Санкт-Петербург пр-т КИМа д 4 лит ПЛ",
            "Санкт-Петербург проспект КИМа д 4 лит ПЛ",
        ),
        (
            "д Пески, Театральная пл, д 16",
            "д Пески, Театральная площадь, д 16",
        ),
        (
            "д. Пески, пр-т КИМа, д 4",
            "д. Пески, проспект КИМа, д 4",
        ),
        (
            "г Санкт-Петербург, Театральная пл, д.16 к 2 стр 3 лит ПЛ",
            "г Санкт-Петербург, Театральная площадь, д.16 к 2 стр 3 лит ПЛ",
        ),
    ],
)
def test_street_shorthand_does_not_rewrite_building_suffixes(source, expected):
    assert normalize_address_input(source) == expected


@pytest.mark.parametrize("marker", ["пр-т", "пл", "площадь"])
def test_street_markers_are_parsed_as_streets_not_localities(marker):
    source = f"г Санкт-Петербург, Тестовая {marker}, д 16"
    assert _has_street_marker(source)
    assert "тестовая" in _street_tokens(source)
    assert ym._street_tokens(source) == {"тестовая"}
    score, reasons, mismatch = _score_locality(source, {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": f"Тестовая {marker}",
    })
    assert not mismatch
    assert "locality_mismatch" not in reasons


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Ломоносов ул Красного Флота д.23 к 1", {"красного", "флота"}),
        ("г Санкт-Петербург пр-т КИМа д 4", {"кима"}),
        ("Ломоносов 15-я линия Васильевского острова д 80", {"15я", "васильевского", "острова"}),
        ("Театральная площадь д 16", {"театральная"}),
    ],
)
def test_no_comma_street_extraction_keeps_the_street_name(source, expected):
    assert ym._street_tokens(source) == expected


def test_multichar_litera_is_removed_before_street_token_matching():
    assert ym._street_tokens(
        "Санкт-Петербург, Театральная площадь д 16 литера ПЛА"
    ) == {"театральная"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ул Литературная д1", {"литературная"}),
        ("Литейный проспект д1", {"литейный"}),
    ],
)
def test_street_names_starting_with_lit_are_not_treated_as_building_letters(source, expected):
    assert ym._street_tokens(source) == expected


def _card(title, subtitle="Санкт-Петербург"):
    return ym.YResult(59.93, 30.31, title, subtitle,
                      "https://yandex.ru/maps/house/test/", True)


@pytest.mark.parametrize(
    ("source", "title", "subtitle", "status"),
    [
        ("Санкт -Петербург б-р Новаторов д.68 кв 42", "бульвар Новаторов, 68", "Санкт-Петербург", "verified"),
        ("г Санкт-Петербург, пр-т КИМа, д 4", "проспект КИМа, 4", "Санкт-Петербург", "verified"),
        ("г Санкт-Петербург, Театральная пл, д 16", "Театральная площадь, 16", "Пушкин, Ленинградская область", "strong_mismatch"),
        ("Ломоносов ул Красного Флота д.23 к 1 кв68", "улица Красного Флота, 23к1", "Ломоносов, Санкт-Петербург", "verified"),
    ],
)
def test_mock_cards_preserve_geography_and_house_guards(
    monkeypatch, tmp_path, source, title, subtitle, status
):
    card = _card(title, subtitle)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    geocoder = YandexMapsGeocoder()
    district = "" if source.startswith("Ломоносов ") else "Красносельский"
    result = geocoder.geocode(source, district)
    resolver = result.raw["_resolver"]

    assert resolver["status"] == status
    assert resolver["version"] == RESOLVER_VERSION == 5
    assert result.normalized_address == source
    if "Санкт" in source:
        assert "Красносельский" not in resolver["query"]
    # Keep the regression isolated from the machine's persistent geocode cache.
    store = cli.Storage(str(tmp_path / "cache.db"))
    store.put_geocode(source, district, result)
    assert store.get_geocode(source, district) is not None
    store.con.close()


@pytest.mark.parametrize(
    ("title", "subtitle", "status"),
    [
        ("улица Красного Флота, 23к1", "Ломоносов, Санкт-Петербург", "verified"),
        ("Советская улица, 23к1", "Ломоносов, Санкт-Петербург", "strong_mismatch"),
        ("улица Красного Флота, 23к1", "Сестрорецк, Санкт-Петербург", "strong_mismatch"),
    ],
)
def test_uncommaed_lomonosov_keeps_street_and_locality_guards(
    monkeypatch, title, subtitle, status
):
    source = "Ломоносов ул Красного Флота д.23 к 1 кв68"
    card = _card(title, subtitle)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    result = YandexMapsGeocoder().geocode(source, "Ломоносовский")
    resolver = result.raw["_resolver"]

    assert "Ломоносовский" not in resolver["query"]
    assert resolver["status"] == status


def test_prefix_locality_uses_only_its_comma_component(monkeypatch):
    source = "тер Осиновая Роща, Ломоносов ул Красного Флота д.23 к 1"
    card = _card("улица Красного Флота, 23к1", "Ломоносов, Санкт-Петербург")
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    result = YandexMapsGeocoder().geocode(source)

    assert ym._prefix_locality_before_street(source) == "Ломоносов"
    assert result.raw["_resolver"]["status"] == "review"


@pytest.mark.parametrize(
    ("title", "expected_status"),
    [
        ("Театральная площадь, 16", "review"),
        ("Театральная площадь, 17", "strong_mismatch"),
    ],
)
def test_multichar_litera_missing_from_card_keeps_house_guard(
    monkeypatch, title, expected_status
):
    source = "Санкт-Петербург, Театральная пл, д 16 литера ПЛА"
    card = _card(title)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    result = YandexMapsGeocoder().geocode(source)

    assert result.raw["_resolver"]["status"] == expected_status


@pytest.mark.parametrize(
    ("source", "title"),
    [
        ("Санкт-Петербург, ул Литературная д1", "Литературная улица, 1"),
        ("Санкт-Петербург, Литейный проспект д1", "Литейный проспект, 1"),
    ],
)
def test_yandex_cards_match_streets_starting_with_lit(monkeypatch, source, title):
    card = _card(title)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card for key, _, _ in jobs})
    result = YandexMapsGeocoder().geocode(source)

    assert result.raw["_resolver"]["status"] == "verified"


def test_liteyny_name_does_not_end_street_shorthand_normalization(monkeypatch):
    source = "Санкт-Петербург, Литейный пр-т, д 1"
    assert normalize_address_input(source) == "Санкт-Петербург, Литейный проспект, д 1"
    wrong_type = _card("Литейный переулок, 1")
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: wrong_type for key, _, _ in jobs})
    result = YandexMapsGeocoder().geocode(source)
    assert result.raw["_resolver"]["status"] == "strong_mismatch"
