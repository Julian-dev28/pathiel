"""Health that means something.

/api/health answered {"status": "running"} unconditionally, and the k8s probes
pointed at it. A dead process behind a live web server therefore read as
perfectly healthy to every monitor — the same silent-failure shape as a data
outage reading as a quiet market, or an unrotated log filling a disk until the
box stops.

The split matters as much as the check:
  /api/health         LIVENESS of the web process. Always 200 if it can serve.
  /api/health/system  Is the SYSTEM working. 503 when it is not.

2026-10-25: the `loop`, `feed` and `ai_brain` checks are gone with the engine
they watched. Each had become a permanent false failure (the live box answered
503 for 23 days with "last heartbeat 1983217s ago"), and a readiness probe on
a check that can never pass keeps a pod out of service forever. What is left
is what can still fail silently in a headless desk: the disk, and the state
directory every desk journal lives in.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import pathiel.log_setup as ls
from pathiel.server import app


@pytest.fixture
def client():
    return TestClient(app)


def _disk(critical: bool = False, warn: bool = False):
    return SimpleNamespace(critical=critical, warn=warn, free_bytes=10 ** 12, log_dir_bytes=1024)


# ── the shallow probe stays shallow ──────────────────────────────────────────

def test_liveness_is_200_even_when_the_system_is_unhealthy(client, monkeypatch):
    monkeypatch.setattr(ls, "check_disk_guard", lambda *a, **k: _disk(critical=True))
    assert client.get("/api/health").status_code == 200


def test_liveness_points_at_the_deep_check(client):
    """Discoverable rather than tribal knowledge."""
    assert client.get("/api/health").json()["system_health"] == "/api/health/system"


# ── the deep probe actually checks ───────────────────────────────────────────

def test_a_critical_disk_fails_the_system_check(client, monkeypatch):
    """An unrotated disk took this system down before anything else noticed."""
    monkeypatch.setattr(ls, "check_disk_guard", lambda *a, **k: _disk(critical=True))
    r = client.get("/api/health/system")
    assert r.status_code == 503 and "disk" in r.json()["failing"]
    assert r.json()["checks"]["disk"]["detail"] == "free space below the critical floor"


def test_a_log_dir_over_its_cap_is_a_warning_not_a_failure(client, monkeypatch):
    monkeypatch.setattr(ls, "check_disk_guard", lambda *a, **k: _disk(warn=True))
    body = client.get("/api/health/system").json()
    assert body["checks"]["disk"]["ok"] is True and body["checks"]["disk"]["warn"] is True


def test_an_unavailable_disk_check_does_not_fail_the_healthcheck(client, monkeypatch):
    """A monitoring gap must not become an outage."""
    monkeypatch.setattr(ls, "check_disk_guard",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    body = client.get("/api/health/system").json()
    assert body["checks"]["disk"]["ok"] is True
    assert "disk" not in body["failing"]


def test_an_unwritable_state_dir_fails_the_system_check(client, monkeypatch, tmp_path):
    """A container whose volume did not mount would serve pages and lose
    every desk journal write; that has to read as unhealthy."""
    import pathiel.state as st
    missing = tmp_path / "not-mounted"
    monkeypatch.setattr(st, "state_file", lambda name: str(missing / name))
    r = client.get("/api/health/system")
    assert r.status_code == 503 and "state" in r.json()["failing"]


def test_a_fully_healthy_system_returns_200(client, monkeypatch, tmp_path):
    """The aggregate green path, with every dependency pinned rather than
    inherited from whatever machine the suite runs on."""
    import pathiel.state as st
    monkeypatch.setattr(ls, "check_disk_guard", lambda *a, **k: _disk())
    monkeypatch.setattr(st, "state_file", lambda name: str(tmp_path / name))
    r = client.get("/api/health/system")
    assert r.status_code == 200 and r.json()["ok"] is True, r.json()["failing"]
    assert set(r.json()["checks"]) == {"disk", "state"}


def test_the_retired_checks_are_gone(client):
    """loop, feed and ai_brain watched processes that no longer exist; a
    check with no writer is a permanent false page."""
    body = client.get("/api/health/system").json()
    assert not {"loop", "feed", "ai_brain"} & set(body["checks"])


def test_the_deep_check_leaks_no_position_or_credential(client):
    """Unauthenticated so an external monitor can reach it, so it must expose
    only whether the parts are alive."""
    body = client.get("/api/health/system").text.lower()
    for leak in ("token", "privatekey", "private_key", "0x", "equity", "coin"):
        assert leak not in body, f"the health payload leaks {leak!r}"


# ── the probes are wired the right way round ─────────────────────────────────

def test_liveness_probes_use_the_shallow_check_and_readiness_the_deep_one():
    """Getting this backwards restarts the web container whenever a
    dependency fails — fixing nothing and losing the only view of what
    happened."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    k8s = (root / "k8s" / "statefulset.yaml").read_text()
    live = k8s.index("livenessProbe")
    ready = k8s.index("readinessProbe")
    # the path on the line block following each probe key
    assert "/api/health/system" in k8s[ready:live], "readiness is not the deep check"
    assert "/api/health/system" not in k8s[live:live + 400], (
        "liveness uses the deep check — a failed dependency would restart the dashboard")


def test_the_system_check_says_where_the_hl_budget_went(client):
    """The trace for "the site is slow" (2026-10-19): weight granted and
    requests refused per request type and priority, with no account data."""
    budget = client.get("/api/health/system").json()["hl_budget"]
    assert {"capacity", "refill_per_sec", "background_reserve", "available_weight",
            "granted_weight", "refused_requests"} <= set(budget)
    assert budget["background_reserve"] <= budget["capacity"]


def test_the_system_check_runs_off_the_event_loop(client, monkeypatch):
    """The disk guard walks logs/ and stats the filesystem; on the event loop
    that froze every page for its duration on a big log directory. The check
    is a plain `def`, so all of it runs on a worker thread."""
    import asyncio
    seen = {}

    def guard(*a, **k):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return _disk()
    monkeypatch.setattr(ls, "check_disk_guard", guard)
    client.get("/api/health/system")
    assert seen == {"on_loop": False}
