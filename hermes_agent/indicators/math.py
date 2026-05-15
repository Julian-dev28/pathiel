"""Technical indicator math.

Ported verbatim from lib/agent/triggers.ts indicator helpers (ema, sma, atr, rsi, adx)
into standalone functions in this module.
"""

from __future__ import annotations

from typing import Any, List


def ema(values: List[float], period: int) -> List[float]:
    """Exponential Moving Average.

    Ported verbatim from lib/agent/triggers.ts.
    """
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
    """Simple Moving Average.

    Ported verbatim from lib/agent/triggers.ts.
    """
    out = [float("nan")] * len(values)
    acc = 0.0
    for i in range(len(values)):
        acc += values[i]
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def atr(candles: List[Dict[str, Any]], period: int = 14) -> List[float]:
    """Average True Range.

    Ported verbatim from lib/agent/triggers.ts.
    candles must have 'h', 'l', 'c' keys.
    """
    # Handle both dict and Candle objects
    def _get(c, key):
        if isinstance(c, dict):
            return c.get(key, 0)
        return getattr(c, key, 0)
    
    tr = [0.0] * len(candles)
    for i in range(1, len(candles)):
        h, l = _get(candles[i], "h"), _get(candles[i], "l")
        pc = _get(candles[i - 1], "c")
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))

    out = [float("nan")] * len(candles)
    if len(candles) <= period:
        return out

    acc = sum(tr[1:period + 1])
    out[period] = acc / period
    for i in range(period + 1, len(candles)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def rsi(candles: List[Dict[str, Any]], period: int = 14) -> List[float]:
    """Relative Strength Index.

    Ported verbatim from lib/agent/triggers.ts.
    """
    # Handle both dict and Candle objects
    def _get(c, key):
        if isinstance(c, dict):
            return c.get(key, 0)
        return getattr(c, key, 0)
    
    out = [float("nan")] * len(candles)
    if len(candles) <= period:
        return out

    g, l = 0.0, 0.0
    for i in range(1, period + 1):
        d = _get(candles[i], "c") - _get(candles[i - 1], "c")
        if d >= 0:
            g += d
        else:
            l -= d

    avg_g = g / period
    avg_l = l / period
    out[period] = 100 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)

    for i in range(period + 1, len(candles)):
        d = _get(candles[i], "c") - _get(candles[i - 1], "c")
        avg_g = (avg_g * (period - 1) + (d if d > 0 else 0)) / period
        avg_l = (avg_l * (period - 1) + (d if d < 0 else -d)) / period
        out[i] = 100 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)

    return out


def adx(candles: List[Dict[str, Any]], period: int = 14) -> List[float]:
    """Average Directional Index.

    Ported verbatim from lib/agent/triggers.ts.
    """
    # Handle both dict and Candle objects
    def _get(c, key):
        if isinstance(c, dict):
            return c.get(key, 0)
        return getattr(c, key, 0)
    
    n = len(candles)
    out = [float("nan")] * n
    if n <= period * 2:
        return out

    tr = [0.0] * n
    p_dm = [0.0] * n
    m_dm = [0.0] * n

    for i in range(1, n):
        h, l = _get(candles[i], "h"), _get(candles[i], "l")
        pc = _get(candles[i - 1], "c")
        ph, pl = _get(candles[i - 1], "h"), _get(candles[i - 1], "l")

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
