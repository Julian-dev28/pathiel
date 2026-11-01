"""Gate tests for the profit lock eval's pure parts
(services/strategies/evals/profit_lock_test.py): the grid, the
counterfactual walk that attributes each lock exit, and the scoring.
Synthetic inputs only."""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import Direction, Params, Run, Trade
from services.strategies.evals.profit_lock_test import (
    attribute, classify, counterfactual_exit, give_back, mfe_r, replay_position, score, variants)
from services.strategies.tests.test_profit_lock import (
    BRENT_PATH, ENTRY, STOP, TARGET, _bars as _sim_bars, _params, _run)

HOUR_MS = 3_600_000
Q = 900_000
LIMIT = Params(target_mode="limit")


def _bars(rows, t0=0):
    return [Candle(t=t0 + i * Q, o=o, h=h, l=l, c=c, v=1.0) for i, (o, h, l, c) in enumerate(rows)]


def _lock_trade(side="long", exit_time=Q, exit_px=101.0):
    return Trade(side=side, entry_time=0, entry_px=100.0, size=1.0, stop_px=exit_px, target_px=104.0 if side == "long" else 96.0,
                 exit_time=exit_time, exit_px=exit_px, exit_reason="lock",
                 orig_stop_px=99.0 if side == "long" else 101.0, stop_moved=True)


def _long(t):
    return Direction(side="long")


def test_grid_has_baseline_and_every_lock_below_its_trigger():
    v = variants()
    assert v["baseline"] == {}
    locks = [k for k in v if k != "baseline"]
    assert len(locks) == 8 * 4          # 8 (trigger, lock) pairs x fixed/trail x confirm/next-bar
    assert all(ov["lock_r"] < ov["lock_trigger_r"] for k, ov in v.items() if ov)
    assert v["lock_t1.5_l1"] == {"lock_trigger_r": 1.5, "lock_r": 1.0, "lock_trail": False, "lock_confirm_close": True}
    assert v["trail_t2_l0.5_nextbar"]["lock_trail"] and not v["trail_t2_l0.5_nextbar"]["lock_confirm_close"]


def test_counterfactual_target_is_a_cut_winner():
    c15 = _bars([(100, 101, 100, 101), (101, 101.5, 100.8, 101.2), (101.2, 104.5, 101, 104.2)])
    reason, px, t = counterfactual_exit(_lock_trade(), c15, {}, _long, LIMIT)
    assert (reason, px, t) == ("target", 104.0, 2 * Q)
    assert classify(reason, 101.0, px, "long") == "cut_winner"


def test_counterfactual_stop_is_a_save_and_the_lock_bar_counts():
    # the lock's own exit bar also traded through the original stop
    c15 = _bars([(100, 102, 100, 101.8), (101.8, 101.9, 98.5, 98.8)])
    reason, px, t = counterfactual_exit(_lock_trade(), c15, {}, _long, LIMIT)
    assert (reason, px, t) == ("stop", 99.0, Q)
    assert classify(reason, 101.0, px, "long") == "saved_stop"


def test_counterfactual_stop_beats_target_in_one_bar():
    c15 = _bars([(100, 102, 100, 101.8), (101.8, 101.9, 101.0, 101.2), (101.2, 104.5, 98.0, 103)])
    assert counterfactual_exit(_lock_trade(), c15, {}, _long, LIMIT)[0] == "stop"


def test_limit_target_needs_a_trade_through():
    c15 = _bars([(100, 102, 100, 101.8), (101.8, 101.9, 101.0, 101.2), (101.2, 104.0, 101.0, 103.5)])
    assert counterfactual_exit(_lock_trade(), c15, {}, _long, LIMIT)[0] == "end"
    assert counterfactual_exit(_lock_trade(), c15, {}, _long, Params(target_mode="touch"))[0] == "target"


def test_counterfactual_invalidation_on_a_1h_close():
    c15 = _bars([(100, 102, 100, 101.8), (101.8, 101.9, 101.0, 101.2), (100.5, 100.6, 100.2, 100.4)])
    c1h = Candle(t=2 * Q - HOUR_MS, o=101, h=102, l=100.1, c=100.3, v=1.0)   # closes at 2Q
    inval = lambda t: Direction(side="long", invalidation_px=100.5)
    reason, px, t = counterfactual_exit(_lock_trade(), c15, {2 * Q: c1h}, inval, LIMIT)
    assert (reason, px, t) == ("invalidation", 100.3, 2 * Q)
    assert classify(reason, 101.0, px, "long") == "saved_giveback"


