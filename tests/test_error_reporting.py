import logging

import pytest

from courier_router import cli, web, yandex_maps as ym
from test_web import _client_logged_in
from test_yandex_primary import config, stop


def test_timeout_is_preserved_through_geocoding_and_is_not_negative_cached(tmp_path, monkeypatch):
    from courier_router.errors import GeocodingError
    from courier_router.storage import Storage
    store = Storage(str(tmp_path / "cache.db"))
    monkeypatch.setattr(ym, "lookup_batch", lambda jobs: {
        key: GeocodingError("timeout", "coordinate selector timed out") for key, _, _ in jobs})
    with pytest.raises(RuntimeError) as caught:
        cli.geocode_stops(config(), [stop()], store)
    from courier_router.errors import user_error
    message = user_error(caught.value)
    assert "дождаться" in message and "Строка 3" in message
    assert store.con.execute("SELECT COUNT(*) FROM yandex_maps_cache").fetchone()[0] == 0
    store.con.close()


@pytest.mark.parametrize("code, fragment", [("timeout", "дождаться"), ("unavailable", "недоступен"),
    ("blocked", "ограничил"), ("extraction", "прочитать"), ("not_found", "проверьте")])
def test_web_errors_are_friendly_and_correlated(tmp_path, monkeypatch, caplog, code, fragment):
    from courier_router.errors import GeocodingError
    client = _client_logged_in(monkeypatch, tmp_path)
    def fail(args):
        raise GeocodingError(code, "INTERNAL Playwright /opt/app/private traceback")
    monkeypatch.setattr(web, "cmd_plan", fail)
    with caplog.at_level(logging.INFO, logger="courier_router"):
        response = client.post("/routes", data={"day":"2026-10-26","depart":"10:00","end":"open"},
            files={"table":("route.xlsx", b"test", "application/octet-stream")}, follow_redirects=False)
    assert response.status_code == 400
    assert fragment in response.text
    assert "INTERNAL" not in response.text and "Playwright" not in response.text
    assert "Номер ошибки:" in response.text
    request_id = response.headers["X-Request-ID"]
    assert request_id in response.text
    failed = [r for r in caplog.records if r.getMessage() == "route.build.failed"]
    assert len(failed) == 1 and failed[0].event_fields["request_id"] == request_id
    assert failed[0].event_fields["run_id"]
    assert failed[0].exc_info
    client.close()


def test_unexpected_error_never_exposes_exception_text(tmp_path, monkeypatch):
    client = _client_logged_in(monkeypatch, tmp_path)
    def fail(args):
        raise RuntimeError("password=secret123 /opt/private SQLite traceback")
    monkeypatch.setattr(web, "cmd_plan", fail)
    response = client.post("/routes", data={"day":"2026-10-26","depart":"10:00"},
        files={"table":("route.xlsx", b"test")}, follow_redirects=False)
    assert response.status_code == 400
    assert "secret123" not in response.text and "/opt/private" not in response.text
    assert "Номер ошибки:" in response.text


def test_request_ids_are_server_generated(tmp_path, monkeypatch):
    client = _client_logged_in(monkeypatch, tmp_path)
    first = client.get("/health", headers={"X-Request-ID":"untrusted-value"})
    second = client.get("/health")
    assert first.headers["X-Request-ID"] != "untrusted-value"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_coordinate_timeout_raises_typed_error_instead_of_not_found():
    from courier_router.errors import GeocodingError
    class Page:
        def wait_for_selector(self, *args, **kwargs):
            raise TimeoutError("coordinate selector timed out")
    with pytest.raises(GeocodingError) as caught:
        ym._coords_from_card(Page())
    assert caught.value.code == "timeout"


def test_coordinate_text_without_numbers_is_extraction_failure():
    from courier_router.errors import GeocodingError
    class Element:
        def inner_text(self): return "Координаты:"
    class Page:
        def wait_for_selector(self, *args, **kwargs): return Element()
    with pytest.raises(GeocodingError) as caught:
        ym._coords_from_card(Page())
    assert caught.value.code == "extraction"


