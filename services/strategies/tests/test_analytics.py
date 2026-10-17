"""Unit tests for services/strategies/analytics.py. Every number here is
checked against a hand-computable case or the literal MCP liquidation
formula — no network, no fixtures beyond the synthetic candles built in
conftest.py's make_candles/make_candles_ohlc.
"""
from __future__ import annotations

import math

import pytest

from contracts.strategies.models import Instrument
from services.strategies import analytics as A
from services.strategies.tests.conftest import flat_meta, make_candles, make_candles_ohlc


# ── quantile ───────────────────────────────────────────────────────────────

def test_quantile_nearest_rank_matches_reference_formula():
    xs = [1, 2, 3, 4, 5]
    assert A.quantile(xs, 0.0) == 1
    assert A.quantile(xs, 0.1) == 1     # int(0.1*5)=0
    assert A.quantile(xs, 0.2) == 2     # int(0.2*5)=1
    assert A.quantile(xs, 0.5) == 3     # int(0.5*5)=2
    assert A.quantile(xs, 0.9) == 5     # int(0.9*5)=4
    assert A.quantile(xs, 0.999) == 5   # int(0.999*5)=4, clamped


def test_quantile_single_element_any_p():
    for p in (0.0, 0.1, 0.5, 0.9, 0.999):
        assert A.quantile([42.0], p) == 42.0


def test_quantile_empty_raises():
    with pytest.raises(ValueError):
        A.quantile([], 0.5)


def test_quantiles_builds_all_five():
    q = A.quantiles([1, 2, 3, 4, 5])
    assert (q.p10, q.p25, q.p50, q.p75, q.p90) == (1, 2, 3, 4, 5)


# ── log returns / vol / atr ────────────────────────────────────────────────

def test_log_returns_hand_computed():
    rets = A.log_returns([100, 110, 99])
    assert rets[0] == pytest.approx(math.log(1.10))
    assert rets[1] == pytest.approx(math.log(99 / 110))


def test_daily_vol_symmetric_series():
    # rets = [+log(1.1), log(100/110)] are exact negatives -> mean 0,
    # pstdev == |log(1.1)| for this 2-point symmetric case.
    v = A.daily_vol([100, 110, 100], 2)
    assert v == pytest.approx(math.log(1.1), rel=1e-9)


def test_daily_vol_flat_series_is_zero():
    assert A.daily_vol([100, 100, 100, 100], 3) == 0.0


def test_daily_vol_insufficient_history_is_none():
    assert A.daily_vol([100, 101], 5) is None


def test_atr14_pct_hand_computed_constant_range():
    # 16 flat-close bars, h=101/l=99 every day (prevclose always 100 too):
    # true range = max(2, 1, 1) = 2 every day -> atr14 = 2 -> pct = 2/100.
    rows = [{"o": 100, "h": 101, "l": 99, "c": 100} for _ in range(16)]
    candles = make_candles_ohlc(rows)
    assert A.atr14_pct(candles) == pytest.approx(0.02)


def test_atr14_pct_insufficient_history_is_none():
    candles = make_candles([100, 101, 99, 100])
    assert A.atr14_pct(candles) is None


# ── liquidation price, cross-checked against the literal MCP formula ──────

def _mcp_formula(entry: float, leverage: float, max_leverage: float, is_long: bool) -> float:
    """Copied verbatim from scripts/pathiel-mcp-server.py:handle_get_liquidation_price
    as an independent oracle — this is NOT analytics.liquidation_price, it's
    the same math typed out a second time."""
    side = 1.0 if is_long else -1.0
    mmr = 1.0 / (2.0 * max_leverage)
    return entry - side * (entry / leverage - mmr * entry) / (1.0 - mmr * side)


