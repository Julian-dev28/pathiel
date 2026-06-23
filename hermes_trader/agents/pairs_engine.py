"""Pairs stat-arb pure engine — market-neutral mean-reversion of co-moving spreads.

Each pair is (coinA, coinB) with historical return correlation > min_corr. The spread is the
log-price ratio: spread = log(A) - log(B). When the spread's z-score (vs its trailing window mean
and stdev) exceeds entry_z, the pair has diverged: SHORT the rich leg / LONG the cheap leg and hold
until the z-score reverts to exit_z or maxhold days elapse.

Validated (edge_pairs.py, V4): +1.08%/trade (entry_z=2.0), tuned +1.98%/trade at entry_z=2.5.
OOS-robust: both halves positive (+1.10/+1.06). Market-neutral: long-short on a spread that
cancels most systematic beta exposure. ORTHOGONAL to momentum (profits from reversion, not trend)
→ stacking diversifies.

PURE module — no network, no orders. Callers pass (coin -> [bars]) and this returns a list of
SpreadSignal objects indicating which trades to open/close.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PairTrade:
    """An open or candidate stat-arb spread trade."""
    coin_a: str         # the "rich" leg that gets shorted when z > entry_z
    coin_b: str         # the "cheap" leg that gets longed when z > entry_z
    side: int           # +1 = long spread (A cheap, B rich: long A, short B); -1 = reverse
    z_entry: float      # z-score at entry (informational)
    spread_mean: float  # mu of the trailing window at entry
    spread_std: float   # sigma at entry

    # Convenience: which coin is LONG and which is SHORT for the executor
    @property
    def long_coin(self) -> str:
        return self.coin_a if self.side == 1 else self.coin_b

    @property
    def short_coin(self) -> str:
        return self.coin_b if self.side == 1 else self.coin_a


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pearson(xs: List[float], ys: List[float]) -> float:
    """Pearson correlation. Returns 0 if degenerate."""
    n = len(xs)
    if n < 5:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0 or dy <= 0:
        return 0.0
    return num / (dx * dy)


def _log_returns(closes: List[float]) -> List[float]:
    """Daily log returns from a close-price list."""
    return [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]


def _closes(bars: List[Any]) -> List[float]:
    """Extract close prices from bar dicts."""
    out = []
    for b in bars:
        c = b.get("c") if isinstance(b, dict) else getattr(b, "c", None)
        if c and float(c) > 0:
            out.append(float(c))
    return out


def _spread(log_closes_a: List[float], log_closes_b: List[float]) -> List[float]:
    """Aligned log spread: log(A) - log(B) for each bar (lengths must match)."""
    n = min(len(log_closes_a), len(log_closes_b))
    return [log_closes_a[i] - log_closes_b[i] for i in range(n)]


# ── Pair scoring ─────────────────────────────────────────────────────────────

def pair_correlation(bars_a: List[Any], bars_b: List[Any], window: int) -> float:
    """Trailing-window return correlation between two coins. Lookahead-safe."""
    closes_a = _closes(bars_a)
    closes_b = _closes(bars_b)
    n = min(len(closes_a), len(closes_b), window + 1)
    if n < window // 2:
        return 0.0
    rets_a = _log_returns(closes_a[-n:])
    rets_b = _log_returns(closes_b[-n:])
    m = min(len(rets_a), len(rets_b))
    if m < 5:
        return 0.0
    return _pearson(rets_a[-m:], rets_b[-m:])


def spread_zscore(
    bars_a: List[Any],
    bars_b: List[Any],
    window: int,
) -> Tuple[Optional[float], float, float]:
    """Compute the current spread z-score.

    Uses the trailing ``window`` bars to estimate mean/stdev, then z-scores the LAST bar's spread.
    Lookahead-safe: the z-score is computed from data ≤ t.

    Returns: (z_score, spread_mean, spread_std). z_score is None if insufficient history.
    """
    closes_a = _closes(bars_a)
    closes_b = _closes(bars_b)
    n = min(len(closes_a), len(closes_b))
    if n < window + 2:
        return None, 0.0, 0.0

    # Align to common length
    closes_a = closes_a[-n:]
    closes_b = closes_b[-n:]

    log_a = [math.log(c) for c in closes_a if c > 0]
    log_b = [math.log(c) for c in closes_b if c > 0]
    m = min(len(log_a), len(log_b))
    if m < window + 2:
        return None, 0.0, 0.0

    log_a = log_a[-m:]
    log_b = log_b[-m:]
    spreads = [log_a[i] - log_b[i] for i in range(m)]

    # Window for mean/std: last `window` bars BEFORE the current bar (strictly historical)
    hist = spreads[-window - 1:-1]   # bars[-window-1:-1] = window obs before the last bar
    if len(hist) < window // 2:
        return None, 0.0, 0.0

    mu = statistics.mean(hist)
    try:
        sd = statistics.pstdev(hist)
    except statistics.StatisticsError:
        return None, 0.0, 0.0
    if sd <= 0:
        return None, mu, sd

    current_spread = spreads[-1]
    z = (current_spread - mu) / sd
    return z, mu, sd


# ── Signal generation ─────────────────────────────────────────────────────────

def compute_signals(
    candles_by_coin: Dict[str, List[Any]],
    entry_z: float = 2.5,
    exit_z: float = 0.5,
    min_corr: float = 0.6,
    window: int = 30,
    open_pairs: Optional[List[PairTrade]] = None,
) -> Tuple[List[PairTrade], List[PairTrade]]:
    """Compute pairs stat-arb signals from the current candle snapshot.

    Returns: (signals_to_open, signals_to_close)

    signals_to_open: PairTrade objects for pairs whose |z| ≥ entry_z AND corr ≥ min_corr.
    signals_to_close: open PairTrade objects (from open_pairs) whose |z| ≤ exit_z (reverted).

    Parameters
    ----------
    candles_by_coin : dict mapping coin -> list of bar dicts with "c" (close).
    entry_z         : z-score threshold to enter (validated V4: 2.5).
    exit_z          : z-score threshold to exit on reversion (validated V4: 0.5).
    min_corr        : trailing-window return correlation floor (validated: 0.6).
    window          : trailing window in days for z-score and correlation (validated: 30).
    open_pairs      : currently open PairTrade objects (to check for close signals).

    Notes
    -----
    Lookahead-safe: all signals derived from data ≤ last bar in candles_by_coin.
    No overlapping signals: an already-open pair (long_coin, short_coin) is not re-opened.
    """
    coins = list(candles_by_coin.keys())
    n_coins = len(coins)
    if n_coins < 2:
        return [], []

    # ── Close signals: check if open pairs have reverted ─────────────────────
    signals_to_close: List[PairTrade] = []
    open_set: set = set()   # (long_coin, short_coin) → don't reopen
    for pt in (open_pairs or []):
        bars_a = candles_by_coin.get(pt.coin_a)
        bars_b = candles_by_coin.get(pt.coin_b)
        if bars_a is None or bars_b is None:
            signals_to_close.append(pt)   # coin delisted or data missing → close
            continue
        z, _mu, _sd = spread_zscore(bars_a, bars_b, window)
        if z is None or abs(z) <= exit_z:
            signals_to_close.append(pt)   # reverted or stale → close
        else:
            open_set.add((pt.long_coin, pt.short_coin))

    # ── Open signals: scan all pairs for entry ────────────────────────────────
    signals_to_open: List[PairTrade] = []
    for i in range(n_coins):
        for j in range(i + 1, n_coins):
            coin_a, coin_b = coins[i], coins[j]
            bars_a = candles_by_coin[coin_a]
            bars_b = candles_by_coin[coin_b]

            # Correlation filter (only trade genuinely co-moving pairs)
            corr = pair_correlation(bars_a, bars_b, window)
            if corr < min_corr:
                continue

            z, mu, sd = spread_zscore(bars_a, bars_b, window)
            if z is None or abs(z) < entry_z:
                continue

            # z > 0: A is "rich" (spread high) → short A, long B
            # z < 0: B is "rich" → short B, long A
            if z > 0:
                long_c, short_c = coin_b, coin_a
                side = -1   # in terms of spread (A short, B long = "short spread")
            else:
                long_c, short_c = coin_a, coin_b
                side = 1

            if (long_c, short_c) in open_set:
                continue   # already open — don't double

            signals_to_open.append(PairTrade(
                coin_a=coin_a, coin_b=coin_b, side=side,
                z_entry=z, spread_mean=mu, spread_std=sd,
            ))

    return signals_to_open, signals_to_close


def is_same_pair(pt: PairTrade, long_coin: str, short_coin: str) -> bool:
    """True if pt represents the same (long, short) trade regardless of A/B labelling."""
    return pt.long_coin == long_coin and pt.short_coin == short_coin
