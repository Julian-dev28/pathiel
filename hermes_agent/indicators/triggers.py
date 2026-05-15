"""Trigger detection functions.

Ported verbatim from lib/agent/triggers.ts.
Computes pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength,
and compositeScore from OHLCV candles.
"""

from __future__ import annotations

from __future__ import annotations

import math
from typing import Any, Dict, List

from hermes_agent.indicators.math import ema, sma, atr as calc_atr, rsi, adx

# Helper to handle both dict and Candle objects
def _get(c, key):
    if isinstance(c, dict):
        return c.get(key, 0)
    return getattr(c, key, 0)

# ── Trigger hit type ─────────────────────────────────────────────────────
# Re-exports for backwards compat
TriggerHit = Dict[str, Any]  # {name, score, reason, fired}

def pct_move_spike(candles: List[Dict[str, Any]], sigma_threshold: float = 3) -> TriggerHit:
    """Current-bar return z-score vs trailing 96-bar std.

    Ported verbatim from lib/agent/triggers.ts.
    """
    if len(candles) < 3:
        return {"name": "pctMoveSpike", "score": 0, "reason": "flat", "fired": False}

    returns = []
    for i in range(1, len(candles)):
        returns.append((_get(candles[i], "c") - _get(candles[i - 1], "c")) / _get(candles[i - 1], "c"))

    current_return = returns[-1]
    prior = returns[:-1][-96:]  # up to 96 trailing bars

    if len(prior) < 2:
        return {"name": "pctMoveSpike", "score": 0, "reason": "flat", "fired": False}

    mean = sum(prior) / len(prior)
    variance = sum((v - mean) ** 2 for v in prior) / len(prior)
    std = variance ** 0.5

    if std == 0:
        return {"name": "pctMoveSpike", "score": 0, "reason": "flat", "fired": False}

    z_score = abs(current_return - mean) / std
    fired = z_score >= sigma_threshold
    score = min(10, max(0, z_score))
    direction = "up" if current_return > mean else "down"

    return {
        "name": "pctMoveSpike",
        "score": score if fired else 0,
        "reason": f"{z_score:.1f}σ return spike {direction}" if fired else "flat",
        "fired": fired,
    }


def volume_spike(candles: List[Dict[str, Any]], sigma_threshold: float = 3) -> TriggerHit:
    """Current volume z-score vs 20-bar rolling.

    Ported verbatim from lib/agent/triggers.ts.
    """
    vols = [_get(c, "v") for c in candles]
    if len(vols) < 21:
        return {"name": "volumeSpike", "score": 0, "reason": "flat", "fired": False}

    window = vols[-21:-1]
    current_vol = vols[-1]

    # Skip if >50% of volume samples are 0 (sparse market)
    zero_count = sum(1 for v in window if v == 0)
    if zero_count > len(window) * 0.5:
        return {"name": "volumeSpike", "score": 0, "reason": "sparse", "fired": False}

    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    std = variance ** 0.5

    if std == 0:
        return {"name": "volumeSpike", "score": 0, "reason": "flat", "fired": False}

    z_score = abs(current_vol - mean) / std
    fired = z_score >= sigma_threshold
    score = min(10, max(0, z_score))

    return {
        "name": "volumeSpike",
        "score": score if fired else 0,
        "reason": f"{z_score:.1f}σ volume spike" if fired else "flat",
        "fired": fired,
    }


def breakout(candles: List[Dict[str, Any]], lookback: int = 48) -> TriggerHit:
    """Breakout detection: prior range high/low over lookback bars.

    Ported verbatim from lib/agent/triggers.ts.
    """
    if len(candles) < lookback + 2:
        return {"name": "breakout", "score": 0, "reason": "flat", "fired": False}

    current = candles[-1]
    prior_start = len(candles) - lookback - 1
    prior_end = len(candles) - 1

    prior_high = float("-inf")
    prior_low = float("inf")
    for i in range(prior_start, prior_end):
        if _get(candles[i], "h") > prior_high:
            prior_high = _get(candles[i], "h")
        if _get(candles[i], "l") < prior_low:
            prior_low = _get(candles[i], "l")

    if _get(current, "c") > prior_high:
        pct_break = (_get(current, "c") - prior_high) / prior_high * 100
        return {
            "name": "breakout",
            "score": min(10, max(0, pct_break)),
            "reason": f"breakout above {lookback}-bar high",
            "fired": True,
        }

    if _get(current, "c") < prior_low:
        pct_break = (prior_low - _get(current, "c")) / prior_low * 100
        return {
            "name": "breakout",
            "score": min(10, max(0, pct_break)),
            "reason": f"breakout below {lookback}-bar low",
            "fired": True,
        }

    # Score proportional to distance from nearest range edge
    dist_up = prior_high - _get(current, "c")
    dist_down = _get(current, "c") - prior_low
    closest = min(dist_up, dist_down)
    range_size = prior_high - prior_low
    score = max(0, (1 - closest / range_size)) * 5 if range_size > 0 else 0

    return {
        "name": "breakout",
        "score": score,
        "reason": "inside range",
        "fired": False,
    }


