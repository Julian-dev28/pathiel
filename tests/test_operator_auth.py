"""Auth on the operator surface: 21 endpoints, several of which move real money.

Three gaps, all cheap to close and all closed here:
  - `!=` on a secret returns early at the first differing byte and leaks the
    token prefix through response timing
  - no failure ceiling: an unlimited-attempt endpoint on the public internet is
    a token that gets guessed eventually
  - no audit trail: a leaked token would leave no record of what was done

The load-bearing subtlety is in the lockout. Refusing a VALID token during a
lockout would let anyone spraying wrong guesses from a shared egress IP lock the
operator out of their own kill switch — turning a brute-force defence into a
denial of service on the one control that must never be unreachable.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import hermes_trader.dashboard as db
from hermes_trader.server import app

TOKEN = "correct-horse-battery-staple"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HERMES_OPERATOR_TOKEN", TOKEN)
    with db._AUTH_LOCK:
        db._AUTH_FAILURES.clear()
    return TestClient(app)


def _wrong(c, n=1):
    for _ in range(n):
        r = c.post("/api/agent/stop", headers={"X-Operator-Token": "nope"})
    return r


# ── the basics ───────────────────────────────────────────────────────────────

def test_a_wrong_token_is_rejected(client):
    assert _wrong(client).status_code == 401


def test_a_missing_token_fails_closed(client, monkeypatch):
    monkeypatch.delenv("HERMES_OPERATOR_TOKEN", raising=False)
    assert client.post("/api/agent/stop").status_code == 503


def test_the_comparison_is_constant_time():
    """A plain != leaks the token prefix through timing."""
    import inspect
    src = inspect.getsource(db._require_operator)
    assert "compare_digest" in src
    assert "provided != expected" not in src


# ── the ceiling ──────────────────────────────────────────────────────────────

def test_repeated_wrong_tokens_hit_a_ceiling(client):
    _wrong(client, db._AUTH_MAX_FAILURES)
    assert _wrong(client).status_code == 429


def test_a_correct_token_still_works_during_a_lockout(client):
    """The property that keeps a brute-force defence from becoming a denial of
    service on the kill switch."""
    _wrong(client, db._AUTH_MAX_FAILURES + 2)
    r = client.post("/api/agent/stop", headers={"X-Operator-Token": TOKEN})
    assert r.status_code != 429, (
        "a valid token was refused during lockout — anyone spraying guesses "
        "from a shared IP could lock the operator out of their own kill switch")


def test_a_successful_auth_clears_the_counter(client):
    _wrong(client, db._AUTH_MAX_FAILURES - 1)
    client.post("/api/agent/stop", headers={"X-Operator-Token": TOKEN})
    assert _wrong(client).status_code == 401, "the counter was not cleared"


def test_the_failure_table_cannot_grow_without_bound(client, monkeypatch):
    """A spray from many source addresses must not exhaust memory."""
    now = 1000.0
    for i in range(db._AUTH_CLIENTS_MAX + 50):
        db._note_auth_failure(f"10.0.0.{i}", now)
    assert len(db._AUTH_FAILURES) <= db._AUTH_CLIENTS_MAX


def test_old_failures_stop_counting(client):
    import time
    now = time.time()
    db._note_auth_failure("1.2.3.4", now - db._AUTH_WINDOW_S - 1)
    assert db._auth_failures("1.2.3.4", now) == 0


# ── the audit trail ──────────────────────────────────────────────────────────

def test_a_mutating_action_is_audited(client, monkeypatch):
    seen = []
    monkeypatch.setattr(db.session_log, "append", lambda e: seen.append(e))
    client.post("/api/agent/stop", headers={"X-Operator-Token": TOKEN})
    actions = [e for e in seen if e.get("event") == "operator_action"]
    assert any(e["action"] == "authorized" for e in actions)
    assert actions[0]["path"] == "/api/agent/stop"


def test_a_failed_attempt_is_audited(client, monkeypatch):
    seen = []
    monkeypatch.setattr(db.session_log, "append", lambda e: seen.append(e))
    _wrong(client)
    assert any(e.get("action") == "auth_failed" for e in seen)


def test_reads_are_not_audited(client, monkeypatch):
    """Auditing every authenticated GET would bury the actions that matter under
    dashboard polling."""
    seen = []
    monkeypatch.setattr(db.session_log, "append", lambda e: seen.append(e))
    client.get("/api/dashboard/summary", headers={"X-Operator-Token": TOKEN})
    assert not [e for e in seen if e.get("action") == "authorized"]


def test_the_audit_never_blocks_the_action(client, monkeypatch):
    """An audit-log failure must not be the reason a kill switch does not
    fire."""
    def boom(_e):
        raise OSError("disk full")

    monkeypatch.setattr(db.session_log, "append", boom)
    r = client.post("/api/agent/stop", headers={"X-Operator-Token": TOKEN})
    assert r.status_code != 500


def test_the_audit_records_no_token(client, monkeypatch):
    seen = []
    monkeypatch.setattr(db.session_log, "append", lambda e: seen.append(e))
    client.post("/api/agent/stop", headers={"X-Operator-Token": TOKEN})
    assert TOKEN not in str(seen)
