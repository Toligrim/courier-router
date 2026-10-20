"""A missing administrative territory must not reject a matching delivery house."""
from types import SimpleNamespace

import pytest

from courier_router import cli, yandex_maps as ym
from courier_router.domain import GeoPoint


ADDRESS = (
    "г Санкт-Петербург, поселок Парголово, тер Осиновая Роща, "
    "Юкковское шоссе, д 14 к 3 кв 104"
)
TITLE = "Юкковское шоссе, 14к3"
SUBTITLE = (
    "Юкковское шоссе, 14к3, посёлок Парголово, Санкт-Петербург, 194362 "
    "Координаты: 60.108442, 30.271905 clipboard Маршрут Сохранить"
)


def config():
    return SimpleNamespace(geocoder="yandex", llm_provider="none")


def stop(address=ADDRESS):
    return SimpleNamespace(source_row=5, order_no=5, address_raw=address,
                           district="Выборгский", warnings=[], geo=None)


def result(title=TITLE, subtitle=SUBTITLE):
    return ym.YResult(60.108442, 30.271905, title, subtitle,
                      "https://yandex.ru/maps/house/example/", True)


@pytest.fixture
def store(tmp_path):
    db = cli.Storage(str(tmp_path / "cache.db"))
    yield db
    db.con.close()


@pytest.mark.parametrize("territory", ["тер Осиновая Роща", "тер. Осиновая Роща", "территория Осиновая Роща"])
def test_matching_house_with_omitted_territory_builds_without_override(monkeypatch, store, territory):
    address = ADDRESS.replace("тер Осиновая Роща", territory)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: result() for key, _, _ in jobs})
    s = stop(address)
    report = cli.geocode_stops(config(), [s], store, allow_low_confidence=False)

    assert report[0]["resolver_status"] == "review"
    assert report[0]["coord_status"] == "review"
    assert report[0]["requires_review"] is True
    assert (s.geo.lat, s.geo.lon) == (60.108442, 30.271905)
    assert s.geo.normalized_address == address
    assert store.get_geocode(address, s.district) is not None


def test_matching_territory_and_house_remain_verified(monkeypatch, store):
    y = result(subtitle=SUBTITLE.replace("посёлок Парголово", "территория Осиновая Роща, посёлок Парголово"))
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    report = cli.geocode_stops(config(), [stop()], store)
    assert report[0]["resolver_status"] == "verified"
    assert report[0]["requires_review"] is False


@pytest.mark.parametrize("y", [
    result(subtitle=SUBTITLE.replace("Парголово", "Пушкин")),
    result(subtitle=SUBTITLE.replace("Санкт-Петербург", "Ленинградская область")),
    result(title="Юкковское шоссе, 16к3"),
    result(title="Юкковское шоссе, 14к2"),
    result(title="Совсем другая улица, 14к3", subtitle="посёлок Парголово, Санкт-Петербург"),
])
def test_omitted_territory_does_not_hide_wrong_delivery_address(monkeypatch, store, y):
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    with pytest.raises(RuntimeError, match="Строка 5.*сильно расходится"):
        cli.geocode_stops(config(), [stop()], store)
    assert store.get_geocode(ADDRESS, "Выборгский") is None


@pytest.mark.parametrize("status", ["strong_mismatch", "review"])
def test_legacy_cached_territory_result_is_revalidated_without_network(monkeypatch, store, status):
    # Earlier builds could persist a false mismatch when the override was enabled.
    g = cli.get_geocoder(config())
    query = g.query(ADDRESS, "Выборгский")
    store.con.executescript(cli._YANDEX_CACHE_SCHEMA)
    y = result()
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found) VALUES(?,?,?,?,?,?,?,1)",
        (cli.Storage.key(query), y.lat, y.lon, y.title, y.subtitle, y.url, 1),
    )
    store.con.commit()
    store.put_geocode(ADDRESS, "Выборгский", GeoPoint(
        y.lat, y.lon, "yandex_maps", ADDRESS, "house", 0.65,
        raw={"_resolver": {"primary": True, "status": status, "query": query}},
    ))

    def no_network(jobs):
        pytest.fail("Revalidation must reuse the cached Yandex card")

    monkeypatch.setattr(ym, "lookup_batch", no_network)
    report = cli.geocode_stops(config(), [stop()], store)
    assert report[0]["source"] == "yandex:cache-refresh"
    assert report[0]["resolver_status"] == "review"
    assert report[0]["resolver_version"] == cli.RESOLVER_VERSION

    repeated = cli.geocode_stops(config(), [stop()], store)
    assert repeated[0]["source"] == "cache"


def test_mismatch_message_shows_actual_yandex_address(monkeypatch, store):
    title = "Юкковское шоссе, 16к3"
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: result(title=title) for key, _, _ in jobs})
    with pytest.raises(RuntimeError) as exc:
        cli.geocode_stops(config(), [stop()], store)
    assert f"Найдено: {title!r}." in str(exc.value)
