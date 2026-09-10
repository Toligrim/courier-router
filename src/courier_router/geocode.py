from __future__ import annotations

import re
import time
from difflib import SequenceMatcher

import httpx

from .domain import GeoPoint

RESOLVER_VERSION = 2

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
    value = value.replace("санкт-петербург", "санкт петербург")
    value = re.sub(r"[^0-9a-zа-я/ -]+", " ", value)
    return " ".join(value.split())


# Маркеры квартиры/офиса. Намеренно БЕЗ одиночного «к» и «с» — это корпус и
# строение, они влияют на точку и должны остаться в запросе.
_APARTMENT_MARK = r"кв|квартира|кварт|апарт(?:аменты)?|оф|офис|пом|помещение"
_APARTMENT_RE = re.compile(
    rf"(?:^|[\s,;]|(?<=\d))\s*(?:{_APARTMENT_MARK})\.?\s*№?\s*(\d+[а-яa-z]?)\b",
    re.I,
)
# «голый» номер сразу после дома (с необязательной литерой через пробел):
# «д.27 в 93» → «д.27 в», «д 27 93» → «д 27». Дробь дома («д 41/14») и корпус
# («д 34 к 3») не трогаем — литерой считаем одиночную букву, кроме к/с.
_TRAILING_FLAT_RE = re.compile(
    r"((?:^|[\s,])(?:д|дом)\.?\s*\d+(?:/\d+)?(?:\s+(?![кс]\b)[а-яa-z](?![а-яa-z]))?)"
    r"\s+\d{1,4}\b(?!\s*/)",
    re.I,
)


def apartment_of(address: str) -> str:
    """Достать номер квартиры/офиса из сырого адреса (для показа курьеру)."""
    m = _APARTMENT_RE.search(address or "")
    return m.group(1) if m else ""


def strip_apartment(address: str) -> str:
    """Убрать квартиру/офис из строки перед геокодированием: на поиск дома она не
    влияет, но регулярно ломает разбор номера дома («д.27 в 93», «д 23кв 33»)."""
    text = _APARTMENT_RE.sub(" ", address or "")
    text = _TRAILING_FLAT_RE.sub(r"\1", text)
    text = re.sub(r"\s*[,;]\s*[,;]+", ", ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,;")


def normalize_address_input(address: str, district: str = "") -> str:
    """Conservative cleanup for courier spreadsheets before external geocoding."""
    text = " ".join((address or "").strip().split())
    text = strip_apartment(text)
    text = re.sub(r"^\s*(?:спб|спб\.|пб)\s*[,;]?\s*", "Санкт-Петербург, ", text, flags=re.I)
    text = re.sub(r"^\s*ло\s*[,;]?\s*", "Ленинградская область, ", text, flags=re.I)
    text = re.sub(r"\bкоменданск(ий|ого|ому|им|ом)\b", r"Комендантск\1", text, flags=re.I)
    text = re.sub(r"\s*[,;]+\s*", ", ", text)
    text = re.sub(r",\s*,+", ", ", text)
    text = text.strip(" ,")
    # Колонка "район" в курьерских таблицах — это зона курьера, а не всегда реальный
    # административный район. Подставляем её в запрос только если в адресе нет своего
    # маркера населённого пункта/региона (иначе ломается геокодирование, напр.
    # "Красносельский, Ленинградская обл, ... деревня Пески" → DaData 0 кандидатов).
    has_locality = re.search(
        r"санкт-?петербург|ленинградск|\bгород\b|\bг\.?\s|\bдеревня\b|\bсел[оа]\b|\bпос[её]лок\b|\bпгт\b|\bдер\.?\s|\bпос\.?\s",
        text, re.I,
    )
    if district and not has_locality and _norm_text(district) not in _norm_text(text):
        text = f"{district}, {text}"
    return text


def _extract_house(source: str) -> str | None:
    # digits (+ optional /fraction) + optional single letter — но литеру берём только
    # если за ней не идёт ещё буква, иначе "д 23кв 33" съедало бы "к" из "кв".
    explicit = re.search(
        r"(?:^|[ ,])(?:д|дом)\.?\s*([0-9]+(?:/[0-9]+)?)(?:([а-яa-z])(?![а-яa-z]))?",
        source, re.I,
    )
    if explicit:
        house = _norm_text(explicit.group(1) + (explicit.group(2) or ""))
        # "д.27 в 93" — курьер отделил литеру дома пробелом. Приклеиваем её обратно,
        # но только настоящую литеру: не к/с (корпус/строение) и не номер квартиры.
        if house.isdigit():
            tail = re.match(r"\s*([а-яa-z])(?=[ ,]|$)", source[explicit.end():], re.I)
            if tail and tail.group(1).lower() not in {"к", "с"}:
                house += tail.group(1).lower()
        return house

    # Courier spreadsheets often contain a compact form like "Савушкина 15".
    # Strip apartment/office/structure values first, then accept a single remaining
    # standalone number as the house number. Ambiguous numeric street names are left
    # unresolved rather than guessed.
    cleaned = re.sub(
        r"(?:^|[ ,])(?:кв|квартира|офис|пом|помещение|к|корп|корпус|лит|литера|стр|строение)\.?\s*[0-9а-яa-z/-]+",
        " ", source, flags=re.I,
    )
    numbers = re.findall(r"(?<![-\w])([0-9]+[а-яa-z]?(?:/[0-9]+)?)(?![-\w])", cleaned, re.I)
    normalized = [_norm_text(x) for x in numbers]
    return normalized[0] if len(normalized) == 1 else None


