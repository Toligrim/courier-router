from types import SimpleNamespace
import sqlite3
import threading

import pytest

from courier_router import cli, yandex_maps as ym
from courier_router.config import Config
from courier_router.domain import GeoPoint
from courier_router.storage import Storage


def config():
    return Config(geocoder="yandex", dadata_token="", dadata_secret="", ors_api_key="",
                  openai_api_key="", anthropic_api_key="")


def card(lat=59.9, lon=30.3, title="Гороховая улица, 68"):
    return ym.YResult(lat, lon, title, "Санкт-Петербург", "https://yandex.ru/maps/", True)


def test_legacy_success_is_refetched_without_dropping_manual_alias(monkeypatch, tmp_path):
    store = Storage(str(tmp_path / "legacy.db"))
    store.con.execute(
        """CREATE TABLE yandex_maps_cache (
        cache_key TEXT PRIMARY KEY, lat REAL, lon REAL, title TEXT, subtitle TEXT,
        url TEXT, is_house INTEGER, found INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    query = "г Санкт-Петербург, ул Гороховая, д 68"
    cache_key = " ".join(query.lower().split())
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (cache_key, 60.111111, 30.111111, "Гороховая улица, 68", "Санкт-Петербург", "old", 1),
    )
    store.put_alias("Закреплённый адрес", "", 59.8, 30.2, note="ручная проверка")
    store.con.commit()
    calls = []

    def lookup(jobs):
        calls.extend(jobs)
        return {key: card() for key, _, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    found = cli._yandex_lookup_cached(config(), store, [("address", query, None)])

    assert len(calls) == 1
    assert (found["address"].lat, found["address"].lon) == (59.9, 30.3)
    row = store.con.execute(
        "SELECT lat,lon,found,extractor_version FROM yandex_maps_cache WHERE cache_key=?",
        (cache_key,),
    ).fetchone()
    assert row == (59.9, 30.3, 1, cli._YANDEX_EXTRACTOR_VERSION)
    assert store.get_alias("Закреплённый адрес") is not None
    assert store.get_alias("Закреплённый адрес").raw["note"] == "ручная проверка"
    store.con.close()


def test_current_warm_cache_is_reused_without_browser(monkeypatch, tmp_path):
    store = Storage(str(tmp_path / "current.db"))
    query = "улица Гороховая, 68"
    cache_key = " ".join(query.lower().split())
    store.con.execute(cli._YANDEX_CACHE_SCHEMA)
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found,extractor_version) "
        "VALUES(?,?,?,?,?,?,?,1,?)",
        (cache_key, 59.9, 30.3, "Гороховая улица, 68", "Санкт-Петербург", "fresh", 1,
         cli._YANDEX_EXTRACTOR_VERSION),
    )
    store.con.commit()

    def no_browser(_jobs):
        raise AssertionError("a current extractor cache entry must be reused")

    monkeypatch.setattr(ym, "lookup_batch", no_browser)
    found = cli._yandex_lookup_cached(config(), store, [("address", query, None)])

    assert found["address"].url == "fresh"
    assert store.con.execute(
        "SELECT extractor_version FROM yandex_maps_cache WHERE cache_key=?", (cache_key,)
    ).fetchone()[0] == cli._YANDEX_EXTRACTOR_VERSION
    store.con.close()


def test_legacy_writer_on_new_schema_is_refetched(monkeypatch, tmp_path):
    store = Storage(str(tmp_path / "rolling-update.db"))
    query = "улица Гороховая, 68"
    cache_key = " ".join(query.lower().split())
    store.con.execute(cli._YANDEX_CACHE_SCHEMA)
    # An older application version does not know the extractor_version column.
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (cache_key, 60.111111, 30.111111, "Гороховая улица, 68", "Санкт-Петербург", "legacy", 1),
    )
    store.con.commit()
    calls = []

    def lookup(jobs):
        calls.extend(jobs)
        return {key: card() for key, _, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    found = cli._yandex_lookup_cached(config(), store, [("address", query, None)])

    assert len(calls) == 1
    assert (found["address"].lat, found["address"].lon) == (59.9, 30.3)
    assert store.con.execute(
        "SELECT extractor_version FROM yandex_maps_cache WHERE cache_key=?", (cache_key,)
    ).fetchone()[0] == cli._YANDEX_EXTRACTOR_VERSION
    store.con.close()


def test_cached_miss_keeps_retrying_under_existing_retry_policy(monkeypatch, tmp_path):
    store = Storage(str(tmp_path / "miss.db"))
    query = "улица Гороховая, 68"
    cache_key = " ".join(query.lower().split())
    store.con.execute(cli._YANDEX_CACHE_SCHEMA)
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,found) VALUES(?,0)", (cache_key,)
    )
    store.con.commit()
    calls = []

    def lookup(jobs):
        calls.append(jobs)
        return {}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    for _ in range(2):
        assert cli._yandex_lookup_cached(config(), store, [("address", query, None)], retry_misses=True) == {
            "address": None
        }

    assert len(calls) == 2
    assert store.con.execute(
        "SELECT found,extractor_version FROM yandex_maps_cache WHERE cache_key=?", (cache_key,)
    ).fetchone() == (0, cli._YANDEX_EXTRACTOR_VERSION)
    store.con.close()


def test_concurrent_legacy_schema_migration_serializes_and_rereads(tmp_path):
    path = tmp_path / "concurrent.db"
    setup = sqlite3.connect(path)
    setup.execute(
        """CREATE TABLE yandex_maps_cache (
        cache_key TEXT PRIMARY KEY, lat REAL, lon REAL, title TEXT, subtitle TEXT,
        url TEXT, is_house INTEGER, found INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    setup.commit()
    setup.close()

    owner = sqlite3.connect(path, timeout=5)
    worker_con = sqlite3.connect(path, timeout=5, check_same_thread=False)
    worker_started = threading.Event()
    worker_finished = threading.Event()
    failures = []
    owner.execute("BEGIN IMMEDIATE")

    def migrate():
        try:
            worker_started.set()
            cli._ensure_yandex_cache_schema(SimpleNamespace(con=worker_con))
        except BaseException as exc:
            failures.append(exc)
        finally:
            worker_finished.set()

    thread = threading.Thread(target=migrate)
    thread.start()
    assert worker_started.wait(timeout=2)
    assert not worker_finished.wait(timeout=0.1)
    # The worker is held at BEGIN IMMEDIATE. Simulate the other builder
    # completing the additive migration before the waiting builder reads schema.
    owner.execute(
        "ALTER TABLE yandex_maps_cache ADD COLUMN extractor_version INTEGER NOT NULL DEFAULT 0"
    )
    owner.commit()
    assert worker_finished.wait(timeout=5)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert failures == []
    assert [row[1] for row in owner.execute("PRAGMA table_info(yandex_maps_cache)")].count(
        "extractor_version"
    ) == 1
    owner.close()
    worker_con.close()


def test_geocode_refreshes_legacy_card_when_final_resolver_cache_is_v4(monkeypatch, tmp_path):
    store = Storage(str(tmp_path / "end-to-end.db"))
    address = "г Санкт-Петербург, ул Гороховая, д 68"
    query = cli._yandex_query(cli.get_geocoder(config()).query(address))
    cache_key = " ".join(query.lower().split())
    store.con.execute(
        """CREATE TABLE yandex_maps_cache (
        cache_key TEXT PRIMARY KEY, lat REAL, lon REAL, title TEXT, subtitle TEXT,
        url TEXT, is_house INTEGER, found INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
    )
    store.con.execute(
        "INSERT INTO yandex_maps_cache(cache_key,lat,lon,title,subtitle,url,is_house,found) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (cache_key, 60.111111, 30.111111, "Гороховая улица, 68", "Санкт-Петербург", "old", 1),
    )
    store.put_geocode(address, "", GeoPoint(
        60.111111, 30.111111, "yandex_maps", address, "house", 0.99,
        raw={"_resolver": {"primary": True, "version": 4, "query": query}},
    ))
    store.con.commit()
    calls = []
    fresh = card()

    def lookup(jobs):
        calls.extend(jobs)
        return {key: fresh for key, _, _ in jobs}

    monkeypatch.setattr(ym, "lookup_batch", lookup)
    stop = SimpleNamespace(source_row=2, order_no=2, address_raw=address, district="",
                           warnings=[], geo=None)
    report = cli.geocode_stops(config(), [stop], store)

    assert len(calls) == 1
    assert (stop.geo.lat, stop.geo.lon) == (fresh.lat, fresh.lon)
    assert report[0]["source"] == "yandex:cache-refresh"
    assert report[0]["resolver_version"] == cli.RESOLVER_VERSION
    assert store.con.execute(
        "SELECT lat,lon,extractor_version FROM yandex_maps_cache WHERE cache_key=?", (cache_key,)
    ).fetchone() == (fresh.lat, fresh.lon, cli._YANDEX_EXTRACTOR_VERSION)
    store.con.close()
