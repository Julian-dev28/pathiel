"""facade.get_brief stale-while-revalidate + the single background worker.

The regression these pin: every /api/desks/<id> call rebuilt analytics live
through the server's 120-weight/min Hyperliquid budget, 22s to 122s per desk
(measured 2026-10-18). A cached brief must come back without touching the
builder, and rebuilds must go through ONE queue, never a stampede.
"""
from __future__ import annotations

import os
import time

import pytest

from services.strategies import desks, facade, pipeline
from services.strategies.tests.conftest import minimal_analytics


@pytest.fixture(autouse=True)
def worker(monkeypatch):
    """Background worker ON, drained synchronously, state reset per test."""
    monkeypatch.setenv("PATHIEL_DESK_BACKGROUND_REFRESH", "1")
    monkeypatch.setenv("PATHIEL_DESK_WARMUP", "1")
    monkeypatch.setattr(facade, "_PENDING", facade.deque())
    monkeypatch.setattr(facade, "_ACTIVE", None)
    monkeypatch.setattr(facade, "_WORKER_RUNNING", False)
    started = []
    monkeypatch.setattr(facade, "_start_worker", lambda fn: started.append(fn))
    return started


@pytest.fixture
def builds(monkeypatch):
    """Count analytics builds per desk; each build returns mid=200."""
    calls = []

    def fake_build(spec, **kwargs):
        calls.append(spec.id)
        return minimal_analytics(spec.id, mid=200.0)

    monkeypatch.setattr(pipeline.analytics_mod, "build_analytics", fake_build)
    return calls


def seed_cache(desk_id: str, *, age_s: float, mid: float = 100.0) -> None:
    primary = desks.require_spec(desk_id).instruments[0].coin
    pipeline.save_analytics(desk_id, minimal_analytics(desk_id, mid=mid, coin=primary))
    t = time.time() - age_s
    os.utime(pipeline._analytics_cache_path(desk_id), (t, t))


def mid(brief) -> float:
    return brief.analytics.instruments[0].mid


def test_fresh_cache_is_served_without_a_build(builds):
    seed_cache("oil", age_s=10)
    brief = facade.get_brief("oil")
    assert builds == [] and mid(brief) == 100.0 and brief.refreshing is False


def test_stale_cache_is_served_at_once_and_queued(builds, worker):
    seed_cache("oil", age_s=facade.BRIEF_MAX_AGE_S + 60)
    brief = facade.get_brief("oil")
    assert builds == [] and mid(brief) == 100.0       # the page gets the old copy NOW
    assert brief.refreshing is True and facade.is_refreshing("oil")
    assert not any("analytics" in s for s in brief.stale)   # minutes old is normal, not a warning
    worker[0]()                                       # the background worker runs
    assert builds == ["oil"] and not facade.is_refreshing("oil")
    assert mid(facade.get_brief("oil")) == 200.0      # next view sees the rebuild


def test_wait_true_rebuilds_inline(builds):
    seed_cache("oil", age_s=facade.BRIEF_MAX_AGE_S + 60)
    brief = facade.get_brief("oil", wait=True)
    assert builds == ["oil"] and mid(brief) == 200.0 and brief.refreshing is False


def test_wait_true_still_serves_a_young_enough_cache(builds):
    seed_cache("oil", age_s=30)
    facade.get_brief("oil", wait=True, max_age_s=120)
    assert builds == []


def test_no_cache_queues_a_background_build_instead_of_blocking(builds, worker):
    """2026-10-23 (docs/overhaul/DESK_UI_2026-10-23.md, Polish section): a
    desk with NO cache at all used to build inline even with wait=False —
    the "fast: candles come from candle_store" theory in this module's own
    docstring, which measured 9-24s against a genuinely cold candle_store
    (top5, ai) and left the Thesis page's one fetch sitting on a blank
    screen for the whole build. The web route (services/strategies/api.py)
    always calls with wait=False, so a cold desk now comes back at once
    with analytics=None and refreshing=True, same shape as the stale-cache
    case above, and the background worker fills it in."""
    brief = facade.get_brief("oil")
    assert builds == [] and brief.analytics is None
    assert brief.refreshing is True and facade.is_refreshing("oil")
    assert any("queued in the background" in s for s in brief.stale)
    worker[0]()
    assert builds == ["oil"]
    assert mid(facade.get_brief("oil")) == 200.0      # now cached, served without a build


def test_no_cache_and_background_disabled_degrades_instead_of_blocking(builds, monkeypatch):
    """The same cold desk with the background worker off (PATHIEL_DESK_
    BACKGROUND_REFRESH=0, the gate suite's own default per root conftest.py)
    — still never blocks; there is simply nothing to wait for."""
    monkeypatch.setenv("PATHIEL_DESK_BACKGROUND_REFRESH", "0")
    brief = facade.get_brief("oil")
    assert builds == [] and brief.analytics is None and brief.refreshing is False
    assert any("unavailable" in s for s in brief.stale)


