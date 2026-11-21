"""Gate tests for `scripts/paper_breakout.py` -- the 2-day PAPER test
(log only, no orders, no money) of the oil breakout entry variant
`a6_15m_k1.0_thesis_half` (docs/strategy/BREAKOUT_ENTRY_TEST_2026-11-19.md).

Covers:
  - signal parity: `build_run` (this script's own wrapper) against a direct
    `desk_tick_sim.simulate()` call, on the same bars, same Params.
  - the full a6 mechanics through this wrapper: open on an 8h/15m-confirmed
    break, stop-out, target fill (limit target: must trade through), the
    Friday weekend flat.
  - `diff_tick`'s state machine: OPEN/CLOSE event emission, idempotency
    across repeated calls on an unchanged window, and the "opened and
    closed inside one missed tick" case.
  - `run_tick` end to end through real files (a temp thesis journal, a
    temp state dir): state persistence across THREE separate calls, one
    per simulated 15-minute tick -- this is the task's own "state
    persistence across runs" requirement.
  - the report's arithmetic (`summarize_paper`/`summarize_live_ledger`).
  - the CLI (`main`): --start required on a fresh test, the restart guard,
    --dry-run touches no file, --report, and the end-of-window auto-report.

Synthetic bars only, no network (every test either calls the pure functions
directly or injects a fake candle/funding fetcher) -- this file never
contacts the live venue and is deselected from online/live markers by
construction (it carries neither).
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import HOUR_MS as H, Direction, Run, Trade, simulate

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("paper_breakout", ROOT / "scripts" / "paper_breakout.py")
PB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PB)

F = 900_000   # FIFTEEN_MIN_MS
DAY_MS = 86_400_000


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp() * 1000)


T0 = _ms(2026, 9, 7)   # a Monday, nowhere near a weekend window


def bar1h(i: int, o: float, h: float, lo: float, c: float, start: int = T0) -> Candle:
    return Candle(t=start + i * H, o=o, h=h, l=lo, c=c, v=1.0)


def bar15(i: int, o: float, h: float, lo: float, c: float, start: int = T0) -> Candle:
    return Candle(t=start + i * F, o=o, h=h, l=lo, c=c, v=1.0)


TIGHT_LO, TIGHT_HI = 99.9, 100.1

LONG_CHASE = lambda t: Direction(side="long", entry_ceiling=50.0, stop_px=85.0, target_px=130.0,   # noqa: E731
                                 regime_id="call1")
NO_BLACKOUT = PB.a6_params(event_blackout_windows=())


def history_1h(n: int = 30, start: int = T0) -> List[Candle]:
    """`n` flat, tight 1h bars ending exactly at `start` (closed by any
    `now_ms >= start`) -- 8h/24h range + ATR14 history from before the test
    window begins."""
    return [bar1h(i - n, 100.0, TIGHT_HI, TIGHT_LO, 100.0, start=start) for i in range(n)]


def calm_15m(n: int = 32, start: int = T0) -> List[Candle]:
    return [bar15(i, 100.0, TIGHT_HI, TIGHT_LO, 100.0, start=start) for i in range(n)]


def open_fixture(start: int = T0):
    """32 calm 15m bars (the 8h a6 lookback), a break bar whose CLOSE clears
    the prior high, then an entry tick -- the a6 long should be open (not
    yet closed) at the end of this fixture. Returns (c15, c1h)."""
    c15 = calm_15m(32, start)
    c15.append(bar15(32, 100.0, 101.2, 99.95, 101.0, start=start))        # break: closes above TIGHT_HI
    c15.append(bar15(33, 101.05, 101.3, 100.9, 101.1, start=start))       # entry tick (market, mid=open)
    return c15, history_1h(start=start)


# ── signal parity: build_run vs a direct simulate() call ────────────────────

def test_build_run_matches_direct_simulate_call_and_actually_fires_a6():
    c15, c1h = open_fixture()
    start_ms, end_ms = T0, T0 + 2 * DAY_MS
    run_a = PB.build_run(c15, c1h, LONG_CHASE, NO_BLACKOUT, None, start_ms, end_ms)

    # the reference call: the exact windowing convention
    # stop_cap_test._window/run_dataset already use for every eval in this
    # family -- not reimplemented, just applied directly here.
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    run_b = simulate(w15, w1h, LONG_CHASE, NO_BLACKOUT, None)

    assert [dataclasses.asdict(t) for t in run_a.trades] == [dataclasses.asdict(t) for t in run_b.trades]
    assert run_a.open_position_at_end == run_b.open_position_at_end
    assert run_a.open_position_levels == run_b.open_position_levels
    assert dict(run_a.refused) == dict(run_b.refused)
    # non-trivial: this fixture must actually produce an a6 fill, or the
    # equality above would hold vacuously on two empty runs.
    assert run_a.open_position_at_end is not None
    assert run_a.open_position_at_end["side"] == "long"
    assert run_a.open_position_levels["entry_kind"] == "a6"
    assert run_a.refused["chase"] > 0   # the main live rule DID refuse first, as the variant requires


def test_build_run_clips_15m_to_the_window_but_not_1h():
    """The windowing convention itself, isolated: a 15m bar strictly before
    start_ms is excluded from the tick loop (adding one changes nothing),
    while 1h history before start_ms is kept in full (removing it would
    starve _levels() of its 8h/24h/ATR14 lookback -- the sim just returns
    no position at all, rather than the same a6 fill)."""
    c15, c1h = open_fixture()
    baseline = PB.build_run(c15, c1h, LONG_CHASE, NO_BLACKOUT, None, T0, T0 + 2 * DAY_MS)
    assert baseline.open_position_at_end is not None

    early_15m = bar15(-1, 999.0, 999.0, 999.0, 999.0)   # a wild, out-of-window bar
    with_early = PB.build_run([early_15m] + c15, c1h, LONG_CHASE, NO_BLACKOUT, None, T0, T0 + 2 * DAY_MS)
    assert with_early.open_position_at_end == baseline.open_position_at_end

    no_1h_history = PB.build_run(c15, [], LONG_CHASE, NO_BLACKOUT, None, T0, T0 + 2 * DAY_MS)
    assert no_1h_history.open_position_at_end is None   # no 8h/24h/ATR14 history: no setup at all


# ── full a6 mechanics: stop, target, weekend flat ───────────────────────────

def test_a6_open_then_stop():
    c15, c1h = open_fixture()
    c15.append(bar15(34, 101.0, 101.05, 95.0, 96.0))   # crashes through the a6 stop
    run = PB.build_run(c15, c1h, LONG_CHASE, NO_BLACKOUT, None, T0, T0 + 2 * DAY_MS)
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.entry_kind == "a6" and tr.exit_reason == "stop"
    assert PB.classify_exit(tr.exit_reason) == "STOP"
    assert run.open_position_at_end is None


def test_a6_open_then_target_needs_a_trade_through_not_a_touch():
    """live_brent_params' target_mode is "limit": a bare touch does not
    fill, only a trade-through by tick_size. The thesis target is 130.0
    (pb_target="thesis"), but target_offset_frac=0.005 (live_brent_params,
    shared by every pullback mode) pulls the actual resting limit in to
    130.0 x 0.995 = 129.35 -- the offset applies to a6 exactly as it does
    to every other mode, so the test targets that real fill price, not the
    thesis's raw number."""
    offset_target_px = 130.0 * (1.0 - 0.005)
    c15, c1h = open_fixture()
    c15.append(bar15(34, 101.2, offset_target_px, 101.0, 102.0))   # touches exactly, no further
    touch_run = PB.build_run(c15, c1h, LONG_CHASE, NO_BLACKOUT, None, T0, T0 + 2 * DAY_MS)
    assert touch_run.open_position_at_end is not None   # still open: a touch never filled the limit

    # ... but a bar that trades THROUGH it does.
    c15b, c1hb = open_fixture()
    c15b.append(bar15(34, 101.2, offset_target_px + 0.05, 101.0, offset_target_px + 0.02))
    through_run = PB.build_run(c15b, c1hb, LONG_CHASE, NO_BLACKOUT, None, T0, T0 + 2 * DAY_MS)
    assert len(through_run.trades) == 1
    tr = through_run.trades[0]
    assert tr.exit_reason == "target" and PB.classify_exit(tr.exit_reason) == "TARGET"
    assert tr.exit_px == pytest.approx(offset_target_px)


