"""Pre-AI technical analysis filter.

Performs pure statistical validation of triggered signals before AI analysis.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from hermes_trader.indicators.math import adx, atr, candle_val, ema, rsi
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.models.types import Candle

logger = logging.getLogger(__name__)


def _assess_trend(candles: List[Candle]) -> str:
    """Bullish / bearish / flat based on EMA8/21 cross and slope."""
    if len(candles) < 30:
        return "flat"

    closes = [candle_val(c, "c") for c in candles]
    ema8_arr = ema(closes, 8)
    ema21_arr = ema(closes, 21)

    i = len(closes) - 1
    e8, e21 = ema8_arr[i], ema21_arr[i]
    if not math.isfinite(e8) or not math.isfinite(e21):
        return "flat"

    e8_prev = ema8_arr[max(0, i - 3)]
    ema_cross = e8 > e21
    slope_rising = e8 > e8_prev

    if ema_cross and slope_rising:
        return "bullish"
    if not ema_cross and not slope_rising:
        return "bearish"
    return "flat"


def _compute_atr4pct(candles: List[Candle]) -> Optional[float]:
    if len(candles) < 20:
        return None
    atr_arr = atr(candles, 14)
    last = atr_arr[-1]
    last_close = candle_val(candles[-1], "c")
    if not math.isfinite(last) or last_close == 0:
        return None
    return (last / last_close) * 100


def _compute_rsi(candles: List[Candle]) -> Optional[float]:
    if len(candles) < 20:
        return None
    arr = rsi(candles, 14)
    last = arr[-1]
    return last if math.isfinite(last) else None


def _compute_adx(candles: List[Candle]) -> Optional[float]:
    if len(candles) < 30:
        return None
    arr = adx(candles, 14)
    last = arr[-1]
    return last if math.isfinite(last) else None


def _check_volume_confirm(candles: List[Candle]) -> bool:
    if len(candles) < 21:
        return False
    last_vol = candle_val(candles[-1], "v")
    avg_vol = sum(candle_val(c, "v") for c in candles[-21:-1]) / 20
    return avg_vol > 0 and last_vol >= avg_vol * 0.8


def _check_ema_cross_recent(candles: List[Candle]) -> bool:
    if len(candles) < 25:
        return False
    closes = [candle_val(c, "c") for c in candles]
    ema8_arr = ema(closes, 8)
    ema21_arr = ema(closes, 21)

    for i in range(len(closes) - 3, len(closes)):
        if i < 1:
            continue
        prev8, prev21 = ema8_arr[i - 1], ema21_arr[i - 1]
        curr8, curr21 = ema8_arr[i], ema21_arr[i]
        if not all(math.isfinite(x) for x in (prev8, prev21, curr8, curr21)):
            continue
        if (prev8 <= prev21 and curr8 > curr21) or (prev8 >= prev21 and curr8 < curr21):
            return True
    return False


def analyze_perception(perception: Dict[str, Any]) -> Dict[str, Any]:
    """Run TA validation on a single perception, returning a TA-result dict."""
    coin = perception["coin"]
    try:
        c1h = fetch_hl_candles(coin, "1h", 60)
        c4h = fetch_hl_candles(coin, "4h", 60)
        c1d = fetch_hl_candles(coin, "1d", 40)

        if len(c4h) < 30:
            return {
                "signal": "REJECTED", "score": 0,
                "trend1h": "flat", "trend4h": "flat", "trend1d": "flat",
                "trend_aligned": False,
                "rsi4h": None, "atr4pct": None, "adx4h": None,
                "ema_cross": False, "volume_confirm": False,
                "reason": "insufficient candle data",
            }

        t1h = _assess_trend(c1h)
        t4h = _assess_trend(c4h)
        t1d = _assess_trend(c1d)

        is_bullish = t4h == "bullish" or t1d == "bullish"
        is_bearish = t4h == "bearish" or t1d == "bearish"
        # Direction of the higher-timeframe trend. Our measured edge is
        # LONG/trend-aligned (ledger: longs in up-regimes win; shorts are weak),
        # so the CONFIRMED gate should NOT treat a clean downtrend the same as a
        # clean uptrend — the old `is_bullish or is_bearish` awarded the full
        # alignment bonus to both, sending strong-downtrend coins to paid AI as
        # "confirmed" with equal weight. Now: bullish gets full credit, bearish
        # gets partial (a short is tradeable but lower-edge), conflicting = none.
        if is_bullish and not is_bearish:
            trend_direction = "bullish"
        elif is_bearish and not is_bullish:
            trend_direction = "bearish"
        else:
            trend_direction = "flat"
        trend_aligned = trend_direction in ("bullish", "bearish")

        rsi4h = _compute_rsi(c4h)
        atr4pct = _compute_atr4pct(c4h)
        adx4h = _compute_adx(c4h)
        ema_cross = _check_ema_cross_recent(c4h)
        volume_confirm = _check_volume_confirm(c4h)

        score = 0
        reasons = []

        # Directional alignment scoring (our edge is LONG/trend-aligned):
        # bullish HTF trend = full +20; bearish = +10 (tradeable short, lower edge);
        # flat/conflicting = 0. This stops the filter from rubber-stamping
        # strong-downtrend coins as equally "confirmed".
        if trend_direction == "bullish":
            score += 20
            reasons.append("trend aligned (bullish)")
        elif trend_direction == "bearish":
            score += 10
            reasons.append("trend aligned (bearish)")
        if rsi4h is not None and 30 < rsi4h < 70:
            score += 15
            reasons.append(f"RSI {rsi4h:.0f}")
        if atr4pct is not None and atr4pct >= 0.5:
            score += 15
            reasons.append(f"ATR {atr4pct:.1f}%")
        if adx4h is not None and adx4h >= 25:
            score += 15
            reasons.append(f"ADX {adx4h:.0f}")
        if ema_cross:
            score += 10
            reasons.append("EMA cross")
        if volume_confirm:
            score += 10
            reasons.append("volume confirmed")
        score += min(15, perception["composite_score"] / 100 * 15)

        verdict = "CONFIRMED" if score >= 22 else "WEAK" if score >= 12 else "REJECTED"

        if verdict != "CONFIRMED":
            logger.info(f"[ta_filter] {coin} -> {verdict} (score {score:.0f}) reasons: {', '.join(reasons) or 'none'}")

        return {
            "signal": verdict,
            "score": min(100, score),
            "trend1h": t1h, "trend4h": t4h, "trend1d": t1d,
            "trend_aligned": trend_aligned,
            "rsi4h": rsi4h, "atr4pct": atr4pct, "adx4h": adx4h,
            "ema_cross": ema_cross, "volume_confirm": volume_confirm,
            "reason": ", ".join(reasons) if reasons else "no signals",
        }
    except Exception as err:
        # Candle fetches hit the network; a failure rejects the candidate
        # (no-trade is the safe direction) and surfaces the cause.
        return {
            "signal": "REJECTED", "score": 0,
            "trend1h": "flat", "trend4h": "flat", "trend1d": "flat",
            "trend_aligned": False,
            "rsi4h": None, "atr4pct": None, "adx4h": None,
            "ema_cross": False, "volume_confirm": False,
            "reason": f"TA error: {err}",
        }
