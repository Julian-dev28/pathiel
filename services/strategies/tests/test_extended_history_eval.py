"""Gate tests for the extended-history eval's pure parts
(services/strategies/evals/extended_history_test.py): --window parsing,
buy-and-hold, the thesis direction-hit rate, the chained-equity run (via
an injected `simulate_fn` stub -- no real candle walk, no network), the
summary CSV rows, and the 2026-11-03 leverage sweep (the risk cap vs.
leverage sizing mechanism, and the sweep's own wiring on the degenerate
zero-candle case -- real `simulate`/`chain_runs`, trivially zero trades,
deterministic and fast). No network."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.backtest.desk_tick_sim import Params, Run, Trade, live_params, risk_capped_notional
from services.strategies.evals import extended_history_test as T

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _th(day: int, side: str, entry_px: float = 100.0, **over) -> ThesisDirection:
    return ThesisDirection(created_at_ms=ms(2026, 9, day, 16), side=side, entry_px=entry_px, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id=f"oil-t{day}", **over)


# ── parse_windows ────────────────────────────────────────────────────────

def test_parse_windows_preserves_order():
    assert T.parse_windows(["sep=/a/b", "oct=/c/d"]) == [("sep", "/a/b"), ("oct", "/c/d")]
    assert T.parse_windows([]) == []


def test_parse_windows_rejects_malformed_entries():
    for bad in ("no-equals-sign", "=novalue", "noname=", "two=equals=signs"):
        with pytest.raises(ValueError):
            T.parse_windows([bad])


# ── buy_hold_pct ─────────────────────────────────────────────────────────

def test_buy_hold_pct_from_first_open_to_last_close():
    start = ms(2026, 9, 1, 0)
    c15 = [Candle(t=start + i * T.FIFTEEN_MS, o=100.0, h=101.0, l=99.0, c=100.0, v=1.0) for i in range(4)]
    c15[0] = Candle(t=c15[0].t, o=100.0, h=101.0, l=99.0, c=100.0, v=1.0)
    c15[-1] = Candle(t=c15[-1].t, o=102.0, h=112.0, l=101.0, c=110.0, v=1.0)
    out = T.buy_hold_pct(c15, start, start + 4 * T.FIFTEEN_MS)
    assert out == 10.0   # 110 / 100 - 1


def test_buy_hold_pct_none_under_two_bars():
    start = ms(2026, 9, 1, 0)
    assert T.buy_hold_pct([], start, start + T.FIFTEEN_MS) is None
    one = [Candle(t=start, o=100.0, h=101.0, l=99.0, c=100.0, v=1.0)]
    assert T.buy_hold_pct(one, start, start + T.FIFTEEN_MS) is None


# ── direction_hit_rate ───────────────────────────────────────────────────

def test_direction_hit_rate_counts_only_sided_theses_with_an_entry():
    long_hit = _th(1, "long", entry_px=100.0)
    short_miss = _th(5, "short", entry_px=100.0)   # far enough from long_hit that the 1-day horizons never overlap
    flat = _th(9, "flat", entry_px=None)
    no_entry = _th(13, "long", entry_px=None)
    bars = []
    # long_hit: price rises after creation -> hit
    bars.append(Candle(t=long_hit.created_at_ms, o=100.0, h=100.0, l=100.0, c=100.0, v=1.0))
    bars.append(Candle(t=long_hit.created_at_ms + HOUR_MS, o=105.0, h=105.0, l=105.0, c=105.0, v=1.0))
    # short_miss: price also rises -> short loses
    bars.append(Candle(t=short_miss.created_at_ms, o=100.0, h=100.0, l=100.0, c=100.0, v=1.0))
    bars.append(Candle(t=short_miss.created_at_ms + HOUR_MS, o=108.0, h=108.0, l=108.0, c=108.0, v=1.0))
    out = T.direction_hit_rate([long_hit, short_miss, flat, no_entry], bars, horizon_days=1.0)
    assert out["n"] == 2   # flat and no-entry excluded
    assert out["hit_rate"] == 0.5


def test_direction_hit_rate_skips_a_thesis_with_no_bar_in_its_horizon():
    th = _th(1, "long", entry_px=100.0)
    bars = [Candle(t=th.created_at_ms - HOUR_MS, o=100.0, h=100.0, l=100.0, c=100.0, v=1.0)]  # before created_at
    out = T.direction_hit_rate([th], bars, horizon_days=1.0)
    assert out == {"n": 0, "hit_rate": None}


# ── chain_runs (injected simulate_fn -- no real candle walk) ────────────

def _trade(net_pnl_usd: float, entry_time: int) -> Trade:
    return Trade(side="long", entry_time=entry_time, entry_px=100.0, size=1.0, stop_px=99.0, target_px=101.0,
                exit_time=entry_time + HOUR_MS, exit_px=100.0 + net_pnl_usd, exit_reason="target",
                net_pnl_usd=net_pnl_usd, risk_usd=1.0, r_multiple=net_pnl_usd)


def _fake_simulate(responses):
    """Returns a simulate_fn stub that hands back each Run in `responses`
    in order, one per call, and records the `starting_equity` it was
    called with (via params.starting_equity) on each Run it returns."""
    calls = []

    def fn(c15, c1h, direction_at, params, funding):
        calls.append(params.starting_equity)
        return responses[len(calls) - 1]

    fn.calls = calls
    return fn


def test_chain_runs_carries_final_equity_into_the_next_legs_starting_equity():
    leg1_run = Run(trades=[_trade(10.0, ms(2026, 9, 1, 0))],
                   equity_curve=[(ms(2026, 9, 1, 0), 64.0), (ms(2026, 9, 2, 0), 74.0)], final_equity=74.0)
    leg2_run = Run(trades=[_trade(-5.0, ms(2026, 9, 3, 0))],
                   equity_curve=[(ms(2026, 9, 3, 0), 74.0), (ms(2026, 9, 4, 0), 69.0)], final_equity=69.0)
    fn = _fake_simulate([leg1_run, leg2_run])
    legs = [
        (lambda t: None, [], [], None, ms(2026, 9, 1, 0), ms(2026, 9, 2, 0)),
        (lambda t: None, [], [], None, ms(2026, 9, 3, 0), ms(2026, 9, 4, 0)),
    ]
    result = T.chain_runs(legs, live_params(), 64.0, simulate_fn=fn)
    assert fn.calls == [64.0, 74.0]              # leg2 started from leg1's final equity
    assert result["final_equity"] == 69.0
    assert result["legs_run"] == 2 and result["legs_total"] == 2
    assert len(result["trades"]) == 2 and len(result["equity_curve"]) == 4
    assert result["liquidated"] is False


def test_chain_runs_stops_after_a_liquidation():
    leg1_run = Run(trades=[_trade(-64.0, ms(2026, 9, 1, 0))],
                   equity_curve=[(ms(2026, 9, 1, 0), 0.0)], final_equity=0.0,
                   liquidated=True, liquidation_time=ms(2026, 9, 1, 12))
    leg2_run = Run(trades=[_trade(100.0, ms(2026, 9, 3, 0))], equity_curve=[], final_equity=100.0)
    fn = _fake_simulate([leg1_run, leg2_run])
    legs = [
        (lambda t: None, [], [], None, ms(2026, 9, 1, 0), ms(2026, 9, 2, 0)),
        (lambda t: None, [], [], None, ms(2026, 9, 3, 0), ms(2026, 9, 4, 0)),
    ]
    result = T.chain_runs(legs, live_params(), 64.0, simulate_fn=fn)
    assert fn.calls == [64.0]       # leg2 never ran
    assert result["legs_run"] == 1 and result["legs_total"] == 2
    assert result["liquidated"] is True
    assert result["final_equity"] == 0.0


def test_summarize_chain_reuses_brent_stop_buffer_tests_summarize():
    run = Run(trades=[_trade(10.0, ms(2026, 9, 1, 0))],
             equity_curve=[(ms(2026, 9, 1, 0), 64.0), (ms(2026, 9, 2, 0), 74.0)], final_equity=74.0)
    result = {"legs_run": 1, "legs_total": 1, "liquidated": False, "liquidation_time": None,
             "final_equity": 74.0, "trades": run.trades, "equity_curve": run.equity_curve,
             "fees_total_usd": 0.0, "funding_total_usd": 0.0}
    s = T.summarize_chain(result, live_params(), 64.0, ms(2026, 9, 1, 0), ms(2026, 9, 2, 0))
    assert s["final_equity"] == 74.0
    assert s["return_pct"] == round((74.0 / 64.0 - 1.0) * 100, 1)
    assert s["trades"] == 1
    assert s["legs_run"] == 1 and s["legs_total"] == 1


def test_max_dd_from_curve_seeds_the_peak_at_start_equity():
    # a curve that never exceeds start_equity: drawdown measured from the start, not from its own first mark
    curve = [(0, 50.0), (1, 40.0)]
    assert T._max_dd_from_curve(curve, 100.0) == pytest.approx(0.6)   # (100-40)/100
    rising = [(0, 110.0), (1, 100.0)]
    assert T._max_dd_from_curve(rising, 100.0) == pytest.approx(10.0 / 110.0)


# ── summary rows ─────────────────────────────────────────────────────────

def test_summary_csv_rows_covers_windows_controls_and_chain():
    thesis_summary = {"final_equity": 74.0, "return_pct": 15.6, "days": 10.0, "max_dd_pct": 5.0, "trades": 4,
                      "win_rate": 0.5, "avg_r": 1.1, "worst_loss_usd": -3.0, "worst_loss_pct": -4.0,
                      "exits": {"target": 2, "stop": 1, "invalidation": 1}}
    ctrl_summary = {"final_equity": 60.0, "return_pct": -6.3, "days": 10.0, "max_dd_pct": 20.0, "trades": 6,
                    "win_rate": 0.3, "avg_r": -0.2, "worst_loss_usd": -8.0, "worst_loss_pct": -12.0,
                    "exits": {"stop": 6}}
    windows = {"sep": {"thesis": thesis_summary, "controls": {"always_long": ctrl_summary}}}
    chain_summary = {**thesis_summary, "final_equity": 80.0}
    rows = T.summary_csv_rows(windows, chain_summary)
    by_key = {(r["dataset"], r["variant"]): r for r in rows}
    assert ("sep", "live") in by_key and ("sep", "always_long") in by_key and ("chained", "live") in by_key
    assert by_key[("sep", "live")]["thesis_close_exits"] == 1
    assert by_key[("sep", "live")]["other_exits"] == 0
    assert by_key[("chained", "live")]["final_equity"] == 80.0
    assert set(rows[0]) == set(T.SUMMARY_FIELDS)


def test_summary_csv_rows_without_a_chain_row():
    windows = {"sep": {"thesis": {"final_equity": 74.0, "return_pct": 0.0, "days": 1.0, "max_dd_pct": 0.0,
                                  "trades": 0, "win_rate": None, "avg_r": None, "worst_loss_usd": 0.0,
                                  "worst_loss_pct": 0.0, "exits": {}}, "controls": {}}}
    rows = T.summary_csv_rows(windows, None)
    assert len(rows) == 1 and rows[0]["dataset"] == "sep"


# ── leverage sweep, 2026-11-03 ("try with 5x 10x and 15x is it better roi?") ─

def test_risk_capped_notional_is_leverage_independent_when_the_cap_binds():
    """A wide-enough risk distance (5%) keeps the 15% cap's target notional
    under even 5x's full-port size at $1000 equity (needs leverage >= about
    0.15/0.05 = 3x to supply it), so the cap -- not the leverage -- binds
    at both 5x and 20x, and sizing is identical."""
    base = Params(risk_cap_frac=0.15, leverage=20.0)
    equity, entry, stop = 1000.0, 100.0, 95.0
    n20 = risk_capped_notional(equity, dataclasses.replace(base, leverage=20.0), None, entry, stop)
    n5 = risk_capped_notional(equity, dataclasses.replace(base, leverage=5.0), None, entry, stop)
    assert n20 == pytest.approx(n5)


def test_risk_capped_notional_leverage_binds_at_low_enough_leverage():
    """A very tight risk distance (0.01%) asks the 15% cap for a huge
    notional; at 1x, the full-port size (bounded by margin, not the cap)
    falls well under that, so leverage -- not the cap -- binds."""
    base = Params(risk_cap_frac=0.15, leverage=20.0)
    equity, entry, stop = 1000.0, 100.0, 99.99
    n20 = risk_capped_notional(equity, dataclasses.replace(base, leverage=20.0), None, entry, stop)
    n1 = risk_capped_notional(equity, dataclasses.replace(base, leverage=1.0), None, entry, stop)
    assert n1 < n20


def test_leverage_params_only_changes_leverage_and_risk_cap_frac():
    base = live_params()
    p = T.leverage_params(base, 5.0, None)
    assert p.leverage == 5.0 and p.risk_cap_frac is None
    assert p.max_leverage == base.max_leverage   # the venue's own max is untouched
    assert p.stop_buffer == base.stop_buffer and p.target_offset_frac == base.target_offset_frac


def test_size_changed_count_ignores_unmatched_and_equal_sizes():
    base_trades = [_trade(1.0, ms(2026, 9, 1, 0)), _trade(1.0, ms(2026, 9, 2, 0))]
    base_trades[0] = dataclasses.replace(base_trades[0], size=10.0)
    base_trades[1] = dataclasses.replace(base_trades[1], size=10.0)
    variant_trades = [
        dataclasses.replace(_trade(1.0, ms(2026, 9, 1, 0)), size=10.0),    # same entry, same size: not changed
        dataclasses.replace(_trade(1.0, ms(2026, 9, 2, 0)), size=4.0),     # same entry, different size: changed
        dataclasses.replace(_trade(1.0, ms(2026, 9, 3, 0)), size=99.0),    # no match in base: a new trade, not counted
    ]
    assert T.size_changed_count(base_trades, variant_trades) == 1


def test_leverage_row_reports_net_and_leverage_gate_refusals():
    s = {"final_equity": 80.0, "return_pct": 25.0, "days": 10.0, "max_dd_pct": 12.0, "trades": 5,
        "worst_loss_pct": -8.0, "refused": {"leverage": 2, "min_r": 3}}
    row = T._leverage_row(s, 64.0, 10.0)
    assert row == {"leverage": 10.0, "net_usd": 16.0, "return_pct": 25.0, "per_day_pct": T.per_day_pct(s),
                   "max_dd_pct": 12.0, "worst_loss_pct": -8.0, "trades": 5,
                   "refused_leverage": 2, "refused_total": 5}


def test_leverage_sweep_single_shape_and_zero_trades_on_empty_candles():
    """The real `simulate` on an empty window: every leverage/mode fires
    with zero trades, zero refusals, zero size-changed -- a degenerate but
    real pass through the sweep's own wiring, not a stub."""
    base = dataclasses.replace(live_params(), starting_equity=64.0, risk_cap_frac=0.15)
    start_ms, end_ms = ms(2026, 9, 1, 0), ms(2026, 9, 2, 0)
    out = T.leverage_sweep_single([], [], lambda t: None, base, start_ms, end_ms, None, 64.0)
    assert set(out) == {"a", "b"}
    assert set(out["a"]) == {"5x", "10x", "15x", "20x"}
    for mode in ("a", "b"):
        for lev_label, row in out[mode].items():
            assert row["trades"] == 0 and row["net_usd"] == 0.0 and row["refused_leverage"] == 0
            if mode == "a":
                assert row["size_changed_trades"] == 0


