"""Gate tests for the AI-informed strategy variants added 2026-10-30
(operator: "more AI informed, AI powered strategy that is more
profitable"): `Params.ai_levels` (variant A, the thesis's own entry/stop/
first target) and `Params.conviction_leverage` (variant B, leverage sized
by the thesis's confidence). Synthetic candles only; no network, no fees
unless a test says so. Mirrors services/strategies/tests/test_strategy_
variants.py's fixtures and conventions.

Golden check (both off by default -> byte-identical to before this file's
variants existed): every pre-existing simulator test in test_desk_tick_
sim.py / test_strategy_variants.py / test_next_trade.py / test_desk_tick_
backtest_eval.py still passes unmodified (166 tests, run alongside this
file) -- none of them sets ai_levels or conviction_leverage, so none of
the new code paths execute for them. This file additionally asserts the
equivalence directly (test_defaults_are_off and test_explicit_off_matches_
implicit_default below).
"""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import (
    CONVICTION_LEVERAGE_FLOOR, Direction, Params, _ai_setup, simulate, trade_leverage)

HOUR_MS = 3_600_000
FIFTEEN_MS = 900_000
T0 = 24 * HOUR_MS


def _1h(n=24, l=99.0, h=101.0):
    return [Candle(t=i * HOUR_MS, o=100.0, h=h, l=l, c=100.0, v=1.0) for i in range(n)]


def _b(i, o, h, l, c):
    return Candle(t=T0 + i * FIFTEEN_MS, o=o, h=h, l=l, c=c, v=1.0)


def _p(**kw):
    base = dict(taker_fee=0.0, maker_fee=0.0, slippage=0.0)
    base.update(kw)
    return Params(**base)


def _dir(**kw):
    d = Direction(side=kw.pop("side", "long"), **kw)
    return lambda t: d


LV = {"entry_low": 99.0, "entry_high": 101.0, "target_low": 99.0, "target_high": 101.0, "atr14": 2.0}


# ── defaults / golden (off keeps every result byte-identical) ──────────────

def test_direction_ai_fields_default_to_none():
    d = Direction(side="long")
    assert (d.stop_px, d.target_px, d.confidence) == (None, None, None)


def test_params_ai_fields_default_off():
    p = Params()
    assert p.ai_levels is False and p.conviction_leverage is None


def test_explicit_off_matches_implicit_default():
    bars = [_b(0, 99.3, 99.3, 99.3, 99.3), _b(1, 99.2, 99.3, 98.9, 99.0)]
    implicit = simulate(bars, _1h(), _dir(), _p())
    explicit = simulate(bars, _1h(), _dir(), _p(ai_levels=False, conviction_leverage=None))
    assert [vars(t) for t in implicit.trades] == [vars(t) for t in explicit.trades]
    assert implicit.final_equity == explicit.final_equity
    assert implicit.equity_curve == explicit.equity_curve


def test_a_thesis_with_ai_levels_off_is_ignored():
    """A thesis carrying stop_px/target_px/confidence changes nothing when
    ai_levels and conviction_leverage are both off -- the structural rule
    alone decides, exactly as if those fields were never set."""
    bars = [_b(0, 99.3, 99.3, 99.3, 99.3), _b(1, 99.2, 99.3, 98.9, 99.0)]
    d = _dir(entry_ceiling=99.5, stop_px=50.0, target_px=500.0, confidence=0.99)
    with_thesis_levels = simulate(bars, _1h(), d, _p())
    without = simulate(bars, _1h(), _dir(entry_ceiling=99.5), _p())
    assert [vars(t) for t in with_thesis_levels.trades] == [vars(t) for t in without.trades]


# ── variant A: ai_levels ────────────────────────────────────────────────────

def test_ai_setup_is_none_without_a_full_thesis_level_set():
    assert _ai_setup(Direction(side="long"), 99.3, Params(), 20.0) is None
    assert _ai_setup(Direction(side="long", entry_ceiling=99.5), 99.3, Params(), 20.0) is None
    assert _ai_setup(Direction(side="long", entry_ceiling=99.5, stop_px=98.0), 99.3, Params(), 20.0) is None


def test_ai_setup_market_fills_when_mid_already_pays_the_thesis_price():
    d = Direction(side="long", entry_ceiling=99.5, stop_px=98.0, target_px=102.0)
    setup = _ai_setup(d, 99.3, Params(), 10.0)   # mid 99.3 <= thesis entry 99.5: market now
    assert setup["is_market"] is True and setup["entry_px"] == pytest.approx(99.3)
    assert setup["stop_px"] == 98.0 and setup["target_px"] == 102.0