def test_weekend_flat_closes_an_open_a6_position():
    """Friday 2026-11-20 is winter (US EST): the oil weekend window is
    21:00Z Friday -> 23:00Z Sunday. An a6 long opened Thursday, never
    stopped or targeted, must be flattened at the window's open."""
    friday_21z = _ms(2026, 11, 20, 21, 0)
    start = friday_21z - 24 * H   # Thursday 21:00Z: a day of runway before the weekend
    c15, c1h = open_fixture(start=start)
    # flat filler bars, well inside stop(~99.9x)/target(130) the whole way
    # through the weekend window's open.
    last = c15[-1].t
    n_fill = (friday_21z + 2 * F - last) // F
    c15 += [bar15(i, 101.0, 101.2, 100.8, 101.0, start=last + F) for i in range(int(n_fill))]
    run = PB.build_run(c15, c1h, LONG_CHASE, NO_BLACKOUT, None, start, start + 3 * DAY_MS)
    assert len(run.trades) == 1
    tr = run.trades[0]
    assert tr.entry_kind == "a6"
    assert tr.exit_reason == "weekend"
    assert PB.classify_exit(tr.exit_reason) == "FLAT"
    assert tr.exit_time is not None and tr.exit_time >= friday_21z


# ── event_blackout_windows: EIA + scheduled-event calendar, both pure ──────

