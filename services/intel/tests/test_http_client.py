"""Unit tests for services/intel/http_client.py: the timeout/one-retry/
never-raises contract every source fetcher relies on."""
from __future__ import annotations

import httpx
import pytest

from services.intel import http_client


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


def test_fetch_json_success_first_try(monkeypatch):
    calls = []

    def fake_request(self, method, url, **kw):
        calls.append(1)
        return _FakeResponse(200, json_data={"ok": True})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    data, err = http_client.fetch_json("https://x.test/a")
    assert data == {"ok": True} and err is None
    assert len(calls) == 1


def test_fetch_json_retries_once_on_timeout_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_request(self, method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("slow")
        return _FakeResponse(200, json_data={"ok": True})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)  # don't actually wait in tests
    data, err = http_client.fetch_json("https://x.test/a")
    assert data == {"ok": True} and err is None
    assert calls["n"] == 2


def test_fetch_json_persistent_failure_returns_none_and_reason(monkeypatch):
    def fake_request(self, method, url, **kw):
        raise httpx.ConnectError("dead")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    data, err = http_client.fetch_json("https://x.test/a")
    assert data is None
    assert err == "ConnectError"


def test_fetch_json_never_raises_on_unexpected_exception(monkeypatch):
    def fake_request(self, method, url, **kw):
        raise RuntimeError("something truly unexpected")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    data, err = http_client.fetch_json("https://x.test/a")  # must not raise
    assert data is None
    assert err is not None


def test_fetch_json_429_retries_once(monkeypatch):
    calls = {"n": 0}

    def fake_request(self, method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429)
        return _FakeResponse(200, json_data={"ok": True})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    data, err = http_client.fetch_json("https://x.test/a")
    assert data == {"ok": True} and err is None
    assert calls["n"] == 2


def test_fetch_json_404_does_not_retry():
    calls = {"n": 0}

    def fake_request(self, method, url, **kw):
        calls["n"] += 1
        return _FakeResponse(404)

    import httpx as _httpx
    orig = _httpx.Client.request
    _httpx.Client.request = fake_request
    try:
        data, err = http_client.fetch_json("https://x.test/a")
    finally:
        _httpx.Client.request = orig
    assert data is None
    assert err == "http_404"
    assert calls["n"] == 1, "a 404 is not retryable — retrying it wastes the timeout budget"


def test_fetch_json_bad_json_body(monkeypatch):
    def fake_request(self, method, url, **kw):
        return _FakeResponse(200, json_data=None)  # .json() raises inside _FakeResponse

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    data, err = http_client.fetch_json("https://x.test/a")
    assert data is None and err == "bad_json"


def test_fetch_text_success(monkeypatch):
    def fake_request(self, method, url, **kw):
        return _FakeResponse(200, text="<rss>hi</rss>")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    text, err = http_client.fetch_text("https://x.test/a")
    assert text == "<rss>hi</rss>" and err is None


def test_fetch_text_timeout_returns_none_not_raise(monkeypatch):
    def fake_request(self, method, url, **kw):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    text, err = http_client.fetch_text("https://x.test/a")
    assert text is None and err == "timeout"


@pytest.mark.parametrize("retries", [0, 2])
def test_fetch_json_custom_retry_count(monkeypatch, retries):
    calls = {"n": 0}

    def fake_request(self, method, url, **kw):
        calls["n"] += 1
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    http_client.fetch_json("https://x.test/a", retries=retries)
    assert calls["n"] == retries + 1


def test_fetch_json_post_sends_json_body_and_method(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kw):
        seen["method"] = method
        seen["json"] = kw.get("json")
        return _FakeResponse(200, json_data={"ok": True})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    data, err = http_client.fetch_json_post("https://x.test/info", {"type": "meta"})
    assert data == {"ok": True} and err is None
    assert seen["method"] == "POST"
    assert seen["json"] == {"type": "meta"}


def test_fetch_json_post_never_raises_on_timeout(monkeypatch):
    def fake_request(self, method, url, **kw):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    data, err = http_client.fetch_json_post("https://x.test/info", {"type": "meta"})
    assert data is None and err == "timeout"


def test_fetch_json_post_bad_json_body(monkeypatch):
    def fake_request(self, method, url, **kw):
        return _FakeResponse(200, json_data=None)

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    data, err = http_client.fetch_json_post("https://x.test/info", {"type": "meta"})
    assert data is None and err == "bad_json"


def test_fetch_json_get_is_unaffected_by_post_method_default(monkeypatch):
    """Guards against a refactor accidentally making GET send a method
    other than 'GET' (the retryable-status / never-raise contract is
    shared code between fetch_json and fetch_json_post)."""
    seen = {}

    def fake_request(self, method, url, **kw):
        seen["method"] = method
        return _FakeResponse(200, json_data={"ok": True})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    http_client.fetch_json("https://x.test/a")
    assert seen["method"] == "GET"


def test_gate_session_blocks_real_network():
    """The session guard in conftest must be active for the default (offline)
    selection: an unmocked fetch fails closed instead of reaching the network."""
    from services.intel import http_client
    data, err = http_client.fetch_json("https://api.gdeltproject.org/api/v2/doc/doc", retries=0)
    assert data is None
    assert err == "ConnectError"
