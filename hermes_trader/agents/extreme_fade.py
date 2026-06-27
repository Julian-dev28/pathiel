"""Extreme-fade overlay.

Validated live behavior is long-only after a completed daily crash. Rally-exhaustion shorts
are handled by their own gated module with separate regime, volume, sizing, and stop rules.

CONFIG: ``extreme_fade`` block. enabled=False → no signals emitted (no-op).

PURE module — no network, no orders. Returns a list of FadeSignal objects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FadeSignal:
    """A candidate extreme-fade entry for the next trading day."""
    coin: str
    side: str              # always "long" for the validated crash-fade leg
    prior_daily_ret: float # the move that triggered the fade
    threshold_pct: float   # the threshold it exceeded (e.g. 12.0 for 12%)


def _daily_return(bars: List[Any]) -> Optional[float]:
    """Return the last completed daily return from a bar list (close[-1]/close[-2]-1).
    Bars are expected in chronological order (oldest first). None if too short."""
    if not bars or len(bars) < 2:
        return None
    def _c(b):
        return b.get("c") if isinstance(b, dict) else getattr(b, "c", None)
    c0 = _c(bars[-2])
    c1 = _c(bars[-1])
    if not c0 or not c1 or float(c0) <= 0:
        return None
    return float(c1) / float(c0) - 1.0


def compute_signals(
    candles_by_coin: Dict[str, List[Any]],
    config: Dict[str, Any],
) -> List[FadeSignal]:
    """Scan the universe for extreme-fade candidates.

    Returns a list of FadeSignal objects. Empty list when enabled=False or no signals.
    Lookahead-safe: signal is based on the PRIOR bar's return (last completed daily bar).
    """
    ef = config.get("extreme_fade") or {}
    if not bool(ef.get("enabled", False)):
        return []

    # crash_pct: the NEGATIVE threshold for the long-after-crash leg (e.g. -0.12 = -12%)
    crash_pct = float(ef.get("crash_pct", -0.12))
    threshold_pct = abs(crash_pct) * 100.0

    signals: List[FadeSignal] = []
    for coin, bars in candles_by_coin.items():
        ret = _daily_return(bars)
        if ret is None:
            continue
        if ret > crash_pct:
            continue
        signals.append(FadeSignal(
            coin=coin, side="long",
            prior_daily_ret=ret,
            threshold_pct=threshold_pct,
        ))

    if signals:
        logger.info(
            f"[extreme-fade] {len(signals)} candidates: "
            + ", ".join(f"{s.coin}({s.side},{s.prior_daily_ret*100:+.1f}%)" for s in signals)
        )

    return signals


def log_signals(signals: List[FadeSignal], config: Dict[str, Any]) -> None:
    """Session-log the fade candidates (call from trading_loop after compute_signals)."""
    if not signals:
        return
    try:
        from hermes_trader.session_log import append as log_event
        ef = config.get("extreme_fade") or {}
        log_event({
            "event": "extreme_fade_candidates",
            "n": len(signals),
            "signals": [{"coin": s.coin, "side": s.side,
                         "ret_pct": round(s.prior_daily_ret * 100, 2)} for s in signals],
        })
    except Exception:
        pass