def test_event_blackout_windows_includes_the_eia_report():
    from services.strategies.backtest.oil_calendar import eia_report_times
    from services.strategies.event_blackout import AFTER_H, BEFORE_H
    start, end = T0, T0 + 10 * DAY_MS
    reports = eia_report_times(start - int(AFTER_H * H) - H, end + int(BEFORE_H * H) + H)
    assert reports, "fixture window should contain at least one Wednesday EIA report"
    report_ms, _why = reports[0]
    got = PB.event_blackout_windows("xyz:BRENTOIL", start, end)
    assert (report_ms - int(BEFORE_H * H), report_ms + int(AFTER_H * H)) in got


def test_event_blackout_windows_includes_scheduled_macro_events(monkeypatch):
    fake_row = {"title": "FOMC decision", "coins": frozenset({"xyz:BRENTOIL"}),
               "window": {"start_ms": T0 + 1000, "end_ms": T0 + 2000}}

    def fake_market_events(start_ms, end_ms, coin=None, path=None):
        return [fake_row]

    import services.intel.scheduled_events as SE
    monkeypatch.setattr(SE, "market_events", fake_market_events)
    got = PB.event_blackout_windows("xyz:BRENTOIL", T0, T0 + DAY_MS)
    assert (T0 + 1000, T0 + 2000) in got


# ── diff_tick: the state machine (pure) ─────────────────────────────────────

def _fake_run(*, open_info=None, open_levels=None, trades=None, refused=None) -> Run:
    r = Run()
    r.open_position_at_end = open_info
    r.open_position_levels = open_levels
    r.trades = list(trades or [])
    r.refused = Counter(refused or {})
    return r


