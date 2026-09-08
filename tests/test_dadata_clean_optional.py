"""DaData Clean недоступен (отключён у токена / квота) — geocoder не должен ронять
маршрут, а деградировать на лучшую Suggestions-подсказку с пометкой на проверку.
"""
from __future__ import annotations

import httpx
import pytest

from courier_router.geocode import DaDataGeocoder

# Два одинаковых кандидата → отрыв 0 < 0.10 → уверенного победителя нет →
# по старой логике пошли бы в Clean.
_SPB_CANDIDATE = {
    "value": "г Санкт-Петербург, улица Тестовая, д 1",
    "data": {
        "region": "г Санкт-Петербург",
        "city": "Санкт-Петербург",
        "street": "улица Тестовая",
        "house": "1",
        "fias_id": "fias-1",
        "geo_lat": "59.90",
        "geo_lon": "30.30",
    },
}
_QUERY = "Санкт-Петербург, улица Тестовая, д 1"


def _geocoder(use_clean: bool, clean_impl):
    g = DaDataGeocoder("token", "secret", use_clean=use_clean)
    g._suggest = lambda query, count=5: [dict(_SPB_CANDIDATE), dict(_SPB_CANDIDATE)]
    g._clean = clean_impl
    return g


def _raise_403(query):
    req = httpx.Request("POST", "https://cleaner.dadata.ru/api/v1/clean/address")
    resp = httpx.Response(403, request=req, text="Feature 'CLEAN' disabled for token")
    raise httpx.HTTPStatusError("403", request=req, response=resp)


def test_clean_403_degrades_to_best_suggestion():
    g = _geocoder(use_clean=True, clean_impl=_raise_403)
    point = g.geocode(_QUERY)

    assert point.provider == "dadata_suggest"
    assert (point.lat, point.lon) == (59.90, 30.30)
    resolver = point.raw["_resolver"]
    assert resolver["status"] == "review"
    assert "clean_http_403" in resolver["reasons"]


def test_clean_disabled_by_config_skips_clean_entirely():
    def _must_not_call(query):
        raise AssertionError("Clean не должен вызываться при use_clean=False")

    g = _geocoder(use_clean=False, clean_impl=_must_not_call)
    point = g.geocode(_QUERY)

    assert point.provider == "dadata_suggest"
    assert point.raw["_resolver"]["status"] == "review"
    assert "clean_disabled" in point.raw["_resolver"]["reasons"]


def test_no_suggestions_and_no_clean_still_raises():
    g = DaDataGeocoder("token", "secret", use_clean=False)
    g._suggest = lambda query, count=5: []
    with pytest.raises(ValueError, match="DaData не вернула адрес"):
        g.geocode(_QUERY)
