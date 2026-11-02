"""Market regime label, causal and pure (OIL_REGIME_TEST_2026-11-02.md).

    features_at(c1h, asof_ms, cfg) -> RegimeFeatures       one point in time
    label_series(c1h, cfg) -> RegimeSeries                 every 1h close, O(n)
    day_labels(series, days) -> {day index: label}         the label at each day's open

Three labels: "trend_up", "trend_down", "chop". None while the history is
too short (warm-up).

Inputs, all from 1h candles whose CLOSE time (`c.t + 1h`) is at or before
`asof_ms`, so a label never reads a bar that had not finished:

  ret20      the close of the last closed bar over the close of the bar that
             closed `trend_days` days earlier, minus 1 (the 20-day return).
  er         efficiency ratio over the last `er_hours` closed bars: the net
             move of the closes over the sum of the absolute hourly moves.
             1 is a straight line, near 0 is chop.
  move       that net move (its sign is the direction of the recent run).
  range_atr  the high-low range of the last `range_hours` closed bars over
             the 1h ATR14 (simple mean of 14 true ranges). A random walk
             sits near 5; a tight, back-and-forth day well under it.

The rule, checked in order:

  1. er >= fast_er:                                 trend in the direction of `move`
     (a straight-line run over two days is a trend now, whatever the
     20-day return says: this is how a trend break shows up)
  2. |ret20| >= trend_ret and range_atr >= chop_range_atr:
                                                    trend in the direction of ret20
  3. otherwise:                                     chop
     (no 20-day trend, or a 20-day trend whose last day went nowhere)

Thresholds sit in `RegimeConfig` and were picked once, from where the
features fall on Brent's own history (the doc has the table), not from
P&L. `RegimeConfig.shifted` moves one threshold by a factor for the
+-25% sensitivity runs.

`regime_blocks(side, features, mode, cfg)` is the entry filter the sim
reads (`desk_tick_sim.Params.regime_filter`): "no_chop", "with_trend",
"trend_only", "veto_20d" (`against_20d`: the side fights the 20-day trend
and the 48h run is not a straight line its way).

Causality: `features_at(c1h, t)` uses only bars that closed at or before
`t`; altering or adding any bar that closes after `t` cannot change it
(gate test). `label_series` returns the same answer at every 1h close as
`features_at` (gate test), with prefix sums instead of a rescan.
"""
from __future__ import annotations

import bisect
import dataclasses
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from pathiel.models.types import Candle

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

TREND_UP = "trend_up"
TREND_DOWN = "trend_down"
CHOP = "chop"
LABELS = (TREND_UP, TREND_DOWN, CHOP)


@dataclass(frozen=True)
class RegimeConfig:
    trend_ret: float = 0.04         # |20d return| at or above this is a 20-day trend
    fast_er: float = 0.30           # 48h efficiency at or above this is a trend in the 48h direction
    chop_range_atr: float = 4.5     # a 20-day trend whose 24h range is under this many ATR14 has stalled
    trend_days: int = 20
    er_hours: int = 48
    range_hours: int = 24
    atr_n: int = 14

    THRESHOLDS = ("trend_ret", "fast_er", "chop_range_atr")

    def shifted(self, name: str, factor: float) -> "RegimeConfig":
        """This config with one threshold multiplied by `factor` (0.75, 1.25)."""
        if name not in self.THRESHOLDS:
            raise ValueError(f"unknown threshold {name!r}")
        return dataclasses.replace(self, **{name: getattr(self, name) * factor})


@dataclass(frozen=True)
class RegimeFeatures:
    asof_ms: int                    # close time of the last bar read
    ret20: Optional[float]
    er: Optional[float]
    move: Optional[float]
    range_atr: Optional[float]
    label: Optional[str]            # None while warming up