def test_diff_tick_logs_open_once_then_close_once():
    open_info = {"side": "long", "size": 1.0, "entry_px": 100.0, "mark_px": 101.0, "unrealized_pnl_usd": 1.0}
    open_lv = {"opened_at": 1000, "stop_px": 98.0, "target_px": 110.0, "entry_kind": "a6"}
    run1 = _fake_run(open_info=open_info, open_levels=open_lv, refused={"chase": 3})
    state1, events1, action1, reason1 = PB.diff_tick(dict(PB.DEFAULT_STATE), run1)
    assert action1 == "OPEN" and len(events1) == 1 and events1[0]["event"] == "OPEN"
    assert state1["logged_opens"] == [1000] and state1["trades_closed"] == 0

    # unchanged window, same tick recomputed: no new events, holding.
    state1b, events1b, action1b, reason1b = PB.diff_tick(state1, run1)
    assert events1b == [] and action1b == "NONE" and reason1b == "holding"

    tr = Trade(side="long", entry_time=1000, entry_px=100.0, size=1.0, stop_px=98.0, target_px=110.0,
              exit_time=2000, exit_px=98.0, exit_reason="stop", net_pnl_usd=-2.0, r_multiple=-1.0,
              entry_kind="a6")
    run2 = _fake_run(trades=[tr], refused={"chase": 3})
    state2, events2, action2, reason2 = PB.diff_tick(state1, run2)
    assert action2 == "STOP"
    assert [e["event"] for e in events2] == ["CLOSE"]   # OPEN already logged: no duplicate
    assert state2["trades_closed"] == 1 and state2["logged_opens"] == [1000]

    # idempotent on the exact same closed run
    state3, events3, action3, reason3 = PB.diff_tick(state2, run2)
    assert events3 == [] and action3 == "NONE"


def test_diff_tick_catches_open_and_close_inside_one_missed_gap():
    tr = Trade(side="short", entry_time=500, entry_px=100.0, size=1.0, stop_px=102.0, target_px=90.0,
              exit_time=600, exit_px=90.0, exit_reason="target", net_pnl_usd=5.0, r_multiple=2.0,
              entry_kind=None)
    run = _fake_run(trades=[tr])
    _state, events, action, reason = PB.diff_tick(dict(PB.DEFAULT_STATE), run)
    assert [e["event"] for e in events] == ["OPEN", "CLOSE"]
    assert action == "TARGET"
    assert "live pullback-to-zone entry" in events[0]["reason"]


def test_diff_tick_idle_reason_reads_the_refused_counter_delta():
    run1 = _fake_run(refused={"chase": 1})
    state1, _e, _a, _r = PB.diff_tick(dict(PB.DEFAULT_STATE), run1)
    run2 = _fake_run(refused={"chase": 1, "min_r": 1})
    _state2, events2, action2, reason2 = PB.diff_tick(state1, run2)
    assert events2 == [] and action2 == "NONE"
    assert reason2 == "no setup: min_r"


def test_classify_exit_and_describe_position_and_format_line():
    assert PB.classify_exit("stop") == "STOP"
    assert PB.classify_exit("lock") == "STOP"
    assert PB.classify_exit("target") == "TARGET"
    for other in ("weekend", "invalidation", "direction_flip", "time", "liquidation"):
        assert PB.classify_exit(other) == "FLAT"

    flat_run = _fake_run()
    assert PB.describe_position(flat_run) == "flat"
    open_run = _fake_run(open_info={"side": "long", "size": 0.421, "entry_px": 88.503, "mark_px": 89.0,
                                   "unrealized_pnl_usd": 0.2},
                         open_levels={"opened_at": 1, "stop_px": 86.10, "target_px": 91.664,
                                     "entry_kind": "a6"})
    desc = PB.describe_position(open_run)
    assert desc == "long 0.4210@88.503 stop=86.100 tp=91.664"

    line = PB.format_tick_line("2026-11-19T23:30:00Z", 90.0475, desc, "OPEN", "a6 breakout entry, long")
    assert line == "PAPER-A6 2026-11-19T23:30:00Z mid=90.0475 pos=long 0.4210@88.503 stop=86.100 tp=91.664 " \
                   "action=OPEN a6 breakout entry, long"
    assert all(tok in line for tok in PB.ACTIONS[1:2])   # sanity: OPEN is a recognized action token


