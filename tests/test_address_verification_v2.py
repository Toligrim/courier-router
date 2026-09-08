from courier_router.address_verification import (
    REJECTED,
    REVIEW,
    VERIFIED,
    VerifiedDaDataGeocoder,
    evaluate_verification,
)


def clean_address(**overrides):
    data = {
        "result": "г Санкт-Петербург, ул Савушкина, д 15",
        "region": "Санкт-Петербург",
        "city": "Санкт-Петербург",
        "settlement": None,
        "street": "Савушкина",
        "house": "15",
        "block": None,
        "fias_id": "house-15",
        "house_fias_id": "house-15",
        "fias_level": "8",
        "fias_actuality_state": "0",
        "geo_lat": "59.985000",
        "geo_lon": "30.300000",
        "qc": 0,
        "qc_complete": 0,
        "qc_house": 2,
        "qc_geo": 0,
    }
    data.update(overrides)
    return data


def suggestion(**overrides):
    data = {
        "region": "Санкт-Петербург",
        "city": "Санкт-Петербург",
        "settlement": None,
        "street": "Савушкина",
        "house": "15",
        "block": None,
        "fias_id": "house-15",
        "house_fias_id": "house-15",
        "fias_level": "8",
        "fias_actuality_state": "0",
        "geo_lat": "59.985050",
        "geo_lon": "30.300050",
    }
    data.update(overrides)
    return data


def test_verified_requires_clean_quality_and_crosscheck():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(),
        suggestion(),
    )

    assert decision.status == VERIFIED
    assert decision.confidence == 0.99
    assert decision.distance_m is not None and decision.distance_m < 20
    assert "house_fias_id_match" in decision.reasons
    assert "registry_house_verified" in decision.reasons


def test_clean_uncertainty_forces_review_even_when_candidate_matches():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(qc=1),
        suggestion(),
    )

    assert decision.status == REVIEW
    assert decision.confidence < 0.80
    assert "qc_1" in decision.reasons


def test_map_confirmed_house_without_fias_can_be_verified():
    clean = clean_address(
        qc_complete=10,
        qc_house=10,
        house_fias_id=None,
        fias_id="street-savushkina",
        fias_level="7",
        fias_actuality_state="0",
    )
    candidate = suggestion(
        house_fias_id=None,
        fias_id=None,
        fias_level="7",
        fias_actuality_state=None,
    )

    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean,
        candidate,
    )

    assert decision.status == VERIFIED
    assert decision.confidence == 0.96
    assert "map_house_verified_without_fias" in decision.reasons


def test_nearest_house_coordinates_remain_review():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(qc_geo=1),
        suggestion(),
    )

    assert decision.status == REVIEW
    assert "qc_geo_1" in decision.reasons


def test_clean_and_suggestion_house_id_disagreement_is_rejected_even_when_text_matches():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(),
        suggestion(house_fias_id="different-house-id", fias_id="different-house-id"),
    )

    assert decision.status == REJECTED
    assert "house_fias_id_mismatch" in decision.reasons
    assert "clean_suggest_disagree" in decision.reasons


def test_missing_house_is_rejected_for_courier_delivery():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(house=None, house_fias_id=None, fias_id="street", fias_level="7", qc_complete=4, qc_house=None, qc_geo=2),
        suggestion(),
    )

    assert decision.status == REJECTED
    assert "qc_complete_4" in decision.reasons


def test_deleted_fias_object_is_rejected():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(fias_actuality_state="99"),
        suggestion(),
    )

    assert decision.status == REJECTED
    assert "fias_deleted" in decision.reasons


def test_renamed_fias_object_requires_review():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(fias_actuality_state="1"),
        suggestion(),
    )

    assert decision.status == REVIEW
    assert "fias_actuality_state_1" in decision.reasons


def test_fias_match_can_verify_when_suggestion_omits_coordinates():
    candidate = suggestion(geo_lat=None, geo_lon=None)
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15",
        clean_address(),
        candidate,
    )

    assert decision.status == VERIFIED
    assert decision.distance_m is None
    assert "house_fias_id_match" in decision.reasons
    assert "suggestion_coordinates_not_required" in decision.reasons


def test_compound_corpus_and_structure_are_understood():
    clean = clean_address(block_type="корпус", block="1 стр 3")
    candidate = suggestion(block_type="корпус", block="1 стр 3")

    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15, к 1, стр 3",
        clean,
        candidate,
    )

    assert decision.status == VERIFIED
    assert "source_block_match" in decision.reasons
    assert "source_structure_match" in decision.reasons


def test_explicit_wrong_corpus_is_rejected():
    decision = evaluate_verification(
        "Санкт-Петербург, ул Савушкина, д 15, к 1",
        clean_address(block_type="корпус", block="2"),
        suggestion(block_type="корпус", block="2"),
    )

    assert decision.status == REJECTED
    assert "source_block_mismatch" in decision.reasons


def test_geocoder_selects_matching_fias_candidate_not_top_scored_candidate(monkeypatch):
    geocoder = VerifiedDaDataGeocoder("token", "secret")
    clean = clean_address()
    wrong_id = {
        "value": "г Санкт-Петербург, ул Савушкина, д 15 (старый объект)",
        "data": suggestion(house_fias_id="other-house", fias_id="other-house"),
    }
    exact_id = {
        "value": clean["result"],
        "data": suggestion(),
    }
    monkeypatch.setattr(geocoder.backend, "_clean", lambda query: clean)
    monkeypatch.setattr(geocoder.backend, "_suggest", lambda query, count=5: [wrong_id, exact_id])

    point = geocoder.geocode("Санкт-Петербург, Савушкина 15")

    resolver = point.raw["_resolver"]
    assert resolver["status"] == VERIFIED
    assert resolver["candidate_selection"] == "house_fias_id_match"
    assert resolver["suggest_house_fias_id"] == "house-15"
    selected = [item for item in resolver["candidates"] if item["selected"]]
    assert len(selected) == 1
    assert selected[0]["house_fias_id"] == "house-15"


def test_verified_geocoder_persists_quality_and_fias_metadata(monkeypatch):
    geocoder = VerifiedDaDataGeocoder("token", "secret")
    clean = clean_address()
    item = {"value": clean["result"], "data": suggestion()}
    monkeypatch.setattr(geocoder.backend, "_clean", lambda query: clean)
    monkeypatch.setattr(geocoder.backend, "_suggest", lambda query, count=5: [item])

    point = geocoder.geocode("Санкт-Петербург, Савушкина 15")

    resolver = point.raw["_resolver"]
    assert point.provider == "dadata_verified"
    assert point.provider_ref == "house-15"
    assert resolver["status"] == VERIFIED
    assert resolver["clean_quality"] == {
        "qc": 0,
        "qc_complete": 0,
        "qc_house": 2,
        "qc_geo": 0,
        "fias_actuality_state": 0,
    }
    assert resolver["clean_house_fias_id"] == "house-15"
    assert resolver["suggest_house_fias_id"] == "house-15"
