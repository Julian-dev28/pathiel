"""E2E tests for api.py via FastAPI's TestClient: every route, every status
code, every response validated against its contract model (not just
eyeballed)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contracts.strategies.models import DeskBrief, DeskCard, RefreshJob, Thesis
from services.strategies import api as strategies_api
from services.strategies import journal, pipeline
from services.strategies.tests.conftest import make_thesis, minimal_analytics


@pytest.fixture
def client(stub_live_analytics):
    analytics = minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL")
    stub_live_analytics(analytics)
    # A warm cache, same as a desk that has been through at least one refresh
    # or background build — matching normal (not first-ever-cold) operation.
    # facade.get_brief() no longer builds inline for a desk with NO cache at
    # all (docs/overhaul/DESK_UI_2026-10-23.md, Polish section: that used to
    # block the request for as long as the live venue build took), so a test
    # exercising the ordinary "brief has analytics" path needs a seeded
    # cache rather than relying on the retired inline-build-on-every-call
    # behavior.
    pipeline.save_analytics("oil", analytics)
    # fresh per-test job registry — a module-level dict would otherwise leak
    # queued/done jobs from one test into the next's assertions.
    strategies_api._JOBS.clear()
    app = FastAPI()
    app.include_router(strategies_api.router)
    return TestClient(app)


def _poll_job(client: TestClient, job_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/desks/jobs/{job_id}")
        body = r.json()
        if body["state"] not in ("queued", "running"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish within {timeout_s}s")


# ── GET /api/desks ───────────────────────────────────────────────────────

def test_get_desks_returns_one_card_per_desk(client):
    r = client.get("/api/desks")
    assert r.status_code == 200
    cards = [DeskCard.model_validate(row) for row in r.json()]
    assert {c.id for c in cards} == {"oil", "gas", "btc", "top5", "ai", "metals", "indices"}


# ── GET /api/desks/{desk_id} ───────────────────────────────────────────────

def test_get_desk_brief_known_desk(client):
    r = client.get("/api/desks/oil")
    assert r.status_code == 200
    brief = DeskBrief.model_validate(r.json())
    assert brief.spec.id == "oil"
    assert brief.analytics is not None


def test_get_desk_brief_unknown_desk_404(client):
    r = client.get("/api/desks/not-a-real-desk")
    assert r.status_code == 404
    assert "unknown desk" in r.json()["detail"]


# ── POST /api/desks/{desk_id}/refresh + GET jobs/{job_id} ─────────────────

def test_post_refresh_runs_in_background_and_job_is_pollable(client):
    r = client.post("/api/desks/oil/refresh", json={"force": True})
    assert r.status_code == 200
    job = RefreshJob.model_validate(r.json())
    assert job.desk_id == "oil"
    assert job.state in ("queued", "running", "done")

    final = _poll_job(client, job.id)
    assert final["id"] == job.id
    assert final["state"] in ("done", "skipped", "error")
    assert final["finished_at"] is not None


def test_post_refresh_unknown_desk_404(client):
    r = client.post("/api/desks/not-a-real-desk/refresh", json={"force": False})
    assert r.status_code == 404


def test_post_refresh_default_body_is_force_false(client):
    r = client.post("/api/desks/oil/refresh", json={})
    assert r.status_code == 200
    # Must be polled to completion, not fire-and-forget: the refresh runs on
    # a background daemon thread (api.py's post_refresh), and this test's
    # `client` fixture stubs build_analytics + the intel/thesis providers
    # only for the lifetime of THIS test's monkeypatch. An unpolled thread
    # that is still running when the next test's fixtures revert those
    # patches goes on to call the REAL network (candles, then intel) under
    # whatever desk/env the NEXT test set up - exactly the kind of
    # leaked-background-work flake that showed up as unrelated "this gate
    # test reached for the network" failures in test_oil_dashboard_routes.py
    # once a slower analytics build (the 2026-10-23 structure fetch) gave
    # the race more time to lose.
    job = RefreshJob.model_validate(r.json())
    final = _poll_job(client, job.id)
    assert final["state"] in ("done", "skipped", "error")


def test_get_job_unknown_404(client):
    r = client.get("/api/desks/jobs/not-a-real-job-id")
    assert r.status_code == 404


# ── theses ─────────────────────────────────────────────────────────────────

def test_get_theses_newest_first(client):
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="old",
                                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="new",
                                    created_at=datetime(2026, 1, 10, tzinfo=timezone.utc)))
    r = client.get("/api/desks/oil/theses")
    assert r.status_code == 200
    rows = [Thesis.model_validate(row) for row in r.json()]
    assert [t.id for t in rows] == ["new", "old"]


def test_get_theses_unknown_desk_404(client):
    r = client.get("/api/desks/not-a-real-desk/theses")
    assert r.status_code == 404


def test_post_thesis_creates_a_user_thesis(client):
    body = {
        "call": {"side": "long", "instrument": "xyz:BRENTOIL", "one_liner": "api test call",
                 "entry_px": 100.0, "stop_px": 90.0, "targets": [110.0], "leverage": 5.0,
                 "confidence": 0.6, "invalidation": "daily close below 90"},
        "scenarios": [],
        "notes": "posted via the API test",
    }
    r = client.post("/api/desks/oil/theses", json=body)
    assert r.status_code == 200
    thesis = Thesis.model_validate(r.json())
    assert thesis.author == "user"
    assert thesis.call.one_liner == "api test call"
    assert journal.get_thesis("oil", thesis.id) is not None


def test_post_thesis_unknown_desk_404(client):
    body = {"call": {"side": "flat", "instrument": "X", "one_liner": "x", "confidence": 0.5,
                     "invalidation": "x"}}
    r = client.post("/api/desks/not-a-real-desk/theses", json=body)
    assert r.status_code == 404


def test_post_thesis_rejects_extra_fields(client):
    """UserThesisIn forbids extra fields (contract base model) — a typo'd
    field name should 422, not silently vanish."""
    body = {"call": {"side": "flat", "instrument": "X", "one_liner": "x", "confidence": 0.5,
                     "invalidation": "x"}, "surprise_field": 1}
    r = client.post("/api/desks/oil/theses", json=body)
    assert r.status_code == 422


# ── HTML routes ──────────────────────────────────────────────────────────

def test_the_desks_grid_list_page_is_gone(client):
    """/desks (the grid of every desk) was removed 2026-10-23 — the Desk
    UI overhaul made /desk/oil ("Thesis" in the 3-tab nav) the only desk
    page the UI links to by default (active_desks). desks.html no longer
    exists on disk either."""
    r = client.get("/desks")
    assert r.status_code == 404
    assert not (Path(__file__).resolve().parents[3] / "pathiel/templates/desks.html").exists()


def test_desk_html_page_known_desk(client):
    r = client.get("/desk/oil")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_desk_html_page_unknown_desk_404(client):
    r = client.get("/desk/not-a-real-desk")
    assert r.status_code == 404


def test_running_job_reports_the_current_stage(client, monkeypatch):
    """The page shows what a refresh is doing ("writing the thesis") while it
    runs, read from the polled job's message."""
    import threading
    from services.strategies import facade
    gate = threading.Event()

    def slow_refresh(desk_id, force=False, on_stage=None):
        on_stage("writing the thesis (about a minute)")
        gate.wait(5)
        return RefreshJob(id="x", desk_id=desk_id, state="done",
                          started_at=datetime.now(timezone.utc), message="thesis written")

    monkeypatch.setattr(facade, "refresh", slow_refresh)
    job = client.post("/api/desks/oil/refresh", json={}).json()
    deadline = time.monotonic() + 5
    msg = None
    while time.monotonic() < deadline:
        body = client.get(f"/api/desks/jobs/{job['id']}").json()
        if body["state"] == "running" and body.get("message"):
            msg = body["message"]
            break
        time.sleep(0.01)
    gate.set()
    assert msg == "writing the thesis (about a minute)"
    assert _poll_job(client, job["id"])["state"] == "done"


def test_brief_payload_carries_the_refreshing_flag(client):
    body = client.get("/api/desks/oil").json()
    assert body["refreshing"] is False