def test_leverage_sweep_chain_shape_and_zero_trades_on_empty_legs():
    base = dataclasses.replace(live_params(), starting_equity=64.0, risk_cap_frac=0.15)
    legs = [
        (lambda t: None, [], [], None, ms(2026, 9, 1, 0), ms(2026, 9, 2, 0)),
        (lambda t: None, [], [], None, ms(2026, 9, 3, 0), ms(2026, 9, 4, 0)),
    ]
    out = T.leverage_sweep_chain(legs, base, 64.0)
    assert set(out) == {"a", "b"}
    for mode in ("a", "b"):
        for lev_label, row in out[mode].items():
            assert row["trades"] == 0 and row["net_usd"] == 0.0
    for lev_label, row in out["a"].items():
        assert row["size_changed_trades"] == 0


def test_leverage_summary_csv_rows_flattens_dataset_mode_and_leverage():
    sweeps = {"sep": {"a": {"5x": {"leverage": 5.0, "net_usd": 1.0, "return_pct": 1.0, "per_day_pct": 0.1,
                                   "max_dd_pct": 2.0, "worst_loss_pct": -1.0, "trades": 1,
                                   "refused_leverage": 0, "refused_total": 0, "size_changed_trades": 0}},
                     "b": {"5x": {"leverage": 5.0, "net_usd": 2.0, "return_pct": 2.0, "per_day_pct": 0.2,
                                  "max_dd_pct": 3.0, "worst_loss_pct": -2.0, "trades": 2,
                                  "refused_leverage": 0, "refused_total": 0}}}}
    rows = T.leverage_summary_csv_rows(sweeps)
    assert len(rows) == 2
    by_mode = {r["mode"]: r for r in rows}
    assert by_mode["a"]["dataset"] == "sep" and by_mode["a"]["net_usd"] == 1.0
    assert by_mode["b"]["size_changed_trades"] is None   # mode b never computes it
    assert set(rows[0]) == set(T.LEVERAGE_SUMMARY_FIELDS)