def classify(ret20: Optional[float], er: Optional[float], move: Optional[float],
             range_atr: Optional[float], cfg: RegimeConfig) -> Optional[str]:
    """The label from the three features (module docstring). None when any
    feature is missing (warm-up). Pure."""
    if ret20 is None or er is None or move is None or range_atr is None:
        return None
    if er >= cfg.fast_er and move != 0:
        return TREND_UP if move > 0 else TREND_DOWN
    if abs(ret20) >= cfg.trend_ret and range_atr >= cfg.chop_range_atr:
        return TREND_UP if ret20 > 0 else TREND_DOWN
    return CHOP


def trend_sign(ret20: Optional[float], cfg: RegimeConfig) -> int:
    """+1 / -1 for a 20-day trend that clears `trend_ret`, else 0."""
    if ret20 is None or abs(ret20) < cfg.trend_ret:
        return 0
    return 1 if ret20 > 0 else -1


def closed_by(c1h: Sequence[Candle], asof_ms: int) -> List[Candle]:
    """The bars of `c1h` that closed at or before `asof_ms`, oldest first."""
    return sorted((c for c in c1h if c.t + HOUR_MS <= asof_ms), key=lambda c: c.t)


def _true_ranges(bars: Sequence[Candle]) -> List[float]:
    return [max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c)) for a, b in zip(bars, bars[1:])]


def features_at(c1h: Sequence[Candle], asof_ms: int, cfg: RegimeConfig = RegimeConfig()) -> RegimeFeatures:
    """The features and label from bars closed at or before `asof_ms`.
    Straightforward (rescans); `label_series` is the fast equivalent."""
    bars = closed_by(c1h, asof_ms)
    if not bars:
        return RegimeFeatures(asof_ms, None, None, None, None, None)
    last = bars[-1]
    last_close_t = last.t + HOUR_MS
    # ret20: the bar that closed trend_days earlier (or the latest one before it)
    ret20 = None
    base_t = last_close_t - cfg.trend_days * DAY_MS
    base = [b for b in bars if b.t + HOUR_MS <= base_t]
    if base and base[-1].t + HOUR_MS > base_t - DAY_MS:     # within a day of the mark (gaps)
        ret20 = last.c / base[-1].c - 1.0
    er = move = None
    if len(bars) >= cfg.er_hours + 1:
        w = bars[-(cfg.er_hours + 1):]
        move = w[-1].c - w[0].c
        path = sum(abs(b.c - a.c) for a, b in zip(w, w[1:]))
        er = abs(move) / path if path > 0 else 0.0
    range_atr = None
    if len(bars) >= max(cfg.range_hours, cfg.atr_n + 1):
        trs = _true_ranges(bars[-(cfg.atr_n + 1):])
        atr = sum(trs) / cfg.atr_n
        rb = bars[-cfg.range_hours:]
        rng = max(b.h for b in rb) - min(b.l for b in rb)
        range_atr = rng / atr if atr > 0 else None
    return RegimeFeatures(last_close_t, ret20, er, move, range_atr, classify(ret20, er, move, range_atr, cfg))


@dataclass
class RegimeSeries:
    """Features at every 1h close, oldest first (`close_ms` sorted)."""
    close_ms: List[int]
    feats: List[RegimeFeatures]
    cfg: RegimeConfig

    def at(self, t_ms: int) -> Optional[RegimeFeatures]:
        """The features as of `t_ms`: the latest 1h close at or before it."""
        i = bisect.bisect_right(self.close_ms, t_ms) - 1
        return self.feats[i] if i >= 0 else None

    def label_at(self, t_ms: int) -> Optional[str]:
        f = self.at(t_ms)
        return f.label if f is not None else None


