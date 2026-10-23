"""pathiel.client.http_session._set_session_timeout: default timeouts and the
fresh-socket retry.

Regression 2026-10-23: after a network outage every exchange call from the
long-lived MCP server took ~15 minutes (place_bracket x2, 935 s and 931 s)
while curl from the same machine answered in milliseconds. A fresh Python
process was fast too. The pooled keep-alive sockets had died in the outage and
requests kept handing them out; each call waited the full read timeout on a
dead socket. The wrapper now drops the pool and retries once.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from pathiel.client.http_session import _set_session_timeout


class FakeSession:
    def __init__(self, fails):
        self.fails = list(fails)          # exceptions to raise, in order, before succeeding
        self.calls = []
        self.closed = 0

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("timeout")))
        if self.fails:
            raise self.fails.pop(0)
        return "ok"

    def close(self):
        self.closed += 1


def _client(fails=()):
    sess = FakeSession(fails)
    return _set_session_timeout(SimpleNamespace(session=sess)), sess


def test_default_timeout_is_connect_and_read_and_only_when_missing():
    client, sess = _client()
    client.session.request("POST", "u", timeout=None)        # the SDK passes None explicitly
    client.session.request("POST", "u", timeout=3)
    client.session.request("POST", "u")
    assert [c[2] for c in sess.calls] == [(5.0, 10.0), 3, (5.0, 10.0)]
    assert sess.closed == 0


@pytest.mark.parametrize("exc", [requests.exceptions.ReadTimeout("dead socket"),
                                 requests.exceptions.ConnectTimeout("no route"),
                                 requests.exceptions.ConnectionError("reset")])
def test_a_dead_socket_drops_the_pool_and_retries_once(exc):
    client, sess = _client([exc])
    assert client.session.request("POST", "u", timeout=None) == "ok"
    assert sess.closed == 1 and len(sess.calls) == 2


def test_two_failures_in_a_row_surface_the_second():
    client, sess = _client([requests.exceptions.ReadTimeout("a"), requests.exceptions.ReadTimeout("b")])
    with pytest.raises(requests.exceptions.ReadTimeout, match="b"):
        client.session.request("POST", "u", timeout=None)
    assert sess.closed == 1 and len(sess.calls) == 2          # never loops


def test_a_real_answer_is_not_retried():
    client, sess = _client([requests.exceptions.HTTPError("429")])
    with pytest.raises(requests.exceptions.HTTPError):
        client.session.request("POST", "u", timeout=None)
    assert sess.closed == 0 and len(sess.calls) == 1


def test_a_client_without_a_session_is_returned_untouched():
    obj = SimpleNamespace()
    assert _set_session_timeout(obj) is obj
