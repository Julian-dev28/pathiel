"""Trigger detection over OHLCV candles.

Computes pct-move spike, volume spike, breakout, range compression and
trend strength, plus a weighted composite score across them.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from hermes_trader.indicators.math import adx, atr, ema, rsi, sma, candle_val
from hermes_trader.models.types import Candle, TriggerHit


def pct_move_spike(candles: List[Candle], sigma_threshold: float = 3) -> TriggerHit:
    """Current-bar return z-score vs trailing 96-bar std."""
    if len(candles) < 3:
        return {"name": "pctMoveSpike", "score": 0, "reason": "flat", "fired": False}

    returns = []
    for i in range(1, len(candles)):
        returns.append((candle_val(candles[i], "c") - candle_val(candles[i - 1], "c")) / candle_val(candles[i - 1], "c"))

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


def volume_spike(candles: List[Candle], sigma_threshold: float = 3) -> TriggerHit:
    """Current volume z-score vs 20-bar rolling window."""
    vols = [candle_val(c, "v") for c in candles]
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


def breakout(candles: List[Candle], lookback: int = 48) -> TriggerHit:
    """Breakout detection against the prior range high/low over lookback bars."""
    if len(candles) < lookback + 2:
        return {"name": "breakout", "score": 0, "reason": "flat", "fired": False}

    current = candles[-1]
    prior_start = len(candles) - lookback - 1
    prior_end = len(candles) - 1

    prior_high = float("-inf")
    prior_low = float("inf")
    for i in range(prior_start, prior_end):
        if candle_val(candles[i], "h") > prior_high:
            prior_high = candle_val(candles[i], "h")
        if candle_val(candles[i], "l") < prior_low:
            prior_low = candle_val(candles[i], "l")

    if candle_val(current, "c") > prior_high:
        pct_break = (candle_val(current, "c") - prior_high) / prior_high * 100
        return {
            "name": "breakout",
            "score": min(10, max(0, pct_break)),
            "reason": f"breakout above {lookback}-bar high",
            "fired": True,
        }

    if candle_val(current, "c") < prior_low:
        pct_break = (prior_low - candle_val(current, "c")) / prior_low * 100
        return {
            "name": "breakout",
            "score": min(10, max(0, pct_break)),
            "reason": f"breakout below {lookback}-bar low",
            "fired": True,
        }

    # Score proportional to distance from nearest range edge
    dist_up = prior_high - candle_val(current, "c")
    dist_down = candle_val(current, "c") - prior_low
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
    candles: List[Candle],
    bb_length: int = 20,
    bb_std_dev: float = 2,
) -> TriggerHit:
    """Bollinger Band squeeze: current bandwidth percentile vs the last 100 bars."""
    closes = [candle_val(c, "c") for c in candles]
    if len(closes) < bb_length + 1:
        return {"name": "rangeCompression", "score": 0, "reason": "flat", "fired": False}

    mid = sma(closes, bb_length)
    upper = [float("nan")] * len(closes)
    lower = [float("nan")] * len(closes)

    for i in range(len(closes)):
        if not math.isfinite(mid[i]):
            continue
        sum_sq = 0.0
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

    percentile = 0.0
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


def trend_strength(candles: List[Candle], adx_period: int = 14) -> TriggerHit:
    """Trend strength via ADX(14)."""
    if len(candles) < adx_period * 2 + 1:
        return {"name": "trendStrength", "score": 0, "reason": "flat", "fired": False}

    adx_values = adx(candles, adx_period)
    last_adx = adx_values[-1]

    if not math.isfinite(last_adx):
        return {"name": "trendStrength", "score": 0, "reason": "flat", "fired": False}

    fired = last_adx >= 25
    score = min(10, max(0, last_adx / 4))

    return {
        "name": "trendStrength",
        "score": score if fired else 0,
        "reason": f"ADX {last_adx:.1f} trending" if fired else "flat",
        "fired": fired,
    }


def momentum_burst(
    candles: List[Candle],
    lookback: int = 2,
    pct_threshold: float = 4.0,
) -> TriggerHit:
    """Large cumulative price move over the last `lookback` bars.

    Unlike the z-score triggers, this fires on the raw % move regardless of how
    volatile the coin already is — so it still catches an explosive move once it
    is underway, when recent bars have already inflated the trailing std and
    pushed pct_move_spike's bar to fire out of reach.
    """
    if len(candles) < lookback + 1:
        return {"name": "momentumBurst", "score": 0, "reason": "flat", "fired": False}

    start = candle_val(candles[-lookback - 1], "c")
    end = candle_val(candles[-1], "c")
    if start == 0:
        return {"name": "momentumBurst", "score": 0, "reason": "flat", "fired": False}

    move_pct = (end - start) / start * 100
    fired = abs(move_pct) >= pct_threshold
    score = min(10, max(0, abs(move_pct) / pct_threshold * 5))  # 10 at 2x threshold
    direction = "up" if move_pct > 0 else "down"

    return {
        "name": "momentumBurst",
        "score": score if fired else 0,
        "reason": f"{move_pct:+.1f}% over {lookback} bars {direction}" if fired else "flat",
        "fired": fired,
    }


def _sustained_move_pct(candles: List[Candle], lookback: int) -> Optional[float]:
    """% change over the last `lookback` bars (close-to-close). None if too short."""
    if len(candles) < lookback + 1:
        return None
    start = candle_val(candles[-lookback - 1], "c")
    end = candle_val(candles[-1], "c")
    if start == 0:
        return None
    return (end - start) / start * 100


def uptrend_momentum(candles: List[Candle], lookback: int = 72, pct_threshold: float = 3.0) -> TriggerHit:
    """Sustained UPward move over `lookback` bars (default ~6h on 5m).

    Surfaces a coin in a steady uptrend that the fast spike triggers (which need
    a sharp 5m/2-bar move) miss. The symmetric counterpart to downtrend_momentum;
    together they remove the long-bias in surfacing — both directions get a
    sustained-trend signal so the AI sees up-movers (LONG) and down-movers (SHORT)
    alike. Used as a surfacing BYPASS (weight 0), so it never reaches the
    composite gate's denominator."""
    move = _sustained_move_pct(candles, lookback)
    if move is None:
        return {"name": "uptrendMomentum", "score": 0, "reason": "insufficient_history", "fired": False}
    fired = move >= pct_threshold
    score = min(10, max(0, move / pct_threshold * 5)) if pct_threshold > 0 else 0
    return {
        "name": "uptrendMomentum",
        "score": score if fired else 0,
        "reason": f"+{move:.1f}% over {lookback} bars (uptrend)" if fired else "flat",
        "fired": fired,
    }


