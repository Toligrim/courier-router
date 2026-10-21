"""Route-sheet line abbreviations must not become locality mismatches."""
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from courier_router import cli, yandex_maps as ym
from courier_router.domain import GeoPoint
from courier_router.geocode import normalize_address_input
from courier_router.parsing import read_excel


MENDELEEV = "г Санкт-Петербург, лн Менделеевская, д 3 литера А"
NUMBERED = "г Санкт-Петербург, лн 15-я В.О., д 80 кв 20"


def config():
    return SimpleNamespace(geocoder="yandex", llm_provider="none")


def stop(address=MENDELEEV):
    return SimpleNamespace(source_row=3, order_no=502, address_raw=address,
                           district="Василеостровский", warnings=[], geo=None)


def card(title="Менделеевская линия, 3", subtitle="Санкт-Петербург"):
    return ym.YResult(59.94, 30.30, title, subtitle,
                      "https://yandex.ru/maps/house/example/", True)


@pytest.fixture
def store(tmp_path):
    db = cli.Storage(str(tmp_path / "cache.db"))
    yield db
    db.con.close()


@pytest.mark.parametrize("marker", ["лн", "лн.", "ЛН", "линия"])
def test_named_line_without_letter_builds_and_preserves_source(monkeypatch, store, marker):
    address = MENDELEEV.replace("лн", marker)
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: card() for key, _, _ in jobs})
    s = stop(address)
    report = cli.geocode_stops(config(), [s], store)
    assert report[0]["resolver_status"] == "review"
    assert report[0]["requires_review"] is True
    assert s.geo.normalized_address == address
    assert s.address_raw == address
    assert store.get_geocode(address, s.district) is not None


@pytest.mark.parametrize("street", [
    "лн 15-я В.О.", "лн. 15-я В. О.", "линия 15-я ВО", "15-я линия В.О.",
    "15-я линия Васильевского острова",
])
def test_numbered_line_and_island_abbreviations_are_verified(monkeypatch, street):
    y = card("15-я линия Васильевского острова, 80")
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    address = f"г Санкт-Петербург, {street}, д 80 кв 20"
    geo = cli.get_geocoder(config()).geocode(address)
    assert geo.raw["_resolver"]["status"] == "verified"
    assert geo.normalized_address == address


@pytest.mark.parametrize("y", [
    card("Совсем другая улица, 3"),
    card("Менделеевская линия, 30"),
    card("Менделеевская линия, 3 литера Б"),
    card(subtitle="Пушкин, Ленинградская область"),
])
def test_abbreviated_line_keeps_rejecting_conflicting_address(monkeypatch, store, y):
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    with pytest.raises(RuntimeError, match="Строка 3.*сильно расходится"):
        cli.geocode_stops(config(), [stop()], store)
    assert store.get_geocode(MENDELEEV, "Василеостровский") is None


def test_island_abbreviation_is_not_expanded_on_unrelated_streets():
    source = "Санкт-Петербург, ул Тестовая, д 3, офис В.О."
    assert normalize_address_input(source) == source


def test_semicolon_separated_numbered_line_is_verified(monkeypatch):
    y = card("15-я линия Васильевского острова, 80")
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: y for key, _, _ in jobs})
    address = "г Санкт-Петербург; лн 15-я В.О.; д 80 кв 20"
    geo = cli.get_geocoder(config()).geocode(address)
    assert "линия 15-я Васильевского острова" in geo.raw["_resolver"]["query"]
    assert geo.raw["_resolver"]["status"] == "verified"
    assert geo.normalized_address == address


def test_old_false_mismatch_is_revalidated(monkeypatch, store):
    y = card()
    query = cli.get_geocoder(config()).query(MENDELEEV)
    store.con.executescript(cli._YANDEX_CACHE_SCHEMA)
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (cli.Storage.key(query), y.lat, y.lon, y.title, y.subtitle, y.url, 1),
    )
    store.con.commit()
    store.put_geocode(MENDELEEV, "Василеостровский", GeoPoint(
        y.lat, y.lon, "yandex_maps", MENDELEEV, "house", 0.65,
        raw={"_resolver": {"primary": True, "version": 2, "status": "strong_mismatch"}},
    ))

    def no_network(jobs):
        pytest.fail("The cached Yandex card should be revalidated locally")

    monkeypatch.setattr(ym, "lookup_batch", no_network)
    report = cli.geocode_stops(config(), [stop()], store)
    assert report[0]["source"] == "yandex:cache-refresh"
    assert report[0]["resolver_status"] == "review"
    assert report[0]["resolver_version"] == cli.RESOLVER_VERSION
    assert cli.geocode_stops(config(), [stop()], store)[0]["source"] == "cache"


def test_full_route_sheet_with_both_line_forms_builds(monkeypatch, store, tmp_path):
    # Delivery addresses from the failing seven-stop sheet, without customer data.
    addresses_and_titles = [
        ("г Санкт-Петербург, шоссе Революции, д 37 к 1 кв 339", "шоссе Революции, 37к1"),
        ("г Санкт-Петербург, ул Гороховая, д 68 кв 29", "Гороховая улица, 68"),
        (MENDELEEV, "Менделеевская линия, 3"),
        ("г Санкт-Петербург, пр-кт КИМа, д 4 кв 147", "проспект КИМа, 4"),
        (NUMBERED, "15-я линия Васильевского острова, 80"),
        ("г Санкт-Петербург, пр-кт Маршала Блюхера, д 50 кв 63", "проспект Маршала Блюхера, 50"),
        ("г Санкт-Петербург, ул Осипенко, д 4 к 1 кв 519", "улица Осипенко, 4к1"),
    ]
    workbook = Workbook()
    for i, (address, _) in enumerate(addresses_and_titles, 1):
        workbook.active.append(["Отвоз", i, "", "", address, "", "17:00-20:30", ""])
    source = tmp_path / "route.xlsx"
    workbook.save(source)
    by_query = {cli.get_geocoder(config()).query(address): card(title)
                for address, title in addresses_and_titles}
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {key: by_query[query] for key, query, _ in jobs})
    stops = read_excel(source)
    report = cli.geocode_stops(config(), stops, store)
    assert len(report) == len(stops) == 7
    assert [r["resolver_status"] for r in report] == [
        "verified", "verified", "review", "verified", "verified", "verified", "verified",
    ]
