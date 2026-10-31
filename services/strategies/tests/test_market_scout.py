"""Offline gate tests for services/strategies/evals/market_scout.py: the
book-walk/slippage math, the ATR/correlation/persistence/session math, the
leverage-gate fraction, the scaled Params builder and the row composers.
Synthetic candles and books only -- no network. `main`, `fetch_market_meta`
and `fetch_l2_book` (the only network-touching parts) are exercised by
running the module live, not here."""
from __future__ import annotations

import math

import pytest

from pathiel.models.types import Candle
from services.strategies.backtest.desk_tick_sim import live_params
from services.strategies.evals import market_scout as M

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
FIFTEEN_MS = 900_000


# ── book walk / slippage ─────────────────────────────────────────────────

def test_walk_book_fills_across_levels_and_reports_the_vwap():
    levels = [(100.0, 10.0), (101.0, 10.0), (102.0, 10.0)]   # $1000, $1010, $1020 per level
    vwap, filled = M.walk_book(levels, 1500.0)
    assert filled
    # $1000 at 100 (10 units) + $500 at 101 (4.9505 units) -> notional/size
    assert vwap == pytest.approx(1500.0 / (10.0 + 500.0 / 101.0))


def test_walk_book_reports_unfilled_when_the_book_runs_out():
    levels = [(100.0, 1.0), (101.0, 1.0)]   # $201 total resting
    vwap, filled = M.walk_book(levels, 10_000.0)
    assert not filled
    assert vwap == pytest.approx(201.0 / 2.0)


def test_walk_book_on_an_empty_book_returns_none_unfilled():
    assert M.walk_book([], 1000.0) == (None, False)


def test_slippage_bps_sign_matches_side_and_rejects_a_bad_side():
    asks = [(101.0, 100.0)]   # plenty of depth, fills entirely at 101
    bids = [(99.0, 100.0)]
    buy = M.slippage_bps(asks, 500.0, mid=100.0, side="buy")
    sell = M.slippage_bps(bids, 500.0, mid=100.0, side="sell")
    assert buy["bps"] == pytest.approx(100.0) and buy["filled"]       # 1% above mid = 100 bps
    assert sell["bps"] == pytest.approx(100.0) and sell["filled"]     # 1% below mid = 100 bps too
    with pytest.raises(ValueError):
        M.slippage_bps(asks, 500.0, 100.0, "hold")


def test_slippage_bps_on_an_unfillable_book_is_none():
    assert M.slippage_bps([], 500.0, 100.0, "buy") == {"bps": None, "vwap_px": None, "filled": False}


# ── behaviour math ───────────────────────────────────────────────────────

def _flat_1h(n=40, h=101.0, l=99.0):
    return [Candle(t=i * HOUR_MS, o=100, h=h, l=l, c=100, v=1) for i in range(n)]


def test_atr14_series_matches_mean_atr_on_a_constant_range():
    c1h = _flat_1h()
    series = M.atr14_series(c1h)
    assert series and all(atr == pytest.approx(2.0) for _, atr, _ in series)
    assert M.atr14_series(c1h[:10]) == []            # under ATR14's own history


def test_leverage_gate_pass_frac_passes_on_a_tight_range_and_fails_on_a_wide_one():
    tight = M.leverage_gate_pass_frac(_flat_1h(h=100.2, l=99.8), trade_leverage=20.0, venue_max_leverage=20.0)
    assert tight["pass_frac"] == 1.0 and tight["n_days"] >= 1
    wide = M.leverage_gate_pass_frac(_flat_1h(h=120.0, l=80.0), trade_leverage=20.0, venue_max_leverage=20.0)
    assert wide["pass_frac"] == 0.0
    assert M.leverage_gate_pass_frac([], 20.0, 20.0) == {"n_days": 0, "pass_frac": None}


def _trending_1h(days=20, step=1.0):
    """One steadily-rising close per day, 24 bars each, so the daily range
    and ATR are well-defined and the trend is unambiguous."""
    out = []
    px = 100.0
    for d in range(days):
        for h in range(24):
            px += step / 24.0
            out.append(Candle(t=(d * 24 + h) * HOUR_MS, o=px, h=px + 0.05, l=px - 0.05, c=px, v=1))
    return out


def test_daily_range_pct_median_reads_the_days_high_low_close():
    c1h = _trending_1h(days=5, step=0.0)   # flat trend, constant +-0.05 range each day
    med = M.daily_range_pct_median(c1h)
    assert med == pytest.approx(0.10 / 100.0, rel=0.05)


