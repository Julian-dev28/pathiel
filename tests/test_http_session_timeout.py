"""The read-timeout guard must apply when the caller passes timeout=None.

hyperliquid.api.API.post calls `self.session.post(url, json=..., timeout=self.timeout)`
and the SDK default is None, so the kwarg is ALWAYS present. The original guard used
`kw.setdefault("timeout", ...)`, which never fires on a present key: every SDK read
ran without a timeout. The copycat dry run caught it on 2026-10-02 as
"ReadTimeout ... (read timeout=None)" out of get_hl_price -> Info.all_mids.
"""

from hyperliquid.api import API

from pathiel.client.http_session import _set_session_timeout


class _Session:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append(kw)
        return kw


class _Client:
    def __init__(self):
        self.session = _Session()


def _wrapped(timeout_s=10.0):
    client = _Client()
    seen = client.session
    _set_session_timeout(client, timeout_s)
    return client, seen


def test_explicit_none_timeout_gets_the_default():
    client, seen = _wrapped(7.0)
    client.session.request("POST", "u", timeout=None)
    assert seen.calls[-1]["timeout"] == 7.0


def test_missing_timeout_gets_the_default():
    client, seen = _wrapped(7.0)
    client.session.request("POST", "u")
    assert seen.calls[-1]["timeout"] == 7.0


def test_an_explicit_timeout_is_kept():
    client, seen = _wrapped(7.0)
    client.session.request("POST", "u", timeout=3)
    assert seen.calls[-1]["timeout"] == 3


def test_the_real_sdk_post_path_ends_up_with_a_timeout(monkeypatch):
    """End to end through the SDK's own API.post: the shape that actually hung."""
    api = API()
    assert api.timeout is None, "SDK default changed; re-check this guard"
    captured = {}

    def fake_request(method, url, **kw):
        captured.update(kw)
        raise RuntimeError("stop before network")

    monkeypatch.setattr(api.session, "request", fake_request)
    _set_session_timeout(api, 10.0)
    try:
        api.post("/info", {"type": "allMids"})
    except RuntimeError:
        pass
    assert captured["timeout"] == 10.0