def downtrend_momentum(candles: List[Candle], lookback: int = 72, pct_threshold: float = 3.0) -> TriggerHit:
    """Sustained DOWNward move over `lookback` bars (default ~6h on 5m).

    The bearish mirror of uptrend_momentum — surfaces a coin in a steady
    downtrend for SHORT research. This is what was missing: the existing weighted
    triggers (breakout/trendStrength/higherLows) are bullish-structured, so a coin
    grinding down -X% scored ~0 and never reached the AI. Surfacing BYPASS
    (weight 0); downstream the AI calls SHORT, the aligned-conf bar + $50M short
    floor + counter-regime gate adjudicate."""
    move = _sustained_move_pct(candles, lookback)
    if move is None:
        return {"name": "downtrendMomentum", "score": 0, "reason": "insufficient_history", "fired": False}
    fired = move <= -pct_threshold
    score = min(10, max(0, abs(move) / pct_threshold * 5)) if pct_threshold > 0 else 0
    return {
        "name": "downtrendMomentum",
        "score": score if fired else 0,
        "reason": f"{move:.1f}% over {lookback} bars (downtrend)" if fired else "flat",
        "fired": fired,
    }


def bearish_reversal_candle(candles: List[Candle], wick_body_ratio: float = 2.0,
                            context_lookback: int = 6, context_pct: float = 1.5) -> TriggerHit:
    """Bearish reversal candlestick at the TOP of a short advance — a shooting star
    (long upper wick rejecting higher prices) or a bearish engulfing bar. Surfaces
    a SHORT.

    Reversal candles only mean something AFTER an up-move, so a preceding advance of
    >= `context_pct` over `context_lookback` bars is required — otherwise every red
    bar in a downtrend would fire. This is the exhaustion/top signal the momentum
    triggers structurally miss. Surfacing signal: the AI + risk gates adjudicate."""
    if len(candles) < context_lookback + 2:
        return {"name": "bearishReversalCandle", "score": 0, "reason": "insufficient_history", "fired": False}
    o, h, l, c = (candle_val(candles[-1], k) for k in ("o", "h", "l", "c"))
    po, pc = candle_val(candles[-2], "o"), candle_val(candles[-2], "c")
    rng = h - l
    if rng <= 0:
        return {"name": "bearishReversalCandle", "score": 0, "reason": "flat", "fired": False}
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    ctx = _sustained_move_pct(candles[:-1], context_lookback)  # advance into the bar
    advanced = ctx is not None and ctx >= context_pct
    shooting_star = (body > 0 and upper_wick >= wick_body_ratio * body
                     and lower_wick <= body and body <= 0.4 * rng)
    bearish_engulf = pc > po and c < o and o >= pc and c <= po  # prior green, cur red engulfs
    if shooting_star:
        pattern, strength = "shooting_star", min(10.0, upper_wick / body * 2.5)
    elif bearish_engulf:
        prior_body = abs(po - pc)
        pattern = "bearish_engulfing"
        strength = min(10.0, abs(o - c) / prior_body * 5.0) if prior_body > 0 else 5.0
    else:
        pattern, strength = None, 0.0
    fired = bool(pattern) and advanced
    return {
        "name": "bearishReversalCandle",
        "score": strength if fired else 0,
        "reason": f"{pattern} after +{ctx:.1f}%" if fired else "flat",
        "fired": fired,
    }


