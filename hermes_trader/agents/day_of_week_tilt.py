"""Day-of-week sizing tilt (calendar edge; SHADOW-first, disabled by default).

VALIDATED but with caveats — from ALPHA-PLAN.md:
  Monday   +0.78%/day (OOS +0.87/+0.68 robust)  → LONG BIAS — increase size slightly
  Thursday −1.64%/day (robust negative)           → REDUCE/FLAT — decrease size or skip

⚠  MULTIPLE-TESTING CAVEAT: 7 weekdays tested; OOS consistency (both halves agree) gives some
   confidence, but this should be treated as a TILT not a standalone edge. Trade as a small sizing
   scalar overlay on top of the primary momentum/factor edges. Calendar edge is ORTHOGONAL
   to momentum + pairs (different mechanism entirely).

CONFIG: ``day_of_week_tilt`` block. enabled=False → scalar is always 1.0 (no-op).

PURE module — no network, no orders. Returns a scalar that the caller (trading_loop.py or
the rebalancers) multiplies against their size. Scalars cap at [0.5, 1.5] (never halts or doubles).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional


# Weekday numbers per Python's datetime.weekday(): 0=Mon, 1=Tue, ..., 6=Sun
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def current_weekday_utc() -> int:
    """Return the current UTC weekday (0=Mon, 6=Sun)."""
    return datetime.datetime.utcnow().weekday()


def tilt_scalar(config: Dict[str, Any], weekday: Optional[int] = None) -> float:
    """Return a sizing scalar for the current (or provided) weekday.

    Returns 1.0 (neutral) when:
    - enabled is False (default).
    - weekday is not one of the configured tilt days.
    - The scalar would fall outside [0.5, 1.5] (hard-clamped).

    weekday: 0=Mon, 1=Tue, ..., 6=Sun (defaults to current UTC weekday).
    """
    dow = config.get("day_of_week_tilt") or {}
    if not bool(dow.get("enabled", False)):
        return 1.0

    if weekday is None:
        weekday = current_weekday_utc()

    # Monday (0): positive bias — increase size
    if weekday == 0:
        scalar = float(dow.get("monday_scalar", 1.15))
    # Thursday (3): negative bias — reduce size
    elif weekday == 3:
        scalar = float(dow.get("thursday_scalar", 0.75))
    else:
        return 1.0

    # Hard clamp — never halt or double
    return max(0.5, min(1.5, scalar))


def current_tilt_info(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dict with day name, weekday number, and the tilt scalar (for logging)."""
    wd = current_weekday_utc()
    s = tilt_scalar(config, weekday=wd)
    dow = config.get("day_of_week_tilt") or {}
    return {
        "weekday_name": _WEEKDAY_NAMES[wd],
        "weekday_num": wd,
        "tilt_scalar": s,
        "enabled": bool(dow.get("enabled", False)),
    }
