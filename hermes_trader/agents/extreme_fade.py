"""Extreme-fade overlay (SHADOW-first, disabled by default).

VALIDATED but marginal — from ALPHA-PLAN.md: "+0.23–0.59% per fade" (net of 10bps cost).
Works as an OVERLAY on top of the existing scanner: when a coin has moved > threshold% in the
prior trading day, flag it for a COUNTER-TREND fade entry the next day.

Signal: single-bar reversal — after |daily return| > threshold, fade it (short after big up,
long after big down). Hold for 1 day. Net cost-adjusted EV: +0.23–0.59% depending on threshold
(8%, 12%, 18% tested; 12–18% sweet spot).

⚠  MARGINAL edge — small per-trade EV, needs many trades. Treat as a small overlay, NOT a
   primary signal. The runner_entry_gate and all safety gates still apply (this tag doesn't
   bypass them — it's NOT external_alpha).

CONFIG: ``extreme_fade`` block. enabled=False → no signals emitted (no-op).
shadow_mode=True → logs candidates but does NOT inject them into the scan queue.

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
    side: str              # "long" (fade big down) or "short" (fade big up)
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

    threshold_pct = float(ef.get("threshold_pct", 12.0))
    threshold = threshold_pct / 100.0

    signals: List[FadeSignal] = []
    for coin, bars in candles_by_coin.items():
        ret = _daily_return(bars)
        if ret is None or abs(ret) < threshold:
            continue
        # Fade: big up → short, big down → long
        side = "short" if ret > 0 else "long"
        signals.append(FadeSignal(
            coin=coin, side=side,
            prior_daily_ret=ret,
            threshold_pct=threshold_pct,
        ))

    if signals:
        shadow = bool(ef.get("shadow_mode", True))
        logger.info(
            f"[extreme-fade]{' SHADOW' if shadow else ' LIVE'} {len(signals)} candidates: "
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
            "shadow": bool(ef.get("shadow_mode", True)),
            "n": len(signals),
            "signals": [{"coin": s.coin, "side": s.side,
                         "ret_pct": round(s.prior_daily_ret * 100, 2)} for s in signals],
        })
    except Exception:
        pass
