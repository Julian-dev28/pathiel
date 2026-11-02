"""Gate tests for the silver stop floor eval's pure parts
(services/strategies/evals/silver_stop_floor_test.py): the grid, the
per-entry attribution (live stop vs floored stop on the same entry), the
episode count, the buffer stats and the Brent proof. Synthetic inputs only,
no network."""
from __future__ import annotations

import dataclasses

import pytest

from services.strategies.backtest.desk_tick_sim import simulate
from services.strategies.evals import silver_stop_floor_test as T
from services.strategies.tests.test_silver_stop_floor import (
    DEEP_LONG, DIP_LONG, ENTER_LONG, LONG, Q, T0, _bars, _params, _warmup)

UP_LONG = (100.0, 104.5, 100.0, 104.4)          # trades through the 104 target


def test_grid_has_live_floors_pcts_mults_and_the_fixed_test_buffer():
    v = T.variants(0.4134)
    assert v["live"] == {"stop_buffer_atr": 1.3}
    for x in (0.25, 0.30, 0.35, 0.41, 0.50):
        assert v[f"floor_{x:.2f}"] == {"stop_buffer_atr": 1.3, "stop_buffer_floor": x}
    assert v["pct_0.8"] == {"stop_buffer_atr": 1.3, "stop_buffer_floor_frac": pytest.approx(0.008)}
    assert v["atr_1.6"] == {"stop_buffer_atr": 1.6} and v["atr_2.0"] == {"stop_buffer_atr": 2.0}
    assert v["fixed_test"] == {"stop_buffer_atr": None, "stop_buffer": 0.4134}


def test_brent_grid_never_turns_the_atr_buffer_on():
    for name, ov in T.brent_variants().items():
        assert "stop_buffer_atr" not in ov and "stop_buffer" not in ov, name


def test_classify():
    base_loss = {"stop_px": 99.8, "outcome": "loss"}
    base_win = {"stop_px": 99.8, "outcome": "win"}
    assert T.classify(base_loss, {"stop_px": 99.5, "outcome": "win"}, None) == "stop_to_win"
    assert T.classify(base_win, {"stop_px": 99.5, "outcome": "loss"}, None) == "win_to_stop"
    assert T.classify(base_loss, {"stop_px": 99.5, "outcome": "loss"}, None) == "both_loss"
    assert T.classify(base_win, {"stop_px": 99.5, "outcome": "win"}, None) == "both_win"
    assert T.classify(base_win, {"stop_px": 99.8, "outcome": "win"}, None) == "same"
    assert T.classify(base_loss, None, "min_r") == "gate_blocked"
    assert T.classify(base_loss, {"stop_px": 99.5, "outcome": "open"}, None) == "other"


def _attr(rows, **var_over):
    c15, c1h = _bars(rows), _warmup()
    base = _params()
    var = dataclasses.replace(base, **var_over)
    run = simulate(c15, c1h, LONG, base)
    return run, T.attribute(run, c15, c1h, LONG, base, var)


def test_attribute_finds_the_entry_the_floor_saves():
    run, rows = _attr([ENTER_LONG, DIP_LONG, UP_LONG], stop_buffer_floor=0.5)
    assert run.trades[0].exit_reason == "stop"
    r = rows[0]
    assert r["time"].endswith("00:00Z") and r["side"] == "long"
    assert r["base"]["reason"] == "stop" and r["base"]["stop_px"] == pytest.approx(99.8)
    assert r["var"]["reason"] == "target" and r["var"]["stop_px"] == pytest.approx(99.5)
    assert r["class"] == "stop_to_win"
    # return on equity at 20x: the live stop loses 0.5 plus taker fees both
    # ways; the floored stop makes 3.7 less a taker entry and a maker target
    p = _params()
    base_move = -0.5 - (p.taker_fee * 100.3 + p.taker_fee * 99.8)
    var_move = 3.7 - (p.taker_fee * 100.3 + p.maker_fee * 104.0)
    assert r["base"]["eq_pct"] == pytest.approx(round(20 * base_move / 100.3 * 100, 2))
    assert r["var"]["eq_pct"] == pytest.approx(round(20 * var_move / 100.3 * 100, 2))
    s = T.attribution_summary(rows)
    assert s["stop_to_win"] == 1 and s["win_to_stop"] == 0 and s["eq_pct_delta"] > 0


