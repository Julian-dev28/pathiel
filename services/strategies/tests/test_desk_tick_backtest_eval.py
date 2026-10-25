"""Offline gate test for the desk-tick backtest eval's PURE logic
(services/strategies/evals/desk_tick_backtest.py): aggregation, the
direction-series builders, and the calibration diff — all exercised on
injected `Run`/`Trade` objects and synthetic candles, never a live
`simulate()` call or the network. The eval's own __main__ fetch path is a
periodic/nightly run, not a commit gate."""
from __future__ import annotations

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, Trade
from services.strategies.evals import desk_tick_backtest as E

DAY_MS = D.DAY_MS
HOUR_MS = D.HOUR_MS


def _trade(**kw) -> Trade:
    base = dict(side="long", entry_time=0, entry_px=100.0, size=1.0, stop_px=95.0, target_px=110.0,
               exit_time=HOUR_MS, exit_px=105.0, exit_reason="target", gross_pnl_usd=5.0,
               fees_usd=0.1, funding_usd=0.0, net_pnl_usd=4.9, risk_usd=5.0, r_multiple=0.98)
    base.update(kw)
    return Trade(**base)


# ── aggregate() ──────────────────────────────────────────────────────────

def test_aggregate_basic_stats():
    win = _trade(net_pnl_usd=10.0, r_multiple=2.0, gross_pnl_usd=10.5, fees_usd=0.5)
    loss = _trade(net_pnl_usd=-5.0, r_multiple=-1.0, gross_pnl_usd=-4.7, fees_usd=0.3, exit_reason="stop")
    run = Run(trades=[win, loss], rested=3, filled=2, cancelled_resting=1,
             fees_total_usd=0.8, funding_total_usd=0.2, gross_pnl_usd=5.8,
             max_drawdown_pct=0.12, final_equity=69.8, params=Params())
    report = E.aggregate("test_run", run)

    assert report["n_trades"] == 2
    assert report["win_rate"] == 0.5
    assert report["expectancy_r"] == pytest.approx(0.5)
    assert report["fill_rate"] == pytest.approx(2 / 3)
    assert report["fees_share_of_gross"] == pytest.approx(0.8 / abs(5.8))
    assert report["funding_share_of_gross"] == pytest.approx(0.2 / abs(5.8))
    assert report["liquidated"] is False


def test_aggregate_empty_run_has_no_rates():
    run = Run(trades=[], rested=0, filled=0, params=Params())
    report = E.aggregate("empty", run)
    assert report["n_trades"] == 0
    assert report["win_rate"] is None
    assert report["expectancy_r"] is None
    assert report["fill_rate"] is None


def test_aggregate_liquidated_run():
    run = Run(trades=[_trade(net_pnl_usd=-64.0, exit_reason="liquidation")], liquidated=True,
             liquidation_time=12_345, final_equity=0.0, params=Params())
    report = E.aggregate("liq", run)
    assert report["liquidated"] is True
    assert report["liquidation_time"] == 12_345
    assert report["final_equity"] == 0.0


# ── direction-series builders ──────────────────────────────────────────────

def test_always_direction_is_constant():
    d_at = E.always_direction("short")
    assert d_at(0).side == "short"
    assert d_at(999_999_999).side == "short"


def test_journaled_direction_switches_at_created_at_and_is_none_before_first():
    th1 = D.ThesisDirection(created_at_ms=1_000, side="long", entry_px=100.0, stop_px=90.0,
                            invalidation_px=91.0, targets=[110.0], thesis_id="a")
    th2 = D.ThesisDirection(created_at_ms=5_000, side="short", entry_px=120.0, stop_px=130.0,
                            invalidation_px=129.0, targets=[100.0], thesis_id="b")
    d_at = E.journaled_direction([th2, th1])   # unsorted input on purpose

    assert d_at(500) is None
    first = d_at(1_000)
    assert first.side == "long" and first.invalidation_px == 91.0 and first.entry_ceiling == 100.0
    assert d_at(4_999).side == "long"
    second = d_at(5_000)
    assert second.side == "short" and second.entry_ceiling == 120.0
    assert d_at(999_999).side == "short"


# ── daily-sign builder ──────────────────────────────────────────────────────

def _daily_candle(day: int, close: float) -> Candle:
    t = day * DAY_MS + 12 * HOUR_MS
    return Candle(t=t, o=close, h=close, l=close, c=close, v=1.0)


def test_daily_sign_direction_warmup_and_sign_uptrend():
    candles = [_daily_candle(d, 100.0 + d) for d in range(30)]   # strict uptrend
    d_at, n_signal, n_no_signal = E.build_daily_sign_direction(candles, lookback_days=20)

    assert n_no_signal == 21     # days 0..20 lack a full 20-day trailing window
    assert n_signal == 9         # days 21..29 have one

    assert d_at(5 * DAY_MS) is None
    for day in range(21, 30):
        d = d_at(day * DAY_MS)
        assert d is not None and d.side == "long"


def test_daily_sign_direction_sign_downtrend():
    candles = [_daily_candle(d, 200.0 - d) for d in range(30)]   # strict downtrend
    d_at, _, _ = E.build_daily_sign_direction(candles, lookback_days=20)
    for day in range(21, 30):
        d = d_at(day * DAY_MS)
        assert d is not None and d.side == "short"


# ── calibration diff ────────────────────────────────────────────────────────

def test_calibration_diff_counts_agreement_and_divergence():
    entry, exit_ = 10 * HOUR_MS, 20 * HOUR_MS
    run = Run(trades=[_trade(entry_time=entry, exit_time=exit_)])
    log_rows = [
        {"ts_ms": 11 * HOUR_MS, "in_position": True},    # inside the trade -> agree
        {"ts_ms": 5 * HOUR_MS, "in_position": False},    # before it -> agree
        {"ts_ms": 15 * HOUR_MS, "in_position": False},   # inside the trade but log says flat -> disagree
    ]
    result = E.calibration_diff(log_rows, run, window_start_ms=0, window_end_ms=24 * HOUR_MS)
    assert result["compared"] == 3
    assert result["agree"] == 2
    assert result["disagree"] == 1
    assert result["divergences_sample"][0]["ts_ms"] == 15 * HOUR_MS


def test_calibration_diff_ignores_rows_outside_window():
    run = Run(trades=[_trade(entry_time=0, exit_time=HOUR_MS)])
    log_rows = [{"ts_ms": 100 * HOUR_MS, "in_position": True}]   # far outside the window
    result = E.calibration_diff(log_rows, run, window_start_ms=0, window_end_ms=10 * HOUR_MS)
    assert result["compared"] == 0
    assert result["agreement_rate"] is None


def test_parse_progress_log_parses_known_lines_and_skips_others(tmp_path):
    log = tmp_path / "progress.log"
    log.write_text(
        "DESK-TICK ai 2026-10-24T20:56:55Z action=SKIP slot held by xyz:BRENTOIL\n"
        "DESK-TICK oil 2026-10-24T21:04:07Z coin=xyz:BRENTOIL mid=111.555 pos=8.69@111.43 action=HOLD blah\n"
        "DESK-TICK oil 2026-10-23T08:21:50Z coin=xyz:BRENTOIL mid=109.125 pos=flat action=NONE\n"
        "garbage line that matches nothing\n"
    )
    rows = E.parse_progress_log(str(log))
    assert len(rows) == 2
    assert rows[0]["in_position"] is True
    assert rows[1]["in_position"] is False


def test_parse_progress_log_missing_file_returns_empty():
    assert E.parse_progress_log("/does/not/exist.log") == []
