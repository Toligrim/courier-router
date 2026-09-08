from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .domain import GeoPoint
from .geocode import (
    DaDataGeocoder,
    _extract_block,
    _extract_structure,
    _norm_text,
    normalize_address_input,
    score_dadata_candidate,
)

VERIFICATION_VERSION = 4
VERIFIED = "verified"
REVIEW = "review"
REJECTED = "rejected"


@dataclass(frozen=True)
class VerificationDecision:
    status: str
    confidence: float
    reasons: list[str]
    distance_m: float | None = None


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coord(data: dict) -> tuple[float, float] | None:
    try:
        if data.get("geo_lat") is None or data.get("geo_lon") is None:
            return None
        return float(data["geo_lat"]), float(data["geo_lon"])
    except (TypeError, ValueError):
        return None


def _distance_m(a: dict, b: dict) -> float | None:
    ca, cb = _coord(a), _coord(b)
    if not ca or not cb:
        return None
    lat1, lon1 = map(math.radians, ca)
    lat2, lon2 = map(math.radians, cb)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(h))


def _house_id(data: dict) -> str | None:
    return data.get("house_fias_id") or (data.get("fias_id") if str(data.get("fias_level")) == "8" else None)


def _fias_actuality(data: dict) -> int | None:
    return _int_or_none(data.get("fias_actuality_state"))


def _building_parts(data: dict) -> tuple[str, tuple[str, str] | None]:
    """Normalize DaData compound block values such as '1 стр 3' or '2 литера Щ'."""
    raw = _norm_text(data.get("block"))
    block_type = _norm_text(data.get("block_type") or data.get("block_type_full"))
    primary = ""
    structure: tuple[str, str] | None = None

    if raw:
        structure_match = re.search(r"(?:^|\s)(лит(?:ера)?|стр(?:оение)?)\s*([0-9а-яa-z/-]+)", raw, re.I)
        if structure_match:
            kind = "лит" if _norm_text(structure_match.group(1)).startswith("лит") else "стр"
            structure = (kind, _norm_text(structure_match.group(2)))

        if block_type.startswith(("к", "корп")):
            primary = re.split(r"\s+(?:лит(?:ера)?|стр(?:оение)?)\b", raw, maxsplit=1)[0].strip()
        elif block_type.startswith("лит"):
            structure = ("лит", raw)
        elif block_type.startswith("стр"):
            structure = ("стр", raw)
        elif not structure:
            primary = raw
        else:
            prefix = re.split(r"\s+(?:лит(?:ера)?|стр(?:оение)?)\b", raw, maxsplit=1)[0].strip()
            primary = prefix if prefix != raw else ""

    # Older/alternate DaData payloads may expose a separate building field.
    building = _norm_text(data.get("building"))
    building_type = _norm_text(data.get("building_type"))
    if building and structure is None:
        kind = "лит" if building_type.startswith("лит") else "стр"
        structure = (kind, building)

    return primary, structure


def _component_tuple(data: dict) -> tuple[str, str, str, str, tuple[str, str] | None]:
    locality = " ".join(str(data.get(k) or "") for k in ("region", "city", "settlement"))
    block, structure = _building_parts(data)
    return (
        _norm_text(locality),
        _norm_text(data.get("street")),
        _norm_text(data.get("house")),
        block,
        structure,
    )