def test_actual_not_found_is_a_different_user_message():
    from courier_router.errors import GeocodingError, user_error
    assert "дождаться" not in user_error(GeocodingError("not_found", "empty response"))


def test_batch_retries_timeout_and_preserves_other_success(monkeypatch):
    from courier_router.errors import GeocodingError
    from playwright import sync_api
    class Page:
        url = "https://yandex.ru/maps/"
        def close(self): pass
    class Browser:
        def new_context(self, **kw): return self
        def new_page(self): return Page()
        def close(self): pass
    class Playwright:
        chromium = None
        def __enter__(self): self.chromium = self; return self
        def __exit__(self, *a): pass
        def launch(self, **kw): return Browser()
    monkeypatch.setattr(sync_api, "sync_playwright", Playwright)
    calls = []
    result = ym.YResult(59.9,30.3,"street, 1","city","https://yandex.ru/maps/house/1",True)
    def one(page,address,near):
        calls.append(address)
        if address == "slow": raise GeocodingError("timeout", "too slow")
        return result
    monkeypatch.setattr(ym, "_one", one)
    found = ym.lookup_batch([("a","slow",None),("b","good",None)])
    assert isinstance(found["a"], GeocodingError) and found["a"].code == "timeout"
    assert found["b"] is result
    assert calls == ["slow", "slow", "good"]


def test_unexpected_http_endpoint_error_is_logged_and_safe(tmp_path, monkeypatch, caplog):
    client = _client_logged_in(monkeypatch, tmp_path)
    monkeypatch.setattr(web, "_list_runs", lambda user: (_ for _ in ()).throw(RuntimeError("private-database-path")))
    with caplog.at_level(logging.INFO, logger="courier_router"):
        response = client.get("/")
    assert response.status_code == 500
    assert "private-database-path" not in response.text
    assert response.headers["X-Request-ID"] in response.text
    assert any(r.getMessage() == "request.failed" and r.exc_info for r in caplog.records)


def test_transient_failure_does_not_break_optional_yandex_crosscheck(tmp_path, monkeypatch):
    from courier_router.errors import GeocodingError
    from courier_router.storage import Storage
    from courier_router.domain import GeoPoint
    store = Storage(str(tmp_path / "cache.db"))
    point = stop()
    point.geo = GeoPoint(59.9,30.3,"dadata","test", "house",0.99)
    monkeypatch.setattr(ym,"lookup_batch",lambda jobs:{key:GeocodingError("timeout","slow") for key,_,_ in jobs})
    report = [{"coord_source":"dadata"}]
    cli._yandex_crosscheck(config(),[point],report,store)
    assert report[0]["coord_source"] == "dadata"
    store.con.close()


@pytest.mark.parametrize("failure", ["new_page", "page_close", "browser_close"])
def test_review_browser_lifecycle_preserves_completed_results(monkeypatch, failure):
    from courier_router.errors import GeocodingError
    from playwright import sync_api
    class Page:
        url = "https://yandex.ru/maps/"
        def close(self):
            if failure == "page_close": raise RuntimeError("page close failed")
    class Browser:
        calls = 0
        def new_context(self, **kw): return self
        def new_page(self):
            self.calls += 1
            if failure == "new_page" and self.calls == 2: raise RuntimeError("new page failed")
            return Page()
        def close(self):
            if failure == "browser_close": raise RuntimeError("browser close failed")
    class Playwright:
        chromium = None
        def __enter__(self): self.chromium = self; return self
        def __exit__(self,*a): pass
        def launch(self, **kw): return Browser()
    monkeypatch.setattr(sync_api,"sync_playwright",Playwright)
    result = ym.YResult(59.9,30.3,"street, 1","city","https://yandex.ru/maps/house/1",True)
    monkeypatch.setattr(ym,"_one",lambda *a:result)
    found = ym.lookup_batch([("first","good",None),("second","other",None)])
    assert found["first"] is result
    if failure == "new_page": assert isinstance(found["second"],GeocodingError)
    else: assert found["second"] is result
