"""One refresh per desk, and a page that can find it again.

2026-10-19: on the BTC desk, Refresh "did nothing". The refresh was real but
slow (its market read sat behind a 213 s background rebuild), its progress
showed only next to a button at the bottom of the page, and a reload lost it
entirely: the page had no way to know a refresh was running, and a second
click started another one (another market read, another model call). Now
POST .../refresh joins the desk's running job, and GET /api/desks/{id}
carries it as `refresh_job` so a reloaded page keeps following it.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contracts.strategies.models import DeskBrief, RefreshJob
from services.strategies import api as strategies_api
from services.strategies import facade
from services.strategies.tests.conftest import minimal_analytics


class SlowRefresh:
    """facade.refresh stand-in that blocks until released and counts calls."""

    def __init__(self, state="done", message="thesis written (no_prior_thesis)"):
        self.calls = []
        self.release = threading.Event()
        self.state, self.message = state, message

    def __call__(self, desk_id, force=False, on_stage=None):
        self.calls.append(desk_id)
        if on_stage:
            on_stage("reading market data")
        assert self.release.wait(5), "test never released the refresh"
        if self.state == "crash":
            raise RuntimeError("thesis service exploded")
        now = datetime.now(timezone.utc)
        return RefreshJob(id="inner", desk_id=desk_id, state=self.state, started_at=now,
                          finished_at=now, message=self.message)


@pytest.fixture
def slow(monkeypatch, stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL"))
    strategies_api._JOBS.clear()
    fake = SlowRefresh()
    monkeypatch.setattr(facade, "refresh", fake)
    app = FastAPI()
    app.include_router(strategies_api.router)
    yield TestClient(app), fake
    fake.release.set()
    strategies_api._JOBS.clear()


def _wait_finished(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/desks/jobs/{job_id}").json()
        if body["state"] not in ("queued", "running"):
            return body
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def _wait_running(client, desk_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = DeskBrief.model_validate(client.get(f"/api/desks/{desk_id}").json()).refresh_job
        if job is not None and job.state == "running" and job.message:
            return job
        time.sleep(0.01)
    raise AssertionError("no running job showed up in the brief")


def test_a_second_click_joins_the_running_refresh(slow):
    client, fake = slow
    first = client.post("/api/desks/oil/refresh", json={"force": False}).json()
    second = client.post("/api/desks/oil/refresh", json={"force": False}).json()
    assert second["id"] == first["id"]
    fake.release.set()
    assert _wait_finished(client, first["id"])["state"] == "done"
    assert fake.calls == ["oil"], "one market read, one model call"


def test_after_it_finishes_the_next_click_starts_a_new_one(slow):
    client, fake = slow
    fake.release.set()
    first = client.post("/api/desks/oil/refresh", json={}).json()
    _wait_finished(client, first["id"])
    second = client.post("/api/desks/oil/refresh", json={}).json()
    assert second["id"] != first["id"]
    _wait_finished(client, second["id"])
    assert fake.calls == ["oil", "oil"]


def test_clicks_landing_together_start_one_refresh(slow):
    client, fake = slow
    ids = []
    threads = [threading.Thread(target=lambda: ids.append(
        client.post("/api/desks/oil/refresh", json={}).json()["id"])) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    fake.release.set()
    assert len(set(ids)) == 1 and len(ids) == 8
    _wait_finished(client, ids[0])
    assert fake.calls == ["oil"]


def test_each_desk_has_its_own_refresh(slow):
    client, fake = slow
    oil = client.post("/api/desks/oil/refresh", json={}).json()
    btc = client.post("/api/desks/btc/refresh", json={}).json()
    assert oil["id"] != btc["id"]
    fake.release.set()
    _wait_finished(client, oil["id"]); _wait_finished(client, btc["id"])
    assert sorted(fake.calls) == ["btc", "oil"]


def test_the_brief_carries_the_running_refresh_with_its_stage(slow):
    client, fake = slow
    job = client.post("/api/desks/oil/refresh", json={}).json()
    running = _wait_running(client, "oil")
    assert running.id == job["id"] and running.message == "reading market data"
    other = DeskBrief.model_validate(client.get("/api/desks/btc").json())
    assert other.refresh_job is None, "another desk's refresh is not this desk's"
    fake.release.set()
    _wait_finished(client, job["id"])
    assert DeskBrief.model_validate(client.get("/api/desks/oil").json()).refresh_job is None


def test_a_crashed_refresh_is_not_offered_as_running(slow, caplog):
    client, fake = slow
    fake.state = "crash"
    job = client.post("/api/desks/oil/refresh", json={}).json()
    fake.release.set()
    assert _wait_finished(client, job["id"])["state"] == "error"
    assert DeskBrief.model_validate(client.get("/api/desks/oil").json()).refresh_job is None
    assert client.post("/api/desks/oil/refresh", json={}).json()["id"] != job["id"]


def test_every_refresh_leaves_a_log_line_with_its_outcome(slow, caplog):
    client, fake = slow
    caplog.set_level("INFO", logger="services.strategies.api")
    fake.release.set()
    job = client.post("/api/desks/btc/refresh", json={}).json()
    _wait_finished(client, job["id"])
    text = caplog.text
    assert "btc: refresh started (force=False)" in text
    assert "btc: refresh done in" in text and "thesis written (no_prior_thesis)" in text
