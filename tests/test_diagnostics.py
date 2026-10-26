import asyncio
import io
import json
import logging

import pytest


@pytest.fixture
def diagnostics_logger():
    from courier_router import diagnostics as d
    logger = logging.getLogger("courier_router")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    yield d, logger
    for handler in logger.handlers:
        if handler not in old_handlers:
            handler.close()
    logger.handlers = old_handlers
    logger.setLevel(old_level)
    logger.propagate = old_propagate


def test_json_log_persists_context_and_traceback(diagnostics_logger, tmp_path):
    d, logger = diagnostics_logger
    path = tmp_path / "logs" / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    with d.log_context(request_id="req123", run_id="run123"):
        with pytest.raises(ValueError), d.stage("geocoding", source_row=5):
            raise ValueError("provider failed")
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    failed = next(e for e in entries if e["event"] == "stage.failed")
    assert failed["request_id"] == "req123" and failed["run_id"] == "run123"
    assert failed["stage"] == "geocoding" and failed["source_row"] == 5
    assert failed["elapsed_ms"] >= 0
    assert "ValueError: provider failed" in failed["traceback"]
    assert failed["timestamp"].endswith("Z")
    assert path.stat().st_mode & 0o777 == 0o600


def test_secret_redaction_covers_fields_messages_and_tracebacks(diagnostics_logger, tmp_path, monkeypatch):
    d, logger = diagnostics_logger
    monkeypatch.setenv("ORS_API_KEY", "super-private-api-key")
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    try:
        raise RuntimeError("https://provider.test/?apikey=super-private-api-key&token=unknown-token")
    except RuntimeError:
        d.log_event("provider.failed", level=logging.ERROR, exc_info=True,
                    token="secret-value", detail="Authorization: Bearer hidden-bearer")
    raw = path.read_text()
    for secret in ("super-private-api-key", "unknown-token", "hidden-bearer", "secret-value"):
        assert secret not in raw
    assert "[REDACTED]" in raw


def test_rotation_is_bounded_and_keeps_json_lines(diagnostics_logger, tmp_path):
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, max_bytes=600, backup_count=2, console=io.StringIO())
    for i in range(40):
        d.log_event("rotation.test", number=i, detail="x" * 100)
    files = list(tmp_path.glob("application.jsonl*"))
    assert len(files) == 3
    for f in files:
        assert f.stat().st_size <= 600
        for line in f.read_text().splitlines():
            json.loads(line)
    assert json.loads(path.read_text().splitlines()[-1])["number"] == 39


def test_context_isolated_between_tasks_and_propagated_to_worker(diagnostics_logger, tmp_path):
    from starlette.concurrency import run_in_threadpool
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    async def request(key):
        with d.log_context(request_id=key):
            await asyncio.sleep(0)
            await run_in_threadpool(d.log_event, "worker.test", expected=key)
    async def run():
        await asyncio.gather(request("first"), request("second"))
    asyncio.run(run())
    d.log_event("outside.test")
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert all(e["request_id"] == e["expected"] for e in entries if e["event"] == "worker.test")
    assert "request_id" not in entries[-1]


def test_unwritable_path_falls_back_without_breaking_request(diagnostics_logger, tmp_path):
    d, logger = diagnostics_logger
    obstacle = tmp_path / "not-a-directory"
    obstacle.write_text("file")
    output = io.StringIO()
    d.configure_logging(obstacle / "application.jsonl", console=output)
    d.log_event("still.running")
    assert "logging.file_unavailable" in output.getvalue()
    assert "still.running" in output.getvalue()


def test_file_write_failure_does_not_raise(diagnostics_logger, tmp_path, monkeypatch):
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    output = io.StringIO()
    d.configure_logging(path, console=output)
    file_handler = next(h for h in logger.handlers if hasattr(h, "baseFilename"))
    monkeypatch.setattr(file_handler, "shouldRollover", lambda record: (_ for _ in ()).throw(OSError("disk full")))
    d.log_event("disk.full.test")
    assert "disk.full.test" in output.getvalue()


def test_long_event_is_bounded(diagnostics_logger, tmp_path):
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    d.log_event("large.event", detail="x" * 100_000)
    assert path.stat().st_size < 40_000


def test_existing_log_permissions_are_restricted(diagnostics_logger, tmp_path):
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    path.write_text("")
    path.chmod(0o644)
    d.configure_logging(path, console=io.StringIO())
    assert path.stat().st_mode & 0o777 == 0o600


def test_quoted_credentials_in_exception_text_are_redacted(diagnostics_logger, tmp_path):
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    d.log_event("unsafe.provider.error", detail="headers={'Authorization': 'Bearer arbitrary-secret'} password='another-secret'")
    raw = path.read_text()
    assert "arbitrary-secret" not in raw
    assert "another-secret" not in raw


def test_web_failure_is_persisted_with_its_displayed_id(diagnostics_logger, tmp_path, monkeypatch):
    from courier_router import web
    from courier_router.errors import GeocodingError
    from test_web import _client_logged_in
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    client = _client_logged_in(monkeypatch, tmp_path)
    def fail(args):
        with d.stage("geocode"):
            raise GeocodingError("timeout", "provider coordinate timeout")
    monkeypatch.setattr(web, "cmd_plan", fail)
    response = client.post("/routes", data={"day":"2026-10-26","depart":"10:00"},
                           files={"table":("route.xlsx",b"test")},follow_redirects=False)
    request_id = response.headers["X-Request-ID"]
    assert request_id in response.text
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    related = [e for e in entries if e.get("request_id") == request_id]
    assert {"route.build.started","stage.failed","route.build.failed","request.completed"} <= {e["event"] for e in related}
    failed = next(e for e in related if e["event"] == "route.build.failed")
    assert failed["run_id"] and "GeocodingError" in failed["traceback"]
    assert "provider coordinate timeout" not in response.text


@pytest.mark.parametrize("detail, secrets", [
    ("Authorization: Basic dXNlcjpzZWNyZXQ=", ["dXNlcjpzZWNyZXQ="]),
    ("Cookie: sessionid=first-cookie; csrftoken=second-cookie", ["first-cookie", "second-cookie"]),
    ("https://alice:unknown-password@example.test/?access_token=external-token&client_secret=external-secret",
     ["alice", "unknown-password", "external-token", "external-secret"]),
])
def test_review_credentials_in_exception_are_redacted(diagnostics_logger, tmp_path, detail, secrets):
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    try:
        raise RuntimeError(detail)
    except RuntimeError:
        d.log_event("unsafe.exception", level=logging.ERROR, exc_info=True)
    raw = path.read_text()
    for secret in secrets:
        assert secret not in raw


def test_review_address_mismatch_does_not_persist_apartment(diagnostics_logger, tmp_path, monkeypatch):
    from courier_router import cli, yandex_maps as ym
    from courier_router.storage import Storage
    from test_yandex_primary import config, stop
    d, logger = diagnostics_logger
    path = tmp_path / "application.jsonl"
    d.configure_logging(path, console=io.StringIO())
    wrong = ym.YResult(59.9,30.3,"Совсем другая улица, 3","Пушкин, Санкт-Петербург","https://yandex.ru/maps/house/1",True)
    monkeypatch.setattr(ym,"lookup_batch",lambda jobs:{key:wrong for key,_,_ in jobs})
    store = Storage(str(tmp_path / "cache.db"))
    with pytest.raises(RuntimeError), d.stage("geocode"):
        cli.geocode_stops(config(),[stop()],store)
    assert "кв 161" not in path.read_text()
    store.con.close()
