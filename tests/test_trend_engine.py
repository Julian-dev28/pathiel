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
import os
import pathlib
import re
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.trend_engine import ai as tai
from services.trend_engine import cache as tcache
from services.trend_engine import flags as tflags
from services.trend_engine import forecast as tfc
from services.trend_engine import hl_trends as hl
from services.trend_engine import metrics as M
from services.trend_engine import playbook as pb
from services.trend_engine import recorders as rec


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
    import pathia.client.universe as U
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


# ── recorders ────────────────────────────────────────────────────────────────


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


def test_recorders_observations_call_out_decay_and_staleness():
    books = [{"book": "a", "verdict": "VALIDATED", "resolved": 20, "ev_pct": 0.8,
              "win_rate": 0.6, "ev_first": 0.9, "ev_second": 0.7, "decaying": False,
              "last_age_h": 3.0, "signals": 20},
             {"book": "b", "verdict": "MARGINAL", "resolved": 20, "ev_pct": 0.1,
              "win_rate": 0.5, "ev_first": 1.0, "ev_second": -0.8, "decaying": True,
              "last_age_h": 400.0, "signals": 20}]
    obs = rec.observations(books)
    joined = " ".join(obs)
    assert "Decaying" in joined and "b " in joined
    assert "idle over a week" in joined


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


def test_playbook_build_groups_and_survives_a_broken_payload():
    out = pb.build("hl", {"status": "ok", "eval": {}, "regimes": {}, "reads": []})
    assert out["status"] == "ok" and set(out["counts"]) == {"do", "dont", "watch"}
    assert pb.build("nope", {"status": "ok"})["status"] == "empty"
    assert pb.build("hl", {"status": "empty"})["actions"] == []


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
    got = tcache.load("recorders")
    assert got["status"] == "empty"
    assert "--lane recorders" in got["hint"]


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
    tcache.save("recorders", {"status": "ok", "generated_at": int(time.time())})
    tcache.attach_ai("recorders", {"status": "ok", "headline": "hi"})
    assert tcache.load("recorders")["ai"]["headline"] == "hi"


def test_cache_refresh_all_isolates_a_failing_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    monkeypatch.setattr(tcache, "refresh_eval", lambda **kw: {"status": "fresh"})

    def compute(lane, **kw):
        if lane == "recorders":
            raise RuntimeError("grader down")
        return {"status": "ok", "generated_at": int(time.time())}

    monkeypatch.setattr(tcache, "compute", compute)
    out = tcache.refresh_all()
    assert out["hl"]["status"] == "ok"
    assert out["recorders"]["status"] == "error"


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
    out = tai.analyze("hl", {"board": {}, "momentum_test": {}, "reads": [],
                             "observations": []},
                      runner=lambda *a, **k: envelope)
    assert out["status"] == "ok" and out["headline"] == "h"
    assert out["setups"][0]["ticker"] == "BTC"


def test_ai_rejects_an_unknown_lane():
    assert tai.analyze("nope", {})["status"] == "bad_lane"


# ── dashboard wiring ─────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from pathia import dashboard as db
    monkeypatch.setattr(tcache, "DIR", str(tmp_path))
    db._TTL_CACHE.clear()
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)


def test_trends_page_renders_self_contained(client):
    r = client.get("/trends")
    assert r.status_code == 200
    body = r.text
    assert "Trends" in body and "HYPERLIQUID" in body
    assert 'id="lane-hl"' in body
    # The lanes that are gone must not creep back as markup or dead script.
    assert "recorders" not in body.lower()
    # no third-party asset may be pulled at render time
    assert "http://" not in body and "https://" not in body
    assert 'href="/static/app.css"' in body