# ── report math ──────────────────────────────────────────────────────────

def _trade(**over) -> Trade:
    base = dict(side="long", entry_time=0, entry_px=100.0, size=1.0, stop_px=98.0, target_px=104.0,
               exit_time=100, exit_px=104.0, exit_reason="target", net_pnl_usd=4.0, r_multiple=2.0,
               entry_kind=None)
    base.update(over)
    return Trade(**base)


def test_summarize_paper_splits_a6_from_the_live_rule():
    run = Run()
    run.trades = [
        _trade(net_pnl_usd=4.0, r_multiple=2.0, entry_kind="a6"),
        _trade(side="short", entry_time=200, exit_time=300, exit_reason="stop", net_pnl_usd=-2.0,
              r_multiple=-1.0, entry_kind=None),
    ]
    s = PB.summarize_paper(run)
    assert s["trades"] == 2 and s["wins"] == 1 and s["win_rate"] == pytest.approx(0.5)
    assert s["net_usd"] == pytest.approx(2.0) and s["net_r"] == pytest.approx(1.0)
    assert s["a6_trades"] == 1 and s["a6_net_usd"] == pytest.approx(4.0)
    assert s["live_rule_trades"] == 1 and s["live_rule_net_usd"] == pytest.approx(-2.0)
    assert s["liquidated"] is False and s["open_position"] is None


def test_summarize_paper_on_no_trades():
    s = PB.summarize_paper(Run())
    assert s == {"trades": 0, "wins": 0, "win_rate": None, "net_r": 0.0, "net_usd": 0.0, "a6_trades": 0,
                "a6_net_usd": 0.0, "live_rule_trades": 0, "live_rule_net_usd": 0.0, "liquidated": False,
                "open_position": None}


def test_summarize_live_ledger_filters_by_coin_and_window():
    rows = [
        {"coin": "xyz:BRENTOIL", "entry_ms": 1000, "net_usd": 5.0, "r_mult": 1.0},
        {"coin": "xyz:BRENTOIL", "entry_ms": 999_999, "net_usd": -1.0, "r_mult": -0.5},  # outside the window
        {"coin": "xyz:GOLD", "entry_ms": 1000, "net_usd": 100.0, "r_mult": 5.0},          # wrong coin
    ]
    s = PB.summarize_live_ledger(rows, "xyz:BRENTOIL", 0, 2000)
    assert s["trades"] == 1 and s["wins"] == 1 and s["net_usd"] == pytest.approx(5.0) and s["net_r"] == pytest.approx(1.0)


def test_render_report_names_the_variant_and_both_sides():
    run = Run()
    run.trades = [_trade(entry_kind="a6")]
    live = {"coin": "xyz:BRENTOIL", "trades": 1, "wins": 1, "win_rate": 1.0, "net_usd": 3.0, "net_r": 1.5}
    txt = PB.render_report(run, 0, 1000, 1000, live)
    assert "PAPER-A6 report" in txt and PB.VARIANT_NAME in txt
    assert "live desk" in txt and "delta" in txt


# ── full pipeline: real theses file + state persistence across runs ───────

def _write_thesis(theses_dir: Path, created_at_ms: int, *, side="long", entry_px=50.0, stop_px=85.0,
                  invalidation="1h close below 40.00", targets=(130.0,), confidence=0.8,
                  thesis_id="t1") -> None:
    theses_dir.mkdir(parents=True, exist_ok=True)
    created_iso = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"id": thesis_id, "created_at": created_iso,
          "call": {"side": side, "entry_px": entry_px, "stop_px": stop_px, "invalidation": invalidation,
                   "targets": list(targets), "confidence": confidence}}
    (theses_dir / f"{thesis_id}.json").write_text(json.dumps(doc))


