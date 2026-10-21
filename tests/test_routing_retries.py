import json

import httpx
import pytest

from courier_router.routing import ORSRouter


@pytest.fixture
def router(monkeypatch):
    sleeps = []
    monkeypatch.setattr("courier_router.routing.time.sleep", sleeps.append)
    router = ORSRouter("test-key")
    yield router, sleeps
    router.client.close()


def use_transport(router, handler):
    router.client.close()
    router.client = httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_matrix_recovers_after_transient_transport_failure(router, error):
    client, sleeps = router
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise error("temporary network failure", request=request)
        return httpx.Response(200, json={"durations": [[0, 60], [60, 0]],
                                        "distances": [[0, 500], [500, 0]]})

    use_transport(client, handler)
    assert client.matrix([(59.9, 30.3), (59.94, 30.30)]) == (
        [[0, 60], [60, 0]], [[0, 500], [500, 0]],
    )
    assert len(calls) == 2 and sleeps == [0.5]
    assert json.loads(calls[0].content) == json.loads(calls[1].content)


def test_persistent_transport_failure_is_bounded(router):
    client, sleeps = router
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectTimeout("TLS timeout", request=request)

    use_transport(client, handler)
    with pytest.raises(httpx.ConnectTimeout, match="TLS timeout"):
        client.matrix([(59.9, 30.3)])
    assert len(calls) == 3 and sleeps == [0.5, 1.0]


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_nonretryable_http_errors_fail_immediately(router, status):
    client, sleeps = router
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    use_transport(client, handler)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        client.matrix([(59.9, 30.3)])
    assert exc.value.response.status_code == status
    assert len(calls) == 1 and sleeps == []


def test_transport_and_server_failures_share_retry_budget(router):
    client, sleeps = router
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        if len(calls) == 2:
            raise httpx.ConnectError("temporary failure", request=request)
        return httpx.Response(200, json={"features": [{"geometry": {"coordinates": [[30.3, 59.9]]}}]})

    use_transport(client, handler)
    assert client.geometry([(59.9, 30.3)]) == [(59.9, 30.3)]
    assert len(calls) == 3 and sleeps == [0.5, 1.0]
