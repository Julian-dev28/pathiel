"""Gate tests for `services/strategies/evals/hype_oil_correlation.py`
(docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md). Synthetic candles for
every pure-math unit test, plus one offline smoke test on a committed
real-data fixture slice (services/strategies/tests/fixtures/hype_oil_corr_*)
so the full pipeline is exercised without the network. No account, no
config, no LLM call."""
from __future__ import annotations

import json
import math
import os

import pytest

from pathiel.models.types import Candle
from services.strategies.evals import hype_oil_correlation as C

T0 = 1_780_000_000_000 - (1_780_000_000_000 % C.HOUR_MS)   # an exact UTC hour
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def bar(i: int, c: float, step: int = C.HOUR_MS, o: float = None, h: float = None, l: float = None) -> Candle:
    o = c if o is None else o
    h = max(o, c) if h is None else h
    l = min(o, c) if l is None else l
    return Candle(t=T0 + i * step, o=o, h=h, l=l, c=c, v=1.0)


def series(closes):
    return [bar(i, c) for i, c in enumerate(closes)]


# ── returns and resampling ──────────────────────────────────────────────────

def test_close_returns_is_simple_return_keyed_at_the_closing_time():
    bars = series([100.0, 110.0, 99.0])
    rs = C.close_returns(bars, C.HOUR_MS)
    assert rs == [(bars[1].t + C.HOUR_MS, pytest.approx(0.10)), (bars[2].t + C.HOUR_MS, pytest.approx(-0.1))]


def test_close_returns_skips_a_non_positive_prior_close():
    bars = [bar(0, 0.0), bar(1, 5.0), bar(2, 10.0)]
    rs = C.close_returns(bars, C.HOUR_MS)
    assert len(rs) == 1 and rs[0][1] == pytest.approx(1.0)   # only the 5 -> 10 pair


