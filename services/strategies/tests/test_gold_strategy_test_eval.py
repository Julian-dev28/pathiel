"""Gate tests for the gold strategy eval's pure parts
(services/strategies/evals/gold_strategy_test.py,
GOLD_STRATEGY_TEST_2026-11-05.md). Synthetic candles/theses only, no
network, no live journal reads."""
from __future__ import annotations

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.evals import gold_strategy_test as G

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _candle(t, c):
    return Candle(t=t, o=c, h=c, l=c, c=c, v=1.0)


def _thesis(created_at_ms, side, entry_px=4000.0, thesis_id=None):
    return ThesisDirection(created_at_ms=created_at_ms, side=side, entry_px=entry_px, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id=thesis_id or f"t{created_at_ms}")


# ── variants / families ──────────────────────────────────────────────────

def test_variants_cover_the_floor_grid_times_both_leverage_rules_and_set_both_knobs():
    named = G.variants()
    assert len(named) == len(G.FLOOR_PCTS) * len(G.LEV_MODES) == 14
    assert named["floor_0.8_fit"] == {"stop_buffer_floor_frac": 0.008, "entry_fit_leverage": (20.0, 15.0)}
    assert named["floor_0.0_fix20"] == {"stop_buffer_floor_frac": None, "entry_fit_leverage": None}
    assert named["floor_0.3_fix20"]["stop_buffer_floor_frac"] == 0.003
    for ov in named.values():
        assert set(ov) == {"stop_buffer_floor_frac", "entry_fit_leverage"}
    assert G.BASELINE in named


def test_families_are_the_floor_grid_in_order_per_leverage_rule():
    fams = G.families()
    assert set(fams) == {"floor_fix20", "floor_fit"}
    assert fams["floor_fit"] == [f"floor_{p:.1f}_fit" for p in G.FLOOR_PCTS]
    named = G.variants()
    for grid in fams.values():
        assert all(name in named for name in grid)


# ── windows and the market's own move ────────────────────────────────────

def test_gold_move_pct_first_close_to_last_close_inside_the_window():
    c1h = [_candle(0, 4000.0), _candle(HOUR_MS, 4100.0), _candle(2 * HOUR_MS, 4040.0), _candle(3 * HOUR_MS, 5000.0)]
    assert G.gold_move_pct(c1h, 0, 3 * HOUR_MS) == 1.0            # 4000 -> 4040; the 5000 bar is outside
    assert G.gold_move_pct(c1h, 0, HOUR_MS) is None               # one bar: no move
    assert G.gold_move_pct([], 0, DAY_MS) is None


def test_journal_window_holds_each_call_for_its_horizon_and_clamps_to_the_data():
    th = [_thesis(10 * DAY_MS, "short"), _thesis(12 * DAY_MS, "short")]
    assert G.journal_window(th, 100 * DAY_MS) == (10 * DAY_MS, 19 * DAY_MS)       # last call + 7 days
    assert G.journal_window(th, 15 * DAY_MS) == (10 * DAY_MS, 15 * DAY_MS)        # the data ends first
    assert G.journal_window(th, 10 * DAY_MS) is None                              # nothing to run
    assert G.journal_window([], 100 * DAY_MS) is None


# ── scoring ──────────────────────────────────────────────────────────────

def _results(by_variant):
    """{dataset: {variants: {name: summary}}} from {name: [(final, dd, liq), ...]} per dataset index."""
    n = len(next(iter(by_variant.values())))
    out = {}
    for i in range(n):
        out[f"ds{i}"] = {"variants": {v: {"final_equity": rows[i][0], "max_dd_pct": rows[i][1],
                                          "max_stop_liq_frac": rows[i][2], "trades": 3}
                                      for v, rows in by_variant.items()}}
    return out


def test_positive_count_counts_datasets_ended_above_the_start():
    res = _results({"a": [(70.0, 10.0, 0.3), (60.0, 10.0, 0.3), (64.0, 10.0, 0.3)]})
    assert G.positive_count(res, "a", ["ds0", "ds1", "ds2"]) == 1
    assert G.positive_count(res, "a", ["ds0", "missing"]) == 1


def test_adopt_verdict_passes_only_when_every_gate_clears():
    scores = {"cand": {"beats": 3, "loses": 0, "of": 3, "geo_mean_ratio": 1.2},
              "left": {"beats": 2, "loses": 1, "of": 3, "geo_mean_ratio": 1.05},
              "right": {"beats": 2, "loses": 1, "of": 3, "geo_mean_ratio": 1.01}}
    res = _results({"cand": [(70.0, 20.0, 0.4), (66.0, 15.0, 0.3), (65.0, 10.0, 0.2)],
                    "left": [(65.0, 20.0, 0.4)] * 3, "right": [(65.0, 20.0, 0.4)] * 3})
    fams = {"grid": ["left", "cand", "right"]}
    v = G.adopt_verdict(scores, res, ["ds0", "ds1", "ds2"], "cand", baseline_dd=20.0, fams=fams)
    assert v["adopt"] is True and v["fails"] == [] and v["positive_controls"] == 3 and v["plateau"] is True
    # the stop past half the liquidation distance fails it alone
    res_liq = _results({"cand": [(70.0, 20.0, 0.51), (66.0, 15.0, 0.3), (65.0, 10.0, 0.2)],
                        "left": [(65.0, 20.0, 0.4)] * 3, "right": [(65.0, 20.0, 0.4)] * 3})
    v2 = G.adopt_verdict(scores, res_liq, ["ds0", "ds1", "ds2"], "cand", 20.0, fams)
    assert v2["adopt"] is False and v2["fails"] == ["stop inside half the liquidation distance"]
    # a drawdown more than 2 points over the baseline's fails it
    res_dd = _results({"cand": [(70.0, 23.0, 0.4), (66.0, 15.0, 0.3), (65.0, 10.0, 0.2)],
                       "left": [(65.0, 20.0, 0.4)] * 3, "right": [(65.0, 20.0, 0.4)] * 3})
    assert G.adopt_verdict(scores, res_dd, ["ds0", "ds1", "ds2"], "cand", 20.0, fams)["fails"] == [
        "max DD within 2pts of the baseline"]