def _same_address(clean: dict, suggest: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    clean_id, suggest_id = _house_id(clean), _house_id(suggest)
    if clean_id and suggest_id:
        if clean_id == suggest_id:
            return True, ["house_fias_id_match"]
        return False, ["house_fias_id_mismatch"]

    cl, cs, ch, cb, cstruct = _component_tuple(clean)
    sl, ss, sh, sb, sstruct = _component_tuple(suggest)
    same = True
    if cl and sl and not (cl in sl or sl in cl):
        same = False
        reasons.append("locality_crosscheck_mismatch")
    if cs and ss and cs != ss:
        same = False
        reasons.append("street_crosscheck_mismatch")
    if ch and sh and ch != sh:
        same = False
        reasons.append("house_crosscheck_mismatch")
    if cb and sb and cb != sb:
        same = False
        reasons.append("block_crosscheck_mismatch")
    if cstruct and sstruct and cstruct != sstruct:
        same = False
        reasons.append("structure_crosscheck_mismatch")
    return same, reasons or ["component_crosscheck_match"]


def _source_building_check(query: str, candidate: dict) -> tuple[bool, bool, list[str]]:
    """Return (clear_mismatch, missing_evidence, reasons) for corpus/litera/structure."""
    source_block = _extract_block(query)
    source_structure = _extract_structure(query)
    candidate_block, candidate_structure = _building_parts(candidate)
    reasons: list[str] = []
    mismatch = False
    missing = False

    if source_block:
        if candidate_block and candidate_block != source_block:
            mismatch = True
            reasons.append("source_block_mismatch")
        elif not candidate_block:
            missing = True
            reasons.append("source_block_not_confirmed")
        else:
            reasons.append("source_block_match")

    if source_structure:
        if candidate_structure and candidate_structure != source_structure:
            mismatch = True
            reasons.append("source_structure_mismatch")
        elif not candidate_structure:
            missing = True
            reasons.append("source_structure_not_confirmed")
        else:
            reasons.append("source_structure_match")

    return mismatch, missing, reasons


def _score_suggestion_components(query: str, suggestion: dict, clean: dict) -> tuple[float, list[str], bool]:
    scoring = dict(suggestion)
    clean_coord = _coord(clean)
    if _coord(scoring) is None and clean_coord is not None:
        # Suggestions can omit coordinates. They are used here only to score textual
        # identity; Clean remains the authority for coordinate quality via qc_geo.
        scoring["geo_lat"], scoring["geo_lon"] = clean_coord
    return score_dadata_candidate(query, scoring)


def _select_crosscheck_candidate(clean: dict, ranked: list[tuple[float, bool, list[str], dict, dict]]):
    if not ranked:
        return None, "no_candidate"

    clean_id = _house_id(clean)
    if clean_id:
        exact_id = [entry for entry in ranked if not entry[1] and _house_id(entry[4]) == clean_id]
        if exact_id:
            return max(exact_id, key=lambda entry: entry[0]), "house_fias_id_match"

    component_matches = []
    for entry in ranked:
        if entry[1]:
            continue
        same, _ = _same_address(clean, entry[4])
        if same:
            component_matches.append(entry)
    if component_matches:
        return max(component_matches, key=lambda entry: entry[0]), "component_match"

    return ranked[0], "highest_score_fallback"


def evaluate_verification(query: str, clean: dict, best_suggestion: dict | None) -> VerificationDecision:
    reasons: list[str] = []
    clean_score, clean_reasons, strong_mismatch = score_dadata_candidate(query, clean)
    reasons.extend(clean_reasons)

    qc = _int_or_none(clean.get("qc"))
    qc_complete = _int_or_none(clean.get("qc_complete"))
    qc_house = _int_or_none(clean.get("qc_house"))
    qc_geo = _int_or_none(clean.get("qc_geo"))
    actuality = _fias_actuality(clean)

    if strong_mismatch:
        return VerificationDecision(REJECTED, 0.20, reasons + ["clean_component_mismatch"])
    if actuality == 99:
        return VerificationDecision(REJECTED, 0.10, reasons + ["fias_deleted"])
    if qc == 2:
        return VerificationDecision(REJECTED, 0.10, reasons + ["clean_garbage"])
    if qc_complete in {1, 2, 3, 4, 6, 7}:
        return VerificationDecision(REJECTED, 0.20, reasons + [f"qc_complete_{qc_complete}"])
    if _coord(clean) is None:
        return VerificationDecision(REJECTED, 0.10, reasons + ["clean_coordinates_missing"])

    if best_suggestion is None:
        return VerificationDecision(REVIEW, min(0.79, max(0.45, clean_score)), reasons + ["suggestion_crosscheck_missing"])

    # Compare registry identity before fuzzy text scoring. A conflicting house FIAS/GAR
    # identifier is stronger evidence than a text similarity score.
    same, cross_reasons = _same_address(clean, best_suggestion)
    reasons.extend(cross_reasons)
    if not same:
        return VerificationDecision(REJECTED, 0.20, reasons + ["clean_suggest_disagree"])

    building_mismatch, building_missing, building_reasons = _source_building_check(query, best_suggestion)
    reasons.extend(building_reasons)
    if building_mismatch:
        return VerificationDecision(REJECTED, 0.20, reasons + ["explicit_building_component_mismatch"])

    suggest_score, suggest_reasons, suggest_mismatch = _score_suggestion_components(query, best_suggestion, clean)
    if suggest_mismatch:
        return VerificationDecision(REJECTED, 0.20, reasons + suggest_reasons + ["suggestion_component_mismatch"])

    distance = _distance_m(clean, best_suggestion)
    if distance is not None:
        reasons.append(f"crosscheck_distance_m:{round(distance)}")
    else:
        reasons.append("suggestion_coordinates_not_required")
    if distance is not None and distance > 500:
        return VerificationDecision(REJECTED, 0.20, reasons + ["coordinate_crosscheck_far"], distance)

    current_fias = actuality in {None, 0}
    coordinates_agree = distance is None or distance <= 200
    scores_good = clean_score >= 0.80 and suggest_score >= 0.80

    registry_verified = (
        qc == 0
        and qc_complete in {0, 5}
        and qc_house == 2
        and qc_geo == 0
        and current_fias
        and scores_good
        and coordinates_agree
        and not building_missing
    )
    if registry_verified:
        return VerificationDecision(VERIFIED, 0.99, reasons + ["registry_house_verified"], distance)

    # DaData documents qc_house=10 + qc_geo=0 as a high-deliverability case: the
    # house is absent from FIAS/GAR but is known on maps. Require an exact component
    # cross-check against Suggestions before accepting it automatically.
    map_verified = (
        qc == 0
        and qc_complete == 10
        and qc_house == 10
        and qc_geo == 0
        and scores_good
        and coordinates_agree
        and not building_missing
    )
    if map_verified:
        return VerificationDecision(VERIFIED, 0.96, reasons + ["map_house_verified_without_fias"], distance)

    review_reasons = []
    if qc in {1, 3}:
        review_reasons.append(f"qc_{qc}")
    if qc_complete in {9, 10}:
        review_reasons.append(f"qc_complete_{qc_complete}")
    if qc_house not in {2, 10}:
        review_reasons.append(f"qc_house_{qc_house}")
    if qc_geo is None or qc_geo > 0:
        review_reasons.append(f"qc_geo_{qc_geo}")
    if actuality not in {None, 0}:
        review_reasons.append(f"fias_actuality_state_{actuality}")
    if building_missing:
        review_reasons.append("explicit_building_component_not_confirmed")
    if not scores_good:
        review_reasons.append("component_score_below_verified_threshold")
    return VerificationDecision(REVIEW, min(0.79, max(0.45, min(clean_score, suggest_score))), reasons + review_reasons, distance)


class VerifiedDaDataGeocoder:
    """DaData Clean + Suggestions cross-check for deterministic courier address verification."""

    def __init__(self, token: str, secret: str, timeout: float = 20):
        self.backend = DaDataGeocoder(token, secret, timeout=timeout)

    def geocode(self, address: str, district: str = "") -> GeoPoint:
        query = normalize_address_input(address, district)
        clean = self.backend._clean(query)
        suggestions = self.backend._suggest(query, count=5)
        ranked: list[tuple[float, bool, list[str], dict, dict]] = []
        for item in suggestions:
            data = item.get("data") or {}
            score, reasons, mismatch = _score_suggestion_components(query, data, clean)
            ranked.append((score, mismatch, reasons, item, data))
        ranked.sort(key=lambda x: x[0], reverse=True)

        selected, selection_reason = _select_crosscheck_candidate(clean, ranked)
        best_data = selected[4] if selected else None
        decision = evaluate_verification(query, clean, best_data)

        chosen = clean if _coord(clean) is not None else best_data
        if chosen is None or _coord(chosen) is None:
            raise ValueError(f"DaData не вернула координаты: {address}")

        resolver = {
            "version": VERIFICATION_VERSION,
            "status": decision.status,
            "input": address,
            "query": query,
            "score": decision.confidence,
            "reasons": decision.reasons,
            "candidate_selection": selection_reason,
            "crosscheck_distance_m": None if decision.distance_m is None else round(decision.distance_m, 1),
            "clean_quality": {
                "qc": _int_or_none(clean.get("qc")),
                "qc_complete": _int_or_none(clean.get("qc_complete")),
                "qc_house": _int_or_none(clean.get("qc_house")),
                "qc_geo": _int_or_none(clean.get("qc_geo")),
                "fias_actuality_state": _fias_actuality(clean),
            },
            "clean_fias_id": clean.get("fias_id"),
            "clean_house_fias_id": clean.get("house_fias_id"),
            "suggest_fias_id": None if best_data is None else best_data.get("fias_id"),
            "suggest_house_fias_id": None if best_data is None else best_data.get("house_fias_id"),
            "candidates": [
                {
                    "value": item.get("value"),
                    "score": round(score, 3),
                    "strong_mismatch": mismatch,
                    "reasons": why,
                    "fias_id": data.get("fias_id"),
                    "house_fias_id": data.get("house_fias_id"),
                    "fias_actuality_state": _fias_actuality(data),
                    "selected": data is best_data,
                }
                for score, mismatch, why, item, data in ranked[:5]
            ],
        }
        raw = dict(chosen)
        raw["_resolver"] = resolver
        coords = _coord(chosen)
        assert coords is not None
        lat, lon = coords
        normalized = clean.get("result") or (selected[3].get("value") if selected else query) or query
        provider_ref = clean.get("house_fias_id") or clean.get("fias_id") or chosen.get("fias_id")
        return GeoPoint(
            lat=lat,
            lon=lon,
            provider="dadata_verified",
            normalized_address=normalized,
            precision="verified_house" if decision.status == VERIFIED else "review",
            confidence=decision.confidence,
            provider_ref=provider_ref,
            raw=raw,
        )