def test_resample_aggregates_four_1h_bars_into_one_aligned_4h_bucket():
    # 8 hourly bars starting exactly on a 4h UTC boundary
    base = (T0 // (4 * C.HOUR_MS)) * (4 * C.HOUR_MS)
    bars = [Candle(t=base + i * C.HOUR_MS, o=100 + i, h=100 + i + 0.5, l=100 + i - 0.5, c=100 + i, v=1.0)
           for i in range(8)]
    out = C.resample(bars, 4)
    assert len(out) == 2
    assert out[0].t == base and out[1].t == base + 4 * C.HOUR_MS
    assert out[0].o == 100 and out[0].c == 103 and out[0].h == 103.5 and out[0].l == 99.5
    assert out[1].o == 104 and out[1].c == 107


def test_resample_drops_a_partial_trailing_bucket():
    base = (T0 // (4 * C.HOUR_MS)) * (4 * C.HOUR_MS)
    bars = [Candle(t=base + i * C.HOUR_MS, o=1, h=1, l=1, c=1, v=1.0) for i in range(6)]   # 4 + 2 leftover
    out = C.resample(bars, 4)
    assert len(out) == 1


def test_resample_hours_le_1_is_a_no_op_sort():
    bars = [bar(1, 2.0), bar(0, 1.0)]
    assert [b.t for b in C.resample(bars, 1)] == [bars[1].t, bars[0].t]


def test_align_inner_joins_on_exact_timestamp():
    a = [(1, 0.1), (2, 0.2), (3, 0.3)]
    b = [(2, 1.2), (3, 1.3), (4, 1.4)]
    ts, xs, ys = C.align(a, b)
    assert ts == [2, 3] and xs == [0.2, 0.3] and ys == [1.2, 1.3]


# ── correlation math ─────────────────────────────────────────────────────────

def test_pearson_perfect_positive_and_negative():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert C.pearson(xs, xs) == pytest.approx(1.0)
    assert C.pearson(xs, [-x for x in xs]) == pytest.approx(-1.0)


def test_pearson_none_on_zero_variance_or_too_few_points():
    assert C.pearson([1.0], [2.0]) is None
    assert C.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


def test_pearson_known_value():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 1.0, 4.0, 3.0]
    # cov = mean((x-mx)(y-my)); mx=my=2.5 -> deviations x:[-1.5,-.5,.5,1.5] y:[-.5,-1.5,1.5,.5]
    # sxy = 0.75+0.75+0.75+0.75=3.0, sxx=5.0, syy=5.0 -> r=3/5=0.6
    assert C.pearson(xs, ys) == pytest.approx(0.6)


def test_rank_averages_ties():
    assert C.rank([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_matches_pearson_on_already_monotonic_data():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [10.0, 30.0, 20.0, 50.0, 40.0]   # a monotonic-ish but not linear series
    assert C.spearman(xs, ys) == pytest.approx(C.pearson(C.rank(xs), C.rank(ys)))


def test_corr_t_stat_matches_the_closed_form():
    r, n = 0.3, 50
    assert C.corr_t_stat(r, n) == pytest.approx(r * math.sqrt((n - 2) / (1 - r * r)))
    assert C.corr_t_stat(None, 50) is None
    assert C.corr_t_stat(0.9, 2) is None   # n too small


def test_beta_of_is_the_ols_slope():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 4.0, 6.0, 8.0]              # y = 2x exactly
    assert C.beta_of(xs, ys) == pytest.approx(2.0)
    ys2 = [10.0, 8.0, 6.0, 4.0]            # y = -2x + 12
    assert C.beta_of(xs, ys2) == pytest.approx(-2.0)
    assert C.beta_of([1.0], [1.0]) is None


def test_bootstrap_ci_is_deterministic_and_brackets_the_point_estimate_on_clean_linear_data():
    xs = [float(i) for i in range(200)]
    ys = [2.0 * x for x in xs]
    ci1 = C.bootstrap_ci(xs, ys, C.pearson, n_boot=500)
    ci2 = C.bootstrap_ci(xs, ys, C.pearson, n_boot=500)
    assert ci1 == ci2                      # fixed seed: reproducible
    assert ci1["lo"] == pytest.approx(1.0, abs=1e-6) and ci1["hi"] == pytest.approx(1.0, abs=1e-6)


def test_bootstrap_ci_too_few_points_is_a_no_op():
    out = C.bootstrap_ci([1.0, 2.0], [1.0, 2.0], C.pearson)
    assert out == {"lo": None, "hi": None, "n_boot": 0, "n": 2}


# ── rolling correlation ──────────────────────────────────────────────────────

def test_rolling_corr_window_and_step_mechanics():
    n = 50
    ts = list(range(n))
    xs = [float(i) for i in range(n)]
    ys = [float(i) for i in range(n)]      # perfectly correlated throughout
    rows = C.rolling_corr(ts, xs, ys, window_bars=10, step_bars=5)
    assert len(rows) == (n - 10) // 5 + 1
    assert all(r["pearson"] == pytest.approx(1.0) for r in rows)
    assert rows[0]["end_t"] == 9 and rows[1]["end_t"] == 14


# ── lead/lag: the required known-lag recovery ───────────────────────────────

def test_lead_lag_recovers_a_known_positive_lag_oil_leads():
    """y[t] = x[t - 3] (HYPE's return 3 hours from now repeats oil's return
    NOW): oil leads HYPE by 3 hours. The cross-correlation must peak at
    lag=+3 by the module's own sign convention (positive lag = x leads y)."""
    n = 300
    ts = [T0 + i * C.HOUR_MS for i in range(n)]
    xs = [math.sin(i * 0.3) + 0.01 * ((i * 37) % 7) for i in range(n)]   # a wiggly but reproducible series
    lag = 3
    # y repeats x's value from `lag` hours earlier, plus a little independent
    # jitter -- strong but not a mathematically exact (|r|==1) relationship,
    # so corr_t_stat has a real, finite value to assert on.
    ys = [0.0] * n
    for i in range(n):
        if i - lag >= 0:
            ys[i] = xs[i - lag] + 1e-4 * math.cos(i * 1.7)
    rows = C.lead_lag(ts, xs, ys, max_lag_h=10, min_n=20)
    best = C.best_lag(rows)
    assert best["lag_h"] == lag
    assert best["pearson"] == pytest.approx(1.0, abs=1e-4)
    assert best["t_stat"] > 100   # a near-exact relationship: a huge t-stat


def test_lead_lag_recovers_a_known_negative_lag_hype_leads():
    """x[t] = y[t - 4]: HYPE leads oil by 4 hours -> the peak sits at
    lag=-4 (negative lag means y/HYPE leads x/oil)."""
    n = 300
    ts = [T0 + i * C.HOUR_MS for i in range(n)]
    ys = [math.cos(i * 0.21) + 0.01 * ((i * 53) % 5) for i in range(n)]
    lag = 4
    xs = [0.0] * n
    for i in range(n):
        if i - lag >= 0:
            xs[i] = ys[i - lag]
    rows = C.lead_lag(ts, xs, ys, max_lag_h=10, min_n=20)
    best = C.best_lag(rows)
    assert best["lag_h"] == -lag
    assert best["pearson"] == pytest.approx(1.0, abs=1e-9)


def test_lead_lag_is_robust_to_a_gap_in_one_series_timestamp_joined_not_index_joined():
    """Drop one bar out of x's series (so x and y no longer share a common
    list index past that point) and confirm `lead_lag` still recovers the
    true lag -- proof the join is by exact timestamp, not by list
    position."""
    n = 200
    lag = 2
    ts_x = [T0 + i * C.HOUR_MS for i in range(n)]
    xs_full = [math.sin(i * 0.37) for i in range(n)]
    ys_full = [xs_full[i - lag] if i - lag >= 0 else 0.0 for i in range(n)]   # y[i] = x[i - lag]: x leads by `lag`
    y_by_t = {t: ys_full[i] for i, t in enumerate(ts_x)}
    drop_i = 50                              # remove one x observation mid-series; y keeps every bar
    ts2 = [t for i, t in enumerate(ts_x) if i != drop_i]
    xs2 = [x for i, x in enumerate(xs_full) if i != drop_i]
    ys2 = [y_by_t[t] for t in ts2]
    rows = C.lead_lag(ts2, xs2, ys2, max_lag_h=8, min_n=20)
    best = C.best_lag(rows)
    assert best["lag_h"] == lag


def test_lead_lag_t_stat_and_n_shrink_with_lag_magnitude():
    n = 100
    ts = [T0 + i * C.HOUR_MS for i in range(n)]
    xs = [float(i % 10) for i in range(n)]
    ys = [float(i % 10) for i in range(n)]
    rows = {r["lag_h"]: r for r in C.lead_lag(ts, xs, ys, max_lag_h=5, min_n=1)}
    assert rows[0]["n"] == n
    assert rows[5]["n"] == n - 5 and rows[-5]["n"] == n - 5


def test_best_lag_empty_when_nothing_is_readable():
    rows = [{"lag_h": 0, "n": 2, "pearson": None, "spearman": None, "t_stat": None}]
    assert C.best_lag(rows) == {"lag_h": None, "n": 0, "pearson": None, "spearman": None, "t_stat": None}


# ── multiple testing and rolling stability ──────────────────────────────────

def test_normal_cdf_matches_known_z_table_values():
    assert C._normal_cdf(0.0) == pytest.approx(0.5)
    assert C._normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert C._normal_cdf(2.0) == pytest.approx(0.9772, abs=1e-3)


def test_lead_lag_significance_check_flags_chance_level_crossings_as_not_significant():
    """48 lags, exactly 2 cross |t|>2 (chance expects ~2.18 at alpha~0.0455):
    this must NOT pass the multiple-testing check."""
    rows = [{"lag_h": lag, "n": 1000, "pearson": 0.01, "t_stat": 0.3} for lag in range(-24, 25) if lag != 0]
    rows[0]["t_stat"] = 2.1
    rows[1]["t_stat"] = -2.2
    out = C.lead_lag_significance_check(rows, t_threshold=2.0)
    assert out["n_tested"] == 48 and out["n_over_threshold"] == 2
    assert out["passes_multiple_testing"] is False


def test_lead_lag_significance_check_flags_a_real_cluster_as_significant():
    """Every lag crosses the threshold: unambiguously more than chance."""
    rows = [{"lag_h": lag, "n": 1000, "pearson": 0.3, "t_stat": 9.0} for lag in range(-24, 25) if lag != 0]
    out = C.lead_lag_significance_check(rows, t_threshold=2.0)
    assert out["passes_multiple_testing"] is True
    assert out["n_over_threshold"] == 48


def test_rolling_stability_reports_sign_agreement():
    rows = [{"pearson": -0.1}, {"pearson": -0.2}, {"pearson": 0.05}, {"pearson": -0.15}]
    s = C.rolling_stability(rows)
    assert s == {"n": 4, "pos": 1, "neg": 3, "min": -0.2, "max": 0.05, "same_sign_frac": 0.75}


def test_rolling_stability_empty():
    assert C.rolling_stability([]) == {"n": 0, "pos": 0, "neg": 0, "min": None, "max": None, "same_sign_frac": None}


# ── regimes ──────────────────────────────────────────────────────────────────

def test_session_masks_use_fixed_utc_hour_buckets():
    from datetime import datetime, timezone
    asia_t = int(datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc).timestamp() * 1000)     # 03:00 UTC
    us_t = int(datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)      # 15:00 UTC
    other_t = int(datetime(2026, 1, 5, 11, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 11:00 UTC
    assert C.in_session(asia_t, C.ASIA_SESSION_UTC) and not C.in_session(asia_t, C.US_SESSION_UTC)
    assert C.in_session(us_t, C.US_SESSION_UTC) and not C.in_session(us_t, C.ASIA_SESSION_UTC)
    assert not C.in_session(other_t, C.ASIA_SESSION_UTC) and not C.in_session(other_t, C.US_SESSION_UTC)


def test_daily_returns_keyed_by_the_closing_days_ordinal():
    from datetime import datetime, timezone
    d0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    bars = [Candle(t=d0, o=100, h=100, l=100, c=100, v=1),
           Candle(t=d0 + C.DAY_MS, o=100, h=100, l=100, c=105, v=1),
           Candle(t=d0 + 2 * C.DAY_MS, o=105, h=105, l=105, c=94.5, v=1)]
    out = C.daily_returns(bars)
    day1 = datetime(2026, 1, 2, tzinfo=timezone.utc).date().toordinal()
    day2 = datetime(2026, 1, 3, tzinfo=timezone.utc).date().toordinal()
    assert out[day1] == pytest.approx(0.05)
    assert out[day2] == pytest.approx(-0.10)


def test_regime_mask_oil_shock_and_hype_drawdown():
    oil_daily = {100: 0.04, 101: -0.01}     # day 100: a +4% shock day
    hype_daily = {100: -0.04, 101: 0.01}    # day 100: a drawdown day
    ts = [None, None]   # placeholder, overwritten below with real day-ordinal timestamps
    import datetime as dt
    d100 = dt.date.fromordinal(100)
    d101 = dt.date.fromordinal(101)
    from datetime import timezone
    t100 = int(dt.datetime(d100.year, d100.month, d100.day, 12, tzinfo=timezone.utc).timestamp() * 1000)
    t101 = int(dt.datetime(d101.year, d101.month, d101.day, 12, tzinfo=timezone.utc).timestamp() * 1000)
    ts = [t100, t101]
    assert C.regime_mask(ts, "oil_shock", oil_daily, hype_daily) == [True, False]
    assert C.regime_mask(ts, "hype_drawdown", oil_daily, hype_daily) == [True, False]


def test_regime_mask_weekend_and_weekday_are_complements():
    ts = [T0 + i * C.HOUR_MS for i in range(200)]
    wk = C.regime_mask(ts, "weekend", {}, {})
    wd = C.regime_mask(ts, "weekday", {}, {})
    assert wk == [not x for x in wd]


def test_regime_correlations_reports_none_below_min_n():
    ts = [T0 + i * C.HOUR_MS for i in range(5)]
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = C.regime_correlations(ts, xs, ys, {}, {}, min_n=10)
    assert set(out) == set(C.REGIMES)
    assert all(r["pearson"] is None and r["spearman"] is None for r in out.values())   # every regime under min_n=10


# ── tail co-movement ─────────────────────────────────────────────────────────

def test_tail_comovement_on_a_perfectly_negatively_correlated_series():
    xs = [float(i) for i in range(-50, 50)]
    ys = [-x for x in xs]
    tc = C.tail_comovement(xs, ys, tail_pct=0.1)
    assert tc["k"] == 10
    # oil's worst 10 hours (most negative x) pair with ys' LARGEST values (positive): never down
    assert tc["worst_oil_hours_hype_also_down_pct"] == 0.0
    assert tc["best_oil_hours_hype_also_up_pct"] == 0.0


def test_tail_comovement_empty_series():
    assert C.tail_comovement([], [])["n"] == 0


# ── offline smoke test on a committed real-data fixture ─────────────────────

def _load_fixture_candles(name: str):
    with open(os.path.join(FIXTURES, name)) as fh:
        rows = json.load(fh)
    return [Candle(**r) for r in rows]


def test_full_pipeline_runs_clean_on_the_committed_real_data_fixture():
    """~1000 hours (41 days) of real, aligned HYPE/BRENTOIL 1h candles,
    committed so this test never touches the network. Not a claim about the
    live market (too short and too old to be "the" finding -- see
    docs/strategy/HYPE_OIL_CORRELATION_2026-11-20.md for the full 208-day
    run) -- just proof the whole pipeline produces sane, bounded, stable
    output end to end."""
    oil_bars = _load_fixture_candles("hype_oil_corr_brentoil_1h_slice.json")
    hype_bars = _load_fixture_candles("hype_oil_corr_hype_1h_slice.json")
    rs = C.build_return_set(oil_bars, hype_bars, C.HOUR_MS)
    assert rs.ts and len(rs.ts) == len(rs.oil) == len(rs.hype)
    r = C.pearson(rs.oil, rs.hype)
    assert r is not None and -1.0 <= r <= 1.0
    spear = C.spearman(rs.oil, rs.hype)
    assert spear is not None and -1.0 <= spear <= 1.0
    rows = C.lead_lag(rs.ts, rs.oil, rs.hype, max_lag_h=12)
    assert len(rows) == 25
    mt = C.lead_lag_significance_check(rows)
    assert mt["n_tested"] == 24
    beta = C.beta_of(rs.oil, rs.hype)
    assert beta is not None and math.isfinite(beta)
    oil_1d = _load_fixture_candles("hype_oil_corr_brentoil_1d_slice.json")
    hype_1d = _load_fixture_candles("hype_oil_corr_hype_1d_slice.json")
    regimes = C.regime_correlations(rs.ts, rs.oil, rs.hype, C.daily_returns(oil_1d), C.daily_returns(hype_1d))
    assert set(regimes) == set(C.REGIMES)
    tc = C.tail_comovement(rs.oil, rs.hype)
    assert tc["n"] == len(rs.ts)


def test_run_end_to_end_on_the_fixture_snapshot_dir(tmp_path):
    """`run()` itself, pointed at a tiny snapshot dir built from the
    committed fixture files (same on-disk layout `snapshot.load_snapshot`
    expects) -- the exact CLI path the task's `--snapshot-dir` flag drives,
    minus the network fetch."""
    import urllib.parse
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    pairs = [(C.OIL, "hype_oil_corr_brentoil_1h_slice.json", "1h"),
            (C.HYPE, "hype_oil_corr_hype_1h_slice.json", "1h"),
            (C.OIL, "hype_oil_corr_brentoil_1d_slice.json", "1d"),
            (C.HYPE, "hype_oil_corr_hype_1d_slice.json", "1d")]
    for coin, fname, interval in pairs:
        with open(os.path.join(FIXTURES, fname)) as fh:
            rows = json.load(fh)
        out_name = f"{urllib.parse.quote(coin, safe='')}__candles_{interval}.json"
        with open(snap_dir / out_name, "w") as fh:
            json.dump(rows, fh)
    report = C.run(str(snap_dir), out_dir=str(tmp_path / "out"))
    assert report["window"]["n_1h"] > 900
    assert os.path.exists(tmp_path / "out" / "report.json")
    with open(tmp_path / "out" / "report.json") as fh:
        on_disk = json.load(fh)
    assert on_disk["window"] == report["window"]