def test_ai_setup_rests_at_the_thesis_entry_when_mid_is_worse():
    d = Direction(side="long", entry_ceiling=99.0, stop_px=98.0, target_px=101.0)
    setup = _ai_setup(d, 99.5, Params(), 20.0)   # mid 99.5 > thesis entry 99.0: rest, never chase
    assert setup["is_market"] is False and setup["entry_px"] == 99.0


def test_ai_setup_is_refused_under_the_min_r_gate():
    # mid 99.3 is worse than the thesis entry 99.0 (a long), so this rests
    # at the thesis's own 99.0/98.8/99.1 -- 0.5R, under the 1.5R gate
    d = Direction(side="long", entry_ceiling=99.0, stop_px=98.8, target_px=99.1)
    assert _ai_setup(d, 99.3, Params(min_r=1.5), 20.0)["refused"] == "ai_min_r"


def test_ai_setup_is_refused_under_the_leverage_gate():
    d = Direction(side="long", entry_ceiling=99.5, stop_px=50.0, target_px=200.0)  # stop far too wide
    assert _ai_setup(d, 99.3, Params(max_leverage=20.0), 20.0)["refused"] == "ai_leverage"


def test_ai_setup_short_side_mirrors_the_long_rule():
    # a short SELLS, so a higher mid is the better price, mirroring a long's
    # "a lower mid is better": market at or above the thesis entry, rest below it.
    d = Direction(side="short", entry_ceiling=100.0, stop_px=101.5, target_px=97.0)
    mkt = _ai_setup(d, 100.5, Params(), 10.0)   # mid 100.5 >= thesis entry 100.0: market now
    assert mkt["is_market"] is True and mkt["entry_px"] == pytest.approx(100.5)
    rest = _ai_setup(d, 99.5, Params(), 10.0)   # mid 99.5 < thesis entry: worse, rest at the thesis entry
    assert rest["is_market"] is False and rest["entry_px"] == 100.0


def test_ai_levels_market_fills_at_the_thesis_levels_in_a_full_simulate():
    bars = [_b(0, 99.3, 99.4, 99.2, 99.3), _b(1, 99.3, 102.1, 99.2, 102.0)]  # reaches the thesis target
    d = _dir(entry_ceiling=99.5, stop_px=98.0, target_px=102.0)
    run = simulate(bars, _1h(), d, _p(ai_levels=True, entry_mode="market", min_r=1.0, leverage=10.0))
    t = run.trades[0]
    assert t.entry_px == pytest.approx(99.3)        # the mid, not the thesis entry -- market branch
    assert t.stop_px == 98.0 and t.target_px == 102.0 and t.exit_reason == "target"
    # the structural rule would have used the 99/101 range instead
    structural = simulate(bars, _1h(), _dir(entry_ceiling=99.5), _p(entry_mode="market", min_r=1.0, leverage=10.0))
    assert structural.trades[0].target_px == pytest.approx(101.0)


def test_ai_levels_falls_back_to_the_structural_rule_when_the_thesis_fails_the_gate():
    """The thesis's own levels pay under min_r, so ai_levels refuses them
    silently (not counted in run.refused -- that count is for the
    ATTEMPT the sim actually rests/fills under) and falls through to the
    normal edge-mode rule."""
    bars = [_b(0, 99.3, 99.3, 99.3, 99.3), _b(1, 99.2, 99.3, 98.9, 99.0)]
    # mid 99.3 is worse than the thesis entry 99.0 (a long), so the ai
    # attempt rests at 99.0/98.8/99.1 -- 0.5R, fails min_r=1.5
    d = _dir(entry_ceiling=99.0, stop_px=98.8, target_px=99.1)
    run = simulate(bars, _1h(), d, _p(ai_levels=True))
    assert run.refused == {}            # the ai refusal never surfaces
    assert run.rested == 1 and run.filled == 1
    t = run.trades[0] if run.trades else None
    assert run.open_position_at_end is not None or t is not None
    # the structural rest/fill uses 99.0 entry / 98.5 stop / 101.0 target, not the thesis's
    pos = run.open_position_at_end
    assert pos is None or pos["entry_px"] == pytest.approx(99.0)