def range_compression(
    candles: List[Dict[str, Any]],
    bb_length: int = 20,
    bb_std_dev: float = 2,
) -> TriggerHit:
    """Bollinger Band squeeze detection: current bandwidth vs last 100 bars.

    Ported verbatim from lib/agent/triggers.ts.
    """
    closes = [_get(c, "c") for c in candles]
    if len(closes) < bb_length + 1:
        return {"name": "rangeCompression", "score": 0, "reason": "flat", "fired": False}

    mid = sma(closes, bb_length)
    upper = [float("nan")] * len(closes)
    lower = [float("nan")] * len(closes)

    for i in range(len(closes)):
        if not math.isfinite(mid[i]):
            continue
        sum_sq = 0
        count = 0
        for j in range(i - bb_length + 1, i + 1):
            if j < 0:
                continue
            sum_sq += (closes[j] - mid[i]) ** 2
            count += 1
        if count < bb_length:
            continue
        sd = (sum_sq / bb_length) ** 0.5
        upper[i] = mid[i] + sd * bb_std_dev
        lower[i] = mid[i] - sd * bb_std_dev

    # Compute bandwidths
    bandwidths = []
    for i in range(len(closes)):
        if (
            math.isfinite(mid[i])
            and math.isfinite(upper[i])
            and math.isfinite(lower[i])
            and mid[i] != 0
        ):
            bandwidths.append((upper[i] - lower[i]) / abs(mid[i]))

    if len(bandwidths) < 2:
        return {"name": "rangeCompression", "score": 0, "reason": "flat", "fired": False}

    current_bw = bandwidths[-1]
    history = bandwidths[-100:]
    sorted_bw = sorted(history)

    # Percentile rank
    percentile = 0
    for i in range(len(sorted_bw)):
        if sorted_bw[i] < current_bw:
            percentile = ((i + 1) / len(sorted_bw)) * 100

    fired = percentile <= 10
    score = 10 * (1 - percentile / 100)

    return {
        "name": "rangeCompression",
        "score": min(10, score) if fired else 0,
        "reason": f"BB squeeze (P{percentile:.0f})" if fired else "BB normal",
        "fired": fired,
    }


def trend_strength(candles: List[Dict[str, Any]], adx_period: int = 14) -> TriggerHit:
    """Trend strength via ADX(14).

    Ported verbatim from lib/agent/triggers.ts.
    """
    if len(candles) < adx_period * 2 + 1:
        return {"name": "trendStrength", "score": 0, "reason": "flat", "fired": False}

    adx_values = adx(candles, adx_period)
    last_adx = adx_values[-1]

    if not __import__("math").isfinite(last_adx):
        return {"name": "trendStrength", "score": 0, "reason": "flat", "fired": False}

    fired = last_adx >= 25
    score = min(10, max(0, last_adx / 4))

    return {
        "name": "trendStrength",
        "score": score if fired else 0,
        "reason": f"ADX {last_adx:.1f} trending" if fired else "flat",
        "fired": fired,
    }


# ── Composite Scoring ─────────────────────────────────────────────────────────

def composite_score(hits: List[TriggerHit], weights: Dict[str, float]) -> float:
    """Weighted composite score from triggered hits.

    Only fires count. Divides by sum of weights for fired triggers only,
    so the score reflects the strength of the combination.
    Clamped 0-100.
    """
    fired_hits = [h for h in hits if h.get("fired")]
    if not fired_hits:
        return 0

    weighted_sum = sum(h["score"] * weights.get(h["name"], 0) for h in fired_hits)
    fired_weight = sum(weights.get(h["name"], 0) for h in fired_hits) or 1
    raw = (weighted_sum / fired_weight) * 10  # scale 0-100
    return max(0, min(100, raw))