def test_lane_apis_are_pure_cache_reads(client):
    for lane in ("hl", "recorders"):
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


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_refresh_runs_in_its_own_process_without_the_servers_hl_throttle(monkeypatch):
    """`restart.sh` gives the server a hard-throttled HL bucket (refill 2/s) so
    its polls yield to the trading loop. An HL scan is ~26 coins x
    `candleSnapshot` at weight 20: inside that budget every request waits its
    30s ceiling and skips, and the refresh never returns. Measured on the live
    server: still running after 601s, while the UI gives up at 300s."""
    import pathia.dashboard as dash
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return _Proc()

    monkeypatch.setenv("PATHIA_HL_RATE_REFILL_PER_SEC", "2")
    monkeypatch.setenv("PATHIA_HL_RATE_CAPACITY", "60")
    monkeypatch.setenv("PATHIA_STATE_READONLY", "1")
    out = dash._refresh_lane_subprocess(
        "hl", runner=runner, loader=lambda ln: {"status": "ok", "generated_at": 7})
    assert out == {"status": "ok", "generated_at": 7}
    assert cmd_has(seen["cmd"], "--refresh-all", "--lanes", "hl")
    assert not [k for k in seen["env"] if k.startswith("PATHIA_HL_RATE_")]
    # the readonly guard covers agent memory and DSL exits — a lane refresh has
    # no business writing either, so it is NOT stripped
    assert seen["env"]["PATHIA_STATE_READONLY"] == "1"


def cmd_has(cmd, *parts):
    return all(p in cmd for p in parts)


def test_refresh_reports_a_failed_child_instead_of_claiming_success(monkeypatch):
    import pathia.dashboard as dash
    out = dash._refresh_lane_subprocess(
        "hl", runner=lambda cmd, **kw: _Proc(returncode=1, stderr="boom\nRuntimeError: hl down"),
        loader=lambda ln: {"status": "ok"})
    assert out["status"] == "error" and "hl down" in out["error"]


def test_refresh_reports_a_timeout_instead_of_hanging_the_job(monkeypatch):
    import subprocess

    import pathia.dashboard as dash

    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    out = dash._refresh_lane_subprocess("hl", timeout_s=5, runner=runner,
                                        loader=lambda ln: {})
    assert out["status"] == "error" and "exceeded 5s" in out["error"]


def test_a_gated_control_says_why_instead_of_alerting(client):
    """`alert()` is a button that does nothing whenever the browser suppresses
    it — which is exactly how the refresh button read from the operator's side
    with no token stored. Every failure path now writes next to the control."""
    body = client.get("/trends").text
    assert "alert('" not in body and 'alert("' not in body
    assert "needs the operator token — paste it above and press save" in body
    assert "operator token rejected (" in body
    assert "openTokenRow(" in body and 'id="token-why"' in body
    # and the gate is visible BEFORE the click, not only after it
    assert "needs the operator token to refresh" in body


def test_the_refresh_button_surfaces_a_failed_job(client):
    """A refresh that errored used to land in a callback that ignored the
    result, so failure looked exactly like success."""
    body = client.get("/trends").text
    assert "refresh failed: " in body
    assert "setRefreshNote" in body and "refreshing… ' +" in body


class _Bar:
    def __init__(self, t):
        self.t = t


def test_a_signal_the_grader_could_not_price_shrinks_the_sample_not_the_ev():
    """A venue outage returns empty forward candles. `simulate_exit` refuses
    those (None, not 0%), so the signal is skipped — but a smaller sample then
    looks exactly like a quieter book unless the count is carried out."""
    class _SL:
        @staticmethod
        def summary(now):
            return [{"book": "b1", "n": 3, "coins": 1, "pending": 0, "gradeable": 3}]

        @staticmethod
        def list_books():
            return ["b1"]

        @staticmethod
        def load(book):
            return [{"coin": "BTC"}]

        @staticmethod
        def grade_records(recs, fwd, now_ms=None, fetch_funding=None):
            return {"n": 1, "pending": 0, "errors": 2, "slip12": {"mean_pct": 1.0}}

        @staticmethod
        def classify(grade, min_n=8):
            return {"label": "PENDING", "why": ""}

    row = rec.grade_books(fetch_fwd=lambda *a, **k: [], sl=_SL())[0]
    assert row["resolved"] == 1 and row["ungraded_errors"] == 2