def bullish_reversal_candle(candles: List[Candle], wick_body_ratio: float = 2.0,
                            context_lookback: int = 6, context_pct: float = 1.5) -> TriggerHit:
    """Bullish reversal candlestick at the BOTTOM of a short decline — a hammer
    (long lower wick rejecting lower prices) or a bullish engulfing bar. Surfaces
    a LONG.

    Mirror of bearish_reversal_candle: requires a preceding DECLINE of >=
    `context_pct` over `context_lookback` bars so it fires at exhaustion, not on
    every green bar in an uptrend. Surfacing signal: the AI + risk gates adjudicate."""
    if len(candles) < context_lookback + 2:
        return {"name": "bullishReversalCandle", "score": 0, "reason": "insufficient_history", "fired": False}
    o, h, l, c = (candle_val(candles[-1], k) for k in ("o", "h", "l", "c"))
    po, pc = candle_val(candles[-2], "o"), candle_val(candles[-2], "c")
    rng = h - l
    if rng <= 0:
        return {"name": "bullishReversalCandle", "score": 0, "reason": "flat", "fired": False}
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    ctx = _sustained_move_pct(candles[:-1], context_lookback)  # decline into the bar
    declined = ctx is not None and ctx <= -context_pct
    hammer = (body > 0 and lower_wick >= wick_body_ratio * body
              and upper_wick <= body and body <= 0.4 * rng)
    bullish_engulf = pc < po and c > o and o <= pc and c >= po  # prior red, cur green engulfs
    if hammer:
        pattern, strength = "hammer", min(10.0, lower_wick / body * 2.5)
    elif bullish_engulf:
        prior_body = abs(po - pc)
        pattern = "bullish_engulfing"
        strength = min(10.0, abs(c - o) / prior_body * 5.0) if prior_body > 0 else 5.0
    else:
        pattern, strength = None, 0.0
    fired = bool(pattern) and declined
    return {
        "name": "bullishReversalCandle",
        "score": strength if fired else 0,
        "reason": f"{pattern} after {ctx:.1f}%" if fired else "flat",
        "fired": fired,
    }


def rsi_divergence(candles: List[Candle], rsi_period: int = 14, lookback: int = 48,
                   min_rsi_gap: float = 3.0, min_extreme_atr: float = 0.25,
                   bull_rsi_max: float = 45.0, bear_rsi_min: float = 55.0) -> TriggerHit:
    """RSI divergence — price makes a NEW extreme while RSI refuses to.

    Bullish (LONG): price prints a lower low vs an earlier low in the window but
    RSI prints a HIGHER low — momentum is fading into the sell-off, the classic
    entry confirmation a hammer/engulfing bar alone lacks. Bearish (SHORT) is the
    mirror: higher high in price, lower high in RSI.

    Detector shape: split the `lookback` window in half, take the price extreme
    (low for bull / high for bear) in each half, and compare the RSI values AT
    those extremes. Guards so it can't fire on noise:
      - the new price extreme must exceed the old one by >= `min_extreme_atr` x
        ATR (scale-free, same convention as band_snapback's poke guard);
      - the RSI extremes must differ by >= `min_rsi_gap` points in the
        divergence direction;
      - zone filter: bullish needs the older RSI low <= `bull_rsi_max` (a real
        washout), bearish needs the older RSI high >= `bear_rsi_min`;
      - the new extreme must sit in the RECENT half (it does by construction)
        and the RSI series must be warm over both extremes.

    Surfacing signal (weight 0 bypass): the AI + risk gates adjudicate.
    """
    name = "rsiDivergence"
    if len(candles) < max(lookback, rsi_period * 2) + 2:
        return {"name": name, "score": 0, "reason": "insufficient_history", "fired": False}

    rsi_arr = rsi(candles, rsi_period)
    atr_arr = atr(candles, 14)
    a = atr_arr[-1]
    if not math.isfinite(a) or a <= 0:
        return {"name": name, "score": 0, "reason": "no_atr", "fired": False}

    # Window = last `lookback` bars; split into older/younger halves.
    start = len(candles) - lookback
    mid = start + lookback // 2

    def _extreme(lo: int, hi: int, key: str) -> Optional[int]:
        """Index of the min (key='l') or max (key='h') value in [lo, hi)."""
        best_i, best_v = None, None
        for i in range(lo, hi):
            v = candle_val(candles[i], key)
            if best_v is None or (v < best_v if key == "l" else v > best_v):
                best_i, best_v = i, v
        return best_i

    # Bull side: lowest lows in each half.
    lo1 = _extreme(start, mid, "l")
    lo2 = _extreme(mid, len(candles), "l")
    if lo1 is not None and lo2 is not None:
        r1, r2 = rsi_arr[lo1], rsi_arr[lo2]
        if math.isfinite(r1) and math.isfinite(r2):
            p1, p2 = candle_val(candles[lo1], "l"), candle_val(candles[lo2], "l")
            if (p2 < p1 - min_extreme_atr * a          # price LOWER low
                    and r2 > r1 + min_rsi_gap          # RSI HIGHER low
                    and r1 <= bull_rsi_max):           # older low was a washout
                gap = r2 - r1
                return {
                    "name": name,
                    "score": min(10.0, gap / min_rsi_gap * 5.0),
                    "reason": (f"long — bullish RSI divergence: price lower low "
                               f"{(p2 - p1) / p1 * 100:.1f}% beyond prior, RSI "
                               f"{r2:.0f} vs {r1:.0f} (+{gap:.1f})"),
                    "fired": True,
                }

    # Bear side: highest highs in each half.
    hi1 = _extreme(start, mid, "h")
    hi2 = _extreme(mid, len(candles), "h")
    if hi1 is not None and hi2 is not None:
        r1, r2 = rsi_arr[hi1], rsi_arr[hi2]
        if math.isfinite(r1) and math.isfinite(r2):
            p1, p2 = candle_val(candles[hi1], "h"), candle_val(candles[hi2], "h")
            if (p2 > p1 + min_extreme_atr * a          # price HIGHER high
                    and r2 < r1 - min_rsi_gap          # RSI LOWER high
                    and r1 >= bear_rsi_min):           # older high was euphoric
                gap = r1 - r2
                return {
                    "name": name,
                    "score": min(10.0, gap / min_rsi_gap * 5.0),
                    "reason": (f"short — bearish RSI divergence: price higher high "
                               f"{(p2 - p1) / p1 * 100:.1f}% beyond prior, RSI "
                               f"{r2:.0f} vs {r1:.0f} (-{gap:.1f})"),
                    "fired": True,
                }

    return {"name": name, "score": 0, "reason": "flat", "fired": False}