def _fixed_fetch(c15: List[Candle], c1h: List[Candle]):
    def fetch(coin, interval, count):
        return c15 if interval == "15m" else c1h
    return fetch


def _no_funding(coin, start_ms, end_ms):
    return []


def test_run_tick_persists_state_and_log_across_three_separate_calls(tmp_path: Path):
    theses_dir = tmp_path / "theses"
    _write_thesis(theses_dir, T0 - H)
    c15, c1h = open_fixture()
    c15.append(bar15(34, 101.0, 101.05, 95.0, 96.0))   # the stop, closed only once "now" passes it

    events_path = tmp_path / "paper" / "breakout_a6.jsonl"
    state_path = tmp_path / "paper" / "breakout_a6_state.json"
    fetch = _fixed_fetch(c15, c1h)
    start_ms, end_ms = T0, T0 + 2 * DAY_MS

    # tick 1: only through the entry tick bar (idx 33) -- the crash (idx 34)
    # has not closed yet.
    now1 = c15[33].t + F
    r1 = PB.run_tick(start_ms=start_ms, end_ms=end_ms, now_ms=now1, coin="xyz:BRENTOIL",
                     theses_dir=str(theses_dir), state_path=str(state_path), events_path=str(events_path),
                     params=NO_BLACKOUT, fetch_candles=fetch, fetch_funding=_no_funding, persist=True)
    assert r1["action"] == "OPEN" and "pos=long" in r1["line"]
    assert state_path.is_file() and events_path.is_file()
    lines1 = events_path.read_text().strip().splitlines()
    assert len(lines1) == 1 and json.loads(lines1[0])["event"] == "OPEN"

    # tick 2: the SAME now -- a duplicate/no-op invocation must change nothing.
    r2 = PB.run_tick(start_ms=start_ms, end_ms=end_ms, now_ms=now1, coin="xyz:BRENTOIL",
                     theses_dir=str(theses_dir), state_path=str(state_path), events_path=str(events_path),
                     params=NO_BLACKOUT, fetch_candles=fetch, fetch_funding=_no_funding, persist=True)
    assert r2["action"] == "NONE" and r2["reason"] == "holding"
    assert len(events_path.read_text().strip().splitlines()) == 1   # still just the OPEN

    # tick 3: now past the crash bar's close -- the stop should fire.
    now3 = c15[34].t + F + H
    r3 = PB.run_tick(start_ms=start_ms, end_ms=end_ms, now_ms=now3, coin="xyz:BRENTOIL",
                     theses_dir=str(theses_dir), state_path=str(state_path), events_path=str(events_path),
                     params=NO_BLACKOUT, fetch_candles=fetch, fetch_funding=_no_funding, persist=True)
    assert r3["action"] == "STOP" and r3["pos"] == "flat"
    lines3 = events_path.read_text().strip().splitlines()
    assert len(lines3) == 2
    assert json.loads(lines3[1])["event"] == "CLOSE" and json.loads(lines3[1])["action"] == "STOP"
    state = json.loads(state_path.read_text())
    assert state["trades_closed"] == 1 and state["logged_opens"] == [c15[33].t]


def test_run_tick_dry_run_writes_nothing_even_with_real_paths(tmp_path: Path):
    theses_dir = tmp_path / "theses"
    _write_thesis(theses_dir, T0 - H)
    c15, c1h = open_fixture()
    events_path = tmp_path / "paper" / "breakout_a6.jsonl"
    state_path = tmp_path / "paper" / "breakout_a6_state.json"
    fetch = _fixed_fetch(c15, c1h)
    PB.run_tick(start_ms=T0, end_ms=T0 + 2 * DAY_MS, now_ms=c15[-1].t + F, coin="xyz:BRENTOIL",
               theses_dir=str(theses_dir), state_path=str(state_path), events_path=str(events_path),
               params=NO_BLACKOUT, fetch_candles=fetch, fetch_funding=_no_funding, persist=False)
    assert not events_path.exists() and not state_path.exists()