def test_forward_candles_are_fetched_once_per_coin_not_once_per_signal(monkeypatch):
    """Every signal on a book asks for forward bars on the same coin, and each
    miss is a rate-limited HL info call at weight 20. Measured 2026-08-04 the
    recorders lane ran 2h13m for 4.8s of CPU — pure waiting — and the /trends
    P&L lane was permanently stale behind it."""
    calls = []
    day = 86_400_000
    now = int(time.time() * 1000)
    bars = [_Bar(now - i * day) for i in range(60)][::-1]

    import pathia.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles",
                        lambda coin, interval, n: calls.append((coin, n)) or bars)
    monkeypatch.setattr(hl_client, "fetch_funding_history", lambda *a: [])

    fwd, _ = rec._live_fetchers()
    first = fwd("BTC", now - 10 * day, 5)
    for i in range(1, 8):                       # seven more signals, same coin
        fwd("BTC", now - (10 - i) * day, 5)
    fwd("ETH", now - 10 * day, 5)               # a different coin does fetch
    assert [c[0] for c in calls] == ["BTC", "ETH"]
    # and the answer is unchanged: still only bars strictly after the signal
    assert first == [b for b in bars if b.t > now - 10 * day]


def test_a_deeper_lookback_refetches_instead_of_serving_a_short_cache(monkeypatch):
    """A cached 10-bar pull cannot answer a 400-bar ask. Serving it would
    silently grade an old signal on a window that never covered it."""
    calls = []
    day = 86_400_000
    now = int(time.time() * 1000)
    import pathia.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles",
                        lambda coin, interval, n: calls.append(n) or [])
    monkeypatch.setattr(hl_client, "fetch_funding_history", lambda *a: [])

    fwd, _ = rec._live_fetchers()
    fwd("BTC", now - day, 5)                    # shallow
    fwd("BTC", now - 200 * day, 5)              # much older signal -> deeper need
    fwd("BTC", now - day, 5)                    # covered by the deep pull now
    assert len(calls) == 2 and calls[1] > calls[0]


def test_funding_is_widened_and_sliced_rather_than_refetched(monkeypatch):
    calls = []
    rows = [{"time": t} for t in range(0, 10_000, 1_000)]
    import pathia.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles", lambda *a: [])
    monkeypatch.setattr(hl_client, "fetch_funding_history",
                        lambda coin, lo, hi: calls.append((lo, hi)) or rows)

    _, funding = rec._live_fetchers()
    inner = funding("BTC", 2_000, 5_000)
    assert [r["time"] for r in inner] == [2_000, 3_000, 4_000, 5_000]
    funding("BTC", 3_000, 4_000)                # inside the cached range
    assert len(calls) == 1
    # a window outside the cache refetches ONCE, widened to the union — never
    # widened to now, which made the API pull months of history per coin and
    # turned 85s of funding into 449s on a single book (measured 2026-08-04)
    funding("BTC", 1_000, 9_000)
    assert len(calls) == 2 and calls[1] == (1_000, 9_000)
    funding("BTC", 1_500, 8_000)
    funding("BTC", 2_500, 6_000)
    assert len(calls) == 2, "a nested window refetched"


def test_the_action_card_folds_the_named_setups(client):
    """HL alone emits 21 actions. A 21-item wall under a header that says
    'read this first' is not a briefing — the rules and the refusals stay
    open, the per-coin WATCH list folds behind a count."""
    body = client.get("/trends").text
    assert "named setups</span>" in body
    assert "const sorted = acts.slice();" in body        # source order, not by kind
    assert "PB_ORDER" not in body                        # the sort is gone, not bypassed


def test_the_action_card_shows_which_sector_each_line_belongs_to(client):
    """crypto and xyz actions are interleaved; without the tag the reader
    cannot tell which tape a line is about."""
    body = client.get("/trends").text
    assert "a.tag && a.tag !== 'method'" in body


