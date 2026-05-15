"""DSL (Dynamic Stop-Loss) exit engine.

Adapted from senpi-skills DSL exit engine for hermes-trader. Manages exit
logic for open positions with two-phase design:

  Phase 1 — Loss protection from entry until price moves up `protect_pct`.
  Phase 2 — Profit locking with tiered retrace thresholds once PnL is positive.

Unlike a plain SL order, DSL trails upward as price rises and only exits
when the mark price breaches the computed floor.

Phase 1 (Loss protection):
  - max_loss_pct below entry → hard stop
  - protect_pct above entry → transition to Phase 2
  - min(profit_floor, entry - max_loss)

Phase 2 (Profit locking):
  - trailing floor at entry + (peak - entry) * (1 - retrace_pct)
  - retrace_pct increases with unrealized profit (tiers)
  - hard_timeout after entry → emergency exit

Usage:
    dsl = ExitPolicy(max_loss_pct=3.0, protect_pct=1.5, ...)
    verdict = dsl.check(position_entry_price, current_mark_price, entry_time)
    if verdict.exit:
        close_position(reason=verdict.reason)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RetraceTier:
    """A profit tier with its own retrace threshold.

    Example: when price is 10% above entry, retrace threshold is 30% —
    so the floor trails at entry + (peak - entry) * (1 - 0.30).
    """
    pct_above_entry: float  # Min profit % above entry to activate this tier
    retrace_threshold: float  # Fraction of peak profit to give back (0-1)


@dataclass
class ExitVerdict:
    """Result of a DSL floor check."""
    exit: bool = False
    reason: str = ""
    floor_price: Optional[float] = None
    peak_price: Optional[float] = None
    phase: str = ""  # "phase1" or "phase2"
    unrealized_pct: float = 0.0


@dataclass
class ExitPolicy:
    """DSL exit policy configuration.

    Tuning profiles:
      Conservative: max_loss_pct=5, retrace=10, protect=3, hard_timeout=360min
      Moderate:     max_loss_pct=2.5, retrace=7, protect=1.5, hard_timeout=180min
      Aggressive:   max_loss_pct=1.5, retrace=5, protect=0.8, hard_timeout=90min
    """
    max_loss_pct: float = 2.5  # Max loss % below entry (hard stop)
    protect_pct: float = 1.5  # Price must rise this % above entry before Phase 2
    retrace_threshold: float = 0.30  # Give back 30% of peak profit (Phase 2 default)
    hard_timeout_minutes: float = 180.0  # Emergency exit after this long
    phase2_tiers: List[RetraceTier] = field(default_factory=lambda: [
        RetraceTier(5.0, 0.30),   # 5% profit → give back 30%
        RetraceTier(10.0, 0.40),  # 10% profit → lock tighter, give back 40%
        RetraceTier(20.0, 0.50),  # 20% profit → lock even tighter
        RetraceTier(50.0, 0.60),  # 50% profit → lock most profit
    ])
    consecutive_breaches_required: int = 1  # Number of consecutive floor breaches before exit


class DSLTracker:
    """Tracks DSL state for a single open position.

    Must be called on every price tick (e.g., from the scan loop's WS mids).
    """
    def __init__(self, coin: str, side: str, entry_px: float,
                 entry_time: float, policy: Optional[ExitPolicy] = None) -> None:
        self.coin = coin
        self.side = side  # "long" | "short"
        self.entry_px = entry_px
        self.entry_time = entry_time
        self.policy = policy or ExitPolicy()

        # State
        self.peak_px = entry_px
        self.consecutive_breaches = 0
        self._last_floor: Optional[float] = None

    def is_long(self) -> bool:
        return self.side == "long"

    def _unrealized_pct(self, mark_px: float) -> float:
        if self.is_long():
            return (mark_px - self.entry_px) / self.entry_px * 100
        return (self.entry_px - mark_px) / self.entry_px * 100

    def _active_tier(self, mark_px: float) -> RetraceTier:
        """Find the highest active retrace tier based on current profit."""
        upct = self._unrealized_pct(mark_px)
        active = RetraceTier(0.0, self.policy.retrace_threshold)  # default
        for tier in self.policy.phase2_tiers:
            if upct >= tier.pct_above_entry:
                active = tier
        return active

    def check(self, mark_px: float) -> ExitVerdict:
        """Evaluate DSL floor against current mark price. Call on every tick."""
        elapsed_min = (time.time() - self.entry_time) / 60
        upct = self._unrealized_pct(mark_px)
        is_long = self.is_long()
        pol = self.policy

        # Update peak (for longs: highest price seen; for shorts: lowest)
        if is_long and mark_px > self.peak_px:
            self.peak_px = mark_px
        elif not is_long and mark_px < self.peak_px:
            self.peak_px = mark_px

        # ── Hard timeout ──────────────────────────────────────────────
        if elapsed_min >= pol.hard_timeout_minutes:
            return ExitVerdict(
                exit=True, reason=f"hard_timeout ({elapsed_min:.0f}min)",
                floor_price=None, peak_price=self.peak_px, phase="timeout",
                unrealized_pct=upct,
            )

        # ── Compute floor ───────────────────────────────────────────
        # Floor only moves UP (for longs) — once it rises above entry,
        # it never falls back. This prevents giving back locked profit.
        if is_long:
            profit_pct = (mark_px - self.entry_px) / self.entry_px * 100
            loss_pct = (self.entry_px - mark_px) / self.entry_px * 100
            
            # Max loss check
            if loss_pct >= pol.max_loss_pct:
                return ExitVerdict(
                    exit=True, reason=f"max_loss ({loss_pct:.1f}% >= {pol.max_loss_pct}%)",
                    floor_price=self.entry_px * (1 - pol.max_loss_pct / 100),
                    peak_price=self.peak_px, phase="phase1", unrealized_pct=upct,
                )
            
            if profit_pct >= pol.protect_pct:
                # Phase 2: floor = entry + profit_range * (1 - retrace)
                tier = self._active_tier(self.peak_px)  # Use PEAK for tier, not current
                profit_range = self.peak_px - self.entry_px
                floor = self.entry_px + profit_range * (1 - tier.retrace_threshold)
            else:
                # Phase 1: floor at max loss
                floor = self.entry_px * (1 - pol.max_loss_pct / 100)
        else:
            # Short side
            profit_pct = (self.entry_px - mark_px) / self.entry_px * 100
            loss_pct = (mark_px - self.entry_px) / self.entry_px * 100
            
            if loss_pct >= pol.max_loss_pct:
                return ExitVerdict(
                    exit=True, reason=f"max_loss ({loss_pct:.1f}% >= {pol.max_loss_pct}%)",
                    floor_price=self.entry_px * (1 + pol.max_loss_pct / 100),
                    peak_price=self.peak_px, phase="phase1", unrealized_pct=upct,
                )
            
            if profit_pct >= pol.protect_pct:
                tier = self._active_tier(self.peak_px)
                profit_range = self.entry_px - self.peak_px
                floor = self.entry_px - profit_range * (1 - tier.retrace_threshold)
            else:
                floor = self.entry_px * (1 + pol.max_loss_pct / 100)

        # Floor should never decrease for longs (or increase for shorts)
        if self._last_floor is not None:
            if is_long:
                floor = max(floor, self._last_floor)
            else:
                floor = min(floor, self._last_floor)

        self._last_floor = floor

        # ── Floor breach check ────────────────────────────────────────
        breached = (is_long and mark_px < floor) or (not is_long and mark_px > floor)
        if breached:
            self.consecutive_breaches += 1
            if self.consecutive_breaches >= pol.consecutive_breaches_required:
                return ExitVerdict(
                    exit=True,
                    reason=f"floor_breach ({self.consecutive_breaches}x consec, floor={floor:.2f})",
                    floor_price=floor, peak_price=self.peak_px,
                    phase="phase2" if self._unrealized_pct(mark_px) >= pol.protect_pct else "phase1",
                    unrealized_pct=upct,
                )
        else:
            self.consecutive_breaches = 0

        return ExitVerdict(
            exit=False, reason="", floor_price=self._last_floor,
            peak_price=self.peak_px,
            phase="phase2" if self._unrealized_pct(mark_px) >= pol.protect_pct else "phase1",
            unrealized_pct=upct,
        )

    def status(self, mark_px: float) -> Dict[str, Any]:
        """Return current DSL status dict (for logging/MCP)."""
        verdict = self.check(mark_px)
        return {
            "coin": self.coin,
            "side": self.side,
            "entry_px": self.entry_px,
            "mark_px": mark_px,
            "peak_px": verdict.peak_price,
            "floor_px": verdict.floor_price,
            "unrealized_pct": round(verdict.unrealized_pct, 2),
            "phase": verdict.phase,
            "consecutive_breaches": self.consecutive_breaches,
            "exit": verdict.exit,
            "exit_reason": verdict.reason,
        }


# ── Global tracker registry ──────────────────────────────────────────

_active_positions: Dict[str, DSLTracker] = {}


def register_position(coin: str, side: str, entry_px: float,
                      entry_time: Optional[float] = None,
                      policy: Optional[ExitPolicy] = None) -> DSLTracker:
    """Register a new position for DSL tracking."""
    key = f"{coin}_{side}"
    tracker = DSLTracker(coin, side, entry_px, entry_time or time.time(), policy)
    _active_positions[key] = tracker
    logger.info(f"[dsl] Registered {key} @ {entry_px}")
    return tracker


def unregister_position(coin: str, side: str) -> None:
    """Remove a position from DSL tracking after it's closed."""
    key = f"{coin}_{side}"
    _active_positions.pop(key, None)
    logger.info(f"[dsl] Unregistered {key}")


def get_tracker(coin: str, side: str) -> Optional[DSLTracker]:
    """Get the DSL tracker for an open position."""
    return _active_positions.get(f"{coin}_{side}")


def check_all_positions(mids: Dict[str, float]) -> List[ExitVerdict]:
    """Check all active positions against current mids. Call each scan tick.

    Returns list of ExitVerdict for positions that should be closed.
    """
    exits = []
    for key, tracker in list(_active_positions.items()):
        mark_px = mids.get(tracker.coin)
        # Handle both str and float values from different sources
        if mark_px is not None:
            try:
                mark_px = float(mark_px)
            except (ValueError, TypeError):
                continue
            if mark_px > 0:
                verdict = tracker.check(mark_px)
                if verdict.exit:
                    exits.append(verdict)
    return exits


def all_status(mids: Dict[str, float]) -> List[Dict[str, Any]]:
    """Get status for all tracked positions."""
    return [
        tracker.status(mids.get(tracker.coin, tracker.entry_px))
        for tracker in _active_positions.values()
    ]