def volume_buildup_1h(candles: List[Candle], ratio_threshold: float = 2.5) -> TriggerHit:
    """Notional-volume surge in the last 4h vs the prior 20h baseline.

    Catches accumulation phases where size is loading into a market before
    price has moved much. Empirically present in 8/10 of the +10% movers
    we missed yesterday (HMSTR 10×, SEI 7.6×, DYDX 6.2×, JTO 21×, ...).
    Needs 1h candles; returns flat if input is anything else or too short.
    """
    if len(candles) < 24:
        return {"name": "volumeBuildup1h", "score": 0, "reason": "insufficient_history", "fired": False}
    recent = sum(candle_val(c, "v") * candle_val(c, "c") for c in candles[-4:])
    prior = sum(candle_val(c, "v") * candle_val(c, "c") for c in candles[-24:-4]) / 20
    if prior <= 0:
        return {"name": "volumeBuildup1h", "score": 0, "reason": "no_baseline", "fired": False}
    ratio = (recent / 4) / prior
    fired = ratio >= ratio_threshold
    score = min(10, ratio / ratio_threshold * 5)  # 10 at 2× threshold
    return {
        "name": "volumeBuildup1h",
        "score": score if fired else 0,
        "reason": f"4h vol {ratio:.1f}× prior 20h baseline" if fired else f"vol {ratio:.1f}× (need {ratio_threshold:.1f}×)",
        "fired": fired,
    }


def trend_flip_1h(candles: List[Candle], lookback_bars: int = 3) -> TriggerHit:
    """1h EMA8 crossed above EMA21 within the last `lookback_bars` bars.

    Catches the inflection moment when a slow downtrend turns. By design
    fires only on UP crosses — a counter-regime LONG bypass is most useful
    when the coin's own 1h trend is actually flipping bullish.
    """
    if len(candles) < 30:
        return {"name": "trendFlip1h", "score": 0, "reason": "insufficient_history", "fired": False}
    closes = [candle_val(c, "c") for c in candles]
    e8 = ema(closes, 8)
    e21 = ema(closes, 21)
    if len(e8) < lookback_bars + 1 or len(e21) < lookback_bars + 1:
        return {"name": "trendFlip1h", "score": 0, "reason": "insufficient_history", "fired": False}
    # Look for a cross in the last N bars: prior bar e8<=e21, current bar e8>e21
    for i in range(-lookback_bars, 0):
        prev_diff = e8[i - 1] - e21[i - 1]
        cur_diff = e8[i] - e21[i]
        if prev_diff <= 0 and cur_diff > 0:
            bars_ago = -i
            return {
                "name": "trendFlip1h",
                "score": 8 if bars_ago == 0 else max(4, 8 - bars_ago * 2),
                "reason": f"EMA8/21 cross up {bars_ago}h ago",
                "fired": True,
            }
    return {"name": "trendFlip1h", "score": 0, "reason": "no recent cross", "fired": False}