def test_stale_data_says_so_on_the_page(client):
    """Stale numbers that look fresh are the ones that mislead. With a single
    lane there is no other tab to carry an amber dot, so the masthead meta is
    the only place left that can say it — it must actually say it."""
    body = client.get("/trends").text
    assert 'id="lane-meta"' in body
    assert "STALE" in body


def test_every_element_the_page_scripts_reach_for_exists(client):
    """A renamed id fails silently in the browser — `$('#gone')` is null and the
    block it feeds just never renders. Catch it here instead of on the tab."""
    import re
    body = client.get("/trends").text
    defined = set(re.findall(r'id="([\w-]+)"', body))
    used = (set(re.findall(r"\$\('#([\w-]+)'\)", body))
            | set(re.findall(r"getElementById\('([\w-]+)'\)", body)))
    assert not (used - defined), f"script reaches for missing ids: {sorted(used - defined)}"
    # ids built at runtime from the lane map must resolve too
    lanes = re.search(r"const REFRESH_BTN = \{(.*?)\};", body, re.S).group(1)
    for btn in re.findall(r"'([\w-]+)'", lanes):
        assert btn in defined, f"REFRESH_BTN points at a missing button: {btn}"


def test_every_endpoint_the_page_calls_is_registered(client):
    """The page is a static shell over the JSON API. A route renamed on the
    server and not in the template is a button that 404s."""
    import re
    body = client.get("/trends").text
    called = set(re.findall(r"'(/api/dashboard/trends/[\w/{}?=-]*)'", body))
    registered = {r.path for r in client.app.routes if hasattr(r, "path")}
    for raw in called:
        path = raw.split("?")[0].rstrip("/")
        if not path:
            continue                                  # the lane-prefix literal
        # the template concatenates the lane, so match on the templated route
        candidates = {path, path.replace("/hl", "/{lane}"),
                      re.sub(r"/trends/[\w-]+/", "/trends/{lane}/", path)}
        assert candidates & registered or any(
            p.startswith(path) for p in registered), f"page calls unregistered {raw}"


