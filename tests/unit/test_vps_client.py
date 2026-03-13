"""Unit tests for VPSAgentWebClient request/retry behavior."""
from __future__ import annotations

import io
import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from memory_tool.migrate_out.approval.config import VPSAgentWebConfig
from memory_tool.migrate_out.approval.vps_client import (
    VPSAgentWebClient,
    VPSAgentWebError,
)


class _FakeResponse:
    def __init__(self, status: int, body: str, content_type: str = "application/json"):
        self._status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def _make_http_error(status: int, body: dict) -> HTTPError:
    return HTTPError(
        url="https://example.com/api",
        code=status,
        msg=f"HTTP {status}",
        hdrs=None,
        fp=io.BytesIO(json.dumps(body).encode("utf-8")),
    )


@pytest.fixture
def vps_client() -> VPSAgentWebClient:
    config = VPSAgentWebConfig(
        url="https://vps-agent-web.example.test",
        timeout_seconds=9,
        retry_count=3,
    )
    return VPSAgentWebClient(config)


def test_make_request_raises_on_4xx_with_error_payload(vps_client, monkeypatch):
    error = _make_http_error(
        409,
        {"error": {"message": "conflict", "code": "version_conflict"}},
    )
    try:
        monkeypatch.setattr(
            "memory_tool.migrate_out.approval.vps_client.urlopen",
            lambda *args, **kwargs: (_ for _ in ()).throw(error),
        )
        monkeypatch.setattr("memory_tool.migrate_out.approval.vps_client.time.sleep", lambda _: None)

        with pytest.raises(VPSAgentWebError, match="conflict") as exc_info:
            vps_client._make_request("POST", "/api/v1/jobs")

        err = exc_info.value
        assert err.status_code == 409
        assert err.error_code == "version_conflict"
        assert err.response_body["error"]["message"] == "conflict"
    finally:
        error.close()


def test_make_request_retries_on_5xx_and_returns_success(vps_client, monkeypatch):
    attempts = {"count": 0}
    server_error = _make_http_error(503, {"error": {"message": "upstream unavailable"}})

    def _fake_urlopen(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise server_error
        return _FakeResponse(200, json.dumps({"success": True}))

    sleep_calls: list[float] = []

    try:
        monkeypatch.setattr("memory_tool.migrate_out.approval.vps_client.urlopen", _fake_urlopen)
        monkeypatch.setattr(
            "memory_tool.migrate_out.approval.vps_client.time.sleep",
            lambda delay: sleep_calls.append(delay),
        )

        status, body = vps_client._make_request("GET", "/healthz")
        assert attempts["count"] == 2
        assert sleep_calls, "retry backoff should sleep at least once"
        assert status == 200
        assert body["success"] is True
    finally:
        server_error.close()


def test_make_request_handles_non_json_response_body(vps_client, monkeypatch):
    monkeypatch.setattr(
        "memory_tool.migrate_out.approval.vps_client.urlopen",
        lambda *args, **kwargs: _FakeResponse(
            502,
            "<html>bad gateway</html>",
            content_type="text/html",
        ),
    )

    status, body = vps_client._make_request("GET", "/healthz")
    assert status == 502
    assert "Non-JSON response: <html>bad gateway</html>" in body["error"]["message"]


def test_make_request_exhausts_retries_on_timeout(vps_client, monkeypatch):
    monkeypatch.setattr(
        "memory_tool.migrate_out.approval.vps_client.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout("connect timeout")),
    )
    monkeypatch.setattr("memory_tool.migrate_out.approval.vps_client.time.sleep", lambda _: None)

    with pytest.raises(VPSAgentWebError, match="Request failed after 3 attempts") as exc_info:
        vps_client._make_request("GET", "/healthz")

    assert "connect timeout" in str(exc_info.value)
    assert exc_info.value.status_code == 0


def test_make_request_exhausts_retries_on_url_error(vps_client, monkeypatch):
    monkeypatch.setattr(
        "memory_tool.migrate_out.approval.vps_client.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("connection refused")),
    )
    monkeypatch.setattr("memory_tool.migrate_out.approval.vps_client.time.sleep", lambda _: None)

    with pytest.raises(VPSAgentWebError, match="Request failed after 3 attempts") as exc_info:
        vps_client._make_request("GET", "/healthz")

    assert "connection refused" in str(exc_info.value)
    assert exc_info.value.status_code == 0
