"""Gate tests for the "fewer, wider, longer" wide-stop swing test
(desk_tick_sim: `Params.entry_fit_leverage`, `_fit_leverage_setup`,
`Params.reentry_after_stop` / `max_stop_reentries` / `reentry_needs_1h_
reclaim`; see docs/strategy/WIDE_STOP_SWING_TEST_2026-11-03.md).

Operator's hypothesis: trade the thesis direction with a WIDER stop, a
LOWER leverage (the highest of 20/15/10/8/5x that still keeps the stop
inside half the way to liquidation), the SAME 15% risk cap, and FEWER
entries per thesis, so the thesis survives the noise. No new field was
needed for the stop/target themselves (entry_lookback_h x stop_buffer_atr,
target_lookback_h x target_offset_frac already cover them -- see the
module docstring). These three fields cover leverage-fit and entry
limits.

Synthetic bars only, no network. Covers: every field defaults to off and
is byte-identical (Params(), live_params(), and over a full run);
`_fit_leverage_setup`'s pure behaviour (picks the highest candidate that
clears `check_leverage`, falls back on a "leverage" refusal only, returns
a non-leverage refusal at once, drops candidates over max_leverage, and
wires the fitted leverage into a live `simulate()` fill);
`reentry_after_stop=False` ("one entry per thesis": blocked forever under
the same call, open again under a new one); `max_stop_reentries` (allows
exactly N re-entries, then blocks); `reentry_needs_1h_reclaim` (gates an
allowed re-entry on a closed 1h bar back inside the range the stop broke
out of).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from pathiel.agents.bracket import check_leverage
from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    Direction, Params, Run, _fit_leverage_setup, live_params, simulate)

HOUR_MS = 3_600_000
Q = 900_000

# Mirrors test_closer_target_sim.py's LV: a flat 24h range of 1000 to
# 1040, ATR14 5.0.
LV = {"atr14": 5.0, "entry_low": 1000.0, "entry_high": 1040.0, "target_low": 1000.0, "target_high": 1040.0,
      "range_8h_low": 1000.0, "range_8h_high": 1040.0, "range_24h_low": 1000.0, "range_24h_high": 1040.0}
LONG = Direction(side="long")


def _base(**over) -> Params:
    return dataclasses.replace(live_params(), starting_equity=225.0, **over)


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# ── defaults / off ──────────────────────────────────────────────────────────

def test_new_fields_default_off():
    for p in (Params(), live_params()):
        assert p.entry_fit_leverage is None
        assert p.reentry_after_stop is True
        assert p.max_stop_reentries is None
        assert p.reentry_needs_1h_reclaim is False


def test_off_is_byte_identical_over_a_full_run():
    c1h = [Candle(t=i * HOUR_MS, o=1020.0, h=1040.0, l=1000.0, c=1020.0, v=1.0) for i in range(24)]
    c15 = [Candle(t=24 * HOUR_MS + i * Q, o=1000.2, h=1000.4, l=1000.1, c=1000.2, v=1.0) for i in range(8)]
    base = simulate(c15, c1h, lambda t: LONG, _base())
    explicit = simulate(c15, c1h, lambda t: LONG, _base(
        entry_fit_leverage=None, reentry_after_stop=True, max_stop_reentries=None,
        reentry_needs_1h_reclaim=False))
    assert [dataclasses.asdict(t) for t in base.trades] == [dataclasses.asdict(t) for t in explicit.trades]
    assert base.equity_curve == explicit.equity_curve
    assert dict(base.refused) == dict(explicit.refused)


# ── _fit_leverage_setup (pure) ───────────────────────────────────────────────

CANDS = (20.0, 15.0, 10.0, 8.0, 5.0)


def test_fit_picks_the_highest_candidate_that_clears_the_gate():
    """A wide stop (50 vs the live 0.75): some candidates fail the 20x
    line, the fit must pick the highest that still passes it."""
    p = _base(entry_fit_leverage=CANDS, stop_buffer=50.0, min_r=0.1)
    setup, lev = _fit_leverage_setup(LONG, 1000.2, LV, p)
    assert setup is not None and not setup.get("refused")
    expected = next(c for c in sorted(CANDS, reverse=True)
                    if check_leverage(setup["entry_px"], setup["stop_px"], c, p.max_leverage)["ok"])
    assert lev == expected
    assert check_leverage(setup["entry_px"], setup["stop_px"], lev, p.max_leverage)["ok"]
    assert lev < p.leverage, "the point of the test: the fit chose something under the fixed 20x"


def test_fit_refuses_when_no_candidate_clears_the_gate():
    """A stop so wide even the lowest candidate fails: refused "leverage"
    at the lowest one tried."""
    p = _base(entry_fit_leverage=CANDS, stop_buffer=900.0, min_r=0.1)
    setup, lev = _fit_leverage_setup(LONG, 1000.2, LV, p)
    assert setup == {"refused": "leverage"}
    assert lev == min(CANDS)


def test_fit_returns_a_non_leverage_refusal_at_once():
    """min_r is leverage-independent: a target capped so tight it fails
    min_r at 20x fails it at every candidate, so the fit must not try the
    lower ones -- it returns the FIRST (highest) candidate's refusal."""
    p = _base(entry_fit_leverage=CANDS, target_cap_frac=0.0001)
    setup, lev = _fit_leverage_setup(LONG, 1000.2, LV, p)
    assert setup == {"refused": "min_r"}
    assert lev == max(CANDS)


