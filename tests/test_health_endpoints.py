"""Health that means something.

/api/health answered {"status": "running"} unconditionally, and both fly.toml
and k8s/statefulset.yaml pointed probes at it. A dead trading loop behind a live
web process therefore read as perfectly healthy to every monitor — the same
silent-failure shape as a data outage reading as a quiet market, or an unrotated
log filling a disk until the box stops.

The split matters as much as the check:
  /api/health         LIVENESS of the web process. Always 200 if it can serve.
  /api/health/system  Is the SYSTEM working. 503 when it is not.

A liveness probe that failed because the trading loop died would restart the web
container, which fixes nothing and destroys the dashboard that is the only way
to see what happened.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import hermes_trader.dashboard as db
from hermes_trader.server import app


@pytest.fixture
def client():
    return TestClient(app)


def _hb(age_s: float):
    ts = int((time.time() - age_s) * 1000)
    return [{"ts": ts, "event": "loop_heartbeat", "equity": 100.0,
             "daily_pnl": 0.0, "open_positions": 0, "available": 100.0}]


# ── the shallow probe stays shallow ──────────────────────────────────────────

def test_liveness_is_200_even_when_the_system_is_unhealthy(client, monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(99_999))
    assert client.get("/api/health").status_code == 200


def test_liveness_points_at_the_deep_check(client):
    """Discoverable rather than tribal knowledge."""
    assert client.get("/api/health").json()["system_health"] == "/api/health/system"


# ── the deep probe actually checks ───────────────────────────────────────────

def test_a_dead_loop_fails_the_system_check(client, monkeypatch):
    """The exact defect: 27 days without a heartbeat used to read as healthy."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(99_999))
    r = client.get("/api/health/system")
    assert r.status_code == 503
    assert "loop" in r.json()["failing"]


def test_a_live_loop_passes(client, monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(30))
    monkeypatch.setattr(db, "_feed_health",
                        lambda: {"trustworthy": True, "gap_frac": 0.0,
                                 "gaps": 0, "markets": 40, "ts": 1})
    r = client.get("/api/health/system")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_a_degraded_feed_fails_the_system_check(client, monkeypatch):
    """An outage reads downstream as a quiet market, so it has to surface here
    rather than as silence."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(30))
    monkeypatch.setattr(db, "_feed_health",
                        lambda: {"trustworthy": False, "gap_frac": 0.9,
                                 "gaps": 36, "markets": 40, "ts": 1})
    r = client.get("/api/health/system")
    assert r.status_code == 503 and "feed" in r.json()["failing"]


def test_a_slow_but_alive_loop_is_not_called_dead(client, monkeypatch):
    """Heartbeat p99 is ~420s on healthy days. A threshold under that would page
    on ordinary cycles, and an alert that cries wolf gets muted."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(500))
    monkeypatch.setattr(db, "_feed_health",
                        lambda: {"trustworthy": True, "gap_frac": 0.0,
                                 "gaps": 0, "markets": 40, "ts": 1})
    assert client.get("/api/health/system").json()["checks"]["loop"]["ok"] is True


def test_an_unavailable_disk_check_does_not_fail_the_healthcheck(client, monkeypatch):
    """A monitoring gap must not become an outage."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(30))
    monkeypatch.setattr(db, "_feed_health",
                        lambda: {"trustworthy": True, "gap_frac": 0.0,
                                 "gaps": 0, "markets": 40, "ts": 1})
    import hermes_trader.log_setup as ls
    monkeypatch.setattr(ls, "check_disk_guard",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    r = client.get("/api/health/system")
    assert r.status_code == 200 and r.json()["checks"]["disk"]["ok"] is True


def test_the_deep_check_leaks_no_position_or_credential(client, monkeypatch):
    """Unauthenticated so an external monitor can reach it, so it must expose
    only whether the parts are alive."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: _hb(30))
    body = client.get("/api/health/system").text.lower()
    for leak in ("token", "privatekey", "private_key", "0x", "equity", "coin"):
        assert leak not in body, f"the health payload leaks {leak!r}"


# ── the probes are wired the right way round ─────────────────────────────────

def test_liveness_probes_use_the_shallow_check_and_readiness_the_deep_one():
    """Getting this backwards restarts the web container whenever the trading
    loop dies — fixing nothing and losing the only view of what happened."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    k8s = (root / "k8s" / "statefulset.yaml").read_text()
    live = k8s.index("livenessProbe")
    ready = k8s.index("readinessProbe")
    # the path on the line block following each probe key
    assert "/api/health/system" in k8s[ready:live], "readiness is not the deep check"
    assert "/api/health/system" not in k8s[live:live + 400], (
        "liveness uses the deep check — a dead loop would restart the dashboard")


def test_fly_checks_both_shallow_and_deep():
    import pathlib
    fly = (pathlib.Path(__file__).resolve().parents[1] / "fly.toml").read_text()
    assert 'path = "/api/health"' in fly
    assert 'path = "/api/health/system"' in fly