def test_ai_levels_off_by_default_even_with_a_full_thesis_level_set():
    bars = [_b(0, 99.3, 99.3, 99.3, 99.3), _b(1, 99.2, 99.3, 98.9, 99.0)]
    d = _dir(entry_ceiling=99.5, stop_px=98.0, target_px=102.0)
    run = simulate(bars, _1h(), d, _p())   # ai_levels defaults False
    pos = run.open_position_at_end
    assert pos is not None and pos["entry_px"] == pytest.approx(99.0)   # structural, not the thesis's 99.5/98.0/102.0


# ── variant B: conviction_leverage ──────────────────────────────────────────

def test_trade_leverage_is_params_leverage_when_the_knob_is_off():
    assert trade_leverage(Direction(side="long", confidence=0.9), Params(leverage=20.0)) == 20.0


def test_trade_leverage_picks_high_or_low_by_the_confidence_threshold():
    p = Params(conviction_leverage=(0.65, 20.0, 15.0))
    assert trade_leverage(Direction(side="long", confidence=0.70), p) == 20.0
    assert trade_leverage(Direction(side="long", confidence=0.65), p) == 20.0   # boundary: >= goes high
    assert trade_leverage(Direction(side="long", confidence=0.50), p) == 15.0


def test_trade_leverage_never_drops_below_the_15x_floor():
    p = Params(conviction_leverage=(0.65, 20.0, 10.0))   # low_lev 10 configured under the floor
    assert trade_leverage(Direction(side="long", confidence=0.10), p) == CONVICTION_LEVERAGE_FLOOR
    assert CONVICTION_LEVERAGE_FLOOR == 15.0


def test_trade_leverage_falls_back_to_params_leverage_without_a_confidence_reading():
    """No thesis confidence (a control direction series, e.g. always_long,
    or a journaled thesis predating this field): params.leverage, floored
    at 15x the same as a conviction-sized trade."""
    p = Params(conviction_leverage=(0.65, 20.0, 10.0), leverage=5.0)
    assert trade_leverage(Direction(side="long"), p) == CONVICTION_LEVERAGE_FLOOR    # 5 -> floored
    assert trade_leverage(None, p) == CONVICTION_LEVERAGE_FLOOR


def test_conviction_sizing_changes_the_actual_fill_size_in_a_full_simulate():
    bars = [_b(0, 99.5, 99.6, 99.4, 99.5), _b(1, 99.6, 101.1, 99.5, 101.0)]
    base = dict(entry_mode="market", target_r=2.0, stop_buffer=0.25, max_leverage=20.0,
               leverage=5.0, conviction_leverage=(0.65, 20.0, 10.0))

    high = simulate(bars, _1h(), _dir(confidence=0.80), _p(**base))
    low = simulate(bars, _1h(), _dir(confidence=0.40), _p(**base))

    entry = 99.5
    assert high.trades[0].size == pytest.approx(64.0 * 20.0 / entry)      # high_lev=20, not params.leverage=5
    assert low.trades[0].size == pytest.approx(64.0 * 15.0 / entry)       # low_lev=10 floored to 15


def test_conviction_sizing_off_by_default_even_with_confidence_on_the_thesis():
    bars = [_b(0, 99.5, 99.6, 99.4, 99.5), _b(1, 99.6, 101.1, 99.5, 101.0)]
    run = simulate(bars, _1h(), _dir(confidence=0.99), _p(entry_mode="market", target_r=2.0, stop_buffer=0.25))
    assert run.trades[0].size == pytest.approx(64.0 * 20.0 / 99.5)   # Params.leverage default (20), unaffected


def test_ai_levels_and_conviction_leverage_compose():
    """A+B together: the ai setup's gate checks at the conviction-sized
    leverage, not params.leverage."""
    d = _dir(entry_ceiling=99.5, stop_px=98.0, target_px=102.0, confidence=0.80)
    bars = [_b(0, 99.3, 99.4, 99.2, 99.3), _b(1, 99.3, 102.1, 99.2, 102.0)]
    run = simulate(bars, _1h(), d, _p(ai_levels=True, entry_mode="market", min_r=1.0,
                                      leverage=5.0, conviction_leverage=(0.65, 15.0, 15.0)))
    t = run.trades[0]
    assert t.entry_px == pytest.approx(99.3) and t.stop_px == 98.0 and t.target_px == 102.0
    assert t.size == pytest.approx(64.0 * 15.0 / 99.3)   # sized at the conviction leverage, not params.leverage=5