def label_series(c1h: Sequence[Candle], cfg: RegimeConfig = RegimeConfig()) -> RegimeSeries:
    """`features_at` at every 1h close in one pass. Equal to calling
    `features_at(c1h, close)` for each close, float for float (gate test):
    the window sums run over the same values in the same order."""
    bars = sorted(c1h, key=lambda c: c.t)
    n = len(bars)
    closes_t = [b.t + HOUR_MS for b in bars]
    absmv = [0.0] * n
    trs = [0.0] * n
    for i in range(1, n):
        a, b = bars[i - 1], bars[i]
        absmv[i] = abs(b.c - a.c)
        trs[i] = max(b.h - b.l, abs(b.h - a.c), abs(b.l - a.c))
    feats: List[RegimeFeatures] = []
    for i in range(n):
        last_close_t = closes_t[i]
        ret20 = None
        base_t = last_close_t - cfg.trend_days * DAY_MS
        j = bisect.bisect_right(closes_t, base_t, 0, i + 1) - 1
        if j >= 0 and closes_t[j] > base_t - DAY_MS:
            ret20 = bars[i].c / bars[j].c - 1.0
        er = move = None
        k = cfg.er_hours
        if i >= k:
            move = bars[i].c - bars[i - k].c
            path = sum(absmv[i - k + 1:i + 1])
            er = abs(move) / path if path > 0 else 0.0
        range_atr = None
        if i + 1 >= max(cfg.range_hours, cfg.atr_n + 1):
            atr = sum(trs[i + 1 - cfg.atr_n:i + 1]) / cfg.atr_n
            lo = i + 1 - cfg.range_hours
            rng = max(b.h for b in bars[lo:i + 1]) - min(b.l for b in bars[lo:i + 1])
            range_atr = rng / atr if atr > 0 else None
        feats.append(RegimeFeatures(last_close_t, ret20, er, move, range_atr,
                                    classify(ret20, er, move, range_atr, cfg)))
    return RegimeSeries(closes_t, feats, cfg)


FILTERS = ("no_chop", "with_trend", "trend_only", "veto_20d")


def against_20d(side: str, f: Optional[RegimeFeatures], cfg: RegimeConfig) -> bool:
    """Does `side` fight the 20-day trend with no sign the trend broke? True
    when |ret20| clears trend_ret, `side` points the other way, and the 48h
    run is NOT a straight line (er >= fast_er) in `side`'s direction."""
    if f is None:
        return False
    ts = trend_sign(f.ret20, cfg)
    want = 1 if side == "long" else -1
    if ts == 0 or ts == want:
        return False
    broke = f.er is not None and f.move is not None and f.er >= cfg.fast_er and (f.move > 0) == (want > 0) \
        and f.move != 0
    return not broke


def regime_blocks(side: str, f: Optional[RegimeFeatures], mode: str, cfg: RegimeConfig) -> Optional[str]:
    """The refusal reason a regime filter gives a new `side` entry, or None.
      no_chop     "regime_chop"     the label is chop
      with_trend  "regime_against"  the label is a trend the other way
      trend_only  both of the above
      veto_20d    "regime_veto"     `against_20d`
    A missing label (warm-up) never blocks. Pure."""
    if mode not in FILTERS:
        raise ValueError(f"unknown regime filter {mode!r}")
    if mode == "veto_20d":
        return "regime_veto" if against_20d(side, f, cfg) else None
    label = f.label if f is not None else None
    if label is None:
        return None
    if mode in ("no_chop", "trend_only") and label == CHOP:
        return "regime_chop"
    if mode in ("with_trend", "trend_only") and label != CHOP:
        if (label == TREND_UP) != (side == "long"):
            return "regime_against"
    return None


def day_labels(series: RegimeSeries, days: Iterable[int]) -> Dict[int, Optional[str]]:
    """The label at each UTC day's open (day index = ms // DAY_MS): what a
    desk knew at 00:00Z that day."""
    return {d: series.label_at(d * DAY_MS) for d in days}


__all__ = ["RegimeConfig", "RegimeFeatures", "RegimeSeries", "classify", "trend_sign", "features_at",
           "label_series", "day_labels", "closed_by", "against_20d", "regime_blocks", "FILTERS",
           "TREND_UP", "TREND_DOWN", "CHOP", "LABELS", "HOUR_MS", "DAY_MS"]
