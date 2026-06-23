"""Cross-sectional momentum rebalancer — the first validated +EV edge (see ALPHA-PLAN.md,
[[project_cross_sectional_momentum_edge]]).

Rank the liquid universe by trailing LB-day return; target a MARKET-NEUTRAL book of the top-K
LONGs and bottom-K SHORTs, rebalanced every hold-days. The edge is the long-SHORT *spread*
(relative strength) — long-only is fragile — so BOTH legs are required.

This module is PURE (candles/holdings in → target book + rebalance plan out; no network, no
orders). The loop drives it on a timer; the executor places the diff. Validated config:
LB=14d, hold=5d, K=8 (most balanced OOS: +2.01%/rebal, 63% win, OOS +1.87/+2.16).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes_trader.indicators.math import candle_val


@dataclass
class TargetBook:
    longs: List[str]
    shorts: List[str]
    scores: Dict[str, float] = field(default_factory=dict)   # coin -> trailing return


def trailing_return(bars: List[Any], lb: int) -> Optional[float]:
    """Trailing `lb`-bar return from closes. Lookahead-safe: uses only the bars passed in
    (caller must pass bars whose last element is the decision bar). None if too short."""
    if not bars or len(bars) < lb + 1:
        return None
    c_now = candle_val(bars[-1], "c")
    c_past = candle_val(bars[-1 - lb], "c")
    if c_past <= 0:
        return None
    return c_now / c_past - 1.0


def _daily_rets(bars: List[Any], window: int) -> List[float]:
    closes = [candle_val(b, "c") for b in bars[-(window + 1):]]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


def _beta(coin_rets: List[float], bench_rets: List[float]) -> float:
    """OLS beta of coin returns on benchmark returns (1.0 if too short / degenerate)."""
    n = min(len(coin_rets), len(bench_rets))
    if n < 8:
        return 1.0
    cr, br = coin_rets[-n:], bench_rets[-n:]
    mb = sum(br) / n
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = sum(cr) / n
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def residual_score(bars: List[Any], bench_bars: List[Any], lb: int, beta_window: int = 30) -> Optional[float]:
    """Idiosyncratic (benchmark-neutral) momentum: coin's lb-return minus beta×benchmark lb-return.
    Validated (edge_sweep4.py) to be both stronger AND smoother across regimes than total return."""
    rc = trailing_return(bars, lb)
    rb = trailing_return(bench_bars, lb)
    if rc is None or rb is None:
        return None
    beta = _beta(_daily_rets(bars, beta_window), _daily_rets(bench_bars, beta_window))
    return rc - beta * rb


def rank_universe(candles_by_coin: Dict[str, List[Any]], lb: int, k: int,
                  bench_bars: Optional[List[Any]] = None, beta_window: int = 30) -> TargetBook:
    """Rank coins by trailing `lb`-day momentum; top-`k` = longs, bottom-`k` = shorts. When
    `bench_bars` is given, ranks on the RESIDUAL (benchmark-neutral) score — the validated, smoother
    core. Else total return. Empty book if < 2k coins have enough history (can't form a clean spread)."""
    scored = []
    for coin, bars in (candles_by_coin or {}).items():
        r = (residual_score(bars, bench_bars, lb, beta_window) if bench_bars is not None
             else trailing_return(bars, lb))
        if r is not None:
            scored.append((coin, r))
    if len(scored) < 2 * k:
        return TargetBook([], [], {})
    scored.sort(key=lambda x: x[1], reverse=True)
    longs = [c for c, _ in scored[:k]]
    shorts = [c for c, _ in scored[-k:]]
    return TargetBook(longs, shorts, dict(scored))


def rebalance_plan(book: TargetBook, current_long: List[str], current_short: List[str]) -> Dict[str, List[str]]:
    """Diff the target book against current holdings. A coin flipping sides is handled naturally
    (it lands in both a close_* and an open_* list). Returns sorted lists per action."""
    tgt_long, tgt_short = set(book.longs), set(book.shorts)
    cur_long, cur_short = set(current_long or []), set(current_short or [])
    return {
        "open_long": sorted(tgt_long - cur_long),
        "open_short": sorted(tgt_short - cur_short),
        "close_long": sorted(cur_long - tgt_long),
        "close_short": sorted(cur_short - tgt_short),
        "hold_long": sorted(tgt_long & cur_long),
        "hold_short": sorted(tgt_short & cur_short),
    }


def is_empty_plan(plan: Dict[str, List[str]]) -> bool:
    return not any(plan.get(k) for k in ("open_long", "open_short", "close_long", "close_short"))
