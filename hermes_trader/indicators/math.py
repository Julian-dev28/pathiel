"""Technical indicators (ema, sma, atr, rsi, adx, ttf, connors_rsi, fib-retracement, trailing_up)
computed over OHLCV candles. The canonical home for all TA used by the agents / rebalancers."""

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


def rsi_values(values: List[float], period: int = 14) -> List[float]:
    """RSI over an arbitrary value series (not candles) — wraps each value as a flat candle so the
    Wilder `rsi` above can be reused (used by Connors RSI's streak/RSI legs)."""
    return rsi([{"o": v, "h": v, "l": v, "c": v} for v in values], period)


def ttf(candles: List[Candle], period: int = 15) -> float | None:
    """Trend Trigger Factor (LazyBear): 100·(BP−SP)/(0.5·(BP+SP)), BP = HH(recent)−LL(prior),
    SP = HH(prior)−LL(recent). > 100 = uptrend trigger. Validated mom-corr ≈ 0 (orthogonal to
    return-momentum); bull-regime signal (tv_lazybear5.py). None if < 2·period bars."""
    if len(candles) < 2 * period:
        return None
    h = [candle_val(c, "h") for c in candles]
    l = [candle_val(c, "l") for c in candles]
    hh_r, ll_r = max(h[-period:]), min(l[-period:])
    hh_p, ll_p = max(h[-2 * period:-period]), min(l[-2 * period:-period])
    bp, sp = hh_r - ll_p, hh_p - ll_r
    denom = 0.5 * (bp + sp)
    return None if denom == 0 else 100.0 * (bp - sp) / denom


def connors_rsi(candles: List[Candle], rsi_period: int = 3, streak_period: int = 2,
                rank_period: int = 100) -> float | None:
    """Connors RSI = avg( RSI(close, 3), RSI(streak, 2), percentrank(ROC1, 100) ). < 30 = oversold
    dip-bounce (tv_qqe_coppock.py). None if too little history."""
    closes = [candle_val(c, "c") for c in candles]
    if len(closes) < max(rsi_period + 1, rank_period + 2):
        return None
    r1 = rsi_values(closes, rsi_period)
    streaks = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            streaks.append(streaks[-1] + 1 if streaks[-1] > 0 else 1.0)
        elif closes[i] < closes[i - 1]:
            streaks.append(streaks[-1] - 1 if streaks[-1] < 0 else -1.0)
        else:
            streaks.append(0.0)
    r2 = rsi_values(streaks, streak_period)
    rocs = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    window = rocs[-rank_period:]
    pr = 100.0 * sum(1 for x in window[:-1] if x < rocs[-1]) / max(1, len(window) - 1)
    a, b = r1[-1], r2[-1]
    if a != a or b != b:                                   # NaN guard (insufficient data)
        return None
    return (a + b + pr) / 3.0


def fib_618_retracement_long(candles: List[Candle], swing: int = 20, band_pct: float = 0.025) -> bool | None:
    """True when close sits within `band_pct` of the 0.618 retracement of the recent `swing`-bar
    up-range — a retracement-bounce long candidate (tv_pivots_fib.py, bull-regime). None if short."""
    if len(candles) < swing + 1:
        return None
    h = [candle_val(c, "h") for c in candles[-swing:]]
    l = [candle_val(c, "l") for c in candles[-swing:]]
    cur = candle_val(candles[-1], "c")
    lo, hi = min(l), max(h)
    rng = hi - lo
    if rng <= 0 or cur <= 0:
        return None
    return abs(cur - (hi - 0.618 * rng)) <= band_pct * cur


def trailing_up(candles: List[Candle], period: int = 20) -> bool | None:
    """Regime helper: True if the trailing `period`-bar close return > 0 (e.g. the BTC up-regime gate
    for long-only regime filters). None if too little history."""
    closes = [candle_val(c, "c") for c in candles]
    if len(closes) < period + 1 or closes[-1 - period] <= 0:
        return None
    return (closes[-1] / closes[-1 - period] - 1.0) > 0
