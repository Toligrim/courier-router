from courier_router.navlinks import (
    RoutePoint,
    build_yandex_url,
    validate_yandex_url,
    haversine_m,
)

DEPOT = RoutePoint("start", "База", 59.849129, 30.295513)


def _stops():
    return [
        RoutePoint("via", "точка 1", 59.8616, 30.1821, "275"),
        RoutePoint("via", "точка 2", 59.8360, 30.2570, "345"),
    ]


def test_round_trip_url_structure_and_validation():
    pts = [DEPOT, *_stops(), RoutePoint("finish", "База", DEPOT.lat, DEPOT.lon)]
    url = build_yandex_url(pts)
    assert url.startswith("yandexnavi://build_route_on_map?")
    assert "lat_from=59.849129" in url and "lon_from=30.295513" in url
    assert "lat_to=59.849129" in url          # кольцевой: финиш = старт
    assert "lat_via_0=59.861600" in url and "lat_via_1=59.836000" in url
    assert "lat_via_2" not in url             # ровно 2 промежуточные точки
    assert validate_yandex_url(url, pts) == []


def test_open_end_url_last_point_is_finish():
    pts = [DEPOT, _stops()[0], _stops()[1]]   # финиш = последняя доставка (не кольцо)
    url = build_yandex_url(pts)
    assert "lat_to=59.836000" in url
    assert "lat_via_0=59.861600" in url and "lat_via_1" not in url
    # validate_yandex_url заточен под кольцевой маршрут — для открытого конца
    # ожидаемо ругается только на "старт != финиш", остальное должно быть чисто.
    problems = validate_yandex_url(url, pts)
    assert problems == ["старт и финиш в URL различаются (ожидался кольцевой маршрут)"]


def test_validation_flags_swapped_lat_lon():
    bad = [RoutePoint("start", "x", 30.3, 59.9), DEPOT, RoutePoint("finish", "x", 30.3, 59.9)]
    problems = validate_yandex_url(build_yandex_url(bad), bad)
    assert any("вне зоны" in p for p in problems)


def test_build_yandex_url_needs_two_points():
    import pytest
    with pytest.raises(ValueError):
        build_yandex_url([DEPOT])


def test_haversine_known_distance():
    # ~1.11 км на градус широты у экватора → на 59° около 0.9 км/0.01°
    d = haversine_m((59.90, 30.30), (59.91, 30.30))
    assert 1100 < d < 1120
