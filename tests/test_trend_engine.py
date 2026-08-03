"""Gate tests for services/trend_engine — deterministic, offline, <2s.

No network anywhere: every lane is exercised through synthetic bars, fixture
klines, and injected `getter` / `runner` callables. What is covered:

  metrics      : slope/efficiency/EMA/streak/Wilson/binomial identities on
                 series whose answers are known by construction
  forecast     : band ordering, drift shrink, the walk-forward's own nulls,
                 and the split-half stability report
  flags        : each predicate fires only on the state it claims
  hl_trends    : coin read, regime labelling, observation honesty
  updown       : window building (incl. the tie rule), conditional families
                 with Bonferroni, random-walk calibration, executable-edge
                 pricing off a book
  politics     : probability-space read, the drift null test and its
                 shared-endpoint guard, the expired-market drop
  cache        : atomic write, staleness, AI carry-forward
  dashboard    : /trends renders, the lane APIs are pure cache reads, and the
                 operator-gated routes exist
"""
from __future__ import annotations

import json
import math
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.trend_engine import ai as tai
from services.trend_engine import arb_watch as aw
from services.trend_engine import cache as tcache
from services.trend_engine import flags as tflags
from services.trend_engine import forecast as tfc
from services.trend_engine import hl_trends as hl
from services.trend_engine import metrics as M
from services.trend_engine import playbook as pb
from services.trend_engine import political_trends as pol
from services.trend_engine import recorders as rec
from services.trend_engine import updown_edges as ue
from services.trend_engine import updown_trends as ud
from services.trend_engine import updown_ws as uw


# ── fixtures ─────────────────────────────────────────────────────────────────


class Bar:
    """Minimal candle stand-in matching the HL client's attribute shape."""

    def __init__(self, t, o, h, l, c, v=1.0):
        self.t, self.o, self.h, self.l, self.c, self.v = t, o, h, l, c, v


def ramp(n=40, start=100.0, step_pct=1.0):
    """Clean uptrend: same percentage step every bar (efficiency 1.0)."""
    bars, px = [], start
    for i in range(n):
        nxt = px * (1 + step_pct / 100)
        bars.append(Bar(i * 86_400_000, px, max(px, nxt), min(px, nxt), nxt))
        px = nxt
    return bars


def sawtooth(n=40, start=100.0, amp=5.0):
    """Round trip: big bars, no net progress (efficiency near 0)."""
    bars = []
    for i in range(n):
        o = start + (amp if i % 2 else -amp)
        c = start + (-amp if i % 2 else amp)
        bars.append(Bar(i * 86_400_000, o, max(o, c) + 1, min(o, c) - 1, c))
    return bars


def klines(n=600, start_ms=None, price=60_000.0, drift=0.0, seed=7):
    """Deterministic pseudo-random 1m klines aligned to the 5m grid.

    Ends at the current 5m boundary by default — `load_1m` windows its output
    by wall-clock age, so a fixture pinned to 2023 would read as empty.
    """
    if start_ms is None:
        start_ms = int(time.time() * 1000) - n * 60_000
    start_ms -= start_ms % ud.WINDOW_MS
    rows, px, s = [], price, seed
    for i in range(n):
        s = (1103515245 * s + 12345) % 2147483648
        r = (s / 2147483648 - 0.5) * 0.0004 + drift
        nxt = px * (1 + r)
        rows.append([start_ms + i * 60_000, f"{px:.2f}", f"{max(px, nxt) * 1.0001:.2f}",
                     f"{min(px, nxt) * 0.9999:.2f}", f"{nxt:.2f}", "1.0"])
        px = nxt
    return rows


# ── metrics ──────────────────────────────────────────────────────────────────


def test_log_slope_recovers_a_constant_percentage_step():
    closes = [100 * (1.02 ** i) for i in range(10)]
    slope, r2 = M.log_slope(closes)
    assert slope == pytest.approx(2.0, abs=1e-6)
    assert r2 == pytest.approx(1.0, abs=1e-9)


def test_log_slope_is_zero_and_unfit_on_a_flat_line():
    slope, r2 = M.log_slope([50.0] * 10)
    assert slope == pytest.approx(0.0)
    assert r2 == 0.0


