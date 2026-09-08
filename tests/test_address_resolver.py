from courier_router.geocode import (
    DaDataGeocoder,
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