@pytest.mark.parametrize("entry,leverage,max_leverage,is_long", [
    (100.0, 10.0, 20.0, True), (100.0, 10.0, 20.0, False),
    (99.339, 5.0, 20.0, True), (99.339, 20.0, 20.0, True),
    (4163.15, 25.0, 25.0, False), (0.5, 3.0, 10.0, True),
])
def test_liquidation_price_matches_mcp_formula(entry, leverage, max_leverage, is_long):
    side = "long" if is_long else "short"
    got = A.liquidation_price(entry, leverage, max_leverage, side)
    want = _mcp_formula(entry, leverage, max_leverage, is_long)
    assert got == pytest.approx(want, rel=1e-12)


def test_liquidation_price_known_value():
    # entry=100, 10x, max_lev=20 -> mmr=0.025, liq = 100 - (10-2.5)/0.975 = 92.3077
    assert A.liquidation_price(100.0, 10.0, 20.0, "long") == pytest.approx(92.30769231, rel=1e-6)
    assert A.liquidation_price(100.0, 10.0, 20.0, "short") == pytest.approx(107.31707317, rel=1e-6)


def test_liquidation_price_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        A.liquidation_price(100.0, 0.0, 20.0, "long")
    with pytest.raises(ValueError):
        A.liquidation_price(100.0, 10.0, 0.0, "long")


# ── verdict rule edges ─────────────────────────────────────────────────────

@pytest.mark.parametrize("p_liq,want", [
    (0.0, "hold"), (0.0999, "hold"),
    (0.10, "dip_only"), (0.3499, "dip_only"),
    (0.35, "intraday_only"), (0.5499, "intraday_only"),
    (0.55, "avoid"), (1.0, "avoid"),
])
def test_verdict_for_rung_edges(p_liq, want):
    assert A.verdict_for_rung(p_liq) == want


# ── instrument snapshot ────────────────────────────────────────────────────

def test_instrument_snapshot_full_history_matches_direct_expressions():
    closes = [100.0 + i for i in range(65)]  # linear ramp, 65 bars
    candles = make_candles(closes, wiggle=0.01)
    instr = Instrument(coin="XYZ", label="XYZ", role="primary")
    snap = A.build_instrument_snapshot(instr, candles, flat_meta(max_leverage=20, funding=1e-5))

    assert snap.coin == "XYZ" and snap.label == "XYZ"
    assert snap.mid == closes[-1]
    assert snap.ret_1d == pytest.approx(closes[-1] / closes[-2] - 1.0)
    assert snap.ret_5d == pytest.approx(closes[-1] / closes[-6] - 1.0)
    assert snap.ret_20d == pytest.approx(closes[-1] / closes[-21] - 1.0)
    assert snap.ret_60d == pytest.approx(closes[-1] / closes[-61] - 1.0)
    assert snap.high_20d == pytest.approx(max(c.h for c in candles[-20:]))
    assert snap.low_20d == pytest.approx(min(c.l for c in candles[-20:]))
    assert snap.funding_hourly == pytest.approx(1e-5)
    assert snap.max_leverage == 20.0
    assert snap.open_interest_usd == 1_000_000.0
    assert snap.volume_24h_usd == 50_000_000.0


def test_instrument_snapshot_minimal_history_fallbacks():
    """With only 3 bars: required fields (ret_1d/5d/20d, high_20d/low_20d)
    fall back instead of raising; genuinely-optional fields stay None."""
    candles = make_candles([100.0, 102.0, 101.0], wiggle=0.0)
    instr = Instrument(coin="XYZ", label="XYZ")
    snap = A.build_instrument_snapshot(instr, candles, {})

    assert snap.ret_1d == pytest.approx(101.0 / 102.0 - 1.0)   # 1 day IS available
    assert snap.ret_5d == 0.0        # not enough history -> fallback, not a crash
    assert snap.ret_20d == 0.0
    assert snap.ret_60d is None       # genuinely optional -> honest None
    assert snap.high_20d == max(c.h for c in candles)   # falls back to full-history extreme
    assert snap.low_20d == min(c.l for c in candles)
    assert snap.high_60d is None
    assert snap.vol_daily_60d is None
    assert snap.funding_hourly is None
    assert snap.max_leverage is None


