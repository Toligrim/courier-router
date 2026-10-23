"""Yandex-only geocoding: no DaData, cache migration and coordinate validation."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from courier_router import cli, yandex_maps as ym
from courier_router.config import Config
from courier_router.domain import GeoPoint
from courier_router.storage import Storage


ADDRESS = "Пушкин, бульвар Детско-Сельский, д 3 кв 161"


def config():
    # Even a leftover cross-check setting must not trigger another provider.
    return Config(geocoder="yandex", dadata_token="", dadata_secret="", ors_api_key="",
                  openai_api_key="", anthropic_api_key="", yandex_crosscheck=True)


def stop(row=3, address=ADDRESS):
    return SimpleNamespace(source_row=row, order_no=row, address_raw=address,
                           district="Пушкинский", warnings=[], geo=None)


def result(lat=59.722, lon=30.417, title="Детскосельский бульвар, 3", is_house=True):
    return ym.YResult(lat, lon, title, "Пушкин, Санкт-Петербург", "https://yandex.ru/maps/house/1/", is_house)


@pytest.fixture
def store(tmp_path):
    db = Storage(str(tmp_path / "cache.db"))
    yield db
    db.con.close()


@pytest.fixture(autouse=True)
def forbid_other_providers(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Yandex-only mode must not use another geocoder or the cross-check")
    monkeypatch.setattr(cli, "DaDataGeocoder", forbidden)
    monkeypatch.setattr(cli, "PublicNominatimGeocoder", forbidden)
    monkeypatch.setattr(cli, "_yandex_crosscheck", forbidden)


def test_yandex_only_refreshes_dadata_cache_and_reuses_new_cache(monkeypatch, store):
    y = result()
    calls = []

    def lookup(jobs):
        calls.append(jobs)
        return {key: y for key, _, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    store.put_geocode(ADDRESS, "Пушкинский", GeoPoint(59.9, 30.3, "dadata_suggest", "old", "house", 0.99))
    s = stop()
    report = cli.geocode_stops(config(), [s], store)

    assert len(calls) == 1
    assert "кв" not in calls[0][0][1] and "161" not in calls[0][0][1]
    assert s.geo.provider == "yandex_maps"
    assert s.geo.normalized_address == ADDRESS
    assert (s.geo.lat, s.geo.lon) == (y.lat, y.lon)
    assert report[0]["source"] == "yandex:cache-refresh"
    assert report[0]["coord_source"] == "yandex"
    assert report[0]["yandex_lat"] == y.lat and report[0]["dadata_lat"] is None
    assert report[0]["requires_review"] is False

    cached = cli.geocode_stops(config(), [stop()], store)
    assert len(calls) == 1
    assert cached[0]["source"] == "cache" and cached[0]["coord_source"] == "yandex"


def test_uncached_stops_share_one_browser_batch_and_alias_skips_lookup(monkeypatch, store):
    calls = []

    def lookup(jobs):
        calls.append(jobs)
        return {key: result(title=query) for key, query, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    pinned = stop(6, "Закреплённый адрес")
    store.put_alias(pinned.address_raw, pinned.district, 59.9, 30.3)
    stops = [stop(), stop(4, "Пушкин, ул Гусарская, д 2"), stop(5), pinned]

    report = cli.geocode_stops(config(), stops, store)

    assert len(calls) == 1 and len(calls[0]) == 2
    assert stops[-1].geo.provider == "alias"
    assert report[-1]["coord_source"] == "alias"


def test_primary_retries_previous_yandex_miss(monkeypatch, store):
    store.con.executescript(cli._YANDEX_CACHE_SCHEMA)
    q = cli.get_geocoder(config()).query(ADDRESS, "Пушкинский")
    store.con.execute("INSERT INTO yandex_maps_cache(cache_key,found) VALUES(?,0)", (q.lower(),))
    store.con.commit()
    calls = []

    def lookup(jobs):
        calls.append(jobs)
        return {key: result(title="Детско-Сельский бульвар, 3") for key, _, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    cli.geocode_stops(config(), [stop()], store)
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["dadata", "dadata_suggest", "dadata_clean", "nominatim"])
def test_other_provider_cache_is_not_accepted(provider):
    cached = GeoPoint(59.9, 30.3, provider, "old", "house", 0.99)
    assert not cli._cache_matches_geocoder(config(), cached)


def test_defaults_disable_dadata_and_clean(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("GEOCODER", raising=False)
    monkeypatch.delenv("DADATA_USE_CLEAN", raising=False)
    defaults = runpy.run_path(str(Path(cli.__file__).with_name("config.py")))["Config"]()
    assert defaults.geocoder == "yandex"
    assert defaults.dadata_use_clean is False


def test_missing_address_fails_without_another_provider(monkeypatch, store):
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {})
    with pytest.raises(RuntimeError, match="Строка 3.*Яндекс Карты не нашли адрес"):
        cli.geocode_stops(config(), [stop()], store)
    assert store.get_geocode(ADDRESS, "Пушкинский") is None


def test_missing_browser_reports_yandex_error(monkeypatch, store):
    def unavailable(jobs):
        raise ym.Unavailable("playwright не установлен")
    monkeypatch.setattr(ym, "lookup_batch", unavailable)
    with pytest.raises(ym.Unavailable, match="playwright не установлен"):
        cli.geocode_stops(config(), [stop()], store)


@pytest.mark.parametrize("y, message", [
    (result(55.75, 37.62, "Детско-Сельский бульвар, 3"), "вне СПб/Ленобласти"),
    (result(title="Детско-Сельский бульвар, 30"), "сильно расходится"),
    (result(title="Совсем другая улица, 3"), "сильно расходится"),
])
def test_bad_coordinates_are_blocked_and_not_cached(monkeypatch, store, y, message):
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    with pytest.raises(RuntimeError, match=message):
        cli.geocode_stops(config(), [stop()], store)
    assert store.get_geocode(ADDRESS, "Пушкинский") is None
    assert store.con.execute("SELECT COUNT(*) FROM yandex_maps_cache").fetchone()[0] == 0


def test_rejected_yandex_result_is_looked_up_again(monkeypatch, store):
    calls = []

    def lookup(jobs):
        calls.append(jobs)
        y = result(title="Детскосельский бульвар, 30") if len(calls) == 1 else result()
        return {key: y for key, _, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    with pytest.raises(RuntimeError, match="сильно расходится"):
        cli.geocode_stops(config(), [stop()], store)
    report = cli.geocode_stops(config(), [stop()], store)
    assert len(calls) == 2
    assert report[0]["coord_status"] == "ok"


def test_coarse_result_is_flagged_for_review(monkeypatch, store):
    y = result(title="Детско-Сельский бульвар", is_house=False)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    s = stop()
    report = cli.geocode_stops(config(), [s], store)
    assert s.coord_status == "review" and report[0]["requires_review"] is True


def test_unpinned_depot_uses_yandex(monkeypatch):
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {
        key: ym.YResult(59.849, 30.295, "улица Костюшко, 2", "Санкт-Петербург", "u", True)
        for key, _, _ in jobs})
    c = config()
    c.depot_lat = c.depot_lon = ""
    assert cli.depot(c).provider == "yandex_maps"


@pytest.mark.parametrize("address, title", [
    ("г Санкт-Петербург, ул Русановская, д 17 к 2 кв 237", "Русановская улица, 17к2"),
    ("г Санкт-Петербург, ул Бабушкина, д 115 к 1 кв 66", "улица Бабушкина, 115к1"),
    (ADDRESS, "Детскосельский бульвар, 3"),
    ("г Санкт-Петербург, шоссе Революции, д 37 к 1 кв 339", "шоссе Революции, 37к1"),
    ("г Санкт-Петербург, ул Караваевская, д 42", "Караваевская улица, 42"),
])
def test_actual_spreadsheet_addresses_match_yandex_cards(monkeypatch, store, address, title):
    y = result(title=title)
    y.subtitle = title + ", Пушкин, Санкт-Петербург, 193149 Координаты: 59.863924, 30.512015 clipboard Маршрут"
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    report = cli.geocode_stops(config(), [stop(address=address)], store)
    assert report[0]["resolver_status"] == "verified"
    assert report[0]["coord_status"] == "ok"


@pytest.mark.parametrize("text, expected", [
    ("Русановская улица, 17к2", "17"),
    ("улица Ленина, 17, корпус 1", "17"),
    ("улица Ленина, 17 стр 1", "17"),
    ("д 17 к 2", "17"),
    ("улица Бабушкина, 115к1", "115"),
    ("ул Колхозная, 12а", "12а"),
    ("ул Ленина, 12/3", "12/3"),
    ("улица Бабушкина, Санкт-Петербург, 192174 Координаты: 59.863071, 30.458125", None),
])
def test_yandex_house_number_ignores_blocks_and_card_noise(text, expected):
    assert ym._house_num(text) == expected


@pytest.mark.parametrize("address, title, subtitle, status", [
    ("Пушкин, ул Ленина, д 2", "улица Ленина, 2", "Кингисепп, Ленинградская область", "strong_mismatch"),
    ("улица Ленина, д 2, г Пушкин", "улица Ленина, 2", "Кингисепп, Ленинградская область", "strong_mismatch"),
    ("улица Ленина, д 2, г Пушкин", "улица Ленина, 2", "Пушкин, Санкт-Петербург", "verified"),
    ("Санкт-Петербург, Комендантский проспект, д 53, к 1", "Комендантский проспект, 53к2", "Санкт-Петербург", "strong_mismatch"),
    ("Санкт-Петербург, Комендантский проспект, д 53, к 1", "Комендантский проспект, 53", "Санкт-Петербург", "review"),
    ("Санкт-Петербург, ул Ленина, д 2 стр 1", "улица Ленина, 2 стр 2", "Санкт-Петербург", "strong_mismatch"),
    ("Санкт-Петербург, ул Ленина, д 17 стр 1", "улица Ленина, 17 стр 1", "Санкт-Петербург", "verified"),
    ("Санкт-Петербург, ул Ленина, д 17 корпус 1", "улица Ленина, 17, корпус 1", "Санкт-Петербург", "verified"),
    ("Санкт-Петербург, ул Ленина, д 2 литера А", "улица Ленина, 2", "Санкт-Петербург", "review"),
    ("Санкт-Петербург, 1-я линия Васильевского острова, д 24", "1-я линия Васильевского острова, 24", "Санкт-Петербург", "verified"),
    ("Санкт-Петербург, 1-я линия Васильевского острова, д 24", "2-я линия Васильевского острова, 24", "Санкт-Петербург", "strong_mismatch"),
])
def test_yandex_locality_structure_and_numbered_streets(monkeypatch, address, title, subtitle, status):
    y = ym.YResult(59.9, 30.3, title, subtitle, "u", True)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    geo = cli.get_geocoder(config()).geocode(address)
    assert geo.raw["_resolver"]["status"] == status
    assert (geo.confidence >= 0.9) == (status == "verified")


@pytest.mark.parametrize("card_title, expected_house, accepted", [
    ("проспект Энгельса, 93, подъезд 9", "93", True),
    ("проспект Энгельса, 93, парадная 9", "93", True),
    ("проспект Энгельса, 93, подъезд № 9", "93", True),
    ("проспект Энгельса, 95, подъезд 9", "95", False),
])
def test_yandex_entrance_is_not_mistaken_for_house(
        monkeypatch, store, card_title, expected_house, accepted):
    source = "г Санкт-Петербург, пр-кт Энгельса, д 93 парадная 9 кв 42"
    y = result(title=card_title)
    y.subtitle = "Санкт-Петербург"
    assert ym._house_num(card_title) == expected_house
    monkeypatch.setattr(
        ym, "lookup_batch",
        lambda jobs: {key: y for key, _, _ in jobs},
    )

    if not accepted:
        with pytest.raises(RuntimeError, match="сильно расходится"):
            cli.geocode_stops(config(), [stop(address=source)], store)
        assert store.get_geocode(source, "Пушкинский") is None
    else:
        report = cli.geocode_stops(config(), [stop(address=source)], store)
        assert report[0]["resolver_status"] == "verified"
        assert report[0]["coord_status"] == "ok"


@pytest.mark.parametrize("title, expected", [
    ("2-я Советская улица, 12, парадная 3", "12"),
    ("улица Ленина, 17, корпус 1, подъезд 4", "17"),
    ("улица Ленина, 17 стр 1, этаж 4", "17"),
    ("улица Бабушкина, Санкт-Петербург, 192174 Координаты: 59.863071, 30.458125", None),
])
def test_house_number_ignores_delivery_details_without_losing_street_number(title, expected):
    assert ym._house_num(title) == expected