def test_attribute_counts_an_entry_the_floored_stop_cannot_take():
    _, rows = _attr([ENTER_LONG, DEEP_LONG], leverage=10.0, stop_buffer_floor=1.6)
    r = rows[0]
    assert r["gate"] == "min_r" and r["var"] is None and r["class"] == "gate_blocked"
    s = T.attribution_summary(rows)
    assert s["gate_blocked"] == {"min_r": 1} and s["gate_blocked_live"] == {"loss": 1}
    assert s["gate_blocked_live_eq_pct"] == pytest.approx(round(r["base"]["eq_pct"], 1))


def test_attribute_same_stop_when_the_floor_does_not_bind():
    _, rows = _attr([ENTER_LONG, DEEP_LONG], stop_buffer_floor=0.1)
    assert rows[0]["class"] == "same"


def test_blocked_episodes_group_consecutive_ticks():
    rows = [{"time": 0, "side": "long", "gate": "min_r"}, {"time": Q, "side": "long", "gate": "min_r"},
            {"time": 3 * Q, "side": "long", "gate": "min_r"},                 # a gap: new episode
            {"time": 4 * Q, "side": "short", "gate": "min_r"},                # side change: new
            {"time": 5 * Q, "side": "short", "gate": "leverage"}]             # gate change: new
    assert T.blocked_episodes(rows) == {"leverage": 1, "min_r": 3}
    assert T.blocked_episodes([]) == {}


def test_buffer_stats_reports_how_often_each_floor_binds():
    # ATR14 4 on the warmup: the live buffer is 5.2 on every tick
    c15 = _bars([ENTER_LONG] * 4)
    s = T.buffer_stats(c15, _warmup(), T0, T0 + 4 * Q, floors=(5.0, 6.0), pcts=(5.0, 6.0))
    assert s["ticks"] == 4 and s["median"] == pytest.approx(5.2)
    assert s["binds_pct"] == {"floor_5.00": 0.0, "floor_6.00": 100.0, "pct_5.0": 0.0, "pct_6.0": 100.0}


def test_summarize_reports_floor_counters():
    run = simulate(_bars([ENTER_LONG, ENTER_LONG]), _warmup(), LONG, _params(leverage=10.0, stop_buffer_floor=1.6))
    s = T.summarize(run, T0, T0 + 2 * Q)
    assert s["floor_blocked_ticks"] == {"min_r": 2} and s["floor_blocked_episodes"] == {"min_r": 1}
    assert s["trades"] == 0 and s["final_equity"] == pytest.approx(64.0)


def test_brent_identical_proves_a_fixed_buffer_ignores_every_floor():
    out = T.brent_identical(_bars([ENTER_LONG, DIP_LONG, ENTER_LONG, DEEP_LONG]), _warmup(), LONG,
                            _params(stop_buffer_atr=None), T0, T0 + 4 * Q)
    assert out["all_identical"] is True and set(out["identical"]) == set(T.brent_variants())


def test_score_vs_live_uses_live_as_the_baseline():
    rows = {"a": {"live": {"final_equity": 100.0}, "x": {"final_equity": 150.0}},
            "b": {"live": {"final_equity": 100.0}, "x": {"final_equity": 50.0}}}
    s = T.score_vs_live(rows)
    assert s["x"]["beats"] == 1 and s["x"]["loses"] == 1 and "live" not in s


def test_run_dataset_and_csv_rows_cover_every_variant():
    named = {"live": {"stop_buffer_atr": 0.05}, "floor_0.50": {"stop_buffer_atr": 0.05, "stop_buffer_floor": 0.5}}
    r = T.run_dataset(_bars([ENTER_LONG, DIP_LONG, UP_LONG]), _warmup(), LONG, _params(), T0, T0 + 3 * Q, named)
    assert set(r["variants"]) == set(named) and "vs_live" not in r["variants"]["live"]
    assert r["variants"]["floor_0.50"]["vs_live"]["stop_to_win"] == 1
    srows = T.summary_csv_rows({"ds": r})
    assert [x["variant"] for x in srows] == ["live", "floor_0.50"]
    assert set(srows[0]) == set(T.SUMMARY_FIELDS)
    erows = T.entry_csv_rows({"ds": r})
    assert len(erows) == 1 and erows[0]["class"] == "stop_to_win" and set(erows[0]) == set(T.ENTRY_FIELDS)
