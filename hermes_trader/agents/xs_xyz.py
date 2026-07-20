"""Pure engine for the xs_xyz_equities book — the validated xs recipe on the xyz
tokenized-equity universe.

AUTHORITATIVE SPEC: research/alpha_swarm/findings/W-X2_xs_xyz_equities.md
(cell A, VERDICT ROBUST: net25 +0.65%/rebalance, p=0.0055 vs 2000 matched
random books, OOS halves +0.18/+1.12, n=34, survives 50bps and a 4x
liquidity-floor tightening).

Recipe (pre-registered):
  - Universe: xyz-dex perps, EQUITIES ONLY — exclude NON_EQUITY_XYZ (indices,
    commodities, fx, and the PURRDAT/DRAM baskets) and the benchmark itself.
  - Eligibility at the decision bar: >= min_history_bars+1 (61) completed daily
    bars AND 30d mean daily notional >= $250k.
  - Score: r7(coin) − beta·r7(xyz:XYZ100); beta = OLS on last 30 daily returns
    (1.0 fallback below 8 points) — exactly xs_momentum.residual_score.
  - Book: long top-5 / short bottom-5, equal weight, 5d non-overlapping hold.

This module is PURE (candles/universe in → eligible set + target book out; no
network, no orders, no state). The _live wiring (xs_xyz_live.py) drives it on
the rebalance timer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hermes_trader.agents.xs_momentum import TargetBook, rank_universe
from hermes_trader.indicators.math import candle_val

_DAY_MS = 86_400_000

# Declared non-equity xyz underlyings (indices/commodities/fx/crypto-wrap +
# the PURRDAT/DRAM baskets) — copied verbatim from the pre-registered list in
# research/alpha_swarm/hypotheses/W-X2_xs_widening.py (NON_EQUITY_XYZ). The
# equity cell was validated EXCLUDING these; changing this set invalidates the
# verdict.
NON_EQUITY_XYZ = frozenset({
    "xyz:XYZ100", "xyz:SP500", "xyz:GOLD", "xyz:SILVER", "xyz:BRENTOIL", "xyz:CL",
    "xyz:NATGAS", "xyz:COPPER", "xyz:PLAT", "xyz:URAN", "xyz:EURUSD", "xyz:GBPUSD",
    "xyz:USDJPY", "xyz:USDCHF", "xyz:AUDUSD", "xyz:USDCAD", "xyz:NIKKEI", "xyz:DAX",
    "xyz:FTSE100", "xyz:HSI", "xyz:XAU", "xyz:XAG", "xyz:WTI", "xyz:BTCEQ",
    "xyz:PURRDAT", "xyz:DRAM",
})

# ── Pre-committed KILL criteria (W-X2 findings SPEC block, operator-authorized) ──
# 1. Cumulative forward net25 EV < 0 after KILL_CUM_NET25_REBALANCES rebalances
#    (~60d) → flip shadow_only the same day.
# 2. Any single rebalance book EV below KILL_SINGLE_REBALANCE_EV_PCT (worst
#    observed in the backtest was −6.79%) → flip shadow_only the same day.
# 3. At rebalance SEMIS_ABLATION_CHECK_REBALANCE: if the no-semis ablation of
#    FORWARD data is also ≈0 and semis dispersion has compressed, halve size
#    preemptively (the observed edge IS the semis-supercycle dispersion).
# Grading lives in scripts/shadow_status.py over the xs_xyz_equities ledger —
# no book grades itself. These constants are the single source of the numbers.
KILL_CUM_NET25_REBALANCES = 12
KILL_SINGLE_REBALANCE_EV_PCT = -8.0
SEMIS_ABLATION_CHECK_REBALANCE = 6


def bar_t(bar: Any) -> int:
    """Bar start timestamp in ms (0 when unreadable)."""
    try:
        return int(bar.get("t") if isinstance(bar, dict) else getattr(bar, "t", 0))
    except Exception:
        return 0


def completed_bars(bars: Optional[List[Any]], now_ms: int) -> List[Any]:
    """Drop the still-forming current daily bar so decisions use COMPLETED bars
    only (the backtest decided on completed bars; live-reads-forming-bar was an
    audited live/backtest divergence). A daily bar is forming iff it STARTED
    < 24h ago; a just-closed bar started exactly 24h ago and is kept."""
    if not bars:
        return []
    last_t = bar_t(bars[-1])
    if last_t and (now_ms - last_t) < _DAY_MS:
        return list(bars)[:-1]
    return list(bars)


def mean_daily_notional(bars: List[Any], window: int = 30) -> float:
    """Mean close×volume USD notional over the last `window` bars (the W-X2
    thin-book floor input). 0.0 when no usable bars."""
    tot, n = 0.0, 0
    for b in (bars or [])[-window:]:
        c = candle_val(b, "c")
        v = candle_val(b, "v")
        try:
            c, v = float(c or 0), float(v or 0)
        except (TypeError, ValueError):
            continue
        if c > 0 and v >= 0:
            tot += c * v
            n += 1
    return tot / n if n else 0.0


def eligible_xyz_coins(universe: List[Dict[str, Any]], benchmark: str) -> List[str]:
    """xyz-dex EQUITY perps from a live universe snapshot: coin must carry the
    `xyz:` prefix, must not be a declared non-equity underlying, and must not
    be the benchmark itself (the benchmark residualizes the score — it can
    never be a leg)."""
    out: List[str] = []
    for m in universe or []:
        coin = (m.get("coin") if isinstance(m, dict) else None) or ""
        if not coin.startswith("xyz:"):
            continue
        if coin in NON_EQUITY_XYZ or coin == benchmark:
            continue
        if isinstance(m, dict) and m.get("type") == "spot":
            continue
        out.append(coin)
    return out


def filter_eligible(candles_by_coin: Dict[str, List[Any]], min_history_bars: int,
                    min_notional_usd: float, notional_window: int = 30) -> Dict[str, List[Any]]:
    """W-X2 eligibility on COMPLETED daily bars: >= min_history_bars+1 bars
    (>=61 at the pre-registered 60) AND `notional_window`-day mean daily
    notional >= min_notional_usd ($250k pre-registered floor)."""
    out: Dict[str, List[Any]] = {}
    for coin, bars in (candles_by_coin or {}).items():
        if not bars or len(bars) < int(min_history_bars) + 1:
            continue
        if mean_daily_notional(bars, notional_window) < float(min_notional_usd):
            continue
        out[coin] = bars
    return out


def rank_xyz(candles_by_coin: Dict[str, List[Any]], bench_bars: List[Any],
             lookback_days: int = 7, k: int = 5, beta_window: int = 30) -> TargetBook:
    """Residual-momentum target book: score = r_lb(coin) − beta·r_lb(benchmark),
    beta = OLS on the last `beta_window` daily returns (1.0 fallback < 8 pts).
    Delegates to the shared validated engine (xs_momentum.rank_universe with
    ranking="raw" + bench_bars → residual_score). Empty book when fewer than
    2k coins score (can't form a clean spread)."""
    if not bench_bars:
        return TargetBook([], [], {})
    return rank_universe(candles_by_coin, int(lookback_days), int(k),
                         bench_bars=bench_bars, beta_window=int(beta_window),
                         ranking="raw")
