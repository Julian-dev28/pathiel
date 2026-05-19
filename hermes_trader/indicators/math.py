"""Technical indicators (ema, sma, atr, rsi, adx) computed over OHLCV candles."""

from __future__ import annotations

from typing import List

from hermes_trader.models.types import Candle


def candle_val(c: Candle | dict[str, float], key: str) -> float:
    """Read an OHLCV field from a Candle or a plain dict."""
    if isinstance(c, dict):
        return c.get(key, 0)
    return getattr(c, key, 0)


def ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average."""
    k = 2 / (period + 1)
    out = [float("nan")] * len(values)
    if not values:
        return out

    e = values[0]
    out[0] = e
    for i in range(1, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def sma(values: List[float], period: int) -> List[float]:
    """Simple moving average."""
    out = [float("nan")] * len(values)
    acc = 0.0
    for i in range(len(values)):
        acc += values[i]
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def atr(candles: List[Candle], period: int = 14) -> List[float]:
    """Average true range."""
    tr = [0.0] * len(candles)
    for i in range(1, len(candles)):
        h, l = candle_val(candles[i], "h"), candle_val(candles[i], "l")
        pc = candle_val(candles[i - 1], "c")
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    out = [float("nan")] * len(candles)
    if len(candles) <= period:
        return out

    acc = sum(tr[1:period + 1])
    out[period] = acc / period
    for i in range(period + 1, len(candles)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def rsi(candles: List[Candle], period: int = 14) -> List[float]:
    """Relative strength index."""
    out = [float("nan")] * len(candles)
    if len(candles) <= period:
        return out

    g, l = 0.0, 0.0
    for i in range(1, period + 1):
        d = candle_val(candles[i], "c") - candle_val(candles[i - 1], "c")
        if d >= 0:
            g += d
        else:
            l -= d

    avg_g = g / period
    avg_l = l / period
    out[period] = 100 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)

    for i in range(period + 1, len(candles)):
        d = candle_val(candles[i], "c") - candle_val(candles[i - 1], "c")
        # gain = positive move else 0; loss = magnitude of a negative move else 0.
        # The loss term must be a non-negative magnitude — the previous
        # `d if d < 0 else -d` fed negatives in and drove RSI below 0.
        avg_g = (avg_g * (period - 1) + (d if d > 0 else 0)) / period
        avg_l = (avg_l * (period - 1) + (-d if d < 0 else 0)) / period
        out[i] = 100 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)

    return out


def adx(candles: List[Candle], period: int = 14) -> List[float]:
    """Average directional index."""
    n = len(candles)
    out = [float("nan")] * n
    if n <= period * 2:
        return out

    tr = [0.0] * n
    p_dm = [0.0] * n
    m_dm = [0.0] * n

    for i in range(1, n):
        h, l = candle_val(candles[i], "h"), candle_val(candles[i], "l")
        pc = candle_val(candles[i - 1], "c")
        ph, pl = candle_val(candles[i - 1], "h"), candle_val(candles[i - 1], "l")

        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

        up = h - ph
        dn = pl - l
        p_dm[i] = up if (up > dn and up > 0) else 0
        m_dm[i] = dn if (dn > up and dn > 0) else 0

    tr_s, p_s, m_s = 0.0, 0.0, 0.0
    for i in range(1, period + 1):
        tr_s += tr[i]
        p_s += p_dm[i]
        m_s += m_dm[i]

    def _compute_dx() -> float:
        pdi = 0 if tr_s == 0 else 100 * p_s / tr_s
        mdi = 0 if tr_s == 0 else 100 * m_s / tr_s
        total = pdi + mdi
        return 0 if total == 0 else 100 * abs(pdi - mdi) / total

    dx = [float("nan")] * n
    dx[period] = _compute_dx()

    for i in range(period + 1, n):
        tr_s = tr_s - tr_s / period + tr[i]
        p_s = p_s - p_s / period + p_dm[i]
        m_s = m_s - m_s / period + m_dm[i]
        dx[i] = _compute_dx()

    adx_s = sum(dx[period:period * 2])
    out[period * 2 - 1] = adx_s / period

    for i in range(period * 2, n):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period

    return out