def _same_house_number(a: str | None, b: str | None) -> bool:
    """'27' vs '27в', '12' vs '12/3' — один номер дома, разная литера/дробь.
    '27а' vs '27в' — разные дома (обе формы с литерой) → False."""
    a, b = (a or "").strip(), (b or "").strip()
    da = re.match(r"([0-9]+)", a)
    db = re.match(r"([0-9]+)", b)
    if not da or not db or da.group(1) != db.group(1):
        return False
    digits = da.group(1)
    short, long = sorted((a, b), key=len)
    return short == digits and long.startswith(digits) and long != short


def _extract_block(source: str) -> str | None:
    m = re.search(r"(?:^|[ ,])(?:к|корп|корпус)\.?\s*([0-9]+[а-яa-z]?)\b", source, re.I)
    return _norm_text(m.group(1)) if m else None


def _extract_structure(source: str) -> tuple[str, str] | None:
    m = re.search(
        r"(?:^|[ ,])(?P<kind>лит(?:ера)?|стр(?:оение)?)\.?\s*(?P<value>[0-9а-яa-z]+)\b",
        source, re.I,
    )
    if not m:
        return None
    kind = _norm_text(m.group("kind"))
    kind = "лит" if kind.startswith("лит") else "стр"
    return kind, _norm_text(m.group("value"))


def _candidate_structure(data: dict) -> tuple[str, str] | None:
    value = _norm_text(data.get("block"))
    if not value:
        return None
    kind = _norm_text(data.get("block_type") or data.get("block_type_full"))
    if kind.startswith("лит"):
        return "лит", value
    if kind.startswith("стр"):
        return "стр", value
    return None


def _street_tokens(value: str | None) -> set[str]:
    tokens = set(_norm_text(value).split())
    return {x for x in tokens if x not in GENERIC_STREET_WORDS and len(x) >= 3}


def _has_street_marker(source: str) -> bool:
    return bool(re.search(r"(?:^|[ ,])(?:ул|улица|пр-?кт|проспект|наб|набережная|ш|шоссе|пер|переулок)\.?\s", source, re.I))


def _candidate_locality(data: dict) -> str:
    return " ".join(str(data.get(key) or "") for key in ("region", "city", "settlement"))


def _street_similarity(source: str, street: str | None) -> float:
    candidate = _street_tokens(street)
    if not candidate:
        return 0.0
    source_tokens = _street_tokens(source)
    if candidate <= source_tokens:
        return 1.0

    # Score every candidate token, not only the single best token pair. This avoids
    # treating "Малая Морская" and "Большая Морская" as identical merely because
    # the word "Морская" matches exactly.
    scores = []
    for token in candidate:
        best = max((SequenceMatcher(None, token, src).ratio() for src in source_tokens), default=0.0)
        scores.append(best)
    return sum(scores) / len(scores)


def _score_locality(source: str, data: dict) -> tuple[float, list[str], bool]:
    normalized = _norm_text(source)
    locality = _norm_text(_candidate_locality(data))
    reasons: list[str] = []
    score = 0.0
    strong_mismatch = False

    if "санкт петербург" in normalized:
        if "санкт петербург" in locality:
            score += 0.25
            reasons.append("city_match")
        else:
            score -= 0.65
            reasons.append("city_mismatch")
            strong_mismatch = True

    if "ленинградская область" in normalized or "ленинградская обл" in normalized:
        if "ленинградск" in locality:
            score += 0.20
            reasons.append("region_match")
        else:
            score -= 0.55
            reasons.append("region_mismatch")
            strong_mismatch = True

    # If the source explicitly names a city/settlement, require that named locality
    # to be present in the candidate locality. Skip Saint Petersburg because it is
    # handled above. Match on the original comma-bearing string, not the
    # comma-stripped _norm_text output, so "^|,\s*" actually anchors to a boundary
    # and "г Санкт-Петербург, <улица>" is not misread as a named locality.
    m = re.search(
        r"(?:^|,)\s*(?:г|город|деревня|дер|поселок|посёлок|пос|село)\.?\s+([А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z -]{2,})",
        source, re.I,
    )
    if m:
        named = _norm_text(m.group(1)).strip()
        named = re.split(r"\s+(?:ул|улица|пр|проспект|наб|ш|шоссе|пер|переулок|б-р|линия)\b", named, maxsplit=1)[0].strip()
        if named and "санкт петербург" not in named:
            if named in locality:
                score += 0.15
                reasons.append("locality_match")
            else:
                score -= 0.45
                reasons.append("locality_mismatch")
                strong_mismatch = True

    return score, reasons, strong_mismatch