def test_wait_true_builds_inline_even_with_no_cache(builds):
    """The one caller-set escape hatch: wait=True (MCP, CLI — no page to
    come back and update later) still rebuilds inline on a cold desk."""
    brief = facade.get_brief("oil", wait=True)
    assert builds == ["oil"] and mid(brief) == 200.0


def test_very_old_cache_says_so_in_stale(builds):
    seed_cache("oil", age_s=facade.STALE_AFTER_S + 600)
    brief = facade.get_brief("oil")
    assert any(s.startswith("analytics:") and "min old" in s for s in brief.stale)


def test_failed_inline_rebuild_serves_the_cache(monkeypatch):
    seed_cache("oil", age_s=facade.BRIEF_MAX_AGE_S + 60)
    monkeypatch.setattr(pipeline.analytics_mod, "build_analytics",
                        lambda spec, **kw: (_ for _ in ()).throw(RuntimeError("429")))
    brief = facade.get_brief("oil", wait=True)
    assert mid(brief) == 100.0
    assert any("rebuild failed" in s for s in brief.stale)


def test_failed_build_with_no_cache_degrades_instead_of_raising(monkeypatch):
    # wait=True: a cold desk with wait=False never attempts the build inline
    # any more (see test_no_cache_queues_a_background_build_instead_of_blocking
    # above), so there is nothing here to fail synchronously unless the
    # caller is one of the ones still asking to wait (MCP, CLI).
    monkeypatch.setattr(pipeline.analytics_mod, "build_analytics",
                        lambda spec, **kw: (_ for _ in ()).throw(RuntimeError("timeout")))
    brief = facade.get_brief("oil", wait=True)
    assert brief.analytics is None
    assert any("unavailable" in s for s in brief.stale)


def test_revalidate_is_idempotent_and_urgent_jumps_the_queue(builds, worker):
    assert facade.revalidate("btc") and facade.revalidate("metals")
    assert facade.revalidate("btc")                   # already queued: not twice
    assert facade.revalidate("oil", urgent=True)      # someone is looking at oil
    assert len(worker) == 1                           # one worker for the whole queue
    worker[0]()
    assert builds == ["oil", "btc", "metals"]


def test_worker_disabled_means_no_queue(builds, monkeypatch):
    monkeypatch.setenv("PATHIEL_DESK_BACKGROUND_REFRESH", "0")
    seed_cache("oil", age_s=facade.BRIEF_MAX_AGE_S + 60)
    brief = facade.get_brief("oil")
    assert brief.refreshing is False and not facade.is_refreshing("oil") and builds == []


def test_one_failing_desk_does_not_stop_the_queue(monkeypatch, worker):
    done = []

    def build(spec, **kw):
        if spec.id == "btc":
            raise RuntimeError("venue down")
        done.append(spec.id)
        return minimal_analytics(spec.id)

    monkeypatch.setattr(pipeline.analytics_mod, "build_analytics", build)
    facade.revalidate("btc")
    facade.revalidate("oil")
    worker[0]()
    assert done == ["oil"] and not facade.is_refreshing("btc")


def test_warm_all_queues_missing_and_stale_desks_in_registry_order(builds, worker):
    ids = desks.list_desk_ids()
    seed_cache(ids[0], age_s=10)                      # fresh: skipped
    seed_cache(ids[1], age_s=facade.BRIEF_MAX_AGE_S + 60)
    queued = facade.warm_all()
    assert queued == ids[1:]
    worker[0]()
    assert builds == ids[1:]


def test_startup_warms_only_desks_with_no_cache(builds, worker):
    """Rebuilding every desk on each restart spent minutes of the server's
    venue budget; old caches are left to stale-while-revalidate."""
    ids = desks.list_desk_ids()
    for desk_id in ids[1:]:
        seed_cache(desk_id, age_s=facade.STALE_AFTER_S + 600)   # old, but present
    assert facade.start_warmup() == [ids[0]]


def test_start_warmup_respects_the_kill_switch(builds, worker, monkeypatch):
    monkeypatch.setenv("PATHIEL_DESK_WARMUP", "0")
    assert facade.start_warmup() == [] and worker == []


def test_list_cards_queues_stale_desks_without_waiting(builds, worker):
    seed_cache("oil", age_s=facade.CARD_MAX_AGE_S + 60)
    cards = {c.id: c for c in facade.list_cards()}
    assert cards["oil"].mid == 100.0 and builds == []  # served from cache
    assert facade.is_refreshing("oil")