def test_adopt_verdict_needs_most_controls_beaten_and_half_positive():
    scores = {"cand": {"beats": 1, "loses": 2, "of": 3, "geo_mean_ratio": 0.8}}
    res = _results({"cand": [(60.0, 10.0, 0.3), (50.0, 10.0, 0.3), (65.0, 10.0, 0.3)]})
    v = G.adopt_verdict(scores, res, ["ds0", "ds1", "ds2"], "cand", 10.0, fams={})
    assert v["adopt"] is False
    assert "beats the baseline on most controls" in v["fails"]
    assert "ends above the start on at least half the controls" in v["fails"]
    assert G.adopt_verdict({}, res, ["ds0"], "cand", 10.0, fams={}) == {"adopt": False, "candidate": "cand",
                                                                        "why": "no score"}


def test_rank_puts_adopters_first_then_beats_minus_loses_geo_mean_and_positives():
    scores = {"a": {"beats": 2, "loses": 1, "of": 3, "geo_mean_ratio": 1.1},
              "b": {"beats": 2, "loses": 1, "of": 3, "geo_mean_ratio": 1.3},
              "c": {"beats": 3, "loses": 0, "of": 3, "geo_mean_ratio": 0.9},
              "d": {"beats": 0, "loses": 3, "of": 3, "geo_mean_ratio": 0.5}}
    verdicts = {"a": {"adopt": False, "positive_controls": 1}, "b": {"adopt": False, "positive_controls": 1},
                "c": {"adopt": False, "positive_controls": 2}, "d": {"adopt": True, "positive_controls": 0}}
    assert G.rank(verdicts, scores) == ["d", "c", "b", "a"]


def test_floor_curve_sums_net_and_trades_and_keeps_the_worst_drawdown():
    named = G.variants()
    res = {"ds0": {"variants": {v: {"final_equity": 70.0, "max_dd_pct": 10.0, "trades": 2} for v in named}},
           "ds1": {"variants": {v: {"final_equity": 60.0, "max_dd_pct": 30.0, "trades": 3} for v in named}}}
    rows = G.floor_curve(res, ["ds0", "ds1"])
    assert len(rows) == 14 and rows[0]["variant"] == "floor_0.0_fix20"
    assert all(r["net_usd"] == 2.0 and r["trades"] == 5 and r["max_dd_pct"] == 30.0 for r in rows)


def test_summary_csv_rows_carry_the_window_and_the_markets_move():
    res = {"always_long_30d": {"window": ["a", "b"], "days": 30.0, "gold_move_pct": -1.2, "variants": {
        "floor_0.8_fit": {"final_equity": 70.0, "return_pct": 9.4, "per_day_pct": 0.3, "max_dd_pct": 12.0, "trades": 4,
                          "win_rate": 0.5, "avg_r": 0.7, "worst_loss_pct": -9.0, "max_stop_liq_frac": 0.4,
                          "exits": {"stop": 2, "target": 2}}}}}
    rows = G.summary_csv_rows(res)
    assert rows == [{"dataset": "always_long_30d", "window_start": "a", "window_end": "b", "days": 30.0,
                     "gold_move_pct": -1.2, "variant": "floor_0.8_fit", "final_equity": 70.0, "net_usd": 6.0,
                     "return_pct": 9.4, "per_day_pct": 0.3, "max_dd_pct": 12.0, "trades": 4, "win_rate": 0.5,
                     "avg_r": 0.7, "worst_loss_pct": -9.0, "max_stop_liq_frac": 0.4, "stop_exits": 2, "target_exits": 2}]
    assert set(rows[0]) == set(G.SUMMARY_FIELDS)


def test_run_variants_on_a_flat_synthetic_market_takes_no_trade_and_reports_the_move():
    """A flat tape (no range) trades nothing in every variant; the row still
    carries the window, the market's move and a zero trade count."""
    from services.strategies.evals.live_params import live_silver_params
    from services.strategies.evals.market_scout import direction_series
    start = 0
    c1h = [Candle(t=i * HOUR_MS, o=4000.0, h=4001.0, l=3999.0, c=4000.0, v=1.0) for i in range(72)]
    c15 = [Candle(t=i * 900_000, o=4000.0, h=4000.5, l=3999.5, c=4000.0, v=1.0) for i in range(72 * 4)]
    base = live_silver_params(25.0, 1.0, starting_equity=G.START)
    out = G.run_variants(c15, c1h, direction_series("always_long", c1h), base, start, 72 * HOUR_MS,
                         {"floor_0.8_fit": G.variants()["floor_0.8_fit"]})
    s = out["variants"]["floor_0.8_fit"]
    assert out["days"] == 3.0 and out["gold_move_pct"] == 0.0
    assert s["trades"] == 0 and s["final_equity"] == G.START and s["max_stop_liq_frac"] == 0.0
    assert s["per_day_pct"] == 0.0