def test_counterfactual_direction_flip_at_the_open():
    c15 = _bars([(100, 102, 100, 101.8), (101.8, 101.9, 101.0, 101.2), (101.6, 101.7, 101.4, 101.5)])
    flip = lambda t: Direction(side="short" if t >= 2 * Q else "long")
    reason, px, t = counterfactual_exit(_lock_trade(), c15, {}, flip, LIMIT)
    assert (reason, px) == ("direction_flip", 101.6)
    assert classify(reason, 101.0, px, "long") == "cut_runner"


def test_counterfactual_short_mirrors():
    c15 = _bars([(100, 100, 98, 98.2), (98.2, 99.2, 98.1, 99.0), (99.0, 99.1, 95.5, 95.8)])
    t = _lock_trade(side="short", exit_px=99.0)
    reason, px, _ = counterfactual_exit(t, c15, {}, lambda t: Direction(side="short"), LIMIT)
    assert (reason, px) == ("target", 96.0)
    assert classify("end", 99.0, 99.5, "short") == "saved_giveback"


def test_attribute_counts_and_delta_r():
    c15 = _bars([(100, 101, 100, 101), (101, 101.5, 100.8, 101.2), (101.2, 104.5, 101, 104.2)])
    run = Run(trades=[_lock_trade(), Trade(side="long", entry_time=0, entry_px=100, size=1, stop_px=99,
                                           target_px=104, exit_time=Q, exit_px=99, exit_reason="stop")])
    att = attribute(run, c15, [], _long, LIMIT)
    assert att["counts"]["cut_winner"] == 1 and sum(att["counts"].values()) == 1   # the plain stop is not attributed
    assert att["net_delta_r"] == pytest.approx(-3.0)      # locked 1R, would have made 4R


def test_mfe_and_give_back():
    c15 = _bars([(100, 101.6, 100, 101.5), (101.5, 102.1, 99.0, 99.0)])
    t = Trade(side="long", entry_time=0, entry_px=100, size=1, stop_px=99, target_px=104,
              exit_time=Q, exit_px=99, exit_reason="stop", orig_stop_px=99)
    assert mfe_r(t, c15) == pytest.approx(2.1)
    assert give_back(Run(trades=[t]), c15) == {"1.0": 1, "1.5": 1, "2.0": 1}


def test_score_counts_wins_and_punishes_a_wipe_out():
    rows = {"a": {"baseline": {"final_equity": 100.0}, "x": {"final_equity": 110.0}},
            "b": {"baseline": {"final_equity": 100.0}, "x": {"final_equity": 0.0}}}
    s = score(rows)["x"]
    assert (s["beats"], s["loses"], s["of"]) == (1, 1, 2)
    assert s["geo_mean_ratio"] == pytest.approx((1.1 * 0.01) ** 0.5, rel=1e-3)


# ── replay_position: one open position, parity with simulate ───────────────

@pytest.mark.parametrize("name", ["baseline", "lock_t1_l0", "lock_t1.5_l1", "trail_t1.5_l1", "lock_t2_l1",
                                  "lock_t1.5_l1_nextbar"])
def test_replay_position_matches_simulate_on_the_brent_path(name):
    ov = variants()[name]
    sim = _run(BRENT_PATH, **ov).trades[0]
    bars = _sim_bars(BRENT_PATH)[1:]                # the bars after the fill bar
    r = replay_position("long", ENTRY, STOP, TARGET, bars, _params(**ov))
    assert r["reason"] == sim.exit_reason
    assert r["exit_px"] == pytest.approx(sim.exit_px)
    assert r["exit_time"] == sim.exit_time


def test_replay_position_same_bar_close_confirm_parity():
    rows = [BRENT_PATH[0], (111.65, 112.20, 111.60, 111.70), (111.70, 111.75, 111.50, 111.60)]
    for confirm in (True, False):
        ov = {"lock_trigger_r": 1.5, "lock_r": 1.0, "lock_confirm_close": confirm}
        sim = _run(rows, **ov).trades[0]
        r = replay_position("long", ENTRY, STOP, TARGET, _sim_bars(rows)[1:], _params(**ov))
        assert (r["reason"], r["exit_time"]) == (sim.exit_reason, sim.exit_time)
        assert r["exit_px"] == pytest.approx(sim.exit_px)


def test_replay_position_target_and_open():
    up = [BRENT_PATH[0], (111.65, 112.40, 111.60, 112.30), (112.30, 113.80, 112.20, 113.70)]
    r = replay_position("long", ENTRY, STOP, TARGET, _sim_bars(up)[1:], _params(lock_trigger_r=1.5, lock_r=1.0))
    assert (r["reason"], r["exit_px"]) == ("target", TARGET)
    flat = [BRENT_PATH[0], (111.65, 111.70, 111.60, 111.66)]
    r = replay_position("long", ENTRY, STOP, TARGET, _sim_bars(flat)[1:], _params())
    assert r["reason"] == "open" and r["exit_px"] == 111.66