def test_fit_drops_candidates_over_max_leverage():
    p = _base(entry_fit_leverage=(30.0, 25.0), max_leverage=20.0)
    setup, lev = _fit_leverage_setup(LONG, 1000.2, LV, p)
    assert setup == {"refused": "leverage"}
    assert lev == p.leverage


def test_fit_handles_an_empty_or_duplicate_candidate_set():
    p = _base(entry_fit_leverage=())
    setup, lev = _fit_leverage_setup(LONG, 1000.2, LV, p)
    assert setup == {"refused": "leverage"} and lev == p.leverage
    dup = _base(entry_fit_leverage=(20.0, 20.0, 15.0))
    setup2, lev2 = _fit_leverage_setup(LONG, 1000.2, LV, dup)
    assert not setup2.get("refused") and lev2 == 20.0


def test_sim_opens_at_the_fitted_leverage():
    """A wide-stop live run: the fill's own `leverage` is the fit's
    answer, not the fixed 20x, and it carries through to the trade row."""
    c1h = [Candle(t=i * HOUR_MS, o=1020.0, h=1040.0, l=1000.0, c=1020.0, v=1.0) for i in range(24)]
    c15 = [Candle(t=24 * HOUR_MS + i * Q, o=1000.2, h=1000.3, l=1000.1, c=1000.2, v=1.0) for i in range(3)]
    c15.append(Candle(t=24 * HOUR_MS + 3 * Q, o=1000.1, h=1000.1, l=900.0, c=940.0, v=1.0))  # crashes past the stop
    base = _base(stop_buffer=50.0, entry_fit_leverage=CANDS, min_r=0.1)
    run = simulate(c15, c1h, lambda t: LONG, base)
    assert run.trades, "expected a fill"
    tr = run.trades[0]
    expected = next(c for c in sorted(CANDS, reverse=True)
                    if check_leverage(tr.entry_px, tr.orig_stop_px, c, base.max_leverage)["ok"])
    assert tr.leverage == expected
    assert expected < base.leverage


# ── entries-per-thesis: a long stopped out, then a chance to re-enter ──────

def _padding_1h(t0: int, n: int = 24):
    """n flat 1h bars (range 100-104) ending exactly at t0 -- the same
    fixture shape as test_loss_fix.py's _flat_run."""
    return [Candle(t=t0 - (n - i) * HOUR_MS, o=102.0, h=104.0, l=100.0, c=102.0, v=1.0) for i in range(n)]


def _entries_run(c1h, ticks, direction_at, **over) -> Run:
    # Lenient min_r and max_leverage: isolates the entry-count/reclaim gate
    # under test, not the R or 20x-liquidation math (irrelevant here).
    base = _base(min_r=1.0, max_leverage=1e6, **over)
    return simulate(ticks, c1h, direction_at, base)


def test_reentry_after_stop_false_blocks_forever_under_the_same_call_only():
    """(a) one entry per thesis: stop once, no second entry under the SAME
    call at any later tick; a NEW call (a thesis rewrite) opens again."""
    t0 = ms(2026, 10, 27)
    c1h = _padding_1h(t0)
    bars = ([Candle(t=t0, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0),             # i0: long fills at 100.3
             Candle(t=t0 + Q, o=100.2, h=100.3, l=99.0, c=99.1, v=1.0)]             # i1: stopped (<=99.25)
            + [Candle(t=t0 + i * Q, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0) for i in range(2, 10)])

    def d_same_call(t: int) -> Direction:
        return Direction(side="long", regime_id="th1")

    run = _entries_run(c1h, bars, d_same_call, reentry_after_stop=False)
    assert len(run.trades) == 1 and run.trades[0].exit_reason == "stop"
    assert run.open_position_at_end is None       # never re-opened under th1, all later ticks flat

    def d_rewrite(t: int) -> Direction:
        return Direction(side="long", regime_id=("th1" if t < t0 + 5 * Q else "th2"))

    run2 = _entries_run(c1h, bars, d_rewrite, reentry_after_stop=False)
    assert len(run2.trades) == 1
    assert run2.open_position_at_end is not None   # th2 is a new call: opens again
    assert run2.open_position_at_end["side"] == "long"