def test_instrument_snapshot_empty_candles_is_none():
    instr = Instrument(coin="XYZ", label="XYZ")
    assert A.build_instrument_snapshot(instr, [], {}) is None


def test_fnum_handles_missing_and_bad_values():
    assert A._fnum(None) is None
    assert A._fnum("not a number") is None
    assert A._fnum("1.5") == 1.5
    assert A._fnum(3) == 3.0


# ── pair snapshot ──────────────────────────────────────────────────────────

def test_pair_snapshot_spread_and_vol():
    long_closes = [110.0] * 25
    short_closes = [100.0] * 25
    long_c = make_candles(long_closes)
    short_c = make_candles(short_closes)
    pair = A.build_pair_snapshot("Test spread", "LONG", "SHORT", long_c, short_c)
    assert pair.spread_now == pytest.approx(10.0)
    assert pair.spread_mean_20d == pytest.approx(10.0)
    assert pair.pair_vol_daily == pytest.approx(0.0)  # flat ratio every day -> zero vol


def test_pair_snapshot_vol_is_log_ratio_change_not_dollar_spread():
    """Regression for the pair_vol_daily bug: it must be the sd of the daily
    CHANGE in log(long/short) (a relative, dimensionless vol), not the sd of
    the raw dollar spread's day-over-day change — the latter is dominated by
    whichever leg is priced in bigger numbers (gold vs silver, say) and was
    the wrong unit this function originally used. 24 bars (>=20, so
    spread_mean_20d resolves) alternating two long closes against a flat
    short leg; expected is recomputed independently here, not copied from
    analytics.py, so this pins the DEFINITION, not just today's output."""
    import statistics as st
    long_closes = [110.0 if i % 2 == 0 else 121.0 for i in range(24)]
    short_closes = [100.0] * 24
    long_c = make_candles(long_closes)
    short_c = make_candles(short_closes)
    pair = A.build_pair_snapshot("Test spread", "LONG", "SHORT", long_c, short_c)
    assert pair is not None

    log_ratio = [math.log(lg / sh) for lg, sh in zip(long_closes, short_closes)]
    ratio_changes = [log_ratio[i] - log_ratio[i - 1] for i in range(1, len(log_ratio))]
    expected = st.pstdev(ratio_changes)
    assert pair.pair_vol_daily == pytest.approx(expected, rel=1e-9)
    # sanity: nowhere near the (much larger, wrong-unit) dollar-spread-diff scale
    assert pair.pair_vol_daily < 1.0


def test_pair_snapshot_insufficient_history_is_none():
    long_c = make_candles([110.0, 111.0])
    short_c = make_candles([100.0, 101.0])
    assert A.build_pair_snapshot("Test spread", "LONG", "SHORT", long_c, short_c) is None


def test_pair_snapshot_requires_aligned_timestamps():
    import datetime as dt
    long_c = make_candles([110.0] * 25)
    # misaligned (different epoch) legs share no timestamps -> no pairing possible
    short_c = make_candles([100.0] * 25, start=dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc))
    assert A.build_pair_snapshot("Test spread", "LONG", "SHORT", long_c, short_c) is None


# ── forward stats ──────────────────────────────────────────────────────────

def test_forward_stats_insufficient_windows_is_none():
    candles = make_candles([100.0] * 10, wiggle=0.01)
    assert A.build_forward_stats("XYZ", candles, horizon_days=7) is None


def test_forward_stats_basic_counts_and_quantiles():
    # 30 bars, horizon 7 -> 23 windows. Flat series (no wiggle): every
    # window's final return is 0, long_mae/short_mae are 0 too.
    candles = make_candles([100.0] * 30, wiggle=0.0)
    fs = A.build_forward_stats("XYZ", candles, horizon_days=7)
    assert fs.n_windows == 30 - 7
    assert fs.final_ret_mean == pytest.approx(0.0)
    assert fs.final_ret.p50 == pytest.approx(0.0)
    assert fs.long_mae.p50 == pytest.approx(0.0)
    assert fs.short_mae.p50 == pytest.approx(0.0)


