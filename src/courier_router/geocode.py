from __future__ import annotations

import re
import time
from difflib import SequenceMatcher

import httpx

from .domain import GeoPoint

QC_CONF = {
    0: ("exact", 0.99),
    1: ("nearest_house", 0.90),
    2: ("street", 0.72),
    3: ("settlement", 0.55),
    4: ("city", 0.45),
    5: ("unknown", 0.30),
}

GENERIC_STREET_WORDS = {
    "ул", "улица", "пр", "просп", "проспект", "пр-кт", "переулок", "пер",
    "наб", "набережная", "ш", "шоссе", "б-р", "бульвар", "проезд", "дорога",
}


def _norm_text(value: str | None) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я/ -]+", " ", value)
    return " ".join(value.split())


def normalize_address_input(address: str, district: str = "") -> str:
    """Conservative cleanup for courier spreadsheets before external geocoding."""
    text = " ".join((address or "").strip().split())
    text = re.sub(r"^\s*(?:спб|спб\.|пб)\s*[,;]?\s*", "Санкт-Петербург, ", text, flags=re.I)
    text = re.sub(r"^\s*ло\s*[,;]?\s*", "Ленинградская область, ", text, flags=re.I)
    text = re.sub(r"\bкоменданск(ий|ого|ому|им|ом)\b", r"Комендантск\1", text, flags=re.I)
    text = re.sub(r"\s*[,;]+\s*", ", ", text)
    text = re.sub(r",\s*,+", ", ", text)
    text = text.strip(" ,")
    if district and _norm_text(district) not in _norm_text(text):
        text = f"{district}, {text}"
    return text


def _extract_house(source: str) -> str | None:
    m = re.search(r"(?:^|[ ,])(?:д|дом)\.?\s*([0-9]+[а-яa-z]?(?:/[0-9]+)?)\b", source, re.I)
    return _norm_text(m.group(1)) if m else None


def _extract_block(source: str) -> str | None:
    m = re.search(r"(?:^|[ ,])(?:к|корп|корпус)\.?\s*([0-9]+[а-яa-z]?)\b", source, re.I)
    return _norm_text(m.group(1)) if m else None


def _street_tokens(value: str | None) -> set[str]:
    tokens = set(_norm_text(value).split())
    return {x for x in tokens if x not in GENERIC_STREET_WORDS and len(x) >= 3}


def _has_street_marker(source: str) -> bool:
    return bool(re.search(r"(?:^|[ ,])(?:ул|улица|пр-?кт|проспект|наб|набережная|ш|шоссе|пер|переулок)\.?\s", source, re.I))


def _explicit_spb(source: str) -> bool:
    value = _norm_text(source)
    return "санкт петербург" in value


def _candidate_locality(data: dict) -> str:
    return " ".join(
        str(data.get(key) or "")
        for key in ("region", "city", "settlement")
    )


def _street_similarity(source: str, street: str | None) -> float:
    candidate = _street_tokens(street)
    if not candidate:
        return 0.0
    source_tokens = set(_norm_text(source).split())
    if candidate <= source_tokens:
        return 1.0
    best = 0.0
    for left in candidate:
        for right in source_tokens:
            best = max(best, SequenceMatcher(None, left, right).ratio())
    return best


def score_dadata_candidate(source: str, data: dict) -> tuple[float, list[str], bool]:
    """Score address components, not only DaData's coarse qc_geo value."""
    score = 0.15
    reasons: list[str] = []
    strong_mismatch = False

    source_house = _extract_house(source)
    source_block = _extract_block(source)
    candidate_house = _norm_text(data.get("house")) or None
    candidate_block = _norm_text(data.get("block")) or None

    if _explicit_spb(source):
        locality = _norm_text(_candidate_locality(data))
        if "санкт петербург" in locality:
            score += 0.25
            reasons.append("city_match")
        else:
            score -= 0.65
            reasons.append("city_mismatch")
            strong_mismatch = True

    street_similarity = _street_similarity(source, data.get("street"))
    if street_similarity >= 0.95:
        score += 0.35
        reasons.append("street_match")
    elif street_similarity >= 0.82:
        score += 0.22
        reasons.append("street_fuzzy_match")
    elif _has_street_marker(source):
        score -= 0.30
        reasons.append("street_mismatch")

    if source_house:
        if candidate_house == source_house:
            score += 0.30
            reasons.append("house_match")
        elif candidate_house:
            score -= 0.45
            reasons.append("house_mismatch")
            strong_mismatch = True
        else:
            score -= 0.12
            reasons.append("house_missing")

    if source_block:
        if candidate_block == source_block:
            score += 0.10
            reasons.append("block_match")
        elif candidate_block:
            score -= 0.15
            reasons.append("block_mismatch")

    if data.get("geo_lat") is None or data.get("geo_lon") is None:
        score -= 0.50
        reasons.append("coordinates_missing")

    return max(0.0, min(0.99, score)), reasons, strong_mismatch


def _precision_from_data(data: dict) -> str:
    qc = data.get("qc_geo")
    if qc is not None:
        return QC_CONF.get(int(qc), ("unknown", 0.30))[0]
    if data.get("house"):
        return "house"
    if data.get("street"):
        return "street"
    if data.get("settlement"):
        return "settlement"
    if data.get("city"):
        return "city"
    return "unknown"