def test_daily_returns_only_pairs_consecutive_days():
    c1h = _trending_1h(days=5, step=1.0)
    rets = M.daily_returns(c1h)
    assert set(rets) == {1, 2, 3, 4}
    assert all(r > 0 for r in rets.values())      # a steady uptrend returns positive every day


def test_pearson_corr_is_plus_one_for_identical_series_minus_one_for_mirrored():
    a = {0: 0.01, 1: -0.02, 2: 0.03, 3: 0.01}
    b = {k: v for k, v in a.items()}
    c = {k: -v for k, v in a.items()}
    assert M.pearson_corr(a, b) == pytest.approx(1.0)
    assert M.pearson_corr(a, c) == pytest.approx(-1.0)
    assert M.pearson_corr({0: 0.1, 1: 0.1}, {0: 0.1, 1: -0.1}) is None    # under 3 days
    assert M.pearson_corr({0: 0.1, 1: 0.1, 2: 0.1}, {0: 0.2, 1: 0.2, 2: 0.2}) is None  # no variance


def test_trend_persistence_is_high_on_a_steady_uptrend_and_low_on_a_mean_reverter():
    up = M.trend_persistence_frac(_trending_1h(days=30, step=1.0), h=5)
    assert up == pytest.approx(1.0)

    # a pure 10-day square wave: +1 for 10 days, -1 for 10 days, repeating.
    # The 5-day return 5 days apart always straddles a flip, so it's never
    # the same sign twice in a row.
    out = []
    px = 100.0
    for d in range(40):
        direction = 1.0 if (d // 10) % 2 == 0 else -1.0
        for h in range(24):
            px += direction * 0.5 / 24.0
            out.append(Candle(t=(d * 24 + h) * HOUR_MS, o=px, h=px + 0.05, l=px - 0.05, c=px, v=1))
    rev = M.trend_persistence_frac(out, h=5)
    assert rev is not None and rev < up


def test_session_share_counts_only_moves_inside_the_window():
    in_session = Candle(t=10 * HOUR_MS, o=100, h=101, l=99, c=101, v=1)    # hour 10, inside (7,21)
    out_session = Candle(t=2 * HOUR_MS, o=100, h=101, l=99, c=101, v=1)    # hour 2, outside
    assert M.session_share([in_session]) == pytest.approx(1.0)
    assert M.session_share([out_session]) == pytest.approx(0.0)
    assert M.session_share([in_session, out_session]) == pytest.approx(0.5)
    assert M.session_share([]) is None


# ── scaled Params / direction series ────────────────────────────────────

def test_scouting_params_rescales_the_buffer_and_caps_leverage_at_the_venue_max():
    p = M.scouting_params(venue_max_leverage=10.0, atr_price_mean=0.5)
    assert p.stop_buffer == pytest.approx(M.ATR_BUFFER_MULT * 0.5)
    assert p.leverage == 10.0 and p.max_leverage == 10.0
    # venue max above the live leverage: leverage stays at the live 20x
    p2 = M.scouting_params(venue_max_leverage=50.0, atr_price_mean=1.0)
    assert p2.leverage == live_params().leverage == 20.0 and p2.max_leverage == 50.0
    # every other live-rule field is untouched
    base = live_params()
    assert p2.entry_mode == base.entry_mode and p2.min_r == base.min_r
    assert p2.target_mode == base.target_mode and p2.target_offset_frac == base.target_offset_frac


def test_direction_series_dispatches_by_name_and_rejects_unknown():
    c1h = _trending_1h(days=30, step=1.0)
    always_long = M.direction_series("always_long", c1h)
    always_short = M.direction_series("always_short", c1h)
    assert always_long(0).side == "long" and always_short(0).side == "short"
    daily_sign = M.direction_series("daily_sign", c1h)
    assert callable(daily_sign)
    with pytest.raises(ValueError):
        M.direction_series("sideways", c1h)


# ── backtest plumbing (small synthetic path, smoke test) ───────────────

def _swinging_series(n15=6 * 24 * 4):
    px = [100 + 1.5 * math.sin(j / 7) + 0.4 * math.sin(j / 2.3) for j in range(n15 + 1)]
    c15 = []
    for j in range(n15):
        o, c = px[j], px[j + 1]
        c15.append(Candle(t=j * FIFTEEN_MS, o=o, h=max(o, c) + 0.1, l=min(o, c) - 0.1, c=c, v=1.0))
    c1h = []
    for i in range(0, n15, 4):
        grp = c15[i:i + 4]
        c1h.append(Candle(t=grp[0].t, o=grp[0].o, h=max(b.h for b in grp), l=min(b.l for b in grp),
                          c=grp[-1].c, v=1.0))
    return c15, c1h


def test_backtest_window_returns_a_per_day_shaped_row():
    c15, c1h = _swinging_series()
    p = M.scouting_params(venue_max_leverage=20.0, atr_price_mean=0.6)
    row = M.backtest_window(c15, c1h, p, c15[0].t, c15[-1].t + M.FIFTEEN_MIN_MS, "always_long")
    for key in ("daily_pct", "weekly_pct", "trades", "win_rate", "max_dd_pct", "liquidated"):
        assert key in row


def test_backtest_coin_covers_every_series_and_both_windows_with_halves():
    c15, c1h = _swinging_series(n15=50 * 24 * 4)   # 50 days, enough for both windows
    out = M.backtest_coin(c15, c1h, venue_max_leverage=20.0, windows_days=(30, 50))
    assert set(out["windows"]) == {30, 50}
    for days, window in out["windows"].items():
        assert set(window) == set(M.SERIES_NAMES)
        for series_row in window.values():
            assert "first" in series_row["halves"] and "second" in series_row["halves"]
    assert out["stop_buffer"] == pytest.approx(M.ATR_BUFFER_MULT * M.mean_atr(c1h)[0])


def test_backtest_coin_caps_windows_to_available_history_and_handles_no_data():
    c15, c1h = _swinging_series(n15=5 * 24 * 4)   # 5 days only
    out = M.backtest_coin(c15, c1h, venue_max_leverage=20.0, windows_days=(30, 50))
    assert set(out["windows"]) == {5}             # both requested windows collapse to the 5 available
    assert M.backtest_coin([], [], venue_max_leverage=20.0) == {"windows": {}, "note": "no candle history"}


# ── row composers (pure given already-fetched meta/book/candles) ───────

def test_tradeability_row_computes_slippage_and_the_leverage_gate_from_given_data():
    meta = {"midPx": 100.0, "markPx": 100.0, "maxLeverage": 10.0, "dayNtlVlm": 1_000_000.0,
           "openInterest": 500.0, "funding_hourly": 0.0000125}
    book = {"bids": [(99.0, 1000.0)], "asks": [(101.0, 1000.0)]}
    c1h = _flat_1h(h=100.2, l=99.8)
    row = M.tradeability_row("TEST", meta, book, c1h, [], [], None)
    assert row["venue_max_leverage"] == 10.0 and row["trade_leverage"] == 10.0   # capped under 20x
    assert row["open_interest_usd"] == pytest.approx(50_000.0)
    assert row["funding_annualized_pct"] == pytest.approx(0.0000125 * 24 * 365 * 100)
    assert row["slippage_bps"]["buy_2500"]["filled"]
    assert row["leverage_gate_pass_frac"]["pass_frac"] == 1.0
    assert row["first_candle_1d"] is None and row["first_candle_15m"] is None


def test_behaviour_row_reads_atr_range_persistence_and_session():
    c1h = _trending_1h(days=30, step=1.0)
    row = M.behaviour_row(c1h, [])
    assert row["atr14_pct_mean"] is not None and row["daily_range_pct_median"] is not None
    assert row["trend_persistence_5d_frac"] == pytest.approx(1.0)
    assert row["session_share_london_ny"] is None     # no 15m candles given


# ── market list shape ────────────────────────────────────────────────────

def test_markets_use_the_corrected_venue_names():
    coins = [c for c, _, _ in M.MARKETS]
    # the operator's named candidates, corrected to what the venue actually has
    assert "xyz:SILVER" in coins
    assert "xyz:DIESEL" not in coins and "xyz:HO" in coins
    assert "io:ANT" not in coins and "io:ANTH" in coins
    assert "io:OPENAI" not in coins and "io:OAI" in coins
    # the four reference markets
    for ref in ("xyz:BRENTOIL", "xyz:CL", "xyz:GOLD", "xyz:NATGAS"):
        assert ref in coins
    assert len(coins) == len(set(coins)) == 8
