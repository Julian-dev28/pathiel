"""Auth on the operator surface after 2026-10-25: there is no shared token.

Until then a static PATHIEL_OPERATOR_TOKEN header opened 21 endpoints, several
of which move real money, to "machine callers" (the scheduler, the supervisor,
smoke checks). Every one of those callers is gone, the MCP server calls the
Python in-process, and the operator works from local mode (loopback peer,
loopback Host, loopback Origin). So the gate is now exactly two doors:

  local mode                  -> allowed, no credential
  a signed-in operator        -> allowed
  anyone else, any header     -> 401

A removed token is one less secret to provision, rotate and leak, and one
less way a deployment could be driven from outside.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pathiel.dashboard as db
from pathiel.server import app
from services.auth import integration

# A mutating, operator-gated endpoint: POST /api/agent/config flips the live
# mode (the old STOP TRADING button's endpoint).
_MUTATING_PATH = "/api/agent/config"
_MUTATING_BODY = {"mode": "OFF"}


@pytest.fixture
def not_deployed(monkeypatch):
    for k in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("PATHIEL_DASHBOARD_READONLY", raising=False)
    monkeypatch.delenv("PATHIEL_PUBLIC_DASHBOARD", raising=False)


def _remote():
    return TestClient(app, client=("203.0.113.9", 4444), raise_server_exceptions=False)


def _local():
    return TestClient(app, client=("127.0.0.1", 4444), raise_server_exceptions=False)


def test_the_operator_on_his_own_box_needs_no_credential(not_deployed, monkeypatch):
    import pathiel.server as srv
    monkeypatch.setattr(srv, "update_agent_config", lambda updates: {"mode": "OFF", **updates})
    r = _local().post(_MUTATING_PATH, json=_MUTATING_BODY, headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 200, r.text


def test_a_remote_caller_is_refused(not_deployed):
    assert _remote().post(_MUTATING_PATH, json=_MUTATING_BODY).status_code == 401


def test_a_cross_site_page_on_the_operators_box_is_refused(not_deployed):
    """Loopback peer (the operator's own browser) but a foreign Origin: a
    page on evil.example POSTing to 127.0.0.1 must not be local mode."""
    r = _local().post(_MUTATING_PATH, json=_MUTATING_BODY, headers={"Origin": "https://evil.example"})
    assert r.status_code == 401


def test_no_header_or_query_string_opens_the_gate_any_more(not_deployed, monkeypatch):
    """The old token, in the header or the URL, buys nothing even when the
    environment still carries one."""
    monkeypatch.setenv("PATHIEL_OPERATOR_TOKEN", "correct-horse-battery-staple")
    c = _remote()
    assert c.post(_MUTATING_PATH, json=_MUTATING_BODY,
                  headers={"X-Operator-Token": "correct-horse-battery-staple"}).status_code == 401
    assert c.post(f"{_MUTATING_PATH}?token=correct-horse-battery-staple",
                  json=_MUTATING_BODY).status_code == 401


def test_the_token_code_is_gone():
    """No `_require_operator`, no lockout table, no token comparison: a
    future "just read the header again" has nothing to hang off."""
    assert not hasattr(db, "_require_operator")
    assert not hasattr(db, "_AUTH_FAILURES")
    src = open(db.__file__).read()
    assert "X-Operator-Token" not in src
    assert 'query_params.get("token")' not in src


def test_every_operator_route_uses_the_role_gate():
    """The eight server.py routes that used the token now share the one gate
    the dashboard's house-account routes use."""
    import ast
    import pathlib
    src = (pathlib.Path(db.__file__).resolve().parent / "server.py").read_text()
    tree = ast.parse(src)
    gated = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Depends":
            if node.args and getattr(node.args[0], "id", None) == "_require_operator_role":
                gated += 1
    assert gated == 8, gated
    assert "_require_operator)" not in src