def score_dadata_candidate(source: str, data: dict) -> tuple[float, list[str], bool]:
    """Score address components, not only DaData's coarse qc_geo value."""
    score = 0.15
    reasons: list[str] = []
    strong_mismatch = False

    locality_score, locality_reasons, locality_mismatch = _score_locality(source, data)
    score += locality_score
    reasons.extend(locality_reasons)
    strong_mismatch |= locality_mismatch

    source_house = _extract_house(source)
    source_block = _extract_block(source)
    source_structure = _extract_structure(source)
    candidate_house = _norm_text(data.get("house")) or None
    candidate_block = _norm_text(data.get("block")) or None
    candidate_structure = _candidate_structure(data)

    street_similarity = _street_similarity(source, data.get("street"))
    if street_similarity >= 0.95:
        score += 0.35
        reasons.append("street_match")
    elif street_similarity >= 0.82:
        score += 0.22
        reasons.append("street_fuzzy_match")
    elif _has_street_marker(source) or data.get("street"):
        score -= 0.30
        reasons.append("street_mismatch")

    if source_house:
        if candidate_house == source_house:
            score += 0.30
            reasons.append("house_match")
        elif candidate_house and _same_house_number(source_house, candidate_house):
            # тот же номер дома, разница только в литере/дроби ("27" ↔ "27в",
            # "12" ↔ "12/3") — это одна точка, не эскалируем до strong_mismatch
            score += 0.10
            reasons.append("house_letter_diff")
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

    if source_structure:
        if candidate_structure == source_structure:
            score += 0.08
            reasons.append("structure_match")
        elif candidate_structure:
            score -= 0.18
            reasons.append("structure_mismatch")
            strong_mismatch = True
        else:
            score -= 0.08
            reasons.append("structure_missing")

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


def _resolver_payload(status: str, address: str, query: str, score: float, reasons: list[str], margin: float | None, ranked: list) -> dict:
    return {
        "version": RESOLVER_VERSION,
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


def _clean_failure_reason(exc: Exception) -> str:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"clean_http_{status}" if status else "clean_unavailable"


class DaDataGeocoder:
    def __init__(self, token: str, secret: str, timeout: float = 20, use_clean: bool = True):
        if not token or not secret:
            raise RuntimeError("Для DaData нужны DADATA_TOKEN и DADATA_SECRET")
        self.token = token
        self.secret = secret
        self.use_clean = use_clean
        self.client = httpx.Client(timeout=timeout)

    def _suggest(self, query: str, count: int = 5) -> list[dict]:
        payload: dict = {"query": query, "count": count}
        normalized = _norm_text(query)
        if "санкт петербург" in normalized:
            payload["locations_boost"] = [{"city": "Санкт-Петербург"}]
        elif "ленинградская область" in normalized or "ленинградская обл" in normalized:
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

        degraded_reason: str | None = None
        if chosen is None:
            clean_data = None
            if self.use_clean:
                try:
                    clean_data = self._clean(query)
                except httpx.HTTPError as exc:
                    degraded_reason = _clean_failure_reason(exc)
            else:
                degraded_reason = "clean_disabled"

            if clean_data and clean_data.get("geo_lat") is not None and clean_data.get("geo_lon") is not None:
                data = clean_data
                score, reasons, strong_mismatch = score_dadata_candidate(query, data)
                qc = int(data.get("qc_geo") if data.get("qc_geo") is not None else 5)
                qc_conf = QC_CONF.get(qc, ("unknown", 0.30))[1]
                confidence = min(qc_conf, max(0.30, score))
                status = "strong_mismatch" if strong_mismatch else "review"
                raw = dict(data)
                raw["_resolver"] = _resolver_payload(status, address, query, score, reasons, margin, ranked)
                return GeoPoint(
                    lat=float(data["geo_lat"]),
                    lon=float(data["geo_lon"]),
                    provider="dadata_clean",
                    normalized_address=data.get("result") or query,
                    precision=_precision_from_data(data),
                    confidence=confidence,
                    provider_ref=data.get("fias_id"),
                    raw=raw,
                )

            # Clean tier unavailable (disabled for the token, quota, network) or empty:
            # fall back to the best Suggestions candidate so one missing tier does not
            # abort the whole route. Such a point is flagged for manual review.
            if not ranked:
                detail = f" ({degraded_reason})" if degraded_reason else ""
                raise ValueError(f"DaData не вернула адрес: {address}{detail}")
            chosen = ranked[0]
            status = "strong_mismatch" if chosen[1] else "review"

        score, _, reasons, item, data = chosen
        if degraded_reason:
            reasons = list(reasons) + [degraded_reason]
        raw = dict(data)
        raw["_resolver"] = _resolver_payload(status, address, query, score, reasons, margin, ranked)
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
