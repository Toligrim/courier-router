from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import GeoPoint
from .geocode import DaDataGeocoder, _norm_text, normalize_address_input, score_dadata_candidate

VERIFICATION_VERSION = 3
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


def _component_tuple(data: dict) -> tuple[str, str, str, str]:
    locality = " ".join(str(data.get(k) or "") for k in ("region", "city", "settlement"))
    return (
        _norm_text(locality),
        _norm_text(data.get("street")),
        _norm_text(data.get("house")),
        _norm_text(data.get("block")),
    )


def _same_address(clean: dict, suggest: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    clean_id, suggest_id = _house_id(clean), _house_id(suggest)
    if clean_id and suggest_id:
        if clean_id == suggest_id:
            return True, ["house_fias_id_match"]
        return False, ["house_fias_id_mismatch"]

    cl, cs, ch, cb = _component_tuple(clean)
    sl, ss, sh, sb = _component_tuple(suggest)
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
    return same, reasons or ["component_crosscheck_match"]


def evaluate_verification(query: str, clean: dict, best_suggestion: dict | None) -> VerificationDecision:
    reasons: list[str] = []
    clean_score, clean_reasons, strong_mismatch = score_dadata_candidate(query, clean)
    reasons.extend(clean_reasons)

    qc = _int_or_none(clean.get("qc"))
    qc_complete = _int_or_none(clean.get("qc_complete"))
    qc_house = _int_or_none(clean.get("qc_house"))
    qc_geo = _int_or_none(clean.get("qc_geo"))

    if strong_mismatch:
        return VerificationDecision(REJECTED, 0.20, reasons + ["clean_component_mismatch"])
    if qc == 2:
        return VerificationDecision(REJECTED, 0.10, reasons + ["clean_garbage"])
    if qc_complete in {1, 2, 3, 4, 6, 7}:
        return VerificationDecision(REJECTED, 0.20, reasons + [f"qc_complete_{qc_complete}"])
    if _coord(clean) is None:
        return VerificationDecision(REJECTED, 0.10, reasons + ["clean_coordinates_missing"])

    if best_suggestion is None:
        return VerificationDecision(REVIEW, min(0.79, max(0.45, clean_score)), reasons + ["suggestion_crosscheck_missing"])

    suggest_score, suggest_reasons, suggest_mismatch = score_dadata_candidate(query, best_suggestion)
    if suggest_mismatch:
        return VerificationDecision(REJECTED, 0.20, reasons + suggest_reasons + ["suggestion_component_mismatch"])

    same, cross_reasons = _same_address(clean, best_suggestion)
    distance = _distance_m(clean, best_suggestion)
    reasons.extend(cross_reasons)
    if distance is not None:
        reasons.append(f"crosscheck_distance_m:{round(distance)}")

    if not same:
        return VerificationDecision(REJECTED, 0.20, reasons + ["clean_suggest_disagree"], distance)
    if distance is not None and distance > 500:
        return VerificationDecision(REJECTED, 0.20, reasons + ["coordinate_crosscheck_far"], distance)

    strict_clean = (
        qc == 0
        and qc_complete in {0, 5}
        and qc_house == 2
        and qc_geo == 0
        and clean_score >= 0.80
        and suggest_score >= 0.80
        and (distance is None or distance <= 200)
    )
    if strict_clean:
        return VerificationDecision(VERIFIED, 0.99, reasons + ["strict_clean_quality_pass"], distance)

    review_reasons = []
    if qc in {1, 3}:
        review_reasons.append(f"qc_{qc}")
    if qc_complete in {9, 10}:
        review_reasons.append(f"qc_complete_{qc_complete}")
    if qc_house != 2:
        review_reasons.append(f"qc_house_{qc_house}")
    if qc_geo is None or qc_geo > 0:
        review_reasons.append(f"qc_geo_{qc_geo}")
    if clean_score < 0.80 or suggest_score < 0.80:
        review_reasons.append("component_score_below_verified_threshold")
    return VerificationDecision(REVIEW, min(0.79, max(0.45, min(clean_score, suggest_score))), reasons + review_reasons, distance)


class VerifiedDaDataGeocoder:
    """DaData Clean + Suggestions cross-check for deterministic courier address verification."""

    def __init__(self, token: str, secret: str, timeout: float = 20):
        self.backend = DaDataGeocoder(token, secret, timeout=timeout)

    def geocode(self, address: str, district: str = "") -> GeoPoint:
        query = normalize_address_input(address, district)
        suggestions = self.backend._suggest(query, count=5)
        ranked: list[tuple[float, bool, list[str], dict, dict]] = []
        for item in suggestions:
            data = item.get("data") or {}
            if _coord(data) is None:
                continue
            score, reasons, mismatch = score_dadata_candidate(query, data)
            ranked.append((score, mismatch, reasons, item, data))
        ranked.sort(key=lambda x: x[0], reverse=True)
        best_data = ranked[0][4] if ranked else None

        clean = self.backend._clean(query)
        decision = evaluate_verification(query, clean, best_data)

        chosen = clean if _coord(clean) is not None else best_data
        if chosen is None:
            raise ValueError(f"DaData не вернула координаты: {address}")

        resolver = {
            "version": VERIFICATION_VERSION,
            "status": decision.status,
            "input": address,
            "query": query,
            "score": decision.confidence,
            "reasons": decision.reasons,
            "crosscheck_distance_m": None if decision.distance_m is None else round(decision.distance_m, 1),
            "clean_quality": {
                "qc": _int_or_none(clean.get("qc")),
                "qc_complete": _int_or_none(clean.get("qc_complete")),
                "qc_house": _int_or_none(clean.get("qc_house")),
                "qc_geo": _int_or_none(clean.get("qc_geo")),
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
                }
                for score, mismatch, why, item, data in ranked[:5]
            ],
        }
        raw = dict(chosen)
        raw["_resolver"] = resolver
        coords = _coord(chosen)
        assert coords is not None
        lat, lon = coords
        normalized = clean.get("result") or (ranked[0][3].get("value") if ranked else query) or query
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
