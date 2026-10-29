"""services/strategies/evals/scenario_sweep.py: the grid, the param mapping and
the ranking (pure parts; no network)."""
from __future__ import annotations

from services.strategies.evals import scenario_sweep as S


def test_the_grid_is_every_combination_once_and_holds_the_live_config():
    combos = list(S.grid())
    assert len(combos) == 5 * 2 * 3 * 2 * 3 * 3 == 540
    assert len({S.combo_key(c) for c in combos}) == 540
    assert S.combo_key(S.LIVE) in {S.combo_key(c) for c in combos}


def test_params_map_every_field_and_turn_size_slippage_on():
    c = {"offset": 0.005, "reentry": False, "stop": "trail", "target": "market", "leverage": 15.0, "buffer": 0.75}
    p = S.params_for(c, tick_minutes=60)
    assert (p.target_offset_frac, p.reentry_after_target, p.stop_mode, p.target_mode, p.leverage,
            p.stop_buffer, p.tick_minutes) == (0.005, False, "trail", "market", 15.0, 0.75, 60)
    assert p.size_slippage is True and p.starting_equity == S.START and p.max_leverage == 20.0


def test_ranking_is_by_journaled_final_equity_and_finds_the_live_rank():
    def row(combo, eq, r):
        return {"combo": combo, "j30": {"final_equity": eq, "total_r": r}}
    other = {**S.LIVE, "offset": 0.005}
    rows = [row(S.LIVE, 124.0, 11.2), row(other, 200.0, 10.3), row({**S.LIVE, "buffer": 0.75}, 124.0, 12.0)]
    ranked = S.rank(rows, 30)
    assert ranked[0]["combo"] == other
    assert ranked[1]["combo"]["buffer"] == 0.75          # equity tie broken by total R
    assert S.live_rank(ranked) == 3
