"""facade: the intel packet (headlines, odds, policy, catalysts, hard data)
is revalidated in the background when older than INTEL_MAX_AGE_S, without a
thesis rewrite and without rebuilding analytics.

Regression: 2026-10-24 the oil Desk page showed headlines from 16 hours
earlier. Only refresh() (the thesis rewrite, once an hour at most, and only
when the tick asked for it) ever rebuilt the intel packet, so every news
source's 10-minute TTL went unexercised between refreshes.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from contracts.strategies.models import IntelPacket
from services.strategies import desks, facade, pipeline
from services.strategies.tests.conftest import minimal_analytics


@pytest.fixture(autouse=True)
def worker(monkeypatch):
    """Background worker ON, drained synchronously, state reset per test."""
    monkeypatch.setenv("PATHIEL_DESK_BACKGROUND_REFRESH", "1")
    monkeypatch.setattr(facade, "_PENDING", facade.deque())
    monkeypatch.setattr(facade, "_WHY", {})
    monkeypatch.setattr(facade, "_ACTIVE", None)
    monkeypatch.setattr(facade, "_WORKER_RUNNING", False)
    started = []
    monkeypatch.setattr(facade, "_start_worker", lambda fn: started.append(fn))
    return started


@pytest.fixture
def analytics_builds(monkeypatch):
    calls = []

    def fake_build(spec, **kwargs):
        calls.append(spec.id)
        return minimal_analytics(spec.id, mid=200.0)
    monkeypatch.setattr(pipeline.analytics_mod, "build_analytics", fake_build)
    return calls


@pytest.fixture
def intel_builds(monkeypatch):
    calls = []

    def fake(spec):
        calls.append(spec.id)
        packet = _packet(spec.id)
        pipeline.save_intel(spec.id, packet)
        return packet, None
    monkeypatch.setattr(pipeline, "build_and_cache_intel", fake)
    return calls


def _packet(desk_id: str, failed=()) -> IntelPacket:
    return IntelPacket(desk_id=desk_id, as_of=datetime.now(timezone.utc), headlines=[],
                       policy=[], catalysts=[], odds=[], positioning=[], datapoints=[],
                       token_estimate=0, sources_failed=list(failed))


def seed_analytics(desk_id: str, *, age_s: float = 10.0) -> None:
    primary = desks.require_spec(desk_id).instruments[0].coin
    pipeline.save_analytics(desk_id, minimal_analytics(desk_id, mid=100.0, coin=primary))
    t = time.time() - age_s
    os.utime(pipeline._analytics_cache_path(desk_id), (t, t))


def seed_intel(desk_id: str, *, age_s: float, failed=()) -> None:
    pipeline.save_intel(desk_id, _packet(desk_id, failed))
    t = time.time() - age_s
    os.utime(pipeline._intel_cache_path(desk_id), (t, t))


def test_young_intel_is_served_and_nothing_is_queued(analytics_builds, intel_builds, worker):
    seed_analytics("oil")
    seed_intel("oil", age_s=60)
    brief = facade.get_brief("oil")
    assert brief.intel is not None
    assert not facade.is_refreshing("oil") and worker == []
    assert analytics_builds == [] and intel_builds == []


def test_old_intel_is_served_now_and_rebuilt_alone_in_the_background(analytics_builds, intel_builds, worker):
    seed_analytics("oil")
    seed_intel("oil", age_s=facade.INTEL_MAX_AGE_S + 60)
    brief = facade.get_brief("oil")
    assert brief.intel is not None                      # the page gets the old packet NOW
    assert brief.refreshing is False                    # analytics are fresh; that flag is theirs
    assert facade.is_refreshing("oil")                  # but the desk is queued
    assert not any(s.startswith("intel:") for s in brief.stale)  # 31 min old is normal
    worker[0]()
    assert intel_builds == ["oil"]
    assert analytics_builds == [], "an intel-only job must not spend venue budget on analytics"
    assert pipeline.cached_intel_age_s("oil") < 5.0
    assert not facade.is_refreshing("oil")


def test_very_old_intel_says_so_in_stale(analytics_builds, intel_builds):
    seed_analytics("oil")
    seed_intel("oil", age_s=facade.STALE_AFTER_S + 600)
    brief = facade.get_brief("oil")
    assert any(s.startswith("intel:") and "h old" in s and "refreshing" in s for s in brief.stale)


def test_very_old_intel_with_worker_off_says_run_refresh(analytics_builds, intel_builds, monkeypatch):
    monkeypatch.setenv("PATHIEL_DESK_BACKGROUND_REFRESH", "0")
    seed_analytics("oil")
    seed_intel("oil", age_s=facade.STALE_AFTER_S + 600)
    brief = facade.get_brief("oil")
    assert any(s.startswith("intel:") and "run refresh" in s for s in brief.stale)
    assert intel_builds == []


def test_no_intel_at_all_is_not_queued_it_needs_a_refresh(analytics_builds, intel_builds, worker):
    """A desk that has never been refreshed has no thesis either; a
    background packet build would not change what the operator must do."""
    seed_analytics("oil")
    brief = facade.get_brief("oil")
    assert brief.intel is None
    assert any("never fetched" in s for s in brief.stale)
    assert worker == [] and intel_builds == []


def test_failed_sources_note_still_shows_alongside_the_age_note(analytics_builds, intel_builds):
    seed_analytics("oil")
    seed_intel("oil", age_s=facade.STALE_AFTER_S + 600, failed=["news:gdelt:timeout"])
    brief = facade.get_brief("oil")
    assert any("source(s) failed" in s for s in brief.stale)
    assert any(s.startswith("intel:") and "h old" in s for s in brief.stale)


def test_list_cards_queues_old_intel_without_waiting(analytics_builds, intel_builds, worker):
    seed_analytics("oil")
    seed_intel("oil", age_s=facade.INTEL_MAX_AGE_S + 60)
    facade.list_cards()
    assert intel_builds == [] and facade.is_refreshing("oil")
    worker[0]()
    assert "oil" in intel_builds


def test_analytics_and_intel_reasons_merge_into_one_job(analytics_builds, intel_builds, worker):
    seed_analytics("oil", age_s=facade.BRIEF_MAX_AGE_S + 60)
    seed_intel("oil", age_s=facade.INTEL_MAX_AGE_S + 60)
    facade.get_brief("oil")
    assert len(worker) == 1
    worker[0]()
    assert analytics_builds == ["oil"] and intel_builds == ["oil"]


def test_intel_rebuild_failure_does_not_stop_the_queue(analytics_builds, monkeypatch, worker):
    def boom(spec):
        raise RuntimeError("feed down")
    monkeypatch.setattr(pipeline, "build_and_cache_intel", boom)
    seed_analytics("oil")
    seed_intel("oil", age_s=facade.INTEL_MAX_AGE_S + 60)
    facade.get_brief("oil")
    facade.revalidate("btc")
    worker[0]()                                   # must not raise
    assert analytics_builds == ["btc"] and not facade.is_refreshing("oil")
