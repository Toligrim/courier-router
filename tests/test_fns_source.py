import io
import re
from zipfile import ZipFile

import httpx
import pytest

from courier_router.fns_source import LATEST_URL, RangeReader, gar_archive, latest_release, official_url

URL = "https://fias-file.nalog.ru/downloads/2026.10.20/gar_xml.zip"


def range_server(payload, *, etag='"version-1"'):
    def serve(request):
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers["range"])
        start, end = map(int, match.groups())
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(206, content=payload[start:end + 1], headers={
            "content-range": f"bytes {start}-{end}/{len(payload)}", "etag": etag,
        })
    return serve


def test_real_zip_can_be_read_through_bounded_ranges(gar_factory):
    payload = gar_factory().read_bytes()
    with httpx.Client(transport=httpx.MockTransport(range_server(payload))) as client:
        with gar_archive(URL, client) as remote, ZipFile(io.BytesIO(payload)) as local:
            assert remote.namelist() == local.namelist()
            for name in remote.namelist():
                assert remote.read(name) == local.read(name)


def test_server_ignoring_range_is_rejected_without_reading_entire_archive():
    class NeverRead(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Must not read a 58 GB response")
            yield b""
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=NeverRead()))) as client:
        with pytest.raises(ValueError, match="HTTP Range"):
            RangeReader(client, URL)


@pytest.mark.parametrize("headers, body", [
    ({"content-range": "bytes 0-0/100"}, b"xx"),
    ({"content-range": "bytes 1-1/100"}, b"x"),
])
def test_wrong_initial_range_fails(headers, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(206, headers=headers, content=body))) as client:
        with pytest.raises(ValueError):
            RangeReader(client, URL)


def test_changed_archive_etag_is_rejected():
    first = True
    def serve(request):
        nonlocal first
        etag = '"one"' if first else '"two"'
        first = False
        return range_server(b"hello", etag=etag)(request)
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        reader = RangeReader(client, URL)
        with pytest.raises(ValueError, match="изменился"):
            reader.read(5)


def test_metadata_contract_and_url_validation():
    data = {"VersionId": 20261020, "Date": "20.10.2026", "GarXMLFullURL": URL}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))) as client:
        assert latest_release(client) == {"version": "20261020", "date": "20.10.2026", "url": URL}
    for url in ("http://fias-file.nalog.ru/test.zip", "https://example.com/test.zip",
                "https://fias-file.nalog.ru@evil.example/test.zip"):
        with pytest.raises(ValueError):
            official_url(url)


def test_redirect_is_checked_even_with_follow_redirects_client():
    calls = []
    def serve(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.com/gar.zip"})
    with httpx.Client(transport=httpx.MockTransport(serve), follow_redirects=True) as client:
        with pytest.raises(ValueError, match="официальный"):
            latest_release(client)
    assert calls == [LATEST_URL]


@pytest.mark.parametrize("failure", ["disconnect", "short_body"])
def test_interrupted_range_retries_without_caching_partial_bytes(monkeypatch, failure):
    monkeypatch.setattr("courier_router.fns_source.time.sleep", lambda _: None)
    calls, closed = [], []
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"he"
            if failure == "disconnect":
                raise httpx.RemoteProtocolError("peer closed connection")
        def close(self):
            closed.append(True)
    def serve(request):
        calls.append(request.headers["range"])
        if len(calls) == 2:
            return httpx.Response(206, headers={"content-range": "bytes 0-4/5", "etag": '"version-1"'},
                                  stream=Interrupted())
        return range_server(b"hello")(request)
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        reader = RangeReader(client, URL)
        assert reader.read() == b"hello"
        assert reader.blocks[0] == b"hello"
        assert reader.transferred == 8  # Probe + incomplete attempt + complete retry.
    assert calls == ["bytes=0-0", "bytes=0-4", "bytes=0-4"]
    assert closed == [True]


def test_persistent_body_error_is_bounded_and_keeps_reader_position(monkeypatch):
    monkeypatch.setattr("courier_router.fns_source.time.sleep", lambda _: None)
    calls = []
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b"he"
            raise httpx.RemoteProtocolError("peer closed connection")
    def serve(request):
        calls.append(request.headers["range"])
        if len(calls) == 1:
            return range_server(b"hello")(request)
        return httpx.Response(206, headers={"content-range": "bytes 0-4/5"}, stream=Interrupted())
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        reader = RangeReader(client, URL)
        with pytest.raises(httpx.RemoteProtocolError):
            reader.read()
        assert not reader.blocks
        assert reader.tell() == 0
        assert reader.transferred == 7
    assert len(calls) == 4


def test_metadata_and_initial_probe_retry_body_disconnect(monkeypatch):
    monkeypatch.setattr("courier_router.fns_source.time.sleep", lambda _: None)
    calls = []
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadError("connection reset")
            yield b""
    def serve(request):
        calls.append(str(request.url))
        if str(request.url) == LATEST_URL:
            if calls.count(LATEST_URL) == 1:
                return httpx.Response(200, stream=Interrupted())
            return httpx.Response(200, json={"VersionId": 20261020, "GarXMLFullURL": URL})
        if calls.count(URL) == 1:
            return httpx.Response(206, headers={"content-range": "bytes 0-0/5"}, stream=Interrupted())
        return range_server(b"hello")(request)
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        assert latest_release(client)["url"] == URL
        assert RangeReader(client, URL).read() == b"hello"
