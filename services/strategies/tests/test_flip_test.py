"""Gate tests for services/strategies/evals/flip_test.py's pure parts."""
from __future__ import annotations

from services.strategies.backtest.desk_tick_sim import Run, Trade
from services.strategies.evals import flip_test as F


def test_variants_are_live_plus_every_flip_combination():
    v = F.variants()
    assert v["live (no flip)"] == {}
    assert len(v) == 1 + len(F.FLIP_MIN_R) * len(F.FLIP_LEVERAGE) * len(F.FLIP_BUFFER) * len(F.FLIP_TIME)
    assert all(ov["flip_after_target"] for k, ov in v.items() if k != "live (no flip)")


def test_flip_stats_count_only_the_flip_trades():
    r = Run()
    r.trades = [Trade(side="long", entry_time=0, entry_px=1, size=1, stop_px=0.9, target_px=1.2,
                      net_pnl_usd=10.0, r_multiple=2.0, exit_reason="target"),
                Trade(side="short", entry_time=1, entry_px=1.2, size=1, stop_px=1.3, target_px=1.0,
                      net_pnl_usd=-5.0, r_multiple=-1.0, exit_reason="stop", is_flip=True)]
    r.refused["flip_leverage"] += 2
    r.refused["min_r"] += 7
    s = F.flip_stats(r)
    assert s == {"flips": 1, "won": 0, "net_usd": -5.0, "total_r": -1.0, "exits": {"stop": 1},
                 "refused": {"flip_leverage": 2}}