def test_the_smoke_script_contract_matches_the_lanes_the_tab_serves():
    """`scripts/smoke_trends.py` asserts a field contract per lane. If a lane is
    added or renamed, the smoke must know about it or it silently checks less."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "smoke_trends", os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     "scripts", "smoke_trends.py"))
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    from pathia import dashboard as dash
    assert set(smoke.LANE_CONTRACT) == set(dash._TREND_LANES)
    assert set(smoke.LANES) == set(dash._TREND_LANES)
    for lane, keys in smoke.LANE_CONTRACT.items():
        assert "playbook" in keys, f"{lane} smoke contract forgot the action layer"


def test_css_build_is_current():
    """The committed app.css must match what the builder emits for the
    templates as they stand — a new class in trends.html without a rebuild
    would render the page unstyled."""
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "scripts/build_static_css.py", "--check"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# ── the Polymarket removal (2026-08-29) ──────────────────────────────────────
# The prediction-market side was deleted, not disabled: the mechanical edge was
# never there (forecasts n=263 against a market already at Brier 0.088, and the
# arb crossed 2 of 276 sampled windows). These are the tests that keep it gone —
# a half-removal that leaves a dead route or a dangling import is worse than
# either state, because the tab still looks alive.


def test_every_page_links_only_the_tabs_that_exist(client):
    """A nav link to a deleted page renders as a 404 the operator only finds by
    clicking. Every data-nav target on every served page must resolve."""
    pages = ("/", "/activity", "/news", "/analytics", "/trends")
    for path in pages:
        body = client.get(path).text
        for target in set(re.findall(r'data-nav="([^"]+)"', body)):
            assert client.get(target).status_code == 200, (
                f"{path} links data-nav={target!r}, which does not resolve")


def test_mutating_trend_routes_are_operator_gated(client):
    """The surviving write surface still refuses an unauthenticated caller."""
    for path, method in (("/api/dashboard/trends/hl/refresh", "POST"),
                         ("/api/dashboard/trends/hl/ai", "POST")):
        r = client.post(path) if method == "POST" else client.get(path)
        assert r.status_code in (401, 403, 503), f"{path} answered {r.status_code}"


def test_no_polymarket_route_survives(client):
    """Every route the prediction-market side owned must be gone, not merely
    unlinked — a live endpoint is a live code path."""
    for path in ("/predictions", "/api/dashboard/predictions",
                 "/api/dashboard/predictions/trades", "/api/dashboard/updown",
                 "/api/dashboard/updown/live", "/api/dashboard/trends/updown",
                 "/api/dashboard/trends/updown/live",
                 "/api/dashboard/trends/arb/preflight"):
        assert client.get(path).status_code == 404, f"{path} still answers"
    for path in ("/api/dashboard/trends/arb/fire",
                 "/api/dashboard/updown/analyze",
                 "/api/dashboard/predictions/analyze"):
        assert client.post(path).status_code == 404, f"{path} still answers"


def test_no_module_imports_the_deleted_polymarket_package():
    """A dangling `from services.polymarket_scout import ...` inside a lazily
    imported function does not fail at collection — it fails in production, on
    the request that first reaches it."""
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for sub in ("pathia", "scripts", "services"):
        for f in (root / sub).rglob("*.py"):
            if "polymarket_scout" in f.read_text():
                offenders.append(str(f.relative_to(root)))
    assert not offenders, f"still reference the deleted package: {offenders}"


# ── machine constants must not reach the reader ─────────────────────────────

def test_trend_labels_are_spelled_out_in_the_narrative():
    """STRONG_UP inside a sentence reads as log output. The playbook writes
    prose, so the label has to be a word."""
    from services.trend_engine.playbook import label_words

    assert label_words("STRONG_UP") == "strongly up"
    assert label_words("STRONG_DOWN") == "strongly down"
    assert label_words("CHOP") == "chopping"
    # an unmapped constant still degrades to something readable
    assert label_words("SOME_NEW_LABEL") == "some new label"
    assert label_words(None) == ""


def test_no_playbook_line_interpolates_a_raw_label():
    import pathlib as _p
    import re as _re

    src = (_p.Path(__file__).resolve().parent.parent
           / "services" / "trend_engine" / "playbook.py").read_text()
    assert not _re.search(r"\{r\.get\(['\"]label['\"]\)\}", src), (
        "a raw trend constant is interpolated into narrative copy")


def test_the_trends_table_labels_its_flags(client):
    """Flag chips shipped their raw codes — EMA_STACK_BULL, BREAKOUT_7D — and
    as adjacent chips they ran together when the page was copied."""
    page = client.get("/trends").text
    assert "FLAG_LABEL" in page and "flagLabel(fl.code)" in page
    assert "esc(fl.code)}</span>" not in page, "still renders the raw code"


def test_no_module_interpolates_a_raw_trend_label_into_prose():
    """A SCREAMING_CONSTANT inside a sentence reads as log output. Every place
    that writes narrative copy has to spell the label."""
    import pathlib as _p
    import re as _re

    root = _p.Path(__file__).resolve().parent.parent / "services" / "trend_engine"
    offenders = []
    for f in sorted(root.glob("*.py")):
        if f.name == "ai.py":          # prompt text, read by a model not a person
            continue
        src = f.read_text()
        for m in _re.finditer(r"f\"[^\"]*\{[a-z]\W*\[?['\"]?label['\"]?\]?\.?g?e?t?\([^)]*\)?\}[^\"]*\"",
                              src):
            if "label_words" not in m.group(0):
                offenders.append(f"{f.name}: {m.group(0)[:70]}")
    assert not offenders, "raw trend constants in narrative copy:\n" + "\n".join(offenders)