def test_forward_stats_conditional_bucket():
    # Flat at 100 for 10 bars, then a +6% jump sustained, then flat again.
    # Any entry day i where closes[i]/closes[i-5]-1 >= 5% should land in the
    # conditional bucket, and its forward return should be ~0 (flat after).
    closes = [100.0] * 10 + [106.0] * 30
    candles = make_candles(closes, wiggle=0.0)
    fs = A.build_forward_stats("XYZ", candles, horizon_days=7)
    assert fs.conditional, "expected at least one conditional bucket"
    cond = fs.conditional[0]
    assert cond.label == A.CONDITIONAL_LABEL
    assert cond.n >= 1
    assert cond.hit_rate_up <= 1.0


# ── leverage ladder ─────────────────────────────────────────────────────────

def test_ladder_leverage_above_max_is_skipped():
    candles = make_candles([100.0] * 30, wiggle=0.01)
    rung = A.simulate_ladder_rung("XYZ", "long", 25.0, 20.0, 1000.0, candles, 7, 0.0)
    assert rung is None


def test_ladder_insufficient_history_is_none():
    candles = make_candles([100.0] * 5, wiggle=0.01)
    rung = A.simulate_ladder_rung("XYZ", "long", 5.0, 20.0, 1000.0, candles, 7, 0.0)
    assert rung is None