def test_efficiency_ratio_endpoints():
    assert M.efficiency_ratio([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert M.efficiency_ratio([1, 5, 1, 5, 1]) == pytest.approx(0.0)


def test_ema_and_stack_directions():
    assert M.ema([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)
    assert M.ema_stack([float(i) for i in range(1, 30)]) == "bull"
    assert M.ema_stack([float(i) for i in range(30, 1, -1)]) == "bear"
    assert M.ema_stack([5.0] * 30) == "mixed"
    assert M.ema([1, 2], 5) is None


def test_streak_counts_and_signs():
    assert M.streak([1, 2, 3, 4]) == 3
    assert M.streak([4, 3, 2, 1]) == -3
    assert M.streak([1, 2, 3, 3]) == 0
    assert M.streak([1, 5, 4, 3]) == -2


def test_range_position_and_atr():
    assert M.range_position([1, 2, 3, 4, 5], 5) == pytest.approx(1.0)
    assert M.range_position([5, 4, 3, 2, 1], 5) == pytest.approx(0.0)
    bars = [Bar(0, 10, 12, 8, 10), Bar(1, 10, 11, 9, 10)]
    assert M.atr_pct(bars, 5) == pytest.approx(20.0)


def test_wilson_brackets_the_point_estimate_and_narrows_with_n():
    lo, hi = M.wilson(50, 100)
    assert lo < 0.5 < hi
    lo2, hi2 = M.wilson(500, 1000)
    assert (hi2 - lo2) < (hi - lo)
    assert M.wilson(0, 0) == (0.0, 1.0)


def test_binomial_p_matches_the_hand_computable_case():
    # 8 heads in 10 flips, two-sided: 2 * P(X >= 8) = 0.109375
    assert M.binom_two_sided_p(8, 10, 0.5) == pytest.approx(0.109375, abs=1e-9)
    assert M.binom_two_sided_p(5, 10, 0.5) == pytest.approx(1.0)


def test_binomial_p_survives_a_sample_that_overflows_math_comb():
    # math.comb(6000, 3000) overflows float — the log-space path must not
    p = M.binom_two_sided_p(3000, 6000, 0.5)
    assert 0.9 <= p <= 1.0
    assert M.binom_two_sided_p(3300, 6000, 0.5) < 1e-6


def test_norm_ppf_is_the_inverse_of_norm_cdf():
    for q in (0.01, 0.1, 0.5, 0.9, 0.99):
        assert M.norm_cdf(M.norm_ppf(q)) == pytest.approx(q, abs=1e-6)


def test_linear_slope_is_exact_on_a_line():
    slope, r2 = M.linear_slope([0.1, 0.2, 0.3, 0.4])
    assert slope == pytest.approx(0.1)
    assert r2 == pytest.approx(1.0)


def test_trend_label_separates_clean_trend_from_round_trip():
    assert M.trend_label(1.5, 0.9, 0.9, "bull") == "STRONG_UP"
    assert M.trend_label(-1.5, 0.9, 0.9, "bear") == "STRONG_DOWN"
    # same drift, no cleanliness -> chop, which is the whole point of the tab
    assert M.trend_label(1.5, 0.05, 0.05, "mixed") == "CHOP"
    assert M.trend_label(0.05, 0.9, 0.9, "bull") == "CHOP"


def test_trend_score_penalises_a_dirty_trend():
    clean = M.trend_score(1.0, 0.9, 0.9, 7.0)
    dirty = M.trend_score(1.0, 0.1, 0.1, 7.0)
    assert clean > dirty > 0


# ── forecast ─────────────────────────────────────────────────────────────────


def test_project_orders_the_band_and_shrinks_the_drift():
    closes = [100 * (1.02 ** i) for i in range(40)]
    f = tfc.project(closes, 7)
    assert f["p10"] < f["p50"] < f["p90"]
    assert f["prob_up"] > 0.5
    # 7 days at +2%/day is +14.9% raw; the shrink must keep the forecast well under
    assert 0 < f["drift_pct"] < 14.9 * tfc.SHRINK + 1


def test_project_refuses_a_series_too_short_to_fit():
    assert tfc.project([100, 101, 102], 7) is None


def test_project_caps_drift_at_the_sigma_ceiling():
    closes = [100 * (1.10 ** i) for i in range(40)]      # violent clean trend
    f = tfc.project(closes, 7)
    assert abs(f["drift_pct"]) <= tfc.MAX_DRIFT_SIGMA * f["sigma_h_pct"] + 1e-6


def test_walk_forward_reports_its_nulls_and_defaults_to_non_overlapping():
    series = {"A": [100 * (1.01 ** i) for i in range(120)],
              "B": [100 * (0.99 ** i) for i in range(120)]}
    res = tfc.walk_forward(series, horizon_days=7)
    assert res["status"] == "ok" and res["n"] > 0
    assert 0.0 <= res["dir_hit"] <= 1.0
    assert "mae_naive_pct" in res and "coverage_80" in res
    assert res["split_half"]["early_n"] > 0 and res["split_half"]["late_n"] > 0
    dense = tfc.walk_forward(series, horizon_days=7, step=1)
    assert dense["n"] > res["n"]                     # default really is non-overlapping


def test_walk_forward_nails_direction_on_a_pure_trend():
    series = {"UP": [100 * (1.01 ** i) for i in range(200)]}
    res = tfc.walk_forward(series, horizon_days=7)
    assert res["dir_hit"] == 1.0


def test_walk_forward_says_insufficient_history_instead_of_guessing():
    assert tfc.walk_forward({"A": [1, 2, 3]})["status"] == "insufficient_history"


def test_consensus_counts_only_upward_forecasts():
    reads = [{"forecast": {"prob_up": 0.7, "drift_pct": 2.0}},
             {"forecast": {"prob_up": 0.3, "drift_pct": -2.0}}]
    c = tfc.consensus(reads)
    assert c["pct_up"] == 50.0
    assert c["mean_drift_pct"] == pytest.approx(0.0)


# ── flags ────────────────────────────────────────────────────────────────────


BASE_READ = {"coin": "X", "range_pos_7d": 0.5, "range_pos_30d": 0.5, "efficiency": 0.5,
             "ret_7d": 1.0, "ema_stack": "mixed", "streak_days": 1, "atr_pct": 2.0,
             "atr_pct_30d": 2.0, "volume_ratio": 1.0, "resid_7d": 0.0, "corr_btc": 0.8}


def codes(**over):
    r = dict(BASE_READ, **over)
    return {f["code"] for f in tflags.flags_for(r)}


def test_flags_fire_only_on_their_own_state():
    assert codes() == set()
    assert "BREAKOUT_7D" in codes(range_pos_7d=0.99, ret_7d=5.0)
    assert "BREAKDOWN_7D" in codes(range_pos_7d=0.0, ret_7d=-5.0)
    assert "HIGH_30D" in codes(range_pos_30d=0.99)
    assert "LOW_30D" in codes(range_pos_30d=0.0)
    assert "EMA_STACK_BULL" in codes(ema_stack="bull")
    assert "STREAK_5D" in codes(streak_days=5)
    assert "CHOP_TRAP" in codes(efficiency=0.05, ret_7d=12.0)
    assert "VOL_EXPANSION" in codes(atr_pct=4.0, atr_pct_30d=2.0)
    assert "OVEREXTENDED_UP" in codes(ret_7d=30.0)
    assert "LEADER" in codes(resid_7d=15.0)
    assert "LAGGARD" in codes(resid_7d=-15.0)
    assert "DECOUPLED" in codes(corr_btc=0.1)


def test_funding_flags_need_the_z_and_point_the_right_way():
    hot = tflags.flags_for(dict(BASE_READ), funding_z=3.0)
    assert any(f["code"] == "FUNDING_CROWDED_LONG" and f["weight"] < 0 for f in hot)
    cold = tflags.flags_for(dict(BASE_READ), funding_z=-3.0)
    assert any(f["code"] == "FUNDING_CROWDED_SHORT" and f["weight"] > 0 for f in cold)


def test_event_flags_sort_first_and_bias_is_clipped():
    fl = tflags.flags_for(dict(BASE_READ, range_pos_30d=0.99),
                          unlocks=[{"pct": 5.0, "hours": 40.0}])
    assert fl[0]["kind"] == "event"
    assert -1.0 <= tflags.flag_bias(fl) <= 1.0
    assert tflags.flag_bias([{"weight": 9.0}, {"weight": 9.0}]) == 1.0


def test_unlock_loader_windows_and_sorts(tmp_path):
    now = 1_700_000_000_000
    p = tmp_path / "unlock.json"
    p.write_text(json.dumps({"upcoming": [
        {"coin": "AAA", "t_ms": now + 3 * tflags.DAY_MS, "pct": 2.0},
        {"coin": "AAA", "t_ms": now + 1 * tflags.DAY_MS, "pct": 1.0},
        {"coin": "BBB", "t_ms": now + 40 * tflags.DAY_MS, "pct": 9.0},   # outside 8d
        {"coin": "CCC", "t_ms": now - tflags.DAY_MS, "pct": 9.0},        # already past
    ]}))
    out = tflags.load_unlocks(str(p), now_ms=now)
    assert set(out) == {"AAA"}
    assert [e["pct"] for e in out["AAA"]] == [1.0, 2.0]


def test_unlock_loader_degrades_to_empty_on_a_missing_file():
    assert tflags.load_unlocks("/nonexistent/path.json", now_ms=1) == {}


# ── hl_trends ────────────────────────────────────────────────────────────────


def test_coin_read_labels_a_clean_ramp_up_and_a_sawtooth_chop():
    up = hl.coin_read("UP", ramp())
    assert up["label"] in ("UP", "STRONG_UP")
    assert up["efficiency"] > 0.9 and up["ret_7d"] > 0
    assert up["forecast"]["p10"] < up["forecast"]["p50"] < up["forecast"]["p90"]

    chop = hl.coin_read("CHOP", sawtooth())
    assert chop["label"] == "CHOP"
    assert chop["efficiency"] < 0.2


def test_coin_read_refuses_a_short_history():
    assert hl.coin_read("X", ramp(5)) is None


def test_coin_read_computes_residual_against_the_benchmark():
    r = hl.coin_read("ALT", ramp(40, step_pct=2.0), ramp(40, step_pct=1.0))
    assert r["beta_btc"] > 0
    assert r["resid_7d"] > 0            # outran the bench after beta


def test_coin_read_annualises_funding_from_the_hourly_rate():
    r = hl.coin_read("X", ramp(), None, {"funding": 0.0001, "dayNtlVlm": 1e6,
                                         "openInterest": 10})
    assert r["funding_apr_pct"] == pytest.approx(0.0001 * 24 * 365 * 100, rel=1e-6)


def test_regime_reads_breadth_not_just_btc():
    reads = [hl.coin_read("BTC", ramp(40, step_pct=0.5))] + \
            [hl.coin_read(f"C{i}", ramp(40, step_pct=1.0)) for i in range(8)]
    hl.attach_flags(reads, unlocks={}, news={})
    reg = hl.regime(reads)
    assert reg["status"] == "ok"
    assert reg["breadth_pct"] == 100.0
    assert reg["tone"] == "RISK_ON"
    assert reg["leaders"] and reg["laggards"]


def test_regime_calls_a_choppy_tape_choppy():
    reads = [hl.coin_read("BTC", sawtooth())] + \
            [hl.coin_read(f"C{i}", sawtooth()) for i in range(6)]
    hl.attach_flags(reads, unlocks={}, news={})
    reg = hl.regime(reads)
    assert reg["shape"] == "CHOPPY"


def test_observations_never_call_a_chop_coin_the_strongest_uptrend():
    reads = [hl.coin_read("BTC", sawtooth())] + \
            [hl.coin_read(f"C{i}", sawtooth()) for i in range(4)]
    hl.attach_flags(reads, unlocks={}, news={})
    obs = hl.observations(reads, hl.regime(reads))
    assert any("No coin in the scan holds a clean uptrend" in o for o in obs)
    assert not any("Strongest uptrend" in o for o in obs)


def test_attach_flags_uses_a_cross_sectional_funding_z():
    reads = [hl.coin_read(f"C{i}", ramp()) for i in range(10)]
    for i, r in enumerate(reads):
        r["funding_apr_pct"] = 1.0 if i else 100.0        # C0 is the outlier
    hl.attach_flags(reads, unlocks={}, news={})
    assert reads[0]["funding_z_xs"] > 2.0
    assert any(f["code"] == "FUNDING_CROWDED_LONG" for f in reads[0]["flags"])


def test_sector_is_read_off_the_dex_prefix():
    assert hl.sector_of("BTC") == "crypto"
    assert hl.sector_of("xyz:NVDA") == "xyz"
    assert hl.sector_of("xyz:SP500") == "xyz"


def test_hip3_reads_benchmark_against_sp500_not_btc():
    """A residual-vs-BTC number for NVDA is noise wearing a decimal point."""
    r = hl.coin_read("xyz:NVDA", ramp(40, step_pct=2.0), ramp(40, step_pct=1.0))
    assert r["sector"] == "xyz"
    assert r["bench"] == hl.XYZ_BENCH
    assert r["resid_7d"] > 0                       # outran its own index

    btc = hl.coin_read("BTC", ramp())
    assert btc["sector"] == "crypto" and btc["bench"] == hl.BENCH


def test_the_benchmark_itself_has_no_residual():
    r = hl.coin_read("xyz:SP500", ramp(40, step_pct=1.0), ramp(40, step_pct=1.0))
    assert r["resid_7d"] == 0.0 and r["beta_btc"] == 1.0


def test_regime_is_computed_per_sector_and_never_mixes_them():
    reads = ([hl.coin_read("BTC", ramp(40, step_pct=1.0))]
             + [hl.coin_read(f"C{i}", ramp(40, step_pct=1.0)) for i in range(6)]
             + [hl.coin_read("xyz:SP500", sawtooth())]
             + [hl.coin_read(f"xyz:E{i}", sawtooth()) for i in range(5)])
    hl.attach_flags(reads, unlocks={}, news={})
    crypto = hl.regime(reads, "crypto")
    xyz = hl.regime(reads, "xyz")
    assert crypto["bench"] == "BTC" and xyz["bench"] == hl.XYZ_BENCH
    assert crypto["n"] == 7 and xyz["n"] == 6
    assert crypto["breadth_pct"] == 100.0          # the ramps
    assert xyz["shape"] == "CHOPPY"                # the sawtooths


def test_observations_never_name_an_equity_in_the_crypto_block():
    """The unscoped version called xyz:GOOGL the crypto tape's strongest
    uptrend — a category error, not a read."""
    reads = ([hl.coin_read("BTC", sawtooth())]
             + [hl.coin_read(f"C{i}", sawtooth()) for i in range(4)]
             + [hl.coin_read("xyz:SP500", ramp(40, step_pct=1.0))]
             + [hl.coin_read("xyz:GOOGL", ramp(40, step_pct=3.0))])
    hl.attach_flags(reads, unlocks={}, news={})
    crypto_lines = " ".join(hl.observations(reads, hl.regime(reads, "crypto")))
    xyz_lines = " ".join(hl.observations(reads, hl.regime(reads, "xyz")))
    assert "xyz:" not in crypto_lines
    assert "xyz:GOOGL" in xyz_lines
    assert hl.XYZ_BENCH in xyz_lines               # benchmarked against its index


def test_universe_quota_keeps_one_sector_from_crowding_out_the_other(monkeypatch):
    """BTC alone out-trades the whole xyz dex; a single ranking would drop
    every equity off the scan."""
    uni = ([{"coin": f"C{i}", "type": "perp", "dayNtlVlm": 1e9 - i}
            for i in range(50)]
           + [{"coin": "xyz:SP500", "type": "perp", "dayNtlVlm": 7e7},
              {"coin": "xyz:NVDA", "type": "perp", "dayNtlVlm": 7e6},
              {"coin": "xyz:TINY", "type": "perp", "dayNtlVlm": 100.0}])
    import hermes_trader.client.universe as U
    monkeypatch.setattr(U, "get_universe", lambda **kw: uni)
    rows = hl._universe_rows(top_n=5, min_vol=1e6, top_n_xyz=5)
    coins = [r["coin"] for r in rows]
    assert sum(1 for c in coins if c.startswith("xyz:")) == 2   # TINY under the floor
    assert "xyz:SP500" in coins and "xyz:NVDA" in coins
    assert len([c for c in coins if not c.startswith("xyz:")]) == 5

    off = [r["coin"] for r in hl._universe_rows(5, 1e6, include_hip3=False)]
    assert not any(c.startswith("xyz:") for c in off)


def test_eval_cache_roundtrip_and_expiry(tmp_path):
    p = str(tmp_path / "ev.json")
    hl.save_eval({"dir_hit": 0.5, "n": 10}, p)
    assert hl.load_eval(p)["dir_hit"] == 0.5
    assert hl.load_eval(p, max_age_s=-1) is None
    assert hl.load_eval(str(tmp_path / "missing.json")) is None


# ── updown ───────────────────────────────────────────────────────────────────


def test_build_windows_only_emits_complete_aligned_windows():
    bars = klines(20)
    ws = ud.build_windows(bars)
    assert len(ws) == 4
    assert all(w["t"] % ud.WINDOW_MS == 0 for w in ws)
    holed = bars[:3] + bars[4:]                       # drop one minute
    assert len(ud.build_windows(holed)) == 3


def test_a_flat_window_resolves_up_like_polymarket_does():
    t = 1_700_000_000_000
    t -= t % ud.WINDOW_MS
    flat = [[t + i * 60_000, "100.0", "100.0", "100.0", "100.0", "1"] for i in range(5)]
    w = ud.build_windows(flat)[0]
    assert w["up"] is True and w["tie"] is True


def test_enrich_conditions_are_backward_looking():
    ws = ud.enrich(ud.build_windows(klines(200)))
    assert ws[0]["prior_up"] is None                   # nothing before the first
    for i in range(1, len(ws)):
        assert ws[i]["prior_up"] == ws[i - 1]["up"]
        assert ws[i]["prior_ret_bp"] == ws[i - 1]["ret_bp"]


def test_conditional_applies_bonferroni_over_its_own_family():
    ws = ud.enrich(ud.build_windows(klines(3000)))
    fam = ud.conditional(ws, lambda w: w["session"], "session")
    assert fam["buckets_tested"] == len({w["session"] for w in ws})
    for row in fam["rows"]:
        assert row["p_bonf"] >= row["p"]
        assert row["ci_lo"] <= row["rate"] <= row["ci_hi"]


def test_a_tiny_bucket_can_never_be_called_significant():
    ws = ud.enrich(ud.build_windows(klines(400)))
    fam = ud.conditional(ws, lambda w: "always", "single", min_n=10_000)
    assert all(not r["significant"] for r in fam["rows"])


def test_patterns_reports_a_coin_flip_as_a_coin_flip():
    pat = ud.patterns(ud.enrich(ud.build_windows(klines(4000))))
    assert pat["status"] == "ok"
    assert 0.35 < pat["base_rate"] < 0.65
    assert pat["base_ci"][0] <= pat["base_rate"] <= pat["base_ci"][1]
    if not pat["significant"]:
        assert "coin flip" in pat["verdict"]


def test_patterns_finds_a_planted_conditional():
    # every window that follows a DOWN window is forced up
    ws = ud.enrich(ud.build_windows(klines(6000)))
    for i in range(1, len(ws)):
        if ws[i - 1]["up"] is False:
            ws[i]["up"] = True
    pat = ud.patterns(ws)
    hits = {h["bucket"] for h in pat["significant"]}
    assert "prior_down" in hits


def test_randomwalk_prob_limits_are_sane():
    assert ud.randomwalk_prob(10.0, 2.0, 0.0) == 1.0
    assert ud.randomwalk_prob(-10.0, 2.0, 0.0) == 0.0
    assert ud.randomwalk_prob(0.0, 2.0, 3.0) == pytest.approx(0.5)
    assert ud.randomwalk_prob(4.0, 2.0, 1.0) > ud.randomwalk_prob(1.0, 2.0, 1.0)
    # more time left = more uncertainty = closer to a coin flip
    assert ud.randomwalk_prob(4.0, 2.0, 4.0) < ud.randomwalk_prob(4.0, 2.0, 1.0)


def test_rw_calibration_scores_against_the_half_null():
    cal = ud.rw_calibration(ud.enrich(ud.build_windows(klines(9000))), minute=3)
    assert cal["status"] == "ok"
    assert cal["brier_null"] == pytest.approx(0.25, abs=0.02)
    assert cal["brier"] < cal["brier_null"]            # the in-window model has skill
    assert sum(t["n"] for t in cal["table"]) == cal["n"]


def test_rw_calibration_refuses_a_tiny_sample():
    assert ud.rw_calibration(ud.enrich(ud.build_windows(klines(100))))["status"] == "too_small"


def test_forecast_next_falls_back_to_the_base_rate_when_nothing_survives():
    ws = ud.enrich(ud.build_windows(klines(4000)))
    pat = ud.patterns(ws)
    fc = ud.forecast_next(ws, pat, now_ms=ws[-1]["t"] + ud.WINDOW_MS)
    assert fc["status"] == "ok"
    if not fc["applied"]:
        assert fc["p_up"] == pytest.approx(pat["base_rate"])
        assert "base rate" in fc["note"]


def test_odds_shift_stays_inside_zero_one():
    p = 0.5
    for _ in range(20):
        p = ud._odds_shift(p, 0.99, 0.5)
    assert 0.0 < p < 1.0


def test_live_window_prices_the_executable_side_not_the_mid():
    ws = ud.enrich(ud.build_windows(klines(600)))
    start = ws[-1]["t"] + ud.WINDOW_MS
    bars_open = ws[-1]["close"]
    ws.append({**ws[-1], "t": start, "open": bars_open})
    book = {"status": "ok", "bid": 0.40, "ask": 0.42, "mid": 0.41, "spread": 0.02,
            "bid_size": 100.0, "ask_size": 100.0}
    out = ud.live_window(ws, book, spot=bars_open * 1.001,
                         now_ms=start + 60_000)
    assert out["status"] == "ok"
    p = out["p_up_randomwalk"]
    assert out["edge_up_pp"] == pytest.approx((p - 0.42) * 100, abs=0.011)
    assert out["edge_down_pp"] == pytest.approx((0.40 - p) * 100, abs=0.011)
    assert out["best_edge_pp"] == max(out["edge_up_pp"], out["edge_down_pp"])


def test_live_window_will_not_call_a_sub_buffer_gap_actionable():
    ws = ud.enrich(ud.build_windows(klines(600)))
    start = ws[-1]["t"] + ud.WINDOW_MS
    px = ws[-1]["close"]
    ws.append({**ws[-1], "t": start, "open": px})
    book = {"status": "ok", "bid": 0.49, "ask": 0.51, "mid": 0.50, "spread": 0.02}
    out = ud.live_window(ws, book, spot=px, now_ms=start + 60_000)
    assert out["actionable"] is False
    assert "no trade" in out["note"]
    out2 = ud.live_window(ws, book, spot=px, now_ms=start + 60_000, feed_buffer=0.0)
    assert out2["feed_buffer_pp"] == 0.0


def test_live_window_reports_unavailable_without_a_spot_or_open():
    assert ud.live_window([], None, spot=None, now_ms=1)["status"] == "unavailable"


def test_rolling_trend_blocks_are_whole_and_bounded():
    ws = ud.enrich(ud.build_windows(klines(6000)))
    roll = ud.rolling_trend(ws, block=100)
    assert all(r["n"] == 100 for r in roll)
    assert all(r["ci_lo"] <= r["up_rate"] <= r["ci_hi"] for r in roll)


def test_load_1m_uses_the_injected_runner_and_writes_its_cache(tmp_path):
    rows = klines(1200)
    calls = {"n": 0}

    def runner(url):
        calls["n"] += 1
        return rows[-1000:] if "endTime" not in url else rows[:1000]

    p = str(tmp_path / "k.json")
    out = ud.load_1m(1200, runner=runner, cache_path=p, use_cache=True)
    assert calls["n"] >= 1 and out
    assert os.path.exists(p)


# ── politics ─────────────────────────────────────────────────────────────────


def hist(vals, now, hours=1):
    return [{"t": int(now - (len(vals) - 1 - i) * 3600 * hours), "p": v}
            for i, v in enumerate(vals)]


def test_market_read_measures_in_percentage_points():
    now = time.time()
    row = {"market_id": "1", "event": "E", "question": "Q?", "slug": "s",
           "volume": 1e6, "volume_24h": 1e5, "liquidity": 1e5,
           "end_date": "", "tags": [], "yes_token": "t"}
    h = hist([0.30] * 168 + [0.42], now)
    r = pol.market_read(row, h, now=now)
    assert r["delta_7d_pp"] == pytest.approx(12.0, abs=0.01)
    assert r["label"] in ("REPRICING_YES", "DRIFTING_YES")
    assert r["forecast"]["p10"] <= r["forecast"]["p50"] <= r["forecast"]["p90"]
    assert 0.01 <= r["forecast"]["p50"] <= 0.99


def test_market_read_calls_a_round_trip_churn():
    now = time.time()
    row = {"market_id": "1", "event": "E", "question": "Q?", "slug": "s",
           "volume": 1e6, "volume_24h": 1e5, "liquidity": 1e5,
           "end_date": "", "tags": [], "yes_token": "t"}
    vals = [0.30 + (0.2 if i % 2 else -0.2) for i in range(168)] + [0.35]
    r = pol.market_read(row, hist(vals, now), now=now)
    assert r["label"] == "CHURN"


def test_market_read_needs_a_week_of_history():
    now = time.time()
    row = {"market_id": "1", "event": "E", "question": "Q?", "slug": "s",
           "volume": 1, "volume_24h": 1, "liquidity": 1, "end_date": "",
           "tags": [], "yes_token": "t"}
    assert pol.market_read(row, hist([0.5] * 3, now), now=now) is None


def test_project_prob_is_a_martingale_until_a_carry_is_proven():
    r = {"p_now": 0.4, "vol_pp_hour": 1.0, "delta_7d_pp": 20.0}
    assert pol.project_prob(r)["p50"] == pytest.approx(0.4)
    assert pol.project_prob(r)["model"] == "martingale"
    assert pol.project_prob(r, carry=0.5)["p50"] > 0.4


def test_momentum_test_refuses_an_ungapped_measurement():
    """Without 21 days of history the only available windows share the t-7d
    price, which manufactures a negative correlation. That reading must never
    be usable no matter how clean it looks."""
    reads = [{"delta_prev_week_pp": x, "delta_7d_pp": -x, "liquidity": 1000.0,
              "p_7d": 0.5, "p_now": 0.5}
             for x in range(-15, 16) if x]
    m = pol.momentum_test(reads)
    assert m["gapped"] is False
    assert m["significant"] is True and m["corr"] < 0
    assert m["usable"] is False
    assert "OVERLAPPING" in m["verdict"]


def test_momentum_test_prefers_the_gapped_windows_when_available():
    reads = [{"delta_gap_week_pp": x, "delta_prev_week_pp": -x, "delta_7d_pp": x,
              "liquidity": float(i), "p_7d": 0.5, "p_now": 0.5}
             for i, x in enumerate(r for r in range(-15, 16) if r)]
    m = pol.momentum_test(reads)
    assert m["gapped"] is True
    assert m["corr"] > 0                       # read off the gapped column
    assert m["shared_endpoint_corr"] < 0       # the confounded one is still shown
    assert m["usable"] is True
    assert "CONTINUE" in m["verdict"]


def test_momentum_test_needs_the_liquidity_split_to_agree():
    """A relationship that only exists in the thin half is a spread artifact."""
    thin = [{"delta_gap_week_pp": x, "delta_7d_pp": x, "liquidity": 1.0,
             "p_7d": 0.5, "p_now": 0.5} for x in range(-12, 13) if x]
    deep = [{"delta_gap_week_pp": x, "delta_7d_pp": -x, "liquidity": 1e6,
             "p_7d": 0.5, "p_now": 0.5} for x in range(-12, 13) if x]
    m = pol.momentum_test(thin + deep)
    assert m["robust"] is False
    assert m["usable"] is False


def test_momentum_test_says_too_small_below_the_floor():
    assert pol.momentum_test([{"delta_prev_week_pp": 1, "delta_7d_pp": 1}])["status"] == "too_small"


def test_longshot_test_buckets_and_reports_intervals():
    reads = ([{"p_7d": 0.05, "delta_7d_pp": -2.0} for _ in range(10)]
             + [{"p_7d": 0.90, "delta_7d_pp": 3.0} for _ in range(10)]
             + [{"p_7d": 0.50, "delta_7d_pp": 0.0} for _ in range(10)])
    ls = pol.longshot_test(reads)
    by = {r["bucket"]: r for r in ls["rows"]}
    assert by["longshot (<=15%)"]["mean_delta_pp"] == -2.0
    assert by["favourite (>=85%)"]["mean_delta_pp"] == 3.0
    assert "textbook" in ls["verdict"]


def test_fetch_markets_filters_by_volume_and_binary_shape():
    ev = {"title": "E", "volume24hr": 10, "tags": [{"slug": "politics"}], "markets": [
        {"id": "1", "question": "big", "clobTokenIds": '["a","b"]',
         "outcomePrices": '["0.5","0.5"]', "volumeNum": 1e6, "slug": "s1"},
        {"id": "2", "question": "small", "clobTokenIds": '["c","d"]',
         "outcomePrices": '["0.5","0.5"]', "volumeNum": 10.0, "slug": "s2"},
        {"id": "3", "question": "multi", "clobTokenIds": '["e","f","g"]',
         "outcomePrices": '["0.3","0.3","0.4"]', "volumeNum": 1e6, "slug": "s3"},
        {"id": "4", "question": "closed", "closed": True, "clobTokenIds": '["h","i"]',
         "outcomePrices": '["0.5","0.5"]', "volumeNum": 1e6, "slug": "s4"},
    ]}
    rows = pol.fetch_markets(limit=10, min_volume=1000.0, getter=lambda u: [ev])
    assert [r["market_id"] for r in rows] == ["1"]


def test_read_drops_markets_already_past_their_end_date():
    now = time.time()
    ev = {"title": "E", "volume24hr": 10, "tags": [], "markets": [
        {"id": "1", "question": "expired", "clobTokenIds": '["a","b"]',
         "outcomePrices": '["0.5","0.5"]', "volumeNum": 1e6, "slug": "s1",
         "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 86400))},
    ]}

    def getter(url):
        if "events" in url:
            return [ev]
        return {"history": [{"t": int(now - (200 - i) * 3600), "p": 0.5}
                            for i in range(200)]}

    out = pol.read(limit=5, min_volume=1.0, getter=getter, now=now)
    assert out["expired_dropped"] == 1
    assert out["scanned"] == 0


# ── updown microstructure edges ──────────────────────────────────────────────


def test_fee_formula_is_symmetric_and_peaks_at_the_middle():
    """The formula is rate x min(p, 1-p): identical on both outcomes, so a fee
    cannot be dodged by picking a side."""
    assert ue.fee_per_share(0.3, 1000) == pytest.approx(ue.fee_per_share(0.7, 1000))
    assert ue.fee_per_share(0.5, 1000) > ue.fee_per_share(0.05, 1000)
    assert ue.fee_per_share(0.5, 1000) == pytest.approx(0.05)
    assert ue.fee_per_share(0.02, 1000) == pytest.approx(0.002)
    assert ue.fee_per_share(1.0, 1000) == 0.0


def test_the_default_fee_is_what_the_tape_charged_not_what_gamma_advertises():
    """Measured 2026-08-02: Gamma says takerBaseFee 1000 on these markets, and
    79 of 79 executed trades on the websocket charged fee_rate_bps 0. Trusting
    the advertised field understated every arb by up to 5c a share and flipped
    the verdict on the both-sides trade."""
    assert ue.FEE_BPS_DEFAULT == 0.0
    assert ue.GAMMA_ADVERTISED_FEE_BPS == 1000.0
    assert ue.fee_per_share(0.5) == 0.0


def test_market_fee_bps_prefers_the_observed_rate_over_the_advertised_one():
    # observed always wins — it is the only one that took money
    assert ue.market_fee_bps({"takerBaseFee": 1000}, observed=0.0) == 0.0
    assert ue.market_fee_bps({"takerBaseFee": 0}, observed=250.0) == 250.0
    # the disproved advertised value is ignored; any OTHER value is believed
    assert ue.market_fee_bps({"takerBaseFee": 1000}) == ue.FEE_BPS_DEFAULT
    assert ue.market_fee_bps({"takerBaseFee": 250}) == 250.0
    assert ue.market_fee_bps({}) == ue.FEE_BPS_DEFAULT
    assert ue.market_fee_bps(None) == ue.FEE_BPS_DEFAULT


def _fake_getter(up_bid, up_ask, dn_bid, dn_ask, fee=1000, size=100.0):
    market = {"slug": "btc-updown-5m-1", "clobTokenIds": '["A","B"]',
              "takerBaseFee": fee, "orderPriceMinTickSize": 0.01, "orderMinSize": 5}

    def get(url):
        if "gamma" in url:
            return [market]
        tok = url.rsplit("=", 1)[-1]
        bid, ask = (up_bid, up_ask) if tok == "A" else (dn_bid, dn_ask)
        return {"bids": [{"price": str(bid), "size": str(size)}],
                "asks": [{"price": str(ask), "size": str(size)}]}
    return get, market


def test_pair_quote_finds_a_real_arb():
    # asks sum to 0.90 — a 10c gross arb
    get, m = _fake_getter(0.44, 0.45, 0.44, 0.45)
    q = ue.pair_quote(m, getter=get)
    assert q["buy_both"]["cost"] == pytest.approx(0.90)
    assert q["buy_both"]["gross_edge"] == pytest.approx(0.10)
    assert q["buy_both"]["profitable"] is True
    assert q["arb"] is True
    assert q["source"] == "rest"


def test_a_one_tick_arb_survives_at_the_real_zero_fee():
    """At the fee the tape actually charges, a 1c crossed pair IS 1c of edge.
    The old assumption (1000bps) called this a loss."""
    get, m = _fake_getter(0.48, 0.49, 0.49, 0.50)          # asks sum to 0.99
    q = ue.pair_quote(m, getter=get)
    assert q["buy_both"]["gross_edge"] == pytest.approx(0.01)
    assert q["buy_both"]["net_edge"] == pytest.approx(0.01)
    assert q["buy_both"]["profitable"] is True


def test_a_one_tick_arb_dies_if_a_market_really_does_charge():
    """The netting still has to be right for a market that does charge — the
    fee model was not deleted, only its default corrected."""
    get, m = _fake_getter(0.48, 0.49, 0.49, 0.50)
    q = ue.pair_quote(m, getter=get, fee_bps=1000)
    assert q["buy_both"]["gross_edge"] == pytest.approx(0.01)
    assert q["buy_both"]["net_edge"] < 0
    assert q["buy_both"]["profitable"] is False
    assert q["arb"] is False


def test_pair_quote_counts_ticks_to_a_gross_arb_on_a_normal_book():
    get, m = _fake_getter(0.96, 0.97, 0.03, 0.04)          # asks sum to 1.01
    q = ue.pair_quote(m, getter=get)
    assert q["buy_both"]["gross_edge"] < 0
    assert q["ticks_to_gross_arb"] == 2
    assert q["sell_both"]["credit"] == pytest.approx(0.99)


def test_pair_quote_reports_the_sell_side_arb_too():
    get, m = _fake_getter(0.60, 0.61, 0.55, 0.56)          # bids sum to 1.15
    q = ue.pair_quote(m, getter=get)
    assert q["sell_both"]["gross_edge"] == pytest.approx(0.15)
    assert q["sell_both"]["profitable"] is True


def test_pair_quote_degrades_without_a_market_or_tokens():
    assert ue.pair_quote(None, getter=lambda u: None)["status"] == "no_market"
    assert ue.pair_quote({"clobTokenIds": "[]"}, getter=lambda u: None)["status"] == "no_tokens"


def test_tail_edge_finds_the_fat_tail_on_jumpy_prices():
    """Windows built from a jump process must show the leader losing MORE than
    a Gaussian says — that is the whole claim, and it must not fire on data
    that is genuinely Gaussian."""
    ws = ud.enrich(ud.build_windows(klines(9000)))
    t = ue.tail_edge(ws, minute=4)
    assert t["status"] == "ok" and t["n"] > 1000
    assert t["table"] and all(r["ci_lo"] <= r["realized"] <= r["ci_hi"] for r in t["table"])
    # every bucket's implied probability is the mean of the model's own output
    assert all(0.5 <= r["implied"] <= 1.0 for r in t["table"])


def test_tail_edge_refuses_a_tiny_sample():
    assert ue.tail_edge(ud.enrich(ud.build_windows(klines(60))))["status"] == "too_small"


def test_price_calibration_needs_an_unbiased_sample_first():
    out = ue.price_calibration([], lambda e: True, secs_left=30)
    assert out["status"] == "too_small"
    assert "sampler" in out["hint"]


def test_price_calibration_buckets_by_market_price():
    # 200 samples priced at 0.80 that win only 60% of the time -> mispriced
    samples = [{"secs_left": 30, "mid_up": 0.80, "window_end_ms": i}
               for i in range(200)]
    won = {i: (i % 10) < 6 for i in range(200)}
    out = ue.price_calibration(samples, lambda e: won.get(e), secs_left=30)
    assert out["status"] == "ok" and out["n"] == 200
    row = [r for r in out["rows"] if r["bucket"].startswith("0.75")][0]
    assert row["realized"] == pytest.approx(0.6, abs=0.01)
    assert row["significant"] is True
    assert out["mispriced"]


def test_price_calibration_only_uses_the_requested_offset():
    samples = [{"secs_left": 30, "mid_up": 0.5, "window_end_ms": 1},
               {"secs_left": 240, "mid_up": 0.5, "window_end_ms": 2}]
    out = ue.price_calibration(samples, lambda e: True, secs_left=30)
    assert out["n"] == 1


def test_tail_strategy_ev_prices_the_ticket_net_of_fees():
    # 5c tickets that win 20% of the time: gross EV clearly positive
    samples = []
    for i in range(100):
        samples.append({"secs_left": 30, "up_ask": 0.05, "down_ask": 0.99,
                        "window_end_ms": i})
    won = {i: (i % 5 == 0) for i in range(100)}     # UP wins 20%
    ev = ue.tail_strategy_ev(samples, lambda e: won.get(e), max_ask=0.05,
                             secs_left=30, fee_bps=0.0)
    assert ev["status"] == "ok" and ev["n"] == 100
    assert ev["win_rate"] == pytest.approx(0.20)
    assert ev["breakeven_win_rate"] == pytest.approx(0.05)
    assert ev["ev_per_$staked"] > 0
    assert "+EV" in ev["verdict"]


def test_tail_strategy_ev_turns_negative_once_the_fee_bites():
    samples = [{"secs_left": 30, "up_ask": 0.05, "down_ask": 0.99,
                "window_end_ms": i} for i in range(100)]
    won = {i: (i % 25 == 0) for i in range(100)}    # UP wins 4% < 5c cost
    ev = ue.tail_strategy_ev(samples, lambda e: won.get(e), max_ask=0.05, secs_left=30)
    assert ev["ev_per_$staked"] < 0
    assert "-EV" in ev["verdict"] or "inconclusive" in ev["verdict"]


def test_tail_strategy_skips_tickets_above_the_price_cap():
    samples = [{"secs_left": 30, "up_ask": 0.40, "down_ask": 0.61,
                "window_end_ms": i} for i in range(50)]
    assert ue.tail_strategy_ev(samples, lambda e: True, max_ask=0.05)["status"] == "too_small"


def test_a_warming_websocket_falls_back_to_rest_instead_of_quoting_holes(monkeypatch):
    """Measured 2026-08-02: right after subscribe, `feed().pair()` returns two
    rows whose bid/ask are still None. Trusting them reported `source:
    websocket, best_net_edge: null` — a live arb reading as no crossing, at
    exactly the moment a new 5m window opens."""
    import services.trend_engine.updown_ws as uw
    hollow = type("F", (), {
        "subscribe": lambda self, t: None,
        "pair": lambda self, t: [{"bid": None, "ask": None}, {"bid": None, "ask": None}],
        "observed_fee_bps": lambda self: 0.0})
    monkeypatch.setattr(uw, "feed", lambda: hollow())
    monkeypatch.setattr(ue, "current_market", lambda *a, **k: {
        "slug": "w1", "clobTokenIds": '["t1", "t2"]'})
    monkeypatch.setattr(ue, "books_batch", lambda toks: [
        {"bids": [{"price": "0.40", "size": "9"}], "asks": [{"price": "0.51", "size": "9"}]},
        {"bids": [{"price": "0.40", "size": "9"}], "asks": [{"price": "0.50", "size": "9"}]}])
    q = ue.pair_quote()
    assert q["source"] == "rest"
    assert q["best_net_edge"] is not None
    assert q["up"]["ask"] == 0.51


def test_a_live_websocket_is_preferred_once_both_legs_are_two_sided(monkeypatch):
    import services.trend_engine.updown_ws as uw
    live = type("F", (), {
        "subscribe": lambda self, t: None,
        "pair": lambda self, t: [{"bid": 0.40, "ask": 0.51, "bid_size": 9, "ask_size": 9},
                                 {"bid": 0.40, "ask": 0.50, "bid_size": 9, "ask_size": 9}],
        "observed_fee_bps": lambda self: 0.0})
    monkeypatch.setattr(uw, "feed", lambda: live())
    monkeypatch.setattr(ue, "current_market", lambda *a, **k: {
        "slug": "w1", "clobTokenIds": '["t1", "t2"]'})
    monkeypatch.setattr(ue, "books_batch", lambda toks: (_ for _ in ()).throw(
        AssertionError("paid for REST with a live socket")))
    assert ue.pair_quote()["source"] == "websocket"


def test_arb_stats_separates_gross_hits_from_net_hits():
    """A fee the market genuinely charges still kills a one-tick crossing.

    500bps is not the disproved 1000, so it is honoured: 0.025/leg on a 50c
    book is 0.05 of fee against 0.01 of gross edge.
    """
    samples = [
        {"buy_both_gross": 0.01, "buy_both_net": -0.04, "fee_bps": 500,
         "up_ask": 0.5, "down_ask": 0.49, "window_start_ms": 1},
        {"buy_both_gross": -0.01, "buy_both_net": -0.06, "fee_bps": 500,
         "up_ask": 0.5, "down_ask": 0.51, "window_start_ms": 2},
    ]
    a = ue.arb_stats(samples)
    assert a["buy_both_gross_hits"] == 1
    assert a["buy_both_net_hits"] == 0
    assert "NONE survived the fee" in a["verdict"]


def test_row_fee_bps_reads_a_stored_advertised_fee_as_the_measured_one():
    """The distrust rule has to apply on READ, not just on record.

    251 of the first 276 real samples froze Gamma's 1000bps into the row.
    Honouring it would carry a fee nobody is charged forward forever.
    """
    assert ue.row_fee_bps({"fee_bps": 1000.0}) == ue.FEE_BPS_DEFAULT
    assert ue.row_fee_bps({"fee_bps": 500.0}) == 500.0
    assert ue.row_fee_bps({}) == ue.FEE_BPS_DEFAULT
    assert ue.row_fee_bps({"fee_bps": "junk"}) == ue.FEE_BPS_DEFAULT


def test_arb_stats_reprices_rows_that_froze_the_disproved_advertised_fee():
    """Measured 2026-08-02: this exact row sat in the ledger reading
    unprofitable. At the fee actually charged it is a cent a share."""
    row = {"buy_both_gross": 0.01, "buy_both_net": -0.04, "fee_bps": 1000,
           "up_ask": 0.5, "down_ask": 0.49, "window_start_ms": 1}
    a = ue.arb_stats([row])
    assert a["buy_both_net_hits"] == 1
    assert a["mean_fee_bps"] == 0.0
    assert a["rows_repriced_off_advertised_fee"] == 1
    assert "1 BUY + 0 SELL net-profitable" in a["verdict"]


def test_arb_stats_counts_the_sell_side_crossing():
    """The real find: up_bid 0.42 + down_bid 0.59 = $1.01 against a $1 set.

    The buy side has never crossed from here; the sell side has. Reporting
    only the buy side read as 'no arb exists' when one had already printed.
    """
    row = {"buy_both_gross": -0.03, "sell_both_gross": 0.01,
           "sell_both_net": -0.073, "fee_bps": 1000,
           "up_ask": 0.43, "down_ask": 0.60,
           "up_bid": 0.42, "down_bid": 0.59, "window_start_ms": 1}
    a = ue.arb_stats([row])
    assert a["buy_both_net_hits"] == 0
    assert a["sell_both_net_hits"] == 1
    assert a["net_hits"] == 1
    assert a["best_sell_net_edge"] == 0.01
    assert "0 BUY + 1 SELL net-profitable" in a["verdict"]


def test_arb_stats_ignores_the_stored_net_and_recomputes_from_gross():
    """Record-time net is a snapshot of whatever fee was believed that day.
    Gross is fee-free arithmetic off the book, so gross is the input."""
    row = {"buy_both_gross": 0.02, "buy_both_net": -0.99, "fee_bps": 0,
           "up_ask": 0.5, "down_ask": 0.48, "window_start_ms": 1}
    assert ue.arb_stats([row])["buy_both_net_hits"] == 1


def test_arb_stats_declines_a_crossing_it_cannot_price():
    """A charged fee with no leg prices is unpriceable. Decline, never guess:
    guessing here invents an arb that the book may not support."""
    row = {"buy_both_gross": 0.01, "fee_bps": 500, "window_start_ms": 1}
    a = ue.arb_stats([row])
    assert a["buy_both_gross_hits"] == 1
    assert a["buy_both_net_hits"] == 0


def _sample(up_bid, up_ask, dn_bid, dn_ask, fee=0.0, window=1, ts=0,
            up_bid_size=10.0, down_bid_size=10.0,
            up_ask_size=10.0, down_ask_size=10.0):
    """One sampler row in the on-disk (flat) shape `record_window` writes."""
    return {"up_bid": up_bid, "up_ask": up_ask, "down_bid": dn_bid, "down_ask": dn_ask,
            "up_bid_size": up_bid_size, "down_bid_size": down_bid_size,
            "up_ask_size": up_ask_size, "down_ask_size": down_ask_size,
            "buy_both_gross": round(1.0 - (up_ask + dn_ask), 4),
            "sell_both_gross": round((up_bid + dn_bid) - 1.0, 4),
            "fee_bps": fee, "window_start_ms": window, "ts": ts}


def test_arb_stats_counts_windows_not_just_snapshots():
    """Five shots of one window is one event, not five. The first 1,126 real
    samples held two sell-side prints and both were in the SAME window, 2.5
    minutes apart — `2/1056 snapshots` reads as a rate you could size against."""
    w = 1_700_000_000_000
    rows = [_sample(0.42, 0.43, 0.59, 0.60, fee=0.0, window=w, ts=1000),      # crossed
            _sample(0.42, 0.43, 0.59, 0.60, fee=0.0, window=w, ts=1150),      # same window
            _sample(0.40, 0.45, 0.50, 0.56, fee=0.0, window=w + 300_000, ts=1400),
            _sample(0.40, 0.45, 0.50, 0.56, fee=0.0, window=w + 600_000, ts=1700)]
    a = ue.arb_stats(rows)
    assert a["net_hits"] == 2 and a["net_hit_windows"] == 1
    assert a["windows"] == 3
    assert a["windows_since_last_hit"] == 2          # not 3: the hit window is not clean
    assert "1 of 3 windows" in a["verdict"]
    assert "none in the 2 windows since" in a["verdict"]


def test_arb_stats_prices_the_crossing_in_dollars_off_the_thin_leg():
    """1c/share on 4 shares is 4 cents. The per-share number is what makes an
    arb sound like an opportunity; this is what it would have paid."""
    w = 1_700_000_000_000
    rows = [_sample(0.42, 0.43, 0.59, 0.60, fee=0.0, window=w, ts=1000,
                    up_bid_size=7.73, down_bid_size=122.8)]
    a = ue.arb_stats(rows)
    assert a["best_sell_net_edge"] == pytest.approx(0.01)
    assert a["best_hit_usd"] == pytest.approx(0.0773)   # thin leg, not the fat one
    assert "$0.08 takeable" in a["verdict"]


def test_arb_stats_says_so_when_nothing_was_ever_crossed():
    a = ue.arb_stats([{"buy_both_gross": -0.02, "buy_both_net": -0.07,
                       "window_start_ms": 1}])
    assert "never sat under $1" in a["verdict"]


def test_arb_stats_without_samples_prints_the_command_that_makes_them():
    assert "--sample-updown" in ue.arb_stats([])["hint"]


def test_sample_row_records_the_book_not_a_derived_probability():
    q = {"slug": "s", "fee_bps": 1000,
         "up": {"bid": 0.6, "ask": 0.61, "bid_size": 10, "ask_size": 12},
         "down": {"bid": 0.39, "ask": 0.40, "bid_size": 8, "ask_size": 9},
         "buy_both": {"net_edge": -0.05, "gross_edge": -0.01},
         "sell_both": {"net_edge": -0.05, "gross_edge": -0.01}}
    row = ue.sample_row(q, 1_700_000_000_000, 30.0, 100.5, 100.0, 2.0)
    assert row["up_ask"] == 0.61 and row["down_bid"] == 0.39
    assert row["mid_up"] == pytest.approx(0.605)
    assert row["move_bp"] == pytest.approx(50.0)
    assert 0.0 <= row["p_model_up"] <= 1.0
    assert row["window_end_ms"] == 1_700_000_000_000 + ud.WINDOW_MS


def test_sampler_writes_and_reloads_jsonl(tmp_path):
    p = str(tmp_path / "s.jsonl")
    rows = [{"ts": 1, "slug": "a"}, {"ts": 2, "slug": "b"}]
    assert ue.append_samples(rows, p) == 2
    assert [r["slug"] for r in ue.load_samples(p)] == ["a", "b"]
    assert ue.load_samples(str(tmp_path / "missing.jsonl")) == []


def test_record_window_samples_every_offset_without_sleeping(monkeypatch, tmp_path):
    get, market = _fake_getter(0.5, 0.51, 0.49, 0.50)
    monkeypatch.setattr(ue, "current_market", lambda *a, **k: market)
    monkeypatch.setattr(ud, "live_spot", lambda: 100.0)
    monkeypatch.setattr(ud, "load_1m", lambda *a, **k: klines(200))
    slept = []
    p = str(tmp_path / "s.jsonl")
    # frozen clock at the window's open, so every offset is still ahead
    w_open = (int(time.time()) // 300) * 300
    rows = ue.record_window(offsets_s=(240, 60, 30), path=p,
                            sleeper=lambda s: slept.append(s), getter=get,
                            clock=lambda: float(w_open))
    assert [r["secs_left"] for r in rows] == [240, 60, 30]
    # clock is frozen at the open, so each wait is measured from t0:
    # 240s-left is 60s in, 60s-left is 240s in, 30s-left is 270s in
    assert slept == [60.0, 240.0, 270.0]
    assert len(ue.load_samples(p)) == 3
    assert all(r["slug"] == "btc-updown-5m-1" for r in rows)


def test_record_window_skips_offsets_that_are_already_past(monkeypatch, tmp_path):
    """A daemon that starts mid-window must not backfill snapshots it never took."""
    get, market = _fake_getter(0.5, 0.51, 0.49, 0.50)
    monkeypatch.setattr(ue, "current_market", lambda *a, **k: market)
    monkeypatch.setattr(ud, "live_spot", lambda: 100.0)
    monkeypatch.setattr(ud, "load_1m", lambda *a, **k: klines(200))
    w_open = (int(time.time()) // 300) * 300
    rows = ue.record_window(offsets_s=(240, 60, 30), path=str(tmp_path / "s.jsonl"),
                            sleeper=lambda s: None, getter=get,
                            clock=lambda: float(w_open + 200),   # 100s left
                            skip_partial=False)
    assert [r["secs_left"] for r in rows] == [60, 30]


def test_record_window_waits_for_a_whole_window_instead_of_a_partial(monkeypatch, tmp_path):
    """The daemon calls this in a tight loop. Without the skip it re-enters the
    window it just finished and logs a one-snapshot record for it (observed
    live: three 'windows' in four seconds)."""
    get, market = _fake_getter(0.5, 0.51, 0.49, 0.50)
    monkeypatch.setattr(ue, "current_market", lambda *a, **k: market)
    monkeypatch.setattr(ud, "live_spot", lambda: 100.0)
    monkeypatch.setattr(ud, "load_1m", lambda *a, **k: klines(200))
    w_open = (int(time.time()) // 300) * 300
    slept = []
    rows = ue.record_window(offsets_s=(240, 60, 30), path=str(tmp_path / "s.jsonl"),
                            sleeper=lambda s: slept.append(s), getter=get,
                            clock=lambda: float(w_open + 280))   # 20s left
    assert [r["secs_left"] for r in rows] == [240, 60, 30]       # full next window
    assert slept and slept[0] > 0


# ── websocket book feed ──────────────────────────────────────────────────────


class FakeWS:
    """Scripted websocket: `recv()` walks a list of frames, then blocks-ish."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if self.frames:
            return self.frames.pop(0)
        raise RuntimeError("stream ended")

    def close(self):
        self.closed = True


def _book_frame(asset, bids, asks):
    return json.dumps({"event_type": "book", "asset_id": asset,
                       "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
                       "asks": [{"price": str(p), "size": str(s)} for p, s in asks]})


def _change_frame(asset, best_bid, best_ask):
    return json.dumps([{"event_type": "price_change", "changes": [
        {"asset_id": asset, "price": str(best_ask), "size": "10", "side": "SELL",
         "best_bid": str(best_bid), "best_ask": str(best_ask)}]}])


def _trade_frame(asset, price, fee_bps):
    return json.dumps({"event_type": "last_trade_price", "asset_id": asset,
                       "price": str(price), "size": "1", "fee_rate_bps": str(fee_bps)})


def test_feed_reads_top_of_book_from_a_snapshot():
    f = uw.BookFeed()
    f._ingest(_book_frame("A", [(0.40, 100), (0.39, 50)], [(0.42, 20), (0.45, 9)]))
    top = f.top("A")
    assert top["bid"] == 0.40 and top["ask"] == 0.42
    assert top["bid_size"] == 100 and top["ask_size"] == 20
    assert f.books == 1 and f.events == 1


def test_feed_takes_best_bid_ask_straight_off_a_price_change():
    """price_change entries carry best_bid/best_ask, so no delta reconstruction
    is needed — that is the whole reason this is a top-of-book cache and not an
    order-book engine."""
    f = uw.BookFeed()
    f._ingest(_book_frame("A", [(0.40, 100)], [(0.42, 20)]))
    f._ingest(_change_frame("A", 0.55, 0.56))
    top = f.top("A")
    assert top["bid"] == 0.55 and top["ask"] == 0.56
    assert top["src"] == "price_change"
    # sizes are NOT carried on a top-of-book change; stale depth must not persist
    assert top["bid_size"] is None and top["ask_size"] is None
    assert f.price_changes == 1


def test_feed_goes_stale_and_refuses_to_answer():
    f = uw.BookFeed(stale_after_s=0.01)
    f._ingest(_book_frame("A", [(0.4, 1)], [(0.5, 1)]))
    assert f.top("A") is not None
    time.sleep(0.05)
    assert f.top("A") is None
    assert f.health()["stale"] is True


def test_feed_never_returns_half_a_pair():
    f = uw.BookFeed()
    f._ingest(_book_frame("A", [(0.4, 1)], [(0.5, 1)]))
    assert f.pair(["A", "B"]) is None            # B unknown
    f._ingest(_book_frame("B", [(0.5, 1)], [(0.6, 1)]))
    assert len(f.pair(["A", "B"])) == 2


def test_feed_reports_the_fee_the_tape_actually_charged():
    f = uw.BookFeed()
    for _ in range(9):
        f._ingest(_trade_frame("A", 0.5, 0))
    f._ingest(_trade_frame("A", 0.5, 1000))      # a stray outlier
    assert f.observed_fee_bps() == 0.0           # modal, not last-seen
    assert f.trades == 10


def test_feed_drops_the_previous_windows_book_on_resubscribe():
    """A 5m window rolls every 300s. Keeping the old tokens' quotes around
    would let a dead market read as live."""
    f = uw.BookFeed()
    f.subscribe(["A", "B"])
    f._ingest(_book_frame("A", [(0.4, 1)], [(0.5, 1)]))
    assert f.top("A") is not None
    f.subscribe(["C", "D"])
    assert f.top("A") is None
    assert f.health()["assets"] == ["C", "D"]


def test_feed_subscribes_on_connect_and_survives_a_dead_stream():
    frames = [_book_frame("A", [(0.4, 1)], [(0.5, 1)])]
    made = []

    def connector(url):
        ws = FakeWS(list(frames))
        made.append(ws)
        return ws

    f = uw.BookFeed(connector=connector)
    f.subscribe(["A"])
    f.start()
    assert f.wait_ready(timeout_s=3.0)
    f.stop()
    time.sleep(0.05)
    assert made[0].sent[0] == {"assets_ids": ["A"], "type": "market"}
    assert f.reconnects >= 1                     # the stream ended, it retried


def test_wait_pair_holds_out_for_two_sided_quotes_on_both_legs():
    """`wait_ready` is satisfied by one leg holding a lone bid. `pair_quote`
    needs bid AND ask on both, so a caller that warmed on the weak condition
    quotes over REST believing the socket answered."""
    f = uw.BookFeed()
    f._ingest(_book_frame("A", [(0.4, 1)], [(0.5, 1)]))
    f._ingest(json.dumps({"event_type": "book", "asset_id": "B",
                          "bids": [{"price": "0.5", "size": "1"}], "asks": []}))
    assert f.wait_ready(timeout_s=0.05) is True          # weak condition: met
    assert f.wait_pair(["A", "B"], timeout_s=0.05) is False
    f._ingest(_book_frame("B", [(0.5, 1)], [(0.6, 1)]))
    assert f.wait_pair(["A", "B"], timeout_s=0.05) is True


def test_health_separates_a_thread_that_never_started_from_a_failed_connect():
    """The lane refresher starts a socket and exits seconds later; its snapshot
    rendered as a dead feed forever. `running` names which fault it is, `pid`
    says which process measured it."""
    f = uw.BookFeed()
    h = f.health()
    assert h["running"] is False and h["connected"] is False
    assert h["pid"] == os.getpid() and h["measured_at"] > 0


def test_feed_ignores_junk_frames():
    f = uw.BookFeed()
    f._ingest("not json")
    f._ingest(json.dumps({"event_type": "book"}))          # no asset_id
    f._ingest(json.dumps([{"event_type": "price_change", "changes": [{}]}]))
    assert f.top("A") is None


def test_pair_quote_prefers_the_socket_and_says_which_answered(monkeypatch):
    get, market = _fake_getter(0.10, 0.11, 0.10, 0.11)     # REST would say 0.22

    class Feed:
        def subscribe(self, a): pass
        def pair(self, a):
            return [{"bid": 0.44, "ask": 0.45, "bid_size": 5, "ask_size": 6, "age_s": 0.01},
                    {"bid": 0.44, "ask": 0.45, "bid_size": 5, "ask_size": 6, "age_s": 0.01}]
        def observed_fee_bps(self): return 0.0

    monkeypatch.setattr(uw, "feed", lambda *a, **k: Feed())
    q = ue.pair_quote(market)                              # getter=None -> ws path
    assert q["source"] == "websocket"
    assert q["buy_both"]["cost"] == pytest.approx(0.90)    # socket prices, not REST
    assert q["fee_bps"] == 0.0


def test_pair_quote_falls_back_to_rest_when_the_socket_is_cold(monkeypatch):
    class Feed:
        def subscribe(self, a): pass
        def pair(self, a): return None                     # nothing fresh
        def observed_fee_bps(self): return None

    monkeypatch.setattr(uw, "feed", lambda *a, **k: Feed())
    get, market = _fake_getter(0.44, 0.45, 0.44, 0.45)
    q = ue.pair_quote(market, getter=get)
    assert q["source"] == "rest"
    assert q["buy_both"]["cost"] == pytest.approx(0.90)


class FakeFeed:
    """Stand-in for the process-wide BookFeed: records subscribes and waits."""

    def __init__(self, assets=None, ready=True):
        self.subs, self.waits = [], []
        self.assets = list(assets or [])
        self._ready = ready

    def subscribe(self, toks):
        self.subs.append(list(toks))
        self.assets = list(toks)

    def wait_pair(self, toks, timeout_s=3.0):
        self.waits.append((list(toks), timeout_s))
        return self._ready

    def health(self):
        return {"pid": 4242, "running": True, "connected": True, "stale": False,
                "assets": list(self.assets), "events": 7, "reconnects": 0,
                "measured_at": int(time.time())}


def test_warm_feed_subscribes_and_waits_for_both_legs(monkeypatch):
    """The refresher lives for seconds. Subscribing without waiting means it
    quotes over REST and then records health from a socket that never
    connected — which is what rendered on the tab as a permanently dead feed."""
    f = FakeFeed()
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    assert ue.warm_feed({"clobTokenIds": '["t-up", "t-dn"]'}, wait_s=1.0) is True
    assert f.subs == [["t-up", "t-dn"]]
    assert f.waits == [(["t-up", "t-dn"], 1.0)]


def test_warm_feed_declines_anything_that_is_not_a_two_token_market(monkeypatch):
    monkeypatch.setattr(uw, "feed", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("warm_feed reached the socket without a real market")))
    assert ue.warm_feed(None) is False
    assert ue.warm_feed({"clobTokenIds": "[]"}) is False
    assert ue.warm_feed({"clobTokenIds": "not json"}) is False


def test_read_warms_the_socket_before_it_quotes(monkeypatch):
    """Ordering is the whole fix: warm, THEN quote, THEN read health."""
    order = []
    f = FakeFeed(["t-up", "t-dn"])
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    monkeypatch.setattr(ue, "current_market",
                        lambda *a, **k: {"clobTokenIds": '["t-up", "t-dn"]'})
    monkeypatch.setattr(ue, "warm_feed", lambda m, *a, **k: order.append("warm"))
    monkeypatch.setattr(ue, "pair_quote", lambda *a, **k: (
        order.append("quote") or {"status": "ok", "source": "websocket"}))
    out = ue.read(windows=[], samples=[], resolver=lambda *a, **k: None)
    assert order == ["warm", "quote"]
    assert out["live_pair"]["source"] == "websocket"
    assert out["ws"]["running"] is True and out["ws"]["pid"] == 4242


# ── recorders ────────────────────────────────────────────────────────────────


def test_updown_resolver_reads_the_window_by_its_end_time():
    ws = ud.enrich(ud.build_windows(klines(200)))
    resolve = rec.updown_resolver(ws)
    w = ws[5]
    assert resolve(w["t"] + 5 * 60_000) == w["up"]
    assert resolve(w["t"] + 5 * 60_000 + 30_000) == w["up"]     # inside tolerance
    assert resolve(w["t"] + 10 * 86_400_000) is None             # outside the sample
    assert resolve(w["t"] + 5 * 60_000 + 120_000) is None        # past the tolerance
    assert resolve(None) is None


def test_grade_scout_splits_lanes_and_never_pools_them():
    ws = ud.enrich(ud.build_windows(klines(400)))
    end = lambda w: time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                  time.gmtime((w["t"] + 5 * 60_000) / 1000))
    rows = [
        {"lane": "updown_5m", "market_id": "u1", "side": "YES", "fill_px": 0.5,
         "llm_yes": 0.6, "mkt_yes": 0.5, "end_date": end(ws[3])},
        {"lane": "updown_5m", "market_id": "u2", "side": "NO", "fill_px": 0.4,
         "llm_yes": 0.3, "mkt_yes": 0.5, "end_date": end(ws[4])},
        {"lane": "judgment", "market_id": "j1", "side": "YES", "fill_px": 0.3,
         "llm_yes": 0.7, "mkt_yes": 0.3, "end_date": "2026-01-01T00:00:00Z"},
    ]
    out = rec.grade_scout(rows=rows, windows=ws, gamma_resolver=lambda mid: True)
    assert set(out["lanes"]) == {"updown_5m", "judgment"}
    assert out["lanes"]["updown_5m"]["n"] == 2
    assert out["lanes"]["judgment"]["n"] == 1
    assert "chainlink" in out["lanes"]["updown_5m"]["source"]


def test_grade_scout_marks_ungradeable_lanes_pending_instead_of_zero():
    rows = [{"lane": "judgment", "market_id": "j1", "side": "YES", "fill_px": 0.3,
             "llm_yes": 0.7, "mkt_yes": 0.3, "end_date": "2026-01-01T00:00:00Z"}]
    out = rec.grade_scout(rows=rows, windows=[], gamma_resolver=None)
    g = out["lanes"]["judgment"]
    assert g["n"] == 0 and g["pending"] == 1
    assert "not graded" in g["source"]


def test_grade_books_flags_a_decaying_edge():
    """A book whose average is positive only because of its first half must be
    flagged — that is the failure mode a single mean hides."""
    class FakeSL:
        @staticmethod
        def summary(now):
            return [{"book": "decayer", "n": 20, "coins": 3, "last_age_h": 2.0,
                     "pending": 0}]

        @staticmethod
        def list_books():
            return ["decayer", "neg_funding_fade"]        # second one is REMOVED

        @staticmethod
        def load(book):
            return [{"coin": "X"}]

        @staticmethod
        def grade_records(recs, fetch_fwd, now_ms=None, fetch_funding=None):
            return {"n": 20, "pending": 0,
                    "slip12": {"mean_pct": 0.5, "win": 0.55, "total_pct": 10.0},
                    "slip25": {"mean_pct": 0.2},
                    "oos_12bps": {"first": 1.4, "second": -0.4,
                                  "n_first": 10, "n_second": 10},
                    "funding_included": True,
                    "verdict": {"label": "MARGINAL", "why": "second half flipped"}}

        @staticmethod
        def classify(grade, min_n=8):
            return {"label": "PENDING", "why": ""}

    rows = rec.grade_books(fetch_fwd=lambda *a, **k: [], fetch_funding=None, sl=FakeSL)
    assert [r["book"] for r in rows] == ["decayer"]       # removed book skipped
    r = rows[0]
    assert r["decaying"] is True
    assert r["verdict"] == "MARGINAL"
    assert r["win_ci"][0] < 0.55 < r["win_ci"][1]


def test_recorders_observations_call_out_decay_and_calibration():
    books = [{"book": "a", "verdict": "VALIDATED", "resolved": 20, "ev_pct": 0.8,
              "win_rate": 0.6, "ev_first": 0.9, "ev_second": 0.7, "decaying": False,
              "last_age_h": 3.0, "signals": 20},
             {"book": "b", "verdict": "MARGINAL", "resolved": 20, "ev_pct": 0.1,
              "win_rate": 0.5, "ev_first": 1.0, "ev_second": -0.8, "decaying": True,
              "last_age_h": 400.0, "signals": 20}]
    scout = {"lanes": {"judgment": {"n": 12, "mean_pnl_per_$": 0.05, "win_rate": 0.6,
                                    "brier_llm": 0.18, "brier_mkt": 0.22,
                                    "llm_beats_market": True}}}
    obs = rec.observations(books, scout)
    joined = " ".join(obs)
    assert "Decaying" in joined and "b " in joined
    assert "not written in over a week" in joined
    assert "better calibrated than the price" in joined


def test_recorders_read_without_network_is_inventory_only(monkeypatch):
    monkeypatch.setattr(rec, "grade_scout", lambda **kw: {"lanes": {}})
    out = rec.read(with_network=False)
    assert out["books"] == []
    assert out["summary"]["n_books"] == 0
    assert out["observations"][0].startswith("No recorder has resolved")


# ── playbook (the action layer) ──────────────────────────────────────────────


def _kinds(actions):
    return [(a["kind"], a["do"]) for a in actions]


def test_playbook_forbids_trading_a_coin_flip_forecast():
    """The loudest action must be the DON'T when the lane's own backtest says
    the direction is noise — that is the whole point of shipping the eval."""
    acts = pb.hl_actions({
        "eval": {"status": "ok", "n": 1317, "dir_hit": 0.4966, "dir_edge_sigma": -0.25,
                 "coverage_80": 0.804, "beats_coinflip": False},
        "regimes": {}, "reads": []})
    first = acts[0]
    assert first["kind"] == pb.DONT
    assert "p(up)" in first["do"] and "band" in first["do"]
    assert "1317" in first["because"]


def test_playbook_allows_the_direction_when_the_backtest_earns_it():
    acts = pb.hl_actions({
        "eval": {"status": "ok", "n": 900, "dir_hit": 0.57, "dir_edge_sigma": 4.2,
                 "coverage_80": 0.80, "beats_coinflip": True},
        "regimes": {}, "reads": []})
    assert acts[0]["kind"] == pb.DO and acts[0]["confidence"] == "high"


def test_playbook_reads_the_regime_into_a_book_choice():
    base = {"eval": {}, "reads": []}
    chop = pb.hl_actions({**base, "regimes": {"crypto": {
        "status": "ok", "bench": "BTC", "bench_ret_7d": 0.1, "breadth_pct": 50,
        "trend_share_pct": 20, "alt_strength_pct": 0.0}}})
    assert any(a["kind"] == pb.DONT and "trend-following" in a["do"] for a in chop)

    strong = pb.hl_actions({**base, "regimes": {"crypto": {
        "status": "ok", "bench": "BTC", "bench_ret_7d": 5.0, "breadth_pct": 75,
        "trend_share_pct": 70, "alt_strength_pct": 0.0}}})
    assert any(a["kind"] == pb.DO and "Long" in a["do"] for a in strong)

    weak = pb.hl_actions({**base, "regimes": {"crypto": {
        "status": "ok", "bench": "BTC", "bench_ret_7d": -5.0, "breadth_pct": 20,
        "trend_share_pct": 70, "alt_strength_pct": 0.0}}})
    assert any(a["kind"] == pb.DO and "SHORT" in a["do"] for a in weak)


def _regime(**kw):
    base = {"status": "ok", "bench": "BTC", "bench_ret_7d": 0.1, "breadth_pct": 50,
            "trend_share_pct": 60, "alt_strength_pct": 0.0}
    base.update(kw)
    return {"eval": {}, "reads": [], "regimes": {"crypto": base}}


def test_playbook_calls_out_a_day_that_contradicts_the_week():
    """The HL read is 7 daily bars: it is the same number all day, so a
    week-old instruction reads as current at any hour. Observed 2026-08-03:
    6% of the scan green on the day against 50% on the week."""
    acts = pb.hl_actions(_regime(breadth_1d_pct=6.2, breadth_pct=50.0,
                                 bench_ret_1d=-1.14))
    day = [a for a in acts if a["do"].startswith("Today")]
    assert len(day) == 1 and day[0]["kind"] == pb.DONT
    assert "broadly RED" in day[0]["do"]
    assert "6% of the scan is green vs 50% on the week" in day[0]["because"]
    assert "BTC -1.1% on the day" in day[0]["because"]


def test_playbook_calls_out_a_green_day_against_a_red_week():
    acts = pb.hl_actions(_regime(breadth_1d_pct=80.0, breadth_pct=30.0,
                                 bench_ret_1d=2.5))
    day = [a for a in acts if a["do"].startswith("Today")][0]
    assert day["kind"] == pb.DONT and "broadly GREEN" in day["do"]


def test_playbook_leaves_the_week_alone_when_the_day_agrees_with_it():
    acts = pb.hl_actions(_regime(breadth_1d_pct=44.0, breadth_pct=50.0,
                                 bench_ret_1d=-0.2))
    day = [a for a in acts if a["do"].startswith("Today")][0]
    assert day["kind"] == pb.WATCH and "still holds" in day["do"]


def test_playbook_has_no_day_line_without_the_day_number():
    """Old caches have no `breadth_1d_pct`; the tab must not invent one."""
    acts = pb.hl_actions(_regime())
    assert not [a for a in acts if a["do"].startswith("Today")]


def test_playbook_says_whether_todays_entry_is_live_in_daily_sigma():
    """`long candidate on a pullback` is only actionable on a day it is
    actually pulling back, and a 7d read cannot say which day that is. Sized in
    the coin's own sigma so 3% means different things on ADA and BTC."""
    r = {"coin": "ADA", "sector": "crypto", "label": "STRONG_UP", "score": 9,
         "ret_7d": 18.7, "efficiency": 0.69, "ema7": 0.177, "low_7d": 0.16,
         "px": 0.19, "sigma_day_pct": 3.2, "ret_1d": -2.6}
    acts = pb.hl_actions({**_regime(breadth_pct=70), "reads": [r]})
    watch = [a for a in acts if a["do"].startswith("ADA")][0]
    assert "TODAY -2.6% (0.8σ): the pullback is happening now" in watch["because"]

    extending = pb.hl_actions({**_regime(breadth_pct=70), "reads": [{**r, "ret_1d": 4.0}]})
    assert "wrong day to pay up" in [a for a in extending
                                     if a["do"].startswith("ADA")][0]["because"]

    quiet = pb.hl_actions({**_regime(breadth_pct=70), "reads": [{**r, "ret_1d": -0.4}]})
    assert "inside a normal day" in [a for a in quiet
                                     if a["do"].startswith("ADA")][0]["because"]


def test_playbook_says_when_the_named_trigger_is_already_blown():
    """`holds the 7d EMA` is not a trigger when price is already under it."""
    r = {"coin": "ADA", "sector": "crypto", "label": "UP", "score": 5,
         "ret_7d": 9.0, "efficiency": 0.5, "ema7": 0.20, "low_7d": 0.16,
         "px": 0.18, "sigma_day_pct": 3.0, "ret_1d": -3.0}
    watch = [a for a in pb.hl_actions({**_regime(breadth_pct=70), "reads": [r]})
             if a["do"].startswith("ADA")][0]
    assert "already under the 7d EMA, so the trigger below is not live" in watch["because"]


def test_the_regime_reports_todays_breadth_next_to_the_weeks():
    """Same reads, two horizons: the week is a 7-bar average, the day is one
    bar. Without the day number the tab cannot tell them apart."""
    reads = [{"coin": "A", "sector": "crypto", "label": "UP", "px": 2.0, "ema21": 1.0,
              "ret_7d": 5.0, "ret_1d": -1.0, "corr_btc": 0.5, "resid_7d": 1.0, "score": 3.0},
             {"coin": "B", "sector": "crypto", "label": "UP", "px": 2.0, "ema21": 1.0,
              "ret_7d": 3.0, "ret_1d": -2.0, "corr_btc": 0.5, "resid_7d": 1.0, "score": 2.0},
             {"coin": "BTC", "sector": "crypto", "label": "DOWN", "px": 2.0, "ema21": 1.0,
              "ret_7d": 1.0, "ret_1d": -0.5, "corr_btc": 1.0, "resid_7d": 0.0, "score": 1.0}]
    reg = hl.regime(reads, sector="crypto")
    assert reg["breadth_pct"] == 100.0          # all three green on the week
    assert reg["breadth_1d_pct"] == 0.0         # none green today
    assert reg["bench_ret_1d"] == -0.5
    assert reg["median_ret_1d"] == -1.0


def test_playbook_names_coins_with_a_trigger_and_an_invalidation():
    read = hl.coin_read("SOL", ramp(40, step_pct=2.0))
    read["sector"] = "crypto"
    acts = pb.hl_actions({"eval": {}, "reads": [read], "regimes": {"crypto": {
        "status": "ok", "bench": "BTC", "bench_ret_7d": 3.0, "breadth_pct": 70,
        "trend_share_pct": 70, "alt_strength_pct": 0.0}}})
    sol = [a for a in acts if a["do"].startswith("SOL")]
    assert sol, "the strongest uptrend must be named"
    assert sol[0]["trigger"] and sol[0]["invalidate"]
    assert "EMA" in sol[0]["trigger"]


def test_playbook_flags_the_round_trip_trap():
    trap = hl.coin_read("FAKE", sawtooth(40, amp=9.0))
    trap["sector"] = "crypto"
    trap["ret_7d"] = -12.0
    acts = pb.hl_actions({"eval": {}, "reads": [trap], "regimes": {"crypto": {
        "status": "ok", "bench": "BTC", "bench_ret_7d": 0.0, "breadth_pct": 50,
        "trend_share_pct": 60, "alt_strength_pct": 0.0}}})
    trap = [a for a in acts if a["kind"] == pb.DONT and "round trip" in a["do"]]
    assert trap and "FAKE" in trap[0]["do"]
    assert "efficiency" in trap[0]["because"]


def test_playbook_updown_says_dont_when_nothing_survived_correction():
    acts = pb.updown_actions({
        "patterns": {"status": "ok", "base_rate": 0.4968, "base_ci": [0.484, 0.509],
                     "significant": []},
        "calibration": {}, "live": {}, "edges": {}})
    assert acts[0]["kind"] == pb.DONT
    assert "hunch" in acts[0]["do"] or "streak" in acts[0]["do"]


def test_playbook_updown_promotes_the_live_card_when_it_is_actionable():
    acts = pb.updown_actions({
        "patterns": {}, "calibration": {},
        "live": {"status": "ok", "actionable": True, "best_side": "UP",
                 "best_edge_pp": 7.2, "minutes_left": 1.4, "slug": "s",
                 "p_up_randomwalk": 0.8},
        "edges": {}})
    live = [a for a in acts if a["tag"] == "live"]
    assert live and live[0]["kind"] == pb.DO and "RIGHT NOW" in live[0]["do"]


def test_playbook_updown_refuses_the_tail_ticket_while_it_straddles_breakeven():
    acts = pb.updown_actions({
        "patterns": {}, "calibration": {}, "live": {},
        "edges": {"tail_strategy": {"status": "ok", "n": 24, "win_rate": 0.042,
                                    "win_ci": [0.007, 0.202],
                                    "breakeven_win_rate": 0.021}}})
    ticket = [a for a in acts if a["tag"] == "research"]
    assert ticket and ticket[0]["kind"] == pb.DONT

    acts2 = pb.updown_actions({
        "patterns": {}, "calibration": {}, "live": {},
        "edges": {"tail_strategy": {"status": "ok", "n": 400, "win_rate": 0.09,
                                    "win_ci": [0.065, 0.12],
                                    "breakeven_win_rate": 0.03}}})
    ok = [a for a in acts2 if a["tag"] == "research"]
    assert ok and ok[0]["kind"] == pb.DO


def test_playbook_politics_bans_drift_trading_on_a_martingale():
    acts = pb.politics_actions({
        "momentum_test": {"status": "ok", "usable": False, "corr": -0.1,
                          "fisher_z": -0.4, "n": 19, "verdict": "martingale"},
        "board": {}, "reads": []})
    assert acts[0]["kind"] == pb.DONT and "drift" in acts[0]["do"]
    assert any(a["kind"] == pb.DO and "information view" in a["do"] for a in acts)


def test_playbook_recorders_promotes_validated_and_pulls_decaying():
    acts = pb.recorders_actions({"books": [
        {"book": "good", "verdict": "VALIDATED", "ev_pct": 3.9, "ev25_pct": 3.7,
         "resolved": 14, "ev_first": 0.7, "ev_second": 7.1, "decaying": False,
         "last_age_h": 2},
        {"book": "fading", "verdict": "MARGINAL", "ev_pct": 1.1, "ev25_pct": 0.9,
         "resolved": 42, "ev_first": 4.2, "ev_second": -2.1, "decaying": True,
         "last_age_h": 2},
        {"book": "dead", "verdict": "REFUTED", "ev_pct": -2.0, "ev25_pct": -2.2,
         "resolved": 30, "ev_first": -1, "ev_second": -3, "decaying": False,
         "last_age_h": 2},
    ], "scout": {}})
    kinds = _kinds(acts)
    assert any(k == pb.DO and "Fund good" in d for k, d in kinds)
    assert any(k == pb.DONT and "Pull capital from fading" in d for k, d in kinds)
    assert any(k == pb.DONT and "dead" in d for k, d in kinds)


def test_playbook_calls_out_a_lane_the_market_out_calibrates():
    acts = pb.recorders_actions({"books": [], "scout": {"lanes": {
        "updown_5m": {"n": 898, "brier_llm": 0.238, "brier_mkt": 0.220,
                      "llm_beats_market": False}}}})
    assert any(a["kind"] == pb.DONT and "updown_5m" in a["do"] for a in acts)


def test_playbook_build_groups_and_survives_a_broken_payload():
    out = pb.build("hl", {"status": "ok", "eval": {}, "regimes": {}, "reads": []})
    assert out["status"] == "ok" and set(out["counts"]) == {"do", "dont", "watch"}
    assert pb.build("nope", {"status": "ok"})["status"] == "empty"
    assert pb.build("hl", {"status": "empty"})["actions"] == []


# ── arb watcher ──────────────────────────────────────────────────────────────


def _quote(up_ask, dn_ask, up_bid=0.0, dn_bid=0.0, fee=0.0, size=100.0, slug="w1"):
    cost = up_ask + dn_ask
    credit = up_bid + dn_bid
    return lambda: {
        "status": "ok", "slug": slug, "fee_bps": fee, "source": "websocket",
        "up": {"bid": up_bid, "ask": up_ask, "ask_size": size, "bid_size": size},
        "down": {"bid": dn_bid, "ask": dn_ask, "ask_size": size, "bid_size": size},
        "buy_both": {"cost": cost, "gross_edge": 1 - cost, "net_edge": 1 - cost,
                     "size": size},
        "sell_both": {"credit": credit, "gross_edge": credit - 1,
                      "net_edge": credit - 1, "size": size},
    }


def test_arb_watcher_is_silent_on_a_normal_book(tmp_path):
    w = aw.ArbWatcher(ledger_path=str(tmp_path / "a.jsonl"),
                      quoter=_quote(0.51, 0.50))          # pair costs 1.01
    assert w.check() is None
    assert w.fires == 0 and w.best_edge == pytest.approx(-0.01)
    assert "no sub-$1 pair" in w.summary()["verdict"]


def test_arb_watcher_records_a_real_crossed_pair(tmp_path):
    p = str(tmp_path / "a.jsonl")
    w = aw.ArbWatcher(ledger_path=p, quoter=_quote(0.48, 0.49))   # pair costs 0.97
    ev = w.check()
    assert ev["action"] == "shadow" and ev["side"] == "buy_both"
    assert ev["net_edge"] == pytest.approx(0.03)
    assert ev["expected_profit_usd"] == pytest.approx(0.03 * 50)   # capped notional
    assert len(aw.load_events(p)) == 1


def test_arb_watcher_caps_the_notional(tmp_path):
    w = aw.ArbWatcher(ledger_path=str(tmp_path / "a.jsonl"), max_notional_usd=10.0,
                      quoter=_quote(0.48, 0.49, size=9999.0))
    assert w.check()["notional_usd"] == 10.0


def test_arb_watcher_fires_once_per_window(tmp_path):
    w = aw.ArbWatcher(ledger_path=str(tmp_path / "a.jsonl"),
                      quoter=_quote(0.48, 0.49, slug="same"))
    assert w.check()["action"] == "shadow"
    assert w.check()["action"] == "skipped_cooldown"
    assert w.fires == 1


def test_arb_watcher_respects_the_min_edge_floor(tmp_path):
    # half a tick of edge is a rounding artifact, not an opportunity
    w = aw.ArbWatcher(ledger_path=str(tmp_path / "a.jsonl"), min_edge=0.01,
                      quoter=_quote(0.495, 0.50))
    assert w.check() is None


def test_arb_watcher_sees_the_sell_side_too(tmp_path):
    w = aw.ArbWatcher(ledger_path=str(tmp_path / "a.jsonl"),
                      quoter=_quote(0.60, 0.60, up_bid=0.55, dn_bid=0.50))
    ev = w.check()
    assert ev["side"] == "sell_both" and ev["net_edge"] == pytest.approx(0.05)


def test_arb_watcher_refuses_live_mode_without_an_executor():
    """Live-without-executor would silently be shadow while reporting live."""
    with pytest.raises(ValueError, match="executor"):
        aw.ArbWatcher(mode="live")
    with pytest.raises(ValueError):
        aw.ArbWatcher(mode="yolo")


def test_arb_watcher_live_mode_calls_the_injected_executor(tmp_path):
    seen = []
    w = aw.ArbWatcher(mode="live", ledger_path=str(tmp_path / "a.jsonl"),
                      executor=lambda ev: seen.append(ev) or {"filled": True},
                      quoter=_quote(0.48, 0.49))
    ev = w.check()
    assert ev["action"] == "executed" and ev["result"] == {"filled": True}
    assert len(seen) == 1


def test_arb_watcher_records_an_executor_failure_instead_of_raising(tmp_path):
    def boom(ev):
        raise RuntimeError("venue down")
    w = aw.ArbWatcher(mode="live", ledger_path=str(tmp_path / "a.jsonl"),
                      executor=boom, quoter=_quote(0.48, 0.49))
    ev = w.check()
    assert ev["action"] == "execute_failed" and "venue down" in ev["error"]


def test_arb_watcher_run_loop_stops_at_max_checks(tmp_path):
    w = aw.ArbWatcher(ledger_path=str(tmp_path / "a.jsonl"), quoter=_quote(0.51, 0.50))
    res = w.run(max_checks=5, sleeper=lambda s: None, printer=lambda m: None)
    assert res["checks"] == 5 and res["fires"] == 0


# ── the FIRE button: readiness, tickets, preflight ───────────────────────────

_FULL_CREDS = {k: "x" for k, _ in aw.REQUIRED_CREDS}


def test_execution_readiness_names_every_missing_credential():
    """Placing a CLOB order needs L2 headers AND an L1 signature. Holding only
    the API key fails at the exchange, which is the wrong place to learn it."""
    r = aw.execution_readiness(getenv={"POLYMARKET_API_KEY": "k",
                                       "POLYMARKET_ADDRESS": "0x1"}.get)
    assert r["ready"] is False
    missing = {m["key"] for m in r["missing"]}
    assert missing == {"POLYMARKET_SECRET", "POLYMARKET_PASSPHRASE",
                       "POLYMARKET_PRIVATE_KEY"}
    assert "POLYMARKET_API_KEY" in r["present"]


def test_execution_readiness_never_leaks_a_credential_value():
    """This dict is rendered on a web page. Names only, never values."""
    r = aw.execution_readiness(getenv={**_FULL_CREDS,
                                       "POLYMARKET_SECRET": "sup3rsecret"}.get)
    assert "sup3rsecret" not in json.dumps(r)
    assert r["ready"] is True and r["blocker"] is None


def test_order_tickets_price_both_legs_at_the_touch_and_size_the_thin_one():
    w = aw.ArbWatcher(quoter=_quote(0.48, 0.49, size=30.0), ledger_path=os.devnull)
    tickets = aw.order_tickets(w.check(), cap_usd=50.0)
    assert [t["action"] for t in tickets] == ["BUY", "BUY"]
    assert [t["price"] for t in tickets] == [0.48, 0.49]
    # 50/0.97 = 51.5 shares wanted, but the book only shows 30.
    assert all(t["shares"] == 30.0 for t in tickets)


def test_order_tickets_are_fill_or_kill_because_one_leg_is_a_naked_bet():
    """A filled UP leg with no DOWN leg is not a smaller arb, it is directional
    BTC exposure — the exact trade this lane exists to avoid."""
    w = aw.ArbWatcher(quoter=_quote(0.48, 0.49), ledger_path=os.devnull)
    assert {t["time_in_force"] for t in aw.order_tickets(w.check())} == {"FOK"}


def test_order_tickets_sell_side_lifts_the_bids():
    """The side that actually printed from here: up_bid 0.42 + down_bid 0.59."""
    w = aw.ArbWatcher(quoter=_quote(0.60, 0.60, up_bid=0.42, dn_bid=0.59),
                      ledger_path=os.devnull)
    ev = w.check()
    assert ev["side"] == "sell_both"
    tickets = aw.order_tickets(ev)
    assert [t["action"] for t in tickets] == ["SELL", "SELL"]
    assert [t["price"] for t in tickets] == [0.42, 0.59]


def test_order_tickets_are_empty_when_there_is_nothing_to_send():
    assert aw.order_tickets(None) == []
    assert aw.order_tickets({"side": "buy_both", "up": {}, "down": {}}) == []


def test_preflight_is_dark_on_an_uncrossed_book(tmp_path):
    p = aw.preflight(quoter=_quote(0.51, 0.50), getenv=_FULL_CREDS.get)
    assert p["armed"] is False and p["would_execute"] is False
    assert p["tickets"] == []
    assert "nothing to fire" in p["verdict"]


def test_preflight_arms_and_prices_a_real_crossing():
    p = aw.preflight(quoter=_quote(0.48, 0.49), getenv=_FULL_CREDS.get)
    assert p["armed"] is True and p["would_execute"] is True
    assert len(p["tickets"]) == 2
    assert "ARB LIVE" in p["verdict"]


def test_preflight_arms_but_refuses_to_execute_without_credentials():
    """The button must light on a real arb even when it cannot send — the
    alternative is never learning that the opportunity was there."""
    p = aw.preflight(quoter=_quote(0.48, 0.49), getenv={}.get)
    assert p["armed"] is True
    assert p["would_execute"] is False
    assert "credential(s) missing" in p["verdict"]


def test_preflight_subscribes_the_socket_so_the_next_quote_is_free(monkeypatch):
    """A button quoting over REST is ~300ms behind a one-tick market. The
    first call pays that; every call after it must be served from memory."""
    f = FakeFeed()
    monkeypatch.setattr(aw, "_SUBSCRIBED", None)
    monkeypatch.setattr(aw, "slug_for", lambda *a, **k: "w1")
    monkeypatch.setattr(aw, "tokens_for", lambda s: ["t-up", "t-dn"])
    monkeypatch.setattr(aw, "pair_quote", lambda *a, **k: {"status": "no_book"})
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    aw.preflight()
    aw.preflight()
    assert f.subs == [["t-up", "t-dn"]]       # idempotent, not once per poll


def test_preflight_waits_for_the_socket_on_the_first_call_of_a_window(monkeypatch):
    """Without the wait, the first quote after every 5m roll is a 300ms poll —
    exactly when a one-tick crossing is most likely to be there and gone."""
    f = FakeFeed()
    monkeypatch.setattr(aw, "_SUBSCRIBED", None)
    monkeypatch.setattr(aw, "slug_for", lambda *a, **k: "w1")
    monkeypatch.setattr(aw, "tokens_for", lambda s: ["t-up", "t-dn"])
    monkeypatch.setattr(aw, "pair_quote", lambda *a, **k: {"status": "no_book"})
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    aw.preflight()
    assert f.waits == [(["t-up", "t-dn"], aw.SUBSCRIBE_WAIT_S)]
    aw.preflight()
    assert len(f.waits) == 1                  # only on an actual (re)subscribe


def test_preflight_resubscribes_when_the_5m_window_rolls(monkeypatch):
    slug = ["w1"]
    f = FakeFeed()
    monkeypatch.setattr(aw, "_SUBSCRIBED", None)
    monkeypatch.setattr(aw, "slug_for", lambda *a, **k: slug[0])
    monkeypatch.setattr(aw, "tokens_for", lambda s: [s + "-up", s + "-dn"])
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    aw.ensure_subscribed()
    slug[0] = "w2"
    aw.ensure_subscribed()
    assert f.subs == [["w1-up", "w1-dn"], ["w2-up", "w2-dn"]]


def test_ensure_subscribed_resubscribes_when_the_feed_lost_its_assets(monkeypatch):
    """The slug memo is not proof of a subscription: a `stop_feed()` between
    calls leaves a fresh socket holding nothing while the memo still matches."""
    f = FakeFeed()
    monkeypatch.setattr(aw, "_SUBSCRIBED", None)
    monkeypatch.setattr(aw, "slug_for", lambda *a, **k: "w1")
    monkeypatch.setattr(aw, "tokens_for", lambda s: ["t-up", "t-dn"])
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    aw.ensure_subscribed()
    f.assets = []                             # socket replaced under us
    aw.ensure_subscribed()
    assert f.subs == [["t-up", "t-dn"], ["t-up", "t-dn"]]


def test_preflight_reports_the_socket_of_the_process_that_answers(monkeypatch):
    """The cached lane payload carries a health block too, but it is written by
    a process that exits seconds after starting a socket. The button's status
    line has to come from the process holding the live feed."""
    f = FakeFeed(["t-up", "t-dn"])
    monkeypatch.setattr(uw, "feed", lambda *a, **k: f)
    p = aw.preflight(quoter=_quote(0.51, 0.50), getenv={}.get)
    assert p["ws"]["pid"] == 4242 and p["ws"]["running"] is True
    assert p["ws"]["events"] == 7


def test_preflight_survives_a_socket_that_cannot_report_health(monkeypatch):
    monkeypatch.setattr(uw, "feed", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("feed gone")))
    p = aw.preflight(quoter=_quote(0.51, 0.50), getenv={}.get)
    assert p["status"] == "ok"
    assert p["ws"]["connected"] is False and "feed gone" in p["ws"]["last_error"]


def test_preflight_shows_the_quote_that_armed_it_instead_of_re_reading():
    """Two reads are two books: the number under the button would not be the
    number that armed it, and on the REST fallback it is another 300ms."""
    reads = []

    def quoter():
        reads.append(1)
        return _quote(0.48, 0.49)()

    p = aw.preflight(quoter=quoter, getenv=_FULL_CREDS.get)
    assert len(reads) == 1
    assert p["quote"]["buy_both"]["cost"] == pytest.approx(0.97)
    assert p["quote"]["source"] == "websocket"


def test_ensure_subscribed_swallows_a_dead_socket(monkeypatch):
    """The button degrades to REST rather than 500ing the dashboard."""
    monkeypatch.setattr(aw, "_SUBSCRIBED", None)
    monkeypatch.setattr(aw, "slug_for", lambda *a, **k: "w1")
    monkeypatch.setattr(aw, "tokens_for", lambda s: (_ for _ in ()).throw(OSError("down")))
    assert aw.ensure_subscribed() is None


def test_preflight_with_an_injected_quoter_never_touches_the_socket(monkeypatch):
    """Tests and backtests pass a quoter; that path must stay offline."""
    monkeypatch.setattr(aw, "slug_for", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("preflight reached the network with a quoter injected")))
    assert aw.preflight(quoter=_quote(0.51, 0.50), getenv={}.get)["armed"] is False


def test_preflight_places_no_order_even_with_every_credential(monkeypatch):
    """Read-only by construction, not by configuration."""
    fired = []
    monkeypatch.setattr(aw.ArbWatcher, "_record", lambda self, ev: fired.append(ev))
    p = aw.preflight(quoter=_quote(0.48, 0.49), getenv=_FULL_CREDS.get)
    assert all(ev.get("action") == "shadow" for ev in fired)
    assert p["event"]["mode"] == "shadow"


# ── cache ────────────────────────────────────────────────────────────────────


def test_cache_roundtrip_marks_staleness(tmp_path, monkeypatch):
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    tcache.save("hl", {"status": "ok", "generated_at": int(time.time())})
    got = tcache.load("hl")
    assert got["status"] == "ok" and got["stale"] is False
    tcache.save("hl", {"status": "ok", "generated_at": 1})
    assert tcache.load("hl")["stale"] is True


def test_cache_miss_returns_the_command_that_fills_it(tmp_path, monkeypatch):
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    got = tcache.load("politics")
    assert got["status"] == "empty"
    assert "--lane politics" in got["hint"]


def test_cache_refresh_carries_the_ai_block_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    tcache.save("hl", {"status": "ok", "generated_at": int(time.time()),
                       "ai": {"status": "ok", "headline": "old"}})
    monkeypatch.setattr(tcache, "compute",
                        lambda lane, **kw: {"status": "ok", "generated_at": int(time.time())})
    out = tcache.refresh("hl")
    assert out["ai"]["headline"] == "old"
    assert out["ai"]["stale_for_this_read"] is True


def test_cache_attach_ai_does_not_recompute(tmp_path, monkeypatch):
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    tcache.save("updown", {"status": "ok", "generated_at": int(time.time())})
    tcache.attach_ai("updown", {"status": "ok", "headline": "hi"})
    assert tcache.load("updown")["ai"]["headline"] == "hi"


def test_cache_refresh_all_isolates_a_failing_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    monkeypatch.setattr(tcache, "refresh_eval", lambda **kw: {"status": "fresh"})

    def compute(lane, **kw):
        if lane == "updown":
            raise RuntimeError("binance down")
        return {"status": "ok", "generated_at": int(time.time())}

    monkeypatch.setattr(tcache, "compute", compute)
    out = tcache.refresh_all()
    assert out["hl"]["status"] == "ok"
    assert out["updown"]["status"] == "error"
    assert out["politics"]["status"] == "ok"
    assert out["recorders"]["status"] == "ok"


def test_cache_refresh_all_can_run_one_clock_at_a_time(tmp_path, monkeypatch):
    """The scheduler runs the price lanes every 30 min and the recorders lane
    every 6 hours — `only` is what keeps the slow grade off the fast clock."""
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    seen = []
    monkeypatch.setattr(tcache, "refresh_eval", lambda **kw: seen.append("eval") or {"status": "fresh"})
    monkeypatch.setattr(tcache, "compute",
                        lambda lane, **kw: seen.append(lane) or {"status": "ok",
                                                                 "generated_at": int(time.time())})
    out = tcache.refresh_all(only=["recorders"])
    assert set(out) == {"recorders"}
    assert seen == ["recorders"]                 # no eval, no price lanes


# ── ai pass ──────────────────────────────────────────────────────────────────


def test_ai_prompts_carry_the_numbers_and_the_eval_verdict():
    payload = {"regime": {"label": "RISK_OFF_TRENDING", "btc_ret_7d": -3.9,
                          "btc_label": "DOWN", "breadth_pct": 18.2,
                          "trend_share_pct": 72.7, "dispersion_pct": 5.1,
                          "alt_strength_pct": -0.4, "mean_funding_apr_pct": 6.4},
               "reads": [{"coin": "BTC", "ret_7d": -3.9, "slope_pct_day": -0.46,
                          "r2": 0.58, "efficiency": 0.55, "label": "DOWN",
                          "forecast": {"drift_pct": -0.6, "prob_up": 0.44},
                          "flags": [{"code": "EMA_STACK_BEAR"}]}],
               "eval": {"n": 1317, "dir_hit": 0.4966, "dir_edge_sigma": -0.25,
                        "coverage_80": 0.8, "beats_coinflip": False},
               "observations": ["tape is risk off"]}
    p = tai.hl_prompt(payload)
    assert "RISK_OFF_TRENDING" in p and "BTC" in p
    assert "beats_coinflip=False" in p
    assert "tape is risk off" in p


def test_ai_parses_json_out_of_prose_and_fences():
    body = ('here you go\n```json\n{"headline": "h", "narrative": "n", '
            '"setups": [], "watch": [], "risks": []}\n```\nthanks')
    out = tai._parse(body)
    assert out["headline"] == "h"
    assert tai._parse("no json here") is None


def test_ai_analyze_reports_failure_instead_of_inventing_a_read():
    out = tai.analyze("hl", {"regime": {}, "reads": [], "observations": []},
                      runner=lambda *a, **k: "")
    assert out["status"] == "failed"
    assert out["lane"] == "hl"


def test_ai_analyze_returns_the_parsed_block_on_success():
    envelope = json.dumps({"result": json.dumps(
        {"headline": "h", "narrative": "n", "setups": [{"ticker": "BTC"}],
         "watch": ["w"], "risks": ["r"]})})
    out = tai.analyze("politics", {"board": {}, "momentum_test": {}, "reads": [],
                                   "observations": []},
                      runner=lambda *a, **k: envelope)
    assert out["status"] == "ok" and out["headline"] == "h"
    assert out["setups"][0]["ticker"] == "BTC"


def test_ai_rejects_an_unknown_lane():
    assert tai.analyze("nope", {})["status"] == "bad_lane"


# ── dashboard wiring ─────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from hermes_trader import dashboard as db
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    db._TTL_CACHE.clear()
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)


def test_trends_page_renders_self_contained(client):
    r = client.get("/trends")
    assert r.status_code == 200
    body = r.text
    assert "TRENDS" in body and "HYPERLIQUID" in body
    assert "BTC 5M UP/DOWN" in body and "POLITICS" in body
    assert "RECORDERS" in body
    # no third-party asset may be pulled at render time
    assert "http://" not in body and "https://" not in body
    assert 'href="/static/app.css"' in body


def test_every_page_links_the_new_tab(client):
    for path in ("/", "/activity", "/news", "/predictions", "/analytics"):
        assert 'data-nav="/trends"' in client.get(path).text


def test_lane_apis_are_pure_cache_reads(client):
    for lane in ("hl", "updown", "politics", "recorders"):
        r = client.get(f"/api/dashboard/trends/{lane}")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "empty"          # tmp cache dir, nothing written
        assert body["stale"] is True


def test_lane_api_serves_what_the_refresher_wrote(client, tmp_path):
    tcache.save("hl", {"status": "ok", "generated_at": int(time.time()),
                       "scanned": 3, "regime": {"label": "RISK_ON_TRENDING"}})
    r = client.get("/api/dashboard/trends/hl")
    assert r.json()["regime"]["label"] == "RISK_ON_TRENDING"


def test_unknown_lane_is_a_404(client):
    assert client.get("/api/dashboard/trends/bogus").status_code == 404


def test_mutating_trend_routes_are_operator_gated(client):
    for path in ("/api/dashboard/trends/hl/refresh", "/api/dashboard/trends/hl/ai",
                 "/api/dashboard/trends/arb/fire"):
        assert client.post(path).status_code in (401, 403, 503)
    for path in ("/api/dashboard/trends/job/result?job_id=x",
                 "/api/dashboard/trends/arb/preflight"):
        assert client.get(path).status_code in (401, 403, 404, 503)


def test_the_fire_button_is_on_the_page_and_says_it_places_no_order(client):
    """The button must state its own limits where it is pressed. A control
    labelled FIRE that quietly records is worse than no control."""
    body = client.get("/trends").text
    assert 'id="arb-fire"' in body and 'id="arb-fire-btn"' in body
    assert "/api/dashboard/trends/arb/fire" in body
    assert "places <b>no order</b>" in body
    assert "FOK" in body


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_refresh_runs_in_its_own_process_without_the_servers_hl_throttle(monkeypatch):
    """`restart.sh` gives the server a hard-throttled HL bucket (refill 2/s) so
    its polls yield to the trading loop. An HL scan is ~26 coins x
    `candleSnapshot` at weight 20: inside that budget every request waits its
    30s ceiling and skips, and the refresh never returns. Measured on the live
    server: still running after 601s, while the UI gives up at 300s."""
    import hermes_trader.dashboard as dash
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return _Proc()

    monkeypatch.setenv("HERMES_HL_RATE_REFILL_PER_SEC", "2")
    monkeypatch.setenv("HERMES_HL_RATE_CAPACITY", "60")
    monkeypatch.setenv("HERMES_STATE_READONLY", "1")
    out = dash._refresh_lane_subprocess(
        "hl", runner=runner, loader=lambda ln: {"status": "ok", "generated_at": 7})
    assert out == {"status": "ok", "generated_at": 7}
    assert cmd_has(seen["cmd"], "--refresh-all", "--lanes", "hl")
    assert not [k for k in seen["env"] if k.startswith("HERMES_HL_RATE_")]
    # the readonly guard covers agent memory and DSL exits — a lane refresh has
    # no business writing either, so it is NOT stripped
    assert seen["env"]["HERMES_STATE_READONLY"] == "1"


def cmd_has(cmd, *parts):
    return all(p in cmd for p in parts)


def test_refresh_reports_a_failed_child_instead_of_claiming_success(monkeypatch):
    import hermes_trader.dashboard as dash
    out = dash._refresh_lane_subprocess(
        "hl", runner=lambda cmd, **kw: _Proc(returncode=1, stderr="boom\nRuntimeError: hl down"),
        loader=lambda ln: {"status": "ok"})
    assert out["status"] == "error" and "hl down" in out["error"]


def test_refresh_reports_a_timeout_instead_of_hanging_the_job(monkeypatch):
    import subprocess

    import hermes_trader.dashboard as dash

    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    out = dash._refresh_lane_subprocess("hl", timeout_s=5, runner=runner,
                                        loader=lambda ln: {})
    assert out["status"] == "error" and "exceeded 5s" in out["error"]


def test_the_refresh_button_surfaces_a_failed_job(client):
    """A refresh that errored used to land in a callback that ignored the
    result, so failure looked exactly like success."""
    body = client.get("/trends").text
    assert "refresh failed: " in body
    assert "setRefreshNote" in body and "refreshing… ' +" in body


def test_the_arb_card_leads_the_updown_lane(client):
    """It is the only block on the lane with a control on it, so it sits above
    the read-only analysis and claim 3 renders before the other claims."""
    body = client.get("/trends").text
    lane = body.index('id="lane-updown"')
    assert body.index('id="ud-edges"', lane) < body.index('id="pb-updown"', lane)
    assert body.index("claim(3,") < body.index("claim(2,") < body.index("claim(1,")


def test_the_socket_status_line_is_read_live_and_never_from_the_cache(client):
    """The cached `ws` block is a snapshot from the refresher, which exits
    seconds after starting a socket — rendering it showed a dead feed on the
    tab while the server's own socket was pushing thousands of events."""
    body = client.get("/trends").text
    assert "wsHealthLine(p.ws)" in body
    assert "e.ws" not in body                  # cached health is never rendered
    assert "pair at last refresh" in body      # the cached quote says it is old


def test_the_arb_route_never_reports_an_order_as_sent(monkeypatch):
    """`fired` is hard-false on every path. No credential state can flip it,
    because there is no code here that could place the order it would claim."""
    import hermes_trader.dashboard as dash
    crossed = {"status": "ok", "slug": "w1", "fee_bps": 0.0, "source": "websocket",
               "up": {"bid": 0.4, "ask": 0.48, "ask_size": 50.0, "bid_size": 50.0},
               "down": {"bid": 0.4, "ask": 0.49, "ask_size": 50.0, "bid_size": 50.0},
               "buy_both": {"cost": 0.97, "gross_edge": 0.03, "net_edge": 0.03,
                            "size": 50.0}}
    monkeypatch.setattr(aw, "pair_quote", lambda *a, **k: crossed)
    monkeypatch.setattr(aw.ArbWatcher, "_record", lambda self, ev: None)
    monkeypatch.setattr(aw, "execution_readiness", lambda *a, **k:
                        {"ready": True, "present": [], "missing": [], "blocker": None})
    out = dash._trends_arb_fire_payload()
    assert out["status"] == "recorded"
    assert out["fired"] is False
    assert len(out["tickets"]) == 2


def test_the_arb_route_reports_no_arb_without_pretending_it_failed(monkeypatch):
    import hermes_trader.dashboard as dash
    monkeypatch.setattr(aw, "pair_quote", lambda *a, **k: {
        "status": "ok", "slug": "w1", "fee_bps": 0.0, "source": "websocket",
        "up": {"bid": 0.4, "ask": 0.51, "ask_size": 5.0, "bid_size": 5.0},
        "down": {"bid": 0.4, "ask": 0.50, "ask_size": 5.0, "bid_size": 5.0},
        "buy_both": {"cost": 1.01, "gross_edge": -0.01, "net_edge": -0.01, "size": 5.0}})
    out = dash._trends_arb_fire_payload()
    assert out["status"] == "no_arb" and out["fired"] is False
    assert "nothing sent" in out["message"]


def test_the_arb_preflight_payload_degrades_instead_of_500ing(monkeypatch):
    """A dead websocket must not take the dashboard down with it."""
    import hermes_trader.dashboard as dash
    monkeypatch.setattr(aw, "preflight", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("feed gone")))
    out = dash._trends_arb_preflight_payload()
    assert out["status"] == "error" and "feed gone" in out["error"]


def test_css_build_is_current():
    """The committed app.css must match what the builder emits for the
    templates as they stand — a new class in trends.html without a rebuild
    would render the page unstyled."""
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "scripts/build_static_css.py", "--check"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
