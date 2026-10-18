"""A desk changing its primary instrument. Oil moved from WTI to Brent on
2026-10-19 (Julien: "for oil desk use the brent oil price").

What this pins: nothing priced off the old primary is served as current or
built on. A cached read with a WTI ladder counts as no cache and is rebuilt;
the last WTI thesis forces a rewrite at once, written from scratch (never an
incremental update that measures Brent's price against WTI's entry); every
written thesis is stamped with the desk's primaries, so the rewrite happens
once, not on every refresh.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import DeskAnalytics, ForwardStats, LadderRung, Quantiles
from services.strategies import desks, facade, journal, pipeline
from services.strategies.tests.conftest import make_thesis, minimal_analytics

WTI, BRENT = "xyz:CL", "xyz:BRENTOIL"
NEW_NOTE = f"thesis: written for {WTI}; this desk now prices off {BRENT} (run refresh)"


def _q() -> Quantiles:
    return Quantiles(p10=-0.1, p25=-0.04, p50=0.01, p75=0.05, p90=0.1)


def priced_off(coin: str, mid: float = 100.0, *, ladder: bool = True,
               forward: bool = False) -> DeskAnalytics:
    """An oil read whose ladder and/or forward stats are built for `coin`,
    the way analytics.build_analytics builds them for the primary only."""
    base = minimal_analytics("oil", coin=coin, mid=mid)
    rungs = [LadderRung(coin=coin, side="long", leverage=5, equity_usd=15.0, notional_usd=75.0,
                        entry_px=mid, liq_px=mid * 0.82, liq_dist_pct=-0.18, p_liq=0.03,
                        p_win=0.55, ev_pct=0.05, equity_ret=_q(), p_double=0.02,
                        p_lose_half=0.09, fees_funding_pct=0.01, verdict="hold")] if ladder else []
    fwd = [ForwardStats(coin=coin, horizon_days=7, n_windows=200,
                        sample_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        sample_end=datetime(2026, 10, 1, tzinfo=timezone.utc),
                        final_ret=_q(), final_ret_mean=0.01, long_mae=_q(),
                        short_mae=_q())] if forward else []
    return base.model_copy(update={"ladder": rungs, "forward": fwd})


def oil():
    return desks.require_spec("oil")


def wti_thesis(**kw):
    """The live oil thesis of 2026-10-18: long WTI 5x at 98.87, written
    before theses carried primary_coins."""
    kw.setdefault("created_at", datetime.now(timezone.utc) - timedelta(minutes=5))
    return make_thesis(thesis_id=kw.pop("thesis_id", "wti-1"), instrument=WTI, entry_px=98.87,
                       stop_px=88.22, targets=(101.19, 105.55), **kw)


class _Seen(list):
    call_on = BRENT   # set to WTI for a desk that deliberately trades the WTI leg


@pytest.fixture
def writer(monkeypatch):
    """No intel; a thesis provider that records the `prev` it was handed and
    writes a call on `writer.call_on`. Returns the list of `prev`s seen."""
    seen = _Seen()

    def write_thesis(spec, result, intel, prev):
        seen.append(prev)
        px = 107.27 if seen.call_on == BRENT else 98.8
        return make_thesis(desk_id=spec.id, thesis_id=f"new-{len(seen)}", instrument=seen.call_on,
                           entry_px=px, stop_px=px * 0.93, targets=(px * 1.05,),
                           created_at=datetime.now(timezone.utc))

    def fake_resolve(path):
        if path == pipeline.THESIS_PROVIDER_PATH:
            return write_thesis
        raise ImportError(path)

    monkeypatch.setattr(pipeline, "_resolve_provider", fake_resolve)
    return seen


# ── primary_coins / analytics_matches_spec ──────────────────────────────────

def test_oil_primary_is_brent():
    assert pipeline.primary_coins(oil()) == [BRENT]


def test_primary_coins_keeps_spec_order_on_a_multi_primary_desk():
    ai = desks.require_spec("ai")
    want = [i.coin for i in ai.instruments if i.role == "primary"]
    assert len(want) > 1 and pipeline.primary_coins(ai) == want


@pytest.mark.parametrize("read,matches", [
    (priced_off(WTI, 98.8), False),                               # WTI ladder
    (priced_off(WTI, 98.8, ladder=False, forward=True), False),   # WTI forward stats only
    (priced_off(BRENT, 107.3, forward=True), True),
    (minimal_analytics("oil", coin=BRENT), True),                 # nothing priced off a primary
])
def test_a_read_matches_the_desk_only_when_priced_off_its_primaries(read, matches):
    assert pipeline.analytics_matches_spec(oil(), read) is matches


def test_a_cache_from_the_wti_desk_is_treated_as_missing():
    pipeline.save_analytics("oil", priced_off(WTI, 98.8))
    assert pipeline.cached_analytics_age_s("oil") is not None   # the file is there...
    assert pipeline.load_cached_analytics("oil") is None        # ...but never served


def test_a_brent_cache_is_served():
    pipeline.save_analytics("oil", priced_off(BRENT, 107.3))
    cached = pipeline.load_cached_analytics("oil")
    assert cached is not None and cached.ladder[0].coin == BRENT


# ── primary_changed ─────────────────────────────────────────────────────────

def test_a_thesis_stamped_with_wti_is_from_the_old_desk():
    stamped = wti_thesis().model_copy(update={"primary_coins": [WTI]})
    assert pipeline.primary_changed(oil(), stamped) is True


def test_a_brent_desk_thesis_that_chose_wti_on_purpose_is_current():
    """Calls may pick a secondary coin; the stamp says which desk wrote it."""
    stamped = wti_thesis().model_copy(update={"primary_coins": [BRENT]})
    assert pipeline.primary_changed(oil(), stamped) is False


@pytest.mark.parametrize("side", ["long", "short"])
def test_an_unstamped_directional_call_on_wti_is_read_as_the_old_desk(side):
    assert pipeline.primary_changed(oil(), wti_thesis(side=side)) is True


@pytest.mark.parametrize("side,instrument", [
    ("flat", WTI), ("pair", "Brent-WTI spread"), ("long", BRENT),
])
def test_an_unstamped_flat_pair_or_brent_call_is_current(side, instrument):
    prev = make_thesis(side=side, instrument=instrument)
    assert pipeline.primary_changed(oil(), prev) is False


# ── the token gate ──────────────────────────────────────────────────────────

def test_gate_rewrites_a_wti_thesis_at_once_even_inside_the_min_interval():
    prev = wti_thesis(created_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    decision, reason = pipeline.needs_rewrite(oil(), priced_off(BRENT, 107.3), None, prev)
    assert (decision, reason) == (True, "primary_changed")


def test_gate_still_holds_a_fresh_brent_thesis():
    prev = make_thesis(instrument=BRENT, entry_px=107.3,
                       created_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    prev = prev.model_copy(update={"primary_coins": [BRENT]})
    decision, reason = pipeline.needs_rewrite(oil(), priced_off(BRENT, 107.3), None, prev)
    assert (decision, reason) == (False, "min_interval_not_elapsed")


# ── refresh ─────────────────────────────────────────────────────────────────

def test_refresh_writes_the_brent_thesis_from_scratch(stub_live_analytics, writer):
    stub_live_analytics(priced_off(BRENT, 107.27))
    journal.save_thesis(wti_thesis())

    job = pipeline.refresh("oil")

    assert job.state == "done" and job.message == "thesis written (primary_changed)"
    assert writer == [None]                    # the WTI call was not handed over as a base
    latest = journal.latest_thesis("oil", author="machine")
    assert latest.call.instrument == BRENT and latest.primary_coins == [BRENT]
    with open(pipeline._usage_log_path()) as fh:
        rows = [json.loads(line) for line in fh]
    assert rows[-1]["decision"] == "written" and rows[-1]["reason"] == "primary_changed"


def test_a_forced_refresh_also_drops_the_wti_base(stub_live_analytics, writer):
    stub_live_analytics(priced_off(BRENT, 107.27))
    journal.save_thesis(wti_thesis())
    job = pipeline.refresh("oil", force=True)
    assert job.message == "thesis written (forced)" and writer == [None]


@pytest.mark.parametrize("call_on", [BRENT, WTI])
def test_the_rewrite_happens_once(stub_live_analytics, writer, call_on):
    """Even when the new call deliberately trades the WTI leg: the stamp, not
    the call's coin, says the thesis is from the Brent desk."""
    writer.call_on = call_on
    stub_live_analytics(priced_off(BRENT, 107.27))
    journal.save_thesis(wti_thesis())
    assert pipeline.refresh("oil").state == "done"
    second = pipeline.refresh("oil")
    assert second.state == "skipped" and second.message.startswith("min_interval_not_elapsed")
    assert len(writer) == 1