def test_ladder_certain_liquidation_long():
    """Every day's low is 25% below the PREVIOUS day's close — deeper than
    5x's liq distance (17.95%) — so day 1 of EVERY possible 7-day window
    breaches liquidation, regardless of which day the window starts on.
    That makes p_liq == 1.0 unambiguous (a single isolated crash bar is NOT
    enough: windows starting before or after it would still survive — the
    first version of this test got exactly that wrong)."""
    rows = [{"o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0}]
    for _ in range(30):
        prev_c = rows[-1]["c"]
        rows.append({"o": prev_c, "h": prev_c, "l": prev_c * 0.75, "c": prev_c * 0.99})
    candles = make_candles_ohlc(rows)
    rung = A.simulate_ladder_rung("XYZ", "long", 5.0, 20.0, 1000.0, candles, 7, 0.0)
    assert rung is not None
    assert rung.p_liq == 1.0
    assert rung.p_stop == 0.0  # liq (checked first) always fires before the stop could
    assert rung.verdict == "avoid"
    assert rung.equity_ret.p50 == pytest.approx(-1.0)  # wiped out every window


def test_ladder_certain_survival_flat_market():
    candles = make_candles([100.0] * 40, wiggle=0.0)
    rung = A.simulate_ladder_rung("XYZ", "long", 5.0, 20.0, 1000.0, candles, 7, 0.0)
    assert rung is not None
    assert rung.p_liq == 0.0
    assert rung.p_stop == 0.0
    # 33 forward windows < MIN_RELIABLE_WINDOWS: a perfect record on thin
    # history drops one level by design.
    assert rung.verdict == "dip_only"
    # flat price, no funding -> only cost is the round-trip taker fee
    assert rung.ev_pct == pytest.approx(-2 * A.TAKER_FEE * 5.0, abs=1e-9)


def test_ladder_flat_market_with_enough_history_is_hold():
    candles = make_candles([100.0] * (A.MIN_RELIABLE_WINDOWS + 20), wiggle=0.0)
    rung = A.simulate_ladder_rung("XYZ", "long", 5.0, 20.0, 1000.0, candles, 7, 0.0)
    assert rung is not None and rung.p_liq == 0.0
    assert rung.verdict == "hold"


def test_ladder_outcomes_partition():
    """p_liq + p_stop + P(rode to the horizon close) must sum to exactly 1 —
    the whole point of running ONE coherent simulation per rung instead of
    splicing a no-stop run's p_liq onto a with-stop run's p_stop (see the
    long comment on simulate_ladder_rung)."""
    import random
    random.seed(7)
    closes = [100.0]
    for _ in range(120):
        closes.append(closes[-1] * (1.0 + random.uniform(-0.04, 0.04)))
    candles = make_candles(closes, wiggle=0.015)
    for leverage in (3.0, 7.0, 15.0):
        rung = A.simulate_ladder_rung("XYZ", "long", leverage, 20.0, 1000.0, candles, 7, 0.0)
        assert rung is not None
        n_windows = len(candles) - 7
        # Reconstruct the "rode to close" count from the rung's own reported
        # rates (p_liq, p_stop are exact fractions of n_windows by construction).
        liq_n = round(rung.p_liq * n_windows)
        stop_n = round(rung.p_stop * n_windows)
        assert 0 <= liq_n + stop_n <= n_windows
        assert (liq_n + stop_n) / n_windows == pytest.approx(rung.p_liq + rung.p_stop)


def test_funding_sign_long_vs_short_fix():
    """Regression for the deliberate fix described in simulate_ladder_rung's
    docstring: with POSITIVE funding_hourly (longs pay), a short's
    fees_funding_pct must be LOWER than a long's at the same leverage — the
    short is partially subsidized by the funding it receives. The reference
    scratch script got this wrong (same sign both sides)."""
    candles = make_candles([100.0] * 40, wiggle=0.01)
    funding_hourly = 2e-5  # longs pay, meaningful size over a week
    long_rung = A.simulate_ladder_rung("XYZ", "long", 10.0, 20.0, 1000.0, candles, 7, funding_hourly)
    short_rung = A.simulate_ladder_rung("XYZ", "short", 10.0, 20.0, 1000.0, candles, 7, funding_hourly)
    assert long_rung.fees_funding_pct > short_rung.fees_funding_pct
    # and the gap is exactly 2x the funding leg (long pays it, short receives it)
    funding_leg = funding_hourly * 24.0 * 7 * 10.0
    assert (long_rung.fees_funding_pct - short_rung.fees_funding_pct) == pytest.approx(2 * funding_leg, rel=1e-9)


def test_cost_frac_matches_taker_plus_signed_funding():
    cost_long = A._cost_frac("long", 10.0, 1e-5, 7)
    cost_short = A._cost_frac("short", 10.0, 1e-5, 7)
    taker = 2.0 * A.TAKER_FEE * 10.0
    funding_leg = 1e-5 * 24.0 * 7 * 10.0
    assert cost_long == pytest.approx(taker + funding_leg)
    assert cost_short == pytest.approx(taker - funding_leg)


# ── build_analytics entrypoint (desk-level, fully injected) ────────────────

def test_build_analytics_end_to_end_with_fakes():
    from contracts.strategies.models import DeskSpec, Instrument as Instr, PairSpec

    spec = DeskSpec(
        id="t1", name="Test", tagline="t",
        instruments=[Instr(coin="A", label="A", role="primary"),
                    Instr(coin="B", label="B", role="secondary")],
        pairs=[PairSpec(label="A-B", long="A", short="B")],
        leverage_ladder=[5, 10], horizon_days=7,
    )
    closes_a = [100.0 + 0.1 * i for i in range(40)]
    closes_b = [50.0 + 0.05 * i for i in range(40)]
    candles = {"A": make_candles(closes_a, wiggle=0.01), "B": make_candles(closes_b, wiggle=0.01)}

    def fetcher(coin, interval, count):
        return candles[coin]

    def meta():
        return {"A": flat_meta(max_leverage=20), "B": flat_meta(max_leverage=10)}

    from contracts.strategies.models import AccountSnapshot

    def account():
        return AccountSnapshot(equity_usd=500.0, source="assumed")

    result = A.build_analytics(spec, candle_fetcher=fetcher, meta_fetcher=meta, account_fetcher=account)
    assert result.desk_id == "t1"
    assert {s.coin for s in result.instruments} == {"A", "B"}
    assert len(result.pairs) == 1
    assert len(result.forward) == 1          # only the primary (A) gets forward stats
    assert result.forward[0].coin == "A"
    levs_a = {r.leverage for r in result.ladder if r.coin == "A"}
    assert levs_a == {5.0, 10.0}
    assert not any(r.coin == "B" for r in result.ladder)   # B is secondary, no ladder
    assert result.account.equity_usd == 500.0


def test_build_analytics_notes_missing_candles():
    from contracts.strategies.models import AccountSnapshot, DeskSpec, Instrument as Instr

    spec = DeskSpec(id="t2", name="Test2", tagline="t",
                    instruments=[Instr(coin="A", label="A", role="primary")])

    def fetcher(coin, interval, count):
        return []

    result = A.build_analytics(spec, candle_fetcher=fetcher, meta_fetcher=lambda: {},
                               account_fetcher=lambda: AccountSnapshot())
    assert result.instruments == []
    assert any("no candle data" in n for n in result.notes)


# ── two-signal verdict (2026-10-17 integrator change) ────────────────────────

def test_touch_probability_known_values():
    # z = 0 -> certain touch; z = 1.959964 -> 5%; huge z -> ~0
    assert A.touch_probability(0.0, 0.02, 7) == pytest.approx(1.0)
    sig, h = 0.01, 4
    d = 1.959964 * sig * 2.0          # sqrt(4) = 2
    assert A.touch_probability(d, sig, h) == pytest.approx(0.05, abs=1e-4)
    assert A.touch_probability(10.0, 0.01, 7) < 1e-12


def test_touch_probability_edges():
    assert A.touch_probability(0.1, 0.0, 7) == 0.0      # no volatility, never moves
    assert A.touch_probability(0.1, 0.02, 0) == 0.0     # no time, never moves
    assert A.touch_probability(0.0, 0.02, 7) == 1.0     # level at the price
    assert A.touch_probability(-0.01, 0.02, 7) == 1.0   # level behind the price


@pytest.mark.parametrize("p_liq,p_touch,n,want", [
    (0.03, None, None, "hold"),            # original single-signal behaviour
    (0.03, 0.05, 500, "hold"),             # both calm
    (0.03, 0.20, 500, "dip_only"),         # vol says worse: worse wins
    (0.40, 0.05, 500, "intraday_only"),    # empirical says worse: worse wins
    (0.03, 0.05, 50, "dip_only"),          # thin sample drops one level
    (0.60, 0.70, 50, "avoid"),             # downgrade caps at avoid
    (0.10, 0.10, 500, "dip_only"),         # boundary reads as the safer bucket
])
def test_verdict_two_signals(p_liq, p_touch, n, want):
    assert A.verdict_for_rung(p_liq, p_touch=p_touch, n_windows=n) == want


def test_sigma_for_verdict_takes_the_larger_vol_and_inflates_it():
    calm = [100.0 * (1.001 ** i) for i in range(60)]
    wild_tail = calm + [calm[-1] * (1.05 if i % 2 else 0.95) for i in range(20)]
    candles = [A.Candle(t=i, o=c, h=c, l=c, c=c, v=1.0) for i, c in enumerate(wild_tail)]
    sig = A._sigma_for_verdict(candles)
    closes = [c.c for c in candles]
    import math as _m, statistics as _st
    rets = [_m.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    assert sig == pytest.approx(max(_st.pstdev(rets), _st.pstdev(rets[-20:])) * A.TAIL_VOL_MULT)
    assert A._sigma_for_verdict(candles[:2]) is None   # unknown, not calm