def higher_lows_1h(candles: List[Candle], required: int = 4) -> TriggerHit:
    """At least `required` of the last 6 1h closes printed a higher low.

    Pure structure signal — accumulation patterns where each pullback
    holds higher than the prior. WLFI and GRASS had 5/5 yesterday before
    their breakouts. Independent of price direction over the window
    (works during consolidation).
    """
    if len(candles) < 7:
        return {"name": "higherLows1h", "score": 0, "reason": "insufficient_history", "fired": False}
    lows = [candle_val(c, "l") for c in candles[-7:]]
    higher = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    fired = higher >= required
    score = min(10, higher / 6 * 10)
    return {
        "name": "higherLows1h",
        "score": score if fired else 0,
        "reason": f"{higher}/6 higher lows" if fired else f"{higher}/6 (need {required})",
        "fired": fired,
    }


def momentum_continuation_1h(
    candles: List[Candle],
    min_trend_pct: float = 8.0,
    max_pullback_pct: float = 6.0,
) -> TriggerHit:
    """Sustained multi-hour uptrend with an orderly pullback (continuation setup).

    LEAK #2 fix: the spike / breakout / burst triggers all fire on FRESH moves, so
    a coin that is already well up over many hours and is now CONSOLIDATING prints
    no signal and gets missed — even while it sits on the 24h movers leaderboard.
    This trigger catches that state: EMA-stacked (ema9 > ema21, price > ema21),
    gained >= `min_trend_pct` over the last ~12 1h bars, and pulled back
    <= `max_pullback_pct` from the window high (orderly rest, not a blow-off top
    and not a breakdown).

    LONG-biased by construction (requires an up move), so it should only be enabled
    when the macro regime is up/neutral; the counter-trend gate is the backstop.
    Needs 1h candles; returns flat if too short.
    """
    if len(candles) < 24:
        return {"name": "momentumContinuation1h", "score": 0, "reason": "insufficient_history", "fired": False}
    closes = [candle_val(c, "c") for c in candles]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    cur = closes[-1]
    window = closes[-12:]
    base = window[0]
    hi = max(window)
    if base <= 0 or hi <= 0:
        return {"name": "momentumContinuation1h", "score": 0, "reason": "flat", "fired": False}
    trend_pct = (cur - base) / base * 100
    pullback_pct = (hi - cur) / hi * 100
    stacked = e9[-1] > e21[-1] and cur > e21[-1]
    fired = stacked and trend_pct >= min_trend_pct and 0 <= pullback_pct <= max_pullback_pct
    score = min(10, max(0, trend_pct / 3)) if fired else 0
    return {
        "name": "momentumContinuation1h",
        "score": score,
        "reason": (f"+{trend_pct:.1f}% 12h uptrend, {pullback_pct:.1f}% pullback, EMA-stacked"
                   if fired else f"trend {trend_pct:+.1f}% / pullback {pullback_pct:.1f}% / stacked={stacked}"),
        "fired": fired,
    }


def _band_ma(candles: List[Candle], span: int, ma_type: str = "ema"):
    """Rolling moving average of the HIGHs (upper edge) and LOWs (lower edge).

    `span` is the MA length (bars). A SHORT span makes the band hug price
    tightly (low lag); a LONG span smooths it out (high lag). Effective EMA
    lag is roughly (span-1)/2 bars.

    Returns (up_ma, lo_ma): each a list with one band-edge value per bar for
    the WHOLE input (leading NaNs where the MA is not yet warm — SMA only;
    EMA is seeded at the first bar). The band CURVES with price — it is a
    moving average, not a single straight fitted line. ma_type: 'ema' | 'sma'.
    """
    highs = [candle_val(c, "h") for c in candles]
    lows = [candle_val(c, "l") for c in candles]
    fn = ema if ma_type == "ema" else sma
    return fn(highs, span), fn(lows, span)


def _project_band_edge(edge_series: List[float]) -> float:
    """Linearly project the last true band-edge reading forward ONE bar.

    The MA edge only has TRUE readings up to the bar before the poke. Judging
    a poke on bar i against the bar i-1 reading uses a stale position
    whenever the band has a gradient — which is exactly the residual lag
    `band_span` tuning can't fully remove (the gate still allows a drifting
    band in chop). This extends the band's last true step one bar:

        slope = edge[-1] - edge[-2]
        projected = edge[-1] + slope

    Identical for EMA and SMA bands, and it uses ONLY true readings (never
    the poke bar's own high/low — a band edge that included the poking wick
    would make the "did it cross" test circular). A flat band projects to
    itself; a band trending at +0.1/bar projects 0.1 above its last reading.
    """
    if len(edge_series) < 2:
        return float(edge_series[-1]) if edge_series else float("nan")
    return edge_series[-1] + (edge_series[-1] - edge_series[-2])