def test_a_current_thesis_is_still_handed_over_as_the_base(stub_live_analytics, writer):
    """Only a thesis from the old desk loses its base role: a stamped Brent
    thesis past the min interval after a material move is the base, so the
    thesis service can still choose its cheap incremental update."""
    prev = make_thesis(thesis_id="brent-0", instrument=BRENT, entry_px=104.0,
                       created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    prev = prev.model_copy(update={"primary_coins": [BRENT]})
    journal.save_thesis(prev)
    stub_live_analytics(priced_off(BRENT, 107.27))     # +3.1% since entry

    job = pipeline.refresh("oil")

    assert job.message == "thesis written (material_move)"
    assert [p.id for p in writer] == ["brent-0"]


def test_every_written_thesis_is_stamped_with_the_desks_primaries(stub_live_analytics, writer):
    stub_live_analytics(priced_off(BRENT, 107.27))
    pipeline.refresh("oil")
    assert journal.latest_thesis("oil").primary_coins == [BRENT]


# ── facade: briefs, cards, warmup ───────────────────────────────────────────

@pytest.fixture
def worker(monkeypatch):
    """Background worker ON but never started: queued desk ids stay visible."""
    monkeypatch.setenv("PATHIEL_DESK_BACKGROUND_REFRESH", "1")
    monkeypatch.setenv("PATHIEL_DESK_WARMUP", "1")
    monkeypatch.setattr(facade, "_PENDING", facade.deque())
    monkeypatch.setattr(facade, "_ACTIVE", None)
    monkeypatch.setattr(facade, "_WORKER_RUNNING", False)
    monkeypatch.setattr(facade, "_start_worker", lambda fn: None)


def _seed_fresh(desk_id: str, read: DeskAnalytics) -> None:
    pipeline.save_analytics(desk_id, read)
    t = time.time() - 10
    os.utime(pipeline._analytics_cache_path(desk_id), (t, t))


def _seed_every_other_desk_fresh() -> None:
    for spec in desks.list_specs():
        if spec.id != "oil":
            _seed_fresh(spec.id, minimal_analytics(spec.id, coin=spec.instruments[0].coin))


def test_brief_rebuilds_rather_than_serve_a_fresh_wti_cache(worker, stub_live_analytics):
    _seed_fresh("oil", priced_off(WTI, 98.8))
    stub_live_analytics(priced_off(BRENT, 107.27))
    brief = facade.get_brief("oil")
    assert {r.coin for r in brief.analytics.ladder} == {BRENT}
    assert pipeline.load_cached_analytics("oil") is not None   # the rebuild replaced the cache


def test_startup_warmup_rebuilds_a_desk_whose_cache_is_from_the_old_primary(worker):
    _seed_every_other_desk_fresh()
    _seed_fresh("oil", priced_off(WTI, 98.8))
    assert facade.start_warmup() == ["oil"]


def test_overview_shows_no_wti_price_on_the_brent_card_and_queues_a_rebuild(worker):
    _seed_every_other_desk_fresh()
    _seed_fresh("oil", priced_off(WTI, 98.8))
    cards = {c.id: c for c in facade.list_cards()}
    assert cards["oil"].primary_coin == BRENT and cards["oil"].mid is None
    assert list(facade._PENDING) == ["oil"]


def test_brief_flags_the_wti_thesis_until_a_refresh(worker):
    _seed_fresh("oil", priced_off(BRENT, 107.27))
    journal.save_thesis(wti_thesis())
    assert NEW_NOTE in facade.get_brief("oil").stale


def test_brief_does_not_flag_a_stamped_brent_thesis_or_a_user_call(worker):
    _seed_fresh("oil", priced_off(BRENT, 107.27))
    brent = make_thesis(thesis_id="brent-1", instrument=BRENT, entry_px=107.27,
                        created_at=datetime.now(timezone.utc))
    journal.save_thesis(brent.model_copy(update={"primary_coins": [BRENT]}))
    assert not [s for s in facade.get_brief("oil").stale if s.startswith("thesis:")]

    journal.save_thesis(wti_thesis(thesis_id="mine", author="user",
                                   created_at=datetime.now(timezone.utc)))
    assert not [s for s in facade.get_brief("oil").stale if s.startswith("thesis:")]
