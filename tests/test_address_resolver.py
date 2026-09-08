from courier_router.geocode import (
    DaDataGeocoder,
    RESOLVER_VERSION,
    normalize_address_input,
    score_dadata_candidate,
)


def test_normalizes_spb_abbreviation_and_known_typo():
    raw = "ПБ,Коменданский пр-т, д 53, к 1, кв 231"
    normalized = normalize_address_input(raw)
    assert normalized.startswith("Санкт-Петербург,")
    assert "Комендантский" in normalized
    assert "д 53" in normalized


def test_component_score_rejects_wrong_city_even_when_house_matches():
    source = normalize_address_input("ПБ,Коменданский пр-т, д 53, к 1, кв 231")
    wrong = {
        "region": "Ленинградская обл",
        "city": "Сосновый Бор",
        "settlement": "",
        "street": "тер СНТ Приморский",
        "house": "53",
        "block": "1",
        "geo_lat": "59.90",
        "geo_lon": "29.10",
    }
    score, reasons, strong_mismatch = score_dadata_candidate(source, wrong)
    assert strong_mismatch
    assert "city_mismatch" in reasons
    assert score < 0.50


def test_component_score_accepts_matching_spb_house():
    source = normalize_address_input("ПБ,Коменданский пр-т, д 53, к 1, кв 231")
    good = {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": "Комендантский пр-кт",
        "house": "53",
        "block": "1",
        "geo_lat": "60.015",
        "geo_lon": "30.245",
    }
    score, reasons, strong_mismatch = score_dadata_candidate(source, good)
    assert not strong_mismatch
    assert "city_match" in reasons
    assert "street_match" in reasons
    assert "house_match" in reasons
    assert score >= 0.90


def test_compact_address_without_house_marker_rejects_wrong_house():
    source = "Санкт-Петербург, Савушкина 15"
    wrong = {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": "Савушкина",
        "house": "151",
        "geo_lat": "59.98",
        "geo_lon": "30.22",
    }
    score, reasons, strong_mismatch = score_dadata_candidate(source, wrong)
    assert strong_mismatch
    assert "house_mismatch" in reasons
    assert score < 0.68


def test_multiword_street_does_not_match_on_one_shared_word():
    source = "Санкт-Петербург, Малая Морская ул, д 10"
    wrong = {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": "Большая Морская ул",
        "house": "10",
        "geo_lat": "59.93",
        "geo_lon": "30.31",
    }
    score, reasons, strong_mismatch = score_dadata_candidate(source, wrong)
    assert "street_mismatch" in reasons
    assert score < 0.68
    assert not strong_mismatch


def test_explicit_leningrad_region_rejects_other_region():
    source = "Ленинградская обл, Ломоносовский р-н, деревня Пески, ул Центральная, д 210"
    wrong = {
        "region": "Псковская обл",
        "city": "",
        "settlement": "Пески",
        "street": "Центральная ул",
        "house": "210",
        "geo_lat": "57.8",
        "geo_lon": "28.3",
    }
    score, reasons, strong_mismatch = score_dadata_candidate(source, wrong)
    assert strong_mismatch
    assert "region_mismatch" in reasons
    assert score < 0.68


def test_structure_mismatch_is_strong_mismatch():
    source = "Санкт-Петербург, ул Тестовая, д 2, литера А"
    wrong = {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": "Тестовая ул",
        "house": "2",
        "block_type": "литера",
        "block": "Б",
        "geo_lat": "59.9",
        "geo_lon": "30.3",
    }
    score, reasons, strong_mismatch = score_dadata_candidate(source, wrong)
    assert strong_mismatch
    assert "structure_mismatch" in reasons


def test_geocoder_ranks_matching_candidate_above_wrong_same_house(monkeypatch):
    geocoder = DaDataGeocoder("token", "secret")
    good = {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": "Комендантский пр-кт",
        "house": "53",
        "block": "1",
        "fias_id": "good-fias",
        "geo_lat": "60.015",
        "geo_lon": "30.245",
    }
    wrong = {
        "region": "Ленинградская обл",
        "city": "Сосновый Бор",
        "street": "тер СНТ Приморский",
        "house": "53",
        "block": "1",
        "fias_id": "wrong-fias",
        "geo_lat": "59.90",
        "geo_lon": "29.10",
    }
    monkeypatch.setattr(
        geocoder,
        "_suggest",
        lambda query, count=5: [
            {"value": "Ленинградская обл, г Сосновый Бор, СНТ Приморский, д 53 к 1", "data": wrong},
            {"value": "г Санкт-Петербург, Комендантский пр-кт, д 53 к 1", "data": good},
        ],
    )
    monkeypatch.setattr(geocoder, "_clean", lambda query: (_ for _ in ()).throw(AssertionError("clean fallback must not run")))

    result = geocoder.geocode("ПБ,Коменданский пр-т, д 53, к 1, кв 231")

    assert result.provider == "dadata_suggest"
    assert result.provider_ref == "good-fias"
    assert "Комендантский" in result.normalized_address
    assert result.raw["_resolver"]["status"] == "resolved"
    assert result.raw["_resolver"]["version"] == RESOLVER_VERSION


def test_geocoder_marks_clean_fallback_strong_mismatch(monkeypatch):
    geocoder = DaDataGeocoder("token", "secret")
    monkeypatch.setattr(geocoder, "_suggest", lambda query, count=5: [])
    monkeypatch.setattr(
        geocoder,
        "_clean",
        lambda query: {
            "result": "Ленинградская обл, г Сосновый Бор, СНТ Приморский, д 53 к 1",
            "region": "Ленинградская обл",
            "city": "Сосновый Бор",
            "street": "тер СНТ Приморский",
            "house": "53",
            "block": "1",
            "fias_id": "wrong-fias",
            "geo_lat": "59.90",
            "geo_lon": "29.10",
            "qc_geo": 3,
        },
    )

    result = geocoder.geocode("ПБ,Коменданский пр-т, д 53, к 1, кв 231")

    assert result.raw["_resolver"]["status"] == "strong_mismatch"
    assert result.confidence < 0.60
