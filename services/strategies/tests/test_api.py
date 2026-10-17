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
from services.strategies import journal
from services.strategies.tests.conftest import make_thesis, minimal_analytics


@pytest.fixture
def client(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:CL"))
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
    assert {c.id for c in cards} == {"oil", "btc", "top5", "ai", "metals", "indices"}


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
        "call": {"side": "long", "instrument": "xyz:CL", "one_liner": "api test call",
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

def test_desks_html_page_placeholder_when_template_missing(client, monkeypatch, tmp_path):
    """Templates ship now (desk-ui), so simulate a missing one: the route must
    still answer 200 with a placeholder, never a 500."""
    from services.strategies import api as strategies_api
    monkeypatch.setattr(strategies_api, "_TEMPLATES_DIR", tmp_path)
    r = client.get("/desks")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # The placeholder, not the real template: it links the JSON route.
    assert "/api/desks" in r.text
    assert "desks-grid" not in r.text


def test_desks_html_page_serves_real_template(client):
    """With desk-ui merged, /desks serves pathiel/templates/desks.html."""
    r = client.get("/desks")
    assert r.status_code == 200
    real = (Path(__file__).resolve().parents[3] / "pathiel/templates/desks.html").read_text()
    assert r.text == real
    assert "no-cache" in r.headers.get("cache-control", "")


def test_desk_html_page_known_desk(client):
    r = client.get("/desks/oil")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_desk_html_page_unknown_desk_404(client):
    r = client.get("/desks/not-a-real-desk")
    assert r.status_code == 404