class DaDataGeocoder:
    def __init__(self, token: str, secret: str, timeout: float = 20):
        if not token or not secret:
            raise RuntimeError("Для DaData нужны DADATA_TOKEN и DADATA_SECRET")
        self.token = token
        self.secret = secret
        self.client = httpx.Client(timeout=timeout)

    def _suggest(self, query: str, count: int = 5) -> list[dict]:
        payload: dict = {"query": query, "count": count}
        normalized = _norm_text(query)
        if "санкт петербург" in normalized:
            payload["locations_boost"] = [{"city": "Санкт-Петербург"}]
        elif "ленинградская область" in normalized:
            payload["locations_boost"] = [{"region": "Ленинградская"}]
        r = self.client.post(
            "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        return list(r.json().get("suggestions") or [])

    def _clean(self, query: str) -> dict:
        r = self.client.post(
            "https://cleaner.dadata.ru/api/v1/clean/address",
            headers={
                "Authorization": f"Token {self.token}",
                "X-Secret": self.secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=[query],
        )
        r.raise_for_status()
        return r.json()[0]

    def geocode(self, address: str, district: str = "") -> GeoPoint:
        query = normalize_address_input(address, district)
        suggestions = self._suggest(query, count=5)
        ranked = []
        for item in suggestions:
            data = item.get("data") or {}
            if data.get("geo_lat") is None or data.get("geo_lon") is None:
                continue
            score, reasons, strong_mismatch = score_dadata_candidate(query, data)
            ranked.append((score, strong_mismatch, reasons, item, data))
        ranked.sort(key=lambda x: x[0], reverse=True)

        chosen = None
        status = "fallback_clean"
        margin = None
        if ranked:
            best = ranked[0]
            margin = best[0] - ranked[1][0] if len(ranked) > 1 else best[0]
            if best[0] >= 0.68 and margin >= 0.10 and not best[1]:
                chosen = best
                status = "resolved"

        if chosen is None:
            data = self._clean(query)
            if data.get("geo_lat") is None or data.get("geo_lon") is None:
                raise ValueError(f"DaData не вернула координаты: {address}")
            score, reasons, strong_mismatch = score_dadata_candidate(query, data)
            precision = _precision_from_data(data)
            qc = int(data.get("qc_geo") if data.get("qc_geo") is not None else 5)
            qc_conf = QC_CONF.get(qc, ("unknown", 0.30))[1]
            confidence = min(qc_conf, max(0.30, score))
            status = "strong_mismatch" if strong_mismatch else "review"
            raw = dict(data)
            raw["_resolver"] = {
                "status": status,
                "input": address,
                "query": query,
                "score": round(score, 3),
                "reasons": reasons,
                "suggestion_margin": None if margin is None else round(margin, 3),
                "candidates": [
                    {
                        "value": item.get("value"),
                        "score": round(s, 3),
                        "strong_mismatch": mismatch,
                        "reasons": why,
                    }
                    for s, mismatch, why, item, _ in ranked[:5]
                ],
            }
            return GeoPoint(
                lat=float(data["geo_lat"]),
                lon=float(data["geo_lon"]),
                provider="dadata_clean",
                normalized_address=data.get("result") or query,
                precision=precision,
                confidence=confidence,
                provider_ref=data.get("fias_id"),
                raw=raw,
            )

        score, _, reasons, item, data = chosen
        raw = dict(data)
        raw["_resolver"] = {
            "status": status,
            "input": address,
            "query": query,
            "score": round(score, 3),
            "reasons": reasons,
            "suggestion_margin": None if margin is None else round(margin, 3),
            "candidates": [
                {
                    "value": candidate.get("value"),
                    "score": round(s, 3),
                    "strong_mismatch": mismatch,
                    "reasons": why,
                }
                for s, mismatch, why, candidate, _ in ranked[:5]
            ],
        }
        return GeoPoint(
            lat=float(data["geo_lat"]),
            lon=float(data["geo_lon"]),
            provider="dadata_suggest",
            normalized_address=item.get("value") or query,
            precision=_precision_from_data(data),
            confidence=score,
            provider_ref=data.get("fias_id"),
            raw=raw,
        )


class PublicNominatimGeocoder:
    """Small-volume fallback only. Respect OSMF Nominatim public usage policy."""
    def __init__(self, user_agent: str, timeout: float = 20):
        self.user_agent = user_agent
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": user_agent})
        self._last = 0.0

    def geocode(self, address: str, district: str = "") -> GeoPoint:
        wait = 1.05 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        q = f"{address}, {district}, Россия" if district else f"{address}, Россия"
        r = self.client.get("https://nominatim.openstreetmap.org/search", params={
            "q": q, "format": "jsonv2", "limit": 3, "countrycodes": "ru", "addressdetails": 1,
        })
        self._last = time.monotonic()
        r.raise_for_status()
        items = r.json()
        if not items:
            raise ValueError(f"Nominatim не нашёл адрес: {address}")
        item = items[0]
        typ = item.get("type", "")
        conf = 0.85 if typ in {"house", "building"} else 0.65
        return GeoPoint(
            lat=float(item["lat"]), lon=float(item["lon"]), provider="nominatim",
            normalized_address=item.get("display_name", address),
            precision=typ or "unknown", confidence=conf,
            provider_ref=str(item.get("place_id", "")), raw=item,
        )