def test_max_stop_reentries_allows_exactly_n_then_blocks():
    """(c)-adjacent control: max_stop_reentries=1, no reclaim gate -- the
    re-entry opens on the very next tick after the first stop; a second
    stop under the same call blocks any further entry."""
    t0 = ms(2026, 10, 27)
    c1h = _padding_1h(t0)
    bars = [
        Candle(t=t0, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0),                  # entry 1
        Candle(t=t0 + Q, o=100.2, h=100.3, l=99.0, c=99.1, v=1.0),                  # stop 1 (re-entry allowed)
        Candle(t=t0 + 2 * Q, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0),          # entry 2 (the one re-entry)
        Candle(t=t0 + 3 * Q, o=100.2, h=100.3, l=99.0, c=99.1, v=1.0),              # stop 2 (over the cap now)
        Candle(t=t0 + 4 * Q, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0),          # would-be entry 3: blocked
    ]

    def d_same_call(t: int) -> Direction:
        return Direction(side="long", regime_id="th1")

    run = _entries_run(c1h, bars, d_same_call, max_stop_reentries=1)
    assert [tr.exit_reason for tr in run.trades] == ["stop", "stop"]
    assert run.open_position_at_end is None        # the third attempt never opened


def test_reentry_needs_1h_reclaim_gates_the_allowed_reentry():
    """(b) at most one re-entry, only after a closed 1h bar back inside
    the prior 24h range: refused "reentry_wait" while price (99.3, then
    still unclosed) sits outside the 100-104 snapshot, and only fills once
    a closed 1h bar's close (barB, 100.2) lands back inside it."""
    t0 = ms(2026, 10, 27)
    c1h = _padding_1h(t0) + [
        Candle(t=t0, o=100.2, h=100.3, l=99.0, c=99.3, v=1.0),            # closes t0+1h, OUTSIDE [100, 104]
        Candle(t=t0 + HOUR_MS, o=99.3, h=100.6, l=99.2, c=100.2, v=1.0),  # closes t0+2h, back INSIDE [100, 104]
    ]
    # 15m ticks t0 .. t0+2h15m (10 ticks, i=0..9): i0 entry, i1 stop, i2-i7
    # flat (refused while waiting), i8 lands exactly on the t0+2h close.
    bars = [Candle(t=t0 + i * Q, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0) for i in range(10)]
    bars[1] = Candle(t=t0 + Q, o=100.2, h=100.3, l=99.0, c=99.1, v=1.0)   # the stop

    def d_same_call(t: int) -> Direction:
        return Direction(side="long", regime_id="th1")

    run = _entries_run(c1h, bars, d_same_call, max_stop_reentries=1, reentry_needs_1h_reclaim=True)
    assert [tr.exit_reason for tr in run.trades] == ["stop"]       # no re-entry filled on the raw sweep below
    assert run.refused["reentry_wait"] >= 4                        # ticks i1..i7 before the reclaim bar closes
    assert run.open_position_at_end is not None                    # the re-entry opened once barB closed inside
    assert run.open_position_at_end["side"] == "long"
    assert run.open_position_levels["opened_at"] >= t0 + 2 * HOUR_MS


def test_reclaim_off_but_cap_on_reentry_is_immediate():
    """The counterpart: with the reclaim gate off, the SAME scenario's
    allowed re-entry (max_stop_reentries=1) fires on the very next flat
    tick, no "reentry_wait" refusals at all."""
    t0 = ms(2026, 10, 27)
    c1h = _padding_1h(t0) + [
        Candle(t=t0, o=100.2, h=100.3, l=99.0, c=99.3, v=1.0),
        Candle(t=t0 + HOUR_MS, o=99.3, h=100.6, l=99.2, c=100.2, v=1.0),
    ]
    bars = [Candle(t=t0 + i * Q, o=100.3, h=100.35, l=100.25, c=100.3, v=1.0) for i in range(4)]
    bars[1] = Candle(t=t0 + Q, o=100.2, h=100.3, l=99.0, c=99.1, v=1.0)

    def d_same_call(t: int) -> Direction:
        return Direction(side="long", regime_id="th1")

    run = _entries_run(c1h, bars, d_same_call, max_stop_reentries=1)
    assert run.refused["reentry_wait"] == 0
    assert run.open_position_at_end is not None
    assert run.open_position_levels["opened_at"] == t0 + 2 * Q