def band_snapback(
    candles: List[Candle],
    band_span: int = 16,
    max_drift_pct: float = 1.5,
    min_poke_atr: float = 0.5,
    ma_type: str = "ema",
    window: Optional[int] = None,
    max_project_atr: Optional[float] = 0.25,
    include_partial: bool = True,
    current_px: Optional[float] = None,
) -> TriggerHit:
    """Wick poke-out + snapback into a moving-average band (fade the poke).

    `band_span` is the ONE window of the whole trigger. The band edges are
    the rolling `ma_type` average of the HIGHs (upper edge) and LOWs (lower
    edge) over `band_span` bars — a CURVED band that hugs price, not a single
    straight fitted line. The drift/direction verdict ("trending" chop gate)
    and the ATR baseline operate over the SAME `band_span` bars — the band's
    own window, by design. There is deliberately no separate longer lookback:
    a band drifting over its own window IS the trend the fade must not fight.
    The old `window` parameter (a separate, longer drift-reference lookback)
    is deprecated and ignored.

    A poke is judged against the band edge AT the poke bar: the last true MA
    reading (bar before the poke — no lookahead) is linearly projected one
    bar forward at the edge's own last gradient (the last two true band
    values extended), so a drifting band is judged at its current position,
    not a stale one. The projection never uses the poking wick itself.
    The drift gate still uses the true lagged readings.

    The projection is capped at `max_project_atr` * ATR of movement per bar
    (default 0.25): a 1-bar slope is genuinely unreliable in the
    accelerating-band tail, and measured overshoot there maxes at ~0.5 ATR
    while the poke threshold is 0.5 ATR, so the cap is invisible in normal
    bars and only bites in the tail. `None`/0 disables it.

    The setup is only valid in CHOP: the drift gate vetoes when the band edge
    itself has drifted more than `max_drift_pct` over the band's own window
    (`band_span` bars). In a trend the band follows price, so a poke-out is
    CONTINUATION, not mean-reversion — the drift gate keeps this from fading
    breakouts. Because the drift is measured over the band's OWN window (no
    separate longer lookback), a trend that moves the band within its window
    vetoes the fade; the drift can only be missed while the band is flat
    over its own window.

    In chop, a bar whose wick pierces the band edge by >= `min_poke_atr` * ATR
    while the price closes (or currently sits) back INSIDE the band proposes a
    mean-reversion: LONG on a lower poke, SHORT on an upper poke.

    Surfacing BYPASS (weight 0, like uptrend/downtrendMomentum): the AI +
    risk gates adjudicate direction/execution. Interval is configured by the
    caller (15m/1h are the primary timeframes; 5m works too).

    include_partial=True (live scan): the last candle is the in-progress bar —
    its close is the current price; the poke bar is the last CLOSED bar
    (candles[-2]). include_partial=False (backtest replay): every candle is
    closed, the poke bar is candles[-1], and `current_px` (default: the poke
    bar's close) is the price at the decision moment.
    """
    name = "bandSnapback"
    flat = {"name": name, "score": 0, "reason": "flat", "fired": False}
    if window is not None:
        import logging
        logging.getLogger(__name__).warning(
            "band_snapback: the `window` parameter is deprecated and ignored "
            f"(got {window}) — the band window is `band_span` only")
    span = max(2, int(band_span))

    # 2*span bars: the drift reference sits `span` bars back in the fit, so
    # the MA must be warm there (exactly `span` bars of history — the band's
    # own window), and the ATR baseline runs over the same span.
    need = 2 * span + 1 + (1 if include_partial else 0)
    if len(candles) < need:
        return {**flat, "reason": "insufficient_history"}

    if include_partial:
        poke = candles[-2]
        px = candle_val(candles[-1], "c")
        fit = candles[-(2 * span + 2):-2]
    else:
        poke = candles[-1]
        px = current_px if current_px is not None else candle_val(poke, "c")
        fit = candles[-(2 * span + 1):-1]

    if px <= 0 or len(fit) < 2 * span:
        return {**flat, "reason": "insufficient_history"}

    # MA edges at the poke-bar edge (fit[-1]) and the drift reference
    # (band_span bars back — the band's own window).
    up_ma, lo_ma = _band_ma(fit, span, ma_type)
    upper_edge, lower_edge = up_ma[-1], lo_ma[-1]
    if not math.isfinite(upper_edge) or not math.isfinite(lower_edge):
        return {**flat, "reason": "ma_not_warm"}
    upper_ref, lower_ref = up_ma[-1 - span], lo_ma[-1 - span]

    # De-lagged band edges at the POKING bar: project the last true reading
    # one bar forward at the edge's own last gradient (linear extrapolation
    # of the last two true MA values — no poke-bar data, no MA-type branching).
    # The poke + snapback check below uses THESE; the DRIFT GATE still uses
    # the true lagged values (how far the band actually moved).
    upper_proj = _project_band_edge(up_ma)
    lower_proj = _project_band_edge(lo_ma)

    mid = candle_val(poke, "c") or px
    if mid <= 0 or upper_edge <= lower_edge:
        return {**flat, "reason": "degenerate_band"}

    # CHOP GATE: the band itself must be near-flat over the window, else a
    # poke-out is continuation in a trend, not a snapback setup.
    drift_pct = max(abs(upper_edge - upper_ref), abs(lower_edge - lower_ref)) / mid * 100
    if drift_pct > max_drift_pct:
        # Directional context for the LLM (silent but informative): the drift
        # gate vetoes the FADE because the band is trending over its OWN
        # window — so short-term mean-reversion pressure points WITH the
        # band's drift, and going against it is a counter-trend scalp. Keep
        # "trending" in the string (near-miss logging and tests key off
        # it); the sign + band position are what the research prompt renders
        # as band context.
        drift_signed = ((upper_edge - upper_ref) + (lower_edge - lower_ref)) / 2 / mid * 100
        direction = "UP" if drift_signed >= 0 else "DOWN"
        return {
            "name": name, "score": 0,
            "reason": (f"band trending {direction} ({drift_pct:.1f}% drift > "
                       f"{max_drift_pct:.1f}% over {span}-bar window; px "
                       f"{(mid / upper_edge - 1) * 100:+.1f}% vs upper edge, "
                       f"{(mid / lower_edge - 1) * 100:+.1f}% vs lower edge)"),
            "fired": False,
        }

    # ATR baseline runs over the band's OWN window (the same span), so the
    # poke-depth unit is the volatility of the window the poke operates on.
    # ATR(period) needs > period candles; fit[-span:] + poke = span+1 candles,
    # so cap the period at span (ATR(14) for span>=14, ATR(span) for tight
    # spans like the 15m span-8 overrides — keeps those from degrading to
    # no_atr / silent).
    a = atr(fit[-span:] + [poke], min(14, span))
    atr_val = a[-1]
    if not math.isfinite(atr_val) or atr_val <= 0:
        return {**flat, "reason": "no_atr"}
    min_depth = min_poke_atr * atr_val

    # Projection CAP: clamp the de-lagged delta to max_project_atr * ATR.
    # Measured on real 15m/1h data (gate-OK regime), the 1-bar extrapolation
    # overshoots the true edge at p99 by ~0.2 ATR and maxes at ~0.5 ATR —
    # so a 0.25 ATR cap is invisible in normal bars and bites only in the
    # accelerating-band tail, where a 1-bar slope is genuinely unreliable.
    # ATR<=0 already returned above, so the divisor is safe here.
    if max_project_atr and max_project_atr > 0:
        cap = max_project_atr * atr_val
        delta_up = upper_proj - upper_edge
        delta_lo = lower_proj - lower_edge
        if delta_up > cap:
            upper_proj = upper_edge + cap
        elif delta_up < -cap:
            upper_proj = upper_edge - cap
        if delta_lo > cap:
            lower_proj = lower_edge + cap
        elif delta_lo < -cap:
            lower_proj = lower_edge - cap

    ph, pl = candle_val(poke, "h"), candle_val(poke, "l")

    side = None
    depth = 0.0
    # Poke judged against the PROJECTED (de-lagged) edge at the poke bar;
    # snapback requires price back inside that same projected edge.
    if pl < lower_proj - min_depth:        # wick pierced below the lower band edge
        if px >= lower_proj:               # price back inside the band
            side, depth = "long", lower_proj - pl
    elif ph > upper_proj + min_depth:      # wick pierced above the upper band edge
        if px <= upper_proj:
            side, depth = "short", ph - upper_proj
    if side is None:
        return {**flat, "reason": (
            f"no snapback (drift {drift_pct:.1f}%, band edge "
            f"{lower_proj / mid * 100 - 100:+.2f}%/{upper_proj / mid * 100 - 100:+.2f}%, "
            f"px {px / mid * 100 - 100:+.2f}%)")}

    depth_atr = depth / atr_val
    score = min(10.0, depth_atr / max(min_poke_atr, 1e-9) * 3.3)  # 10 at ~3x threshold
    side_word = "lower" if side == "long" else "upper"
    return {
        "name": name,
        "score": score,
        "reason": (f"{side} — {side_word} wick {depth_atr:.1f}x ATR past "
                   f"projected {ma_type.upper()}/{span}-bar band edge "
                   f"(de-lagged 1 bar), snapped back inside "
                   f"(drift {drift_pct:.1f}%)"),
        "fired": True,
    }