# ── CLI (main) ──────────────────────────────────────────────────────────

def _patch_live_io(monkeypatch, c15, c1h, ledger_rows=None):
    monkeypatch.setattr(PB, "fetch_hl_candles", lambda coin, interval, count: c15 if interval == "15m" else c1h)
    monkeypatch.setattr(PB, "fetch_funding_history", lambda coin, start_ms, end_ms=None: [])
    monkeypatch.setattr(PB, "event_blackout_windows", lambda coin, s, e: ())
    monkeypatch.setattr(PB, "load_live_ledger_rows", lambda state_dir=None: list(ledger_rows or []))


def test_main_requires_start_on_a_fresh_test(tmp_path, capsys):
    rc = PB.main(["--state-dir", str(tmp_path)])
    assert rc == 2
    assert "--start is required" in capsys.readouterr().err


def test_main_dry_run_never_touches_any_file(tmp_path, monkeypatch, capsys):
    c15, c1h = open_fixture()
    _patch_live_io(monkeypatch, c15, c1h)
    progress = tmp_path / "progress.log"
    rc = PB.main(["--start", PB._iso(T0), "--now", PB._iso(c15[-1].t + F), "--state-dir", str(tmp_path),
                 "--theses-dir", str(tmp_path / "theses"), "--progress-log", str(progress), "--dry-run"])
    assert rc == 0
    assert "PAPER-A6" in capsys.readouterr().out
    assert not (tmp_path / "paper").exists()
    assert not progress.exists()


def test_main_restart_guard_refuses_a_different_start(tmp_path, monkeypatch, capsys):
    c15, c1h = open_fixture()
    _patch_live_io(monkeypatch, c15, c1h)
    progress = tmp_path / "progress.log"
    common = ["--state-dir", str(tmp_path), "--theses-dir", str(tmp_path / "theses"),
             "--progress-log", str(progress)]
    rc1 = PB.main(["--start", PB._iso(T0), "--now", PB._iso(c15[-1].t + F)] + common)
    assert rc1 == 0
    rc2 = PB.main(["--start", PB._iso(T0 + DAY_MS), "--now", PB._iso(c15[-1].t + F)] + common)
    assert rc2 == 2
    assert "already started" in capsys.readouterr().err
    state = json.loads((tmp_path / "paper" / "breakout_a6_state.json").read_text())
    assert state["start_ms"] == T0   # unchanged by the refused call


def test_main_report_flag_prints_the_report_and_never_trades(tmp_path, monkeypatch, capsys):
    c15, c1h = open_fixture()
    _patch_live_io(monkeypatch, c15, c1h, ledger_rows=[{"coin": "xyz:BRENTOIL", "entry_ms": T0 + 10,
                                                        "net_usd": 7.5, "r_mult": 1.1}])
    rc = PB.main(["--start", PB._iso(T0), "--now", PB._iso(c15[-1].t + F), "--state-dir", str(tmp_path),
                 "--theses-dir", str(tmp_path / "theses"), "--report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PAPER-A6 report" in out and "live desk" in out
    assert not (tmp_path / "paper").exists()   # --report never writes paper state


def test_main_prints_final_report_after_the_end_and_stops_trading(tmp_path, monkeypatch, capsys):
    c15, c1h = open_fixture()
    _patch_live_io(monkeypatch, c15, c1h)
    progress = tmp_path / "progress.log"
    end_iso = PB._iso(T0 + 2 * DAY_MS)
    common = ["--state-dir", str(tmp_path), "--theses-dir", str(tmp_path / "theses"),
             "--progress-log", str(progress), "--start", PB._iso(T0), "--end", end_iso]
    rc = PB.main(common + ["--now", PB._iso(T0 + 3 * DAY_MS)])   # well past --end
    assert rc == 0
    out = capsys.readouterr().out
    assert "PAPER-A6 report" in out   # the auto-printed final report
    assert "action=" in out           # the ordinary tick line still printed first
