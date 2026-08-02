"""Next-week price projection + the walk-forward that keeps it honest.

The model is deliberately small: shrunken trend drift plus a lognormal band.
It is a BASELINE, not an edge. `walk_forward()` is the eval that reports what
it is actually worth — directional hit rate against a coin flip, p50 error
against a random walk (last price carried forward), and band coverage against
its own nominal 80%. If the hit rate is not clear of 50% the tab says so.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.trend_engine.metrics import (
    clamp, daily_returns, efficiency_ratio, log_slope, mean, norm_cdf,
    norm_ppf, stdev,
)

# Momentum decays. Cross-sectional work in this repo put honest 7d-momentum
# carry-through around a third of the trailing drift, so 0.35 is the shrink
# applied BEFORE the quality weight — never extrapolate a trend at full slope.
SHRINK = 0.35
# Hard cap: the projected drift may never exceed this many sigma of the horizon.
MAX_DRIFT_SIGMA = 1.5
VOL_WINDOW = 30
TREND_WINDOW = 7


def project(closes: Sequence[float], horizon_days: int = 7,
            shrink: float = SHRINK) -> Optional[Dict[str, Any]]:
    """Project `horizon_days` ahead from a daily close series.

    Returns p10/p50/p90 prices, the drift in percent, prob(up over horizon),
    and the confidence weight that produced them. None if the series is too
    short to fit a trend and a vol at all.
    """
    closes = [float(c) for c in closes if c and c > 0]
    if len(closes) < TREND_WINDOW + 2:
        return None
    px = closes[-1]

    slope_pct_day, r2 = log_slope(closes[-(TREND_WINDOW + 1):])
    eff = efficiency_ratio(closes[-(TREND_WINDOW + 1):])
    confidence = math.sqrt(max(0.0, r2) * max(0.0, eff))     # 0..1

    rets = daily_returns(closes[-(VOL_WINDOW + 1):])
    sigma_day = stdev(rets)
    if sigma_day <= 0:
        sigma_day = 0.02                                     # degenerate series floor
    sigma_h = sigma_day * math.sqrt(horizon_days)

    # log-drift, shrunk by decay and by how believable the trend shape is
    mu = math.log1p(slope_pct_day / 100.0) * horizon_days * shrink * confidence
    mu = clamp(mu, -MAX_DRIFT_SIGMA * sigma_h, MAX_DRIFT_SIGMA * sigma_h)

    z10 = norm_ppf(0.10)
    return {
        "px": px,
        "horizon_days": horizon_days,
        "p50": px * math.exp(mu),
        "p10": px * math.exp(mu + z10 * sigma_h),
        "p90": px * math.exp(mu - z10 * sigma_h),
        "drift_pct": (math.exp(mu) - 1.0) * 100.0,
        "sigma_h_pct": sigma_h * 100.0,
        "prob_up": norm_cdf(mu / sigma_h) if sigma_h > 0 else 0.5,
        "confidence": confidence,
        "slope_pct_day": slope_pct_day,
        "r2": r2,
        "efficiency": eff,
    }


def walk_forward(series_by_coin: Dict[str, Sequence[float]], horizon_days: int = 7,
                 step: Optional[int] = None, min_history: int = 33,
                 shrink: float = SHRINK) -> Dict[str, Any]:
    """Score `project()` on every anchor with a known outcome.

    For each coin and each anchor bar t (stepping by `step`, default = the
    horizon so windows do NOT overlap — overlapping anchors share outcome bars
    and inflate the significance of whatever they find), fit on closes [:t+1]
    and grade against close[t+horizon]. Three scores, each against its own
    null:

      dir_hit      vs 0.50            — is the sign informative at all
      mae_pct      vs mae_naive_pct   — is the point forecast better than
                                        "price stays where it is"
      coverage_80  vs 0.80            — are the bands honest

    Returns a dict shaped for direct JSON display. n is the number of graded
    anchors; anything under ~200 should be read as noise.
    """
    step = horizon_days if step is None else step
    hits = 0
    n = 0
    abs_err: List[float] = []
    abs_err_naive: List[float] = []
    inside = 0
    per_coin: Dict[str, Dict[str, float]] = {}
    anchors: List[Tuple[int, bool]] = []          # (bar index, was the call right)
    for coin, closes in series_by_coin.items():
        cs = [float(c) for c in closes if c and c > 0]
        if len(cs) < min_history + horizon_days:
            continue
        c_hits = c_n = 0
        for t in range(min_history, len(cs) - horizon_days, max(1, step)):
            f = project(cs[:t + 1], horizon_days, shrink=shrink)
            if not f:
                continue
            actual = cs[t + horizon_days]
            base = cs[t]
            if base <= 0:
                continue
            actual_up = actual > base
            pred_up = f["prob_up"] >= 0.5
            n += 1
            c_n += 1
            anchors.append((t, actual_up == pred_up))
            if actual_up == pred_up:
                hits += 1
                c_hits += 1
            abs_err.append(abs(f["p50"] - actual) / base * 100.0)
            abs_err_naive.append(abs(base - actual) / base * 100.0)
            if f["p10"] <= actual <= f["p90"]:
                inside += 1
        if c_n:
            per_coin[coin] = {"n": c_n, "dir_hit": round(c_hits / c_n, 4)}
    if not n:
        return {"n": 0, "status": "insufficient_history"}
    dir_hit = hits / n
    mae = mean(abs_err)
    mae_naive = mean(abs_err_naive)
    # binomial standard error on the hit rate; 2 SE is the "is this real" bar
    se = math.sqrt(0.25 / n)
    return {
        "n": n,
        "horizon_days": horizon_days,
        "dir_hit": round(dir_hit, 4),
        "dir_hit_se": round(se, 4),
        "dir_edge_sigma": round((dir_hit - 0.5) / se, 2) if se > 0 else 0.0,
        "mae_pct": round(mae, 3),
        "mae_naive_pct": round(mae_naive, 3),
        "mae_vs_naive_pct": round((mae - mae_naive) / mae_naive * 100.0, 2) if mae_naive > 0 else 0.0,
        "coverage_80": round(inside / n, 4),
        "beats_coinflip": bool((dir_hit - 0.5) / se >= 2.0) if se > 0 else False,
        "beats_randomwalk": bool(mae < mae_naive),
        "split_half": _split_half(anchors),
        "per_coin": per_coin,
        "status": "ok",
    }


def _split_half(anchors: Sequence[Tuple[int, bool]]) -> Dict[str, Any]:
    """Hit rate on the older half vs the newer half of the SAME anchor grid.

    The stability test that matters: a directional edge that flips sign between
    halves is a window artifact, not an edge. Split by bar index (identical
    grid both sides) — re-running the walk on truncated series instead would
    shift the anchor phase and manufacture a difference that isn't there.
    """
    if len(anchors) < 20:
        return {"status": "too_small"}
    idx = sorted({t for t, _ in anchors})
    cut = idx[len(idx) // 2]
    early = [ok for t, ok in anchors if t < cut]
    late = [ok for t, ok in anchors if t >= cut]
    if not early or not late:
        return {"status": "too_small"}
    he, hl = sum(early) / len(early), sum(late) / len(late)
    return {
        "cut_bar": cut,
        "early_n": len(early), "early_hit": round(he, 4),
        "late_n": len(late), "late_hit": round(hl, 4),
        "sign_stable": bool((he - 0.5) * (hl - 0.5) > 0),
    }


def consensus(reads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-coin forecasts into one market-level expectation.

    Volume-blind on purpose: this is breadth of *expectation*, so every coin in
    the scan gets one vote. Capital-weighted views belong to the regime read.
    """
    ups = [r for r in reads if (r.get("forecast") or {}).get("prob_up", 0.5) > 0.5]
    probs = [(r.get("forecast") or {}).get("prob_up") for r in reads
             if (r.get("forecast") or {}).get("prob_up") is not None]
    drifts = [(r.get("forecast") or {}).get("drift_pct") for r in reads
              if (r.get("forecast") or {}).get("drift_pct") is not None]
    return {
        "n": len(reads),
        "pct_up": round(len(ups) / len(reads) * 100.0, 1) if reads else 0.0,
        "mean_prob_up": round(mean(probs), 4) if probs else 0.5,
        "mean_drift_pct": round(mean(drifts), 3) if drifts else 0.0,
    }
