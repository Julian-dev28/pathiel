"""Range-structure features: HTF range state, geometry, deviation vs acceptance.

RESEARCH-ONLY MODULE (KillaXBT methodology research, research/killa_xbt/SPEC.md).
It must NOT be imported by any live trading path (trading_loop, executor,
research.py, ta_filter, risk_gates). It is deliberately dependency-free
(stdlib only, operates on raw ``[t, o, h, l, c, v]`` bar lists) so importing it
can never drag live code in, and vice versa. tests/test_range_structure.py
enforces the no-live-import rule.

Lookahead discipline
--------------------
Every feature at bar index ``i`` is computed from bars ``<= i`` only, and the
range boundary used to classify bar ``i``'s poke/close excludes bar ``i``
itself (window ``bars[i-L : i]``). A multi-bar excursion beyond the boundary
progressively lifts the trailing boundary, which makes deviation detection
conservative, never optimistic. There is no future-confirmed boundary anywhere.
ACCUMULATION/REDISTRIBUTION labels are *_CANDIDATE and provisional by
construction: they use only trailing impulse direction and long-EMA slope,
never the eventual resolution of the range.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

# bar layout: [t_ms, open, high, low, close, volume]
T, O, H, L, C, V = 0, 1, 2, 3, 4, 5

HOUR_MS = 3_600_000

# ---------------------------------------------------------------------------
# state labels (spec Part 4A)
# ---------------------------------------------------------------------------
TREND_UP = "TREND_UP"
TREND_DOWN = "TREND_DOWN"
RANGE = "RANGE"
BREAKOUT_UP_CANDIDATE = "BREAKOUT_UP_CANDIDATE"
BREAKOUT_DOWN_CANDIDATE = "BREAKOUT_DOWN_CANDIDATE"
ACCUMULATION_CANDIDATE = "ACCUMULATION_CANDIDATE"
REDISTRIBUTION_CANDIDATE = "REDISTRIBUTION_CANDIDATE"
UNKNOWN = "UNKNOWN"

RANGE_FAMILY = frozenset({RANGE, ACCUMULATION_CANDIDATE, REDISTRIBUTION_CANDIDATE})

# location buckets (spec Part 4E)
LOCATION_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.00, 0.20, "0.00-0.20"),
    (0.20, 0.35, "0.20-0.35"),
    (0.35, 0.65, "0.35-0.65"),
    (0.65, 0.80, "0.65-0.80"),
    (0.80, 1.01, "0.80-1.00"),
)


def location_bucket(loc: float) -> str:
    """Map a clamped 0..1 range location to its spec bucket label."""
    loc = min(max(loc, 0.0), 1.0)
    for lo, hi, name in LOCATION_BUCKETS:
        if lo <= loc < hi:
            return name
    return LOCATION_BUCKETS[-1][2]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RangeConfig:
    """Thresholds for range-structure classification.

    Defaults are the pre-registered starting grid centre; anything tuned must
    be tuned on TRAIN periods only (spec Part 4C / Part 6).
    """

    lookback: int = 42            # bars defining the trailing range envelope
    atr_period: int = 14
    adx_period: int = 14
    ema_fast: int = 20
    ema_slow: int = 50
    ema_long: int = 100           # long EMA for accumulation/redistribution tag
    adx_trend: float = 25.0       # ADX >= this -> trending
    adx_range: float = 20.0       # ADX < this -> range-eligible
    min_width_atr: float = 2.0    # range must be at least this many ATRs wide
    max_width_atr: float = 12.0   # wider than this is not one structure
    touch_band_atr: float = 0.25  # "touch" = within this ATR fraction of edge
    min_touches: int = 2          # per side, for RANGE
    dev_thresholds_atr: tuple[float, ...] = (0.10, 0.25, 0.50)
    acceptance_closes: tuple[int, ...] = (1, 2, 3)
    impulse_ret: float = 0.05     # |1-bar ret| >= this counts as an impulse bar
    accum_lookback: int = 90      # bars for prior-impulse context
    accum_drop: float = -0.15     # prior ret <= this -> capitulation context
    ema_long_flat: float = 0.0005 # |per-bar ema slope| below this = flat


# ---------------------------------------------------------------------------
# small math helpers (self-contained; live math.py takes Candle objects,
# this module takes raw bar lists — no shared import by design)
# ---------------------------------------------------------------------------
def ema_series(values: Sequence[float], period: int) -> List[float]:
    """Standard EMA; NaN until the first `period` values are seen."""
    out = [math.nan] * len(values)
    if len(values) < period or period <= 0:
        return out
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    out[period - 1] = e
    for i in range(period, len(values)):
        e = values[i] * k + e * (1.0 - k)
        out[i] = e
    return out


def atr_series(bars: Sequence[Sequence[float]], period: int = 14) -> List[float]:
    """Wilder ATR; NaN until `period` true ranges are seen."""
    n = len(bars)
    out = [math.nan] * n
    if n < period + 1 or period <= 0:
        return out
    trs = []
    for i in range(1, n):
        h, l, pc = bars[i][H], bars[i][L], bars[i - 1][C]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i - 1]) / period
        out[i] = a
    return out


def adx_series(bars: Sequence[Sequence[float]], period: int = 14) -> List[float]:
    """Wilder ADX; NaN until 2*period bars. Mirrors hermes math.py semantics."""
    n = len(bars)
    out = [math.nan] * n
    if n <= period * 2:
        return out
    tr = [0.0] * n
    p_dm = [0.0] * n
    m_dm = [0.0] * n
    for i in range(1, n):
        h, l = bars[i][H], bars[i][L]
        pc, ph, pl = bars[i - 1][C], bars[i - 1][H], bars[i - 1][L]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up, dn = h - ph, pl - l
        p_dm[i] = up if (up > dn and up > 0) else 0.0
        m_dm[i] = dn if (dn > up and dn > 0) else 0.0
    tr_s = sum(tr[1 : period + 1])
    p_s = sum(p_dm[1 : period + 1])
    m_s = sum(m_dm[1 : period + 1])

    def _dx() -> float:
        pdi = 0.0 if tr_s == 0 else 100.0 * p_s / tr_s
        mdi = 0.0 if tr_s == 0 else 100.0 * m_s / tr_s
        tot = pdi + mdi
        return 0.0 if tot == 0 else 100.0 * abs(pdi - mdi) / tot

    dx = [math.nan] * n
    dx[period] = _dx()
    for i in range(period + 1, n):
        tr_s = tr_s - tr_s / period + tr[i]
        p_s = p_s - p_s / period + p_dm[i]
        m_s = m_s - m_s / period + m_dm[i]
        dx[i] = _dx()
    out[period * 2 - 1] = sum(dx[period : period * 2]) / period
    for i in range(period * 2, n):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


def aggregate_bars(bars_1h: Sequence[Sequence[float]], hours: int) -> List[List[float]]:
    """Aggregate 1h bars into `hours`-hour bars aligned to epoch boundaries.

    Only complete groups (all `hours` constituent bars present and contiguous)
    are emitted, so a data hole never fabricates a bar. Fail-safe: returns []
    on empty/short input.
    """
    if hours <= 0:
        raise ValueError("hours must be positive")
    if hours == 1:
        return [list(b) for b in bars_1h]
    out: List[List[float]] = []
    group: List[Sequence[float]] = []
    span = hours * HOUR_MS
    for b in bars_1h:
        t = int(b[T])
        if group and (t // span != int(group[0][T]) // span or t - int(group[-1][T]) != HOUR_MS):
            group = []
        if not group and t % span != 0:
            continue  # wait for an aligned group start
        group.append(b)
        if len(group) == hours:
            out.append([
                int(group[0][T]),
                float(group[0][O]),
                max(float(g[H]) for g in group),
                min(float(g[L]) for g in group),
                float(group[-1][C]),
                sum(float(g[V]) for g in group),
            ])
            group = []
    return out


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------
@dataclass
class RangeSnapshot:
    """Point-in-time range-structure features at the close of one bar."""

    t: int
    state: str
    # geometry (spec Part 4B)
    range_high: float = math.nan
    range_low: float = math.nan
    range_mid: float = math.nan
    width_pct: float = math.nan
    width_atr: float = math.nan
    price_location: float = math.nan      # 0 = at range_low, 1 = at range_high
    dist_to_high_atr: float = math.nan
    dist_to_low_atr: float = math.nan
    dist_to_mid_atr: float = math.nan
    touches_high: int = 0
    touches_low: int = 0
    # deviation / acceptance (spec Part 4C) — keyed by threshold
    upper_deviation: dict = field(default_factory=dict)   # {thr_atr: bool}
    lower_deviation: dict = field(default_factory=dict)
    upper_acceptance: dict = field(default_factory=dict)  # {k_closes: bool}
    lower_acceptance: dict = field(default_factory=dict)
    # time-in-structure (spec Part 4D)
    bars_in_range: int = 0
    bars_since_impulse: int = -1          # -1 = no impulse seen in window
    # context
    atr: float = math.nan
    adx: float = math.nan
    location_bucket: str = ""


class RangeStructure:
    """Computes RangeSnapshots over a bar series with no lookahead.

    Usage::

        rs = RangeStructure(bars_4h, cfg)
        snap = rs.snapshot(i)   # features known at close of bars_4h[i]

    All indicator series are precomputed once (each value at index j uses
    bars <= j only), so snapshot(i) is O(lookback).
    """

    def __init__(self, bars: Sequence[Sequence[float]], cfg: Optional[RangeConfig] = None):
        self.bars = [list(b) for b in bars]
        self.cfg = cfg or RangeConfig()
        c = self.cfg
        closes = [b[C] for b in self.bars]
        self._atr = atr_series(self.bars, c.atr_period)
        self._adx = adx_series(self.bars, c.adx_period)
        self._ema_f = ema_series(closes, c.ema_fast)
        self._ema_s = ema_series(closes, c.ema_slow)
        self._ema_l = ema_series(closes, c.ema_long)
        self._min_bars = max(c.lookback + 1, c.adx_period * 2, c.ema_long, c.atr_period + 1)
        # Acceptance runs: a run starts when a close exceeds the trailing
        # boundary; the boundary is then FROZEN at its run-start value (using
        # a ratcheting trailing envelope would demand each close beat prior
        # wicks, which is not the "k closes beyond the range" semantic).
        # Everything here uses bars <= i only.
        self._run_up: List[int] = [0] * len(self.bars)
        self._run_dn: List[int] = [0] * len(self.bars)
        rh_run = math.nan
        rl_run = math.nan
        up_len = dn_len = 0
        for i in range(len(self.bars)):
            rh, rl = self._boundary(i)
            close = self.bars[i][C]
            if math.isnan(rh):
                up_len = dn_len = 0
            else:
                if up_len > 0 and close > rh_run:
                    up_len += 1
                elif close > rh:
                    up_len, rh_run = 1, rh
                else:
                    up_len = 0
                if dn_len > 0 and close < rl_run:
                    dn_len += 1
                elif close < rl:
                    dn_len, rl_run = 1, rl
                else:
                    dn_len = 0
            self._run_up[i] = up_len
            self._run_dn[i] = dn_len
        # state series is computed lazily bar-by-bar (needs only trailing data)
        self._state_cache: dict[int, str] = {}
        self._snap_cache: dict[int, "RangeSnapshot"] = {}

    # -- internals ----------------------------------------------------------
    def _boundary(self, i: int) -> tuple[float, float]:
        """Trailing range envelope over bars[i-L : i] (bar i excluded)."""
        lb = self.cfg.lookback
        if i < lb:
            return math.nan, math.nan
        win = self.bars[i - lb : i]
        return max(b[H] for b in win), min(b[L] for b in win)

    def _impulse_age(self, i: int) -> int:
        """Bars since the last |1-bar return| >= impulse_ret, scanning back
        at most accum_lookback bars. -1 if none found."""
        c = self.cfg
        lo = max(1, i - c.accum_lookback)
        for j in range(i, lo - 1, -1):
            p0 = self.bars[j - 1][C]
            if p0 > 0 and abs(self.bars[j][C] / p0 - 1.0) >= c.impulse_ret:
                return i - j
        return -1

    def _classify(self, i: int) -> str:
        """State at close of bar i. Uses bars <= i only."""
        if i in self._state_cache:
            return self._state_cache[i]
        c = self.cfg
        state = UNKNOWN
        if i >= self._min_bars:
            rh, rl = self._boundary(i)
            atr = self._atr[i - 1]  # volatility context ENTERING bar i
            adx = self._adx[i]
            ef, es, el = self._ema_f[i], self._ema_s[i], self._ema_l[i]
            es_prev = self._ema_s[i - 1]
            el_prev = self._ema_l[i - 1]
            close = self.bars[i][C]
            ok = not any(math.isnan(x) for x in (rh, rl, atr, adx, ef, es, el, es_prev, el_prev))
            if ok and atr > 0:
                width_atr = (rh - rl) / atr
                trending_up = adx >= c.adx_trend and ef > es and es > es_prev
                trending_dn = adx >= c.adx_trend and ef < es and es < es_prev
                if trending_up:
                    state = TREND_UP
                elif trending_dn:
                    state = TREND_DOWN
                elif close > rh:
                    state = BREAKOUT_UP_CANDIDATE
                elif close < rl:
                    state = BREAKOUT_DOWN_CANDIDATE
                else:
                    win = self.bars[i - c.lookback : i]
                    band = c.touch_band_atr * atr
                    th = sum(1 for b in win if b[H] >= rh - band)
                    tl = sum(1 for b in win if b[L] <= rl + band)
                    if (adx < c.adx_range and c.min_width_atr <= width_atr <= c.max_width_atr
                            and th >= c.min_touches and tl >= c.min_touches):
                        # provisional accumulation/redistribution sub-tags
                        j0 = max(0, i - c.accum_lookback)
                        p0 = self.bars[j0][C]
                        prior_ret = close / p0 - 1.0 if p0 > 0 else 0.0
                        el_slope = (el - el_prev) / el if el > 0 else 0.0
                        if prior_ret <= c.accum_drop and el_slope < -c.ema_long_flat:
                            state = REDISTRIBUTION_CANDIDATE
                        elif prior_ret <= c.accum_drop and abs(el_slope) <= c.ema_long_flat:
                            state = ACCUMULATION_CANDIDATE
                        else:
                            state = RANGE
                    else:
                        state = UNKNOWN
        self._state_cache[i] = state
        return state

    # -- public -------------------------------------------------------------
    def snapshot(self, i: int) -> RangeSnapshot:
        """Full feature snapshot at close of bar i (fail-safe on short history).

        Snapshots are pure functions of bars <= i, so they are memoized."""
        bars, c = self.bars, self.cfg
        if i < 0 or i >= len(bars):
            raise IndexError(f"bar index {i} out of range 0..{len(bars) - 1}")
        if i in self._snap_cache:
            return self._snap_cache[i]
        t = int(bars[i][T])
        state = self._classify(i)
        snap = RangeSnapshot(t=t, state=state)
        rh, rl = self._boundary(i)
        # ATR as of the PRIOR bar's close: the volatility context entering
        # bar i, so a large poke cannot inflate its own deviation threshold
        atr = self._atr[i - 1] if i >= 1 else math.nan
        snap.atr = atr
        snap.adx = self._adx[i]
        if math.isnan(rh) or math.isnan(atr) or atr <= 0 or rh <= rl:
            self._snap_cache[i] = snap
            return snap
        close = bars[i][C]
        mid = (rh + rl) / 2.0
        w = rh - rl
        snap.range_high, snap.range_low, snap.range_mid = rh, rl, mid
        snap.width_pct = w / mid * 100.0 if mid > 0 else math.nan
        snap.width_atr = w / atr
        snap.price_location = min(max((close - rl) / w, 0.0), 1.0)
        snap.location_bucket = location_bucket(snap.price_location)
        snap.dist_to_high_atr = (rh - close) / atr
        snap.dist_to_low_atr = (close - rl) / atr
        snap.dist_to_mid_atr = (close - mid) / atr
        band = c.touch_band_atr * atr
        win = bars[i - c.lookback : i]
        snap.touches_high = sum(1 for b in win if b[H] >= rh - band)
        snap.touches_low = sum(1 for b in win if b[L] <= rl + band)
        # deviation: bar i pokes >= thr*ATR beyond the boundary that existed
        # BEFORE bar i, closes back inside it, and is NOT part of an ongoing
        # acceptance run (a close still beyond the run's frozen boundary is
        # acceptance-in-progress, not a rejection)
        for thr in c.dev_thresholds_atr:
            snap.upper_deviation[thr] = (
                bars[i][H] >= rh + thr * atr and close <= rh and self._run_up[i] == 0
            )
            snap.lower_deviation[thr] = (
                bars[i][L] <= rl - thr * atr and close >= rl and self._run_dn[i] == 0
            )
        # acceptance: k consecutive closes beyond the boundary frozen at the
        # start of the excursion
        for k in c.acceptance_closes:
            snap.upper_acceptance[k] = self._run_up[i] >= k
            snap.lower_acceptance[k] = self._run_dn[i] >= k
        # time-in-structure
        n_in = 0
        j = i
        while j >= 0 and self._classify(j) in RANGE_FAMILY:
            n_in += 1
            j -= 1
        snap.bars_in_range = n_in
        snap.bars_since_impulse = self._impulse_age(i)
        self._snap_cache[i] = snap
        return snap