def band_state(
    candles: List[Candle],
    band_span: int = 16,
    max_drift_pct: float = 1.5,
    ma_type: str = "ema",
    include_partial: bool = True,
    current_px: Optional[float] = None,
    drift_ref: Optional[int] = None,
    window: Optional[int] = None,
) -> Optional[Dict]:
    """Band drift state — the drift-gate half of `band_snapback`, extracted.

    `band_span` is the band's ONE window: the edges are the rolling MA over
    `band_span` bars and the drift/direction verdict is measured over that
    same window (no separate longer lookback — the old `window` parameter is
    deprecated and ignored).

    `drift_ref` (bars) optionally LENGTHENS only the drift-reference LAG: the
    SAME band edge sampled `drift_ref` bars back instead of `band_span` bars
    back. It is NOT a second, longer band — the edges the breach is measured
    against stay the `band_span` MA. None (default) = `band_span`, i.e. the
    trigger's own-window chop-gate semantics, byte-identical. Callers whose
    job is the LONGER trend (the `band_counter_breach` late-chase veto) pass
    a larger ref; the fit then grows to `band_span + drift_ref` bars so the
    reference point still has `band_span` bars of MA history behind it (SMA
    warmth).

    Returns None when history is too short or the band is degenerate (no
    opinion). Otherwise a dict:

      trending       drift_pct > max_drift_pct — the band itself is trending
                     over the drift window; the same chop-gate condition that
                     vetoes the snapback fade
      direction      "UP" | "DOWN" — the band's drift sign (drift_signed)
      drift_pct      max-edge drift over the drift window, % of price (the
                     trigger's chop-gate math, verbatim)
      drift_signed   signed average edge drift, % of price
      px_upper_pct   (px / upper_edge - 1) * 100 — positive = price ABOVE the
                     upper band edge
      px_lower_pct   (px / lower_edge - 1) * 100 — negative = price BELOW the
                     lower band edge
      breach_opposite_pct  % beyond the OPPOSITE-side edge of the drift
                     (down-drift: px_upper_pct; up-drift: -px_lower_pct),
                     clamped at 0 — the counter-trend-breach measure

    Framing mirrors band_snapback (include_partial: the last candle is the
    still-forming bar whose close is the live mid; the fit excludes the last
    two bars). The `band_counter_breach` risk gate uses this: a COUNTER-trend
    breach (price extended past the OPPOSITE-side edge of a drifting band) is
    the deterministic encoding of the shape the research prompt tells the LLM
    is a reversion, not a continuation (GRASS long 2026-08-26 19:07: 1h band
    DOWN 6.4% drift, px +6.6% vs upper edge, entered long at 0.82 conf).
    """
    if window is not None:
        import logging
        logging.getLogger(__name__).warning(
            "band_state: the `window` parameter is deprecated and ignored "
            f"(got {window}) — the band window is `band_span` only")
    span = max(2, int(band_span))
    # Drift-reference lag: the same MA edge sampled `ref` bars back. Default
    # = the band's own window (single-window rework semantics, byte-identical
    # when drift_ref is None). The fit spans `span + ref` bars so the drift
    # reference point has a full `span` of MA history behind it (SMA warmth);
    # for ref == span that is exactly the old 2*span fit.
    ref = span if drift_ref is None else max(1, int(drift_ref))
    fit_len = span + ref
    need = fit_len + 1 + (1 if include_partial else 0)
    if len(candles) < need:
        return None
    if include_partial:
        px = candle_val(candles[-1], "c")
        fit = candles[-(fit_len + 2):-2]
    else:
        px = current_px if current_px is not None else candle_val(candles[-1], "c")
        fit = candles[-(fit_len + 1):-1]
    if px <= 0 or len(fit) < fit_len:
        return None
    up_ma, lo_ma = _band_ma(fit, span, ma_type)
    upper_edge, lower_edge = up_ma[-1], lo_ma[-1]
    if not math.isfinite(upper_edge) or not math.isfinite(lower_edge):
        return None
    if upper_edge <= lower_edge:
        return None
    upper_ref, lower_ref = up_ma[-1 - ref], lo_ma[-1 - ref]
    drift_pct = max(abs(upper_edge - upper_ref), abs(lower_edge - lower_ref)) / px * 100
    drift_signed = ((upper_edge - upper_ref) + (lower_edge - lower_ref)) / 2 / px * 100
    px_upper_pct = (px / upper_edge - 1) * 100  # >0 = price ABOVE upper edge
    px_lower_pct = (px / lower_edge - 1) * 100  # <0 = price BELOW lower edge
    # The counter-trend breach: price extended beyond the OPPOSITE-side edge
    # of the drift (down-drift + above upper / up-drift + below lower). The
    # band_counter_breach gate keys on this; 0.0 when the shape is absent.
    if drift_signed < 0:
        breach_opposite_pct = max(0.0, px_upper_pct)
    else:
        breach_opposite_pct = max(0.0, -px_lower_pct)
    return {
        "trending": drift_pct > max_drift_pct,
        "direction": "UP" if drift_signed >= 0 else "DOWN",
        "drift_pct": drift_pct,
        "drift_signed": drift_signed,
        "px_upper_pct": px_upper_pct,
        "px_lower_pct": px_lower_pct,
        "breach_opposite_pct": breach_opposite_pct,
    }


def composite_score(hits: List[TriggerHit], weights: Dict[str, float]) -> float:
    """Weighted composite score from triggered hits, clamped 0-100.

    Normalizes against the sum of ALL trigger weights (not just fired ones),
    so a single max-score trigger cannot alone score 100; co-firing triggers
    score proportionally higher.
    """
    fired_hits = [h for h in hits if h.get("fired")]
    if not fired_hits:
        return 0

    total_weight = sum(weights.values()) or 1
    weighted_sum = sum(h["score"] * weights.get(h["name"], 0) for h in fired_hits)
    raw = (weighted_sum / total_weight) * 10
    return max(0, min(100, raw))
