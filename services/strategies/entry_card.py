"""The Levels card's entry rows (the Desk page "/", 2026-11-19).

Operator, looking at the Levels card (mid/last-closed/8h/24h/48h range/
ATR14/mid position in the 24h range): "should add entry to FE right?" -- he
wants to see at a glance where the desk will trade, on the same card.

Three rows, built here from numbers the tick already computes, never
re-derived:
  - the thesis's own entry (side + price, from the thesis's `call`);
  - the live entry: where next_trade()'s rule will actually act, with its
    stop, target and R, plus a status (ready / waiting / the blocker);
  - the distance from the mid to that live entry (price, %, ATRs).

Every number comes from two existing pure functions, called by the
caller (services/strategies/api.py) with the exact same inputs the desk
tick and GET /api/desks/{id}/next use:
  - services/strategies/next_trade.next_trade -- the stop/target/trigger/R
  - services/strategies/event_blackout.event_blackout -- the active
    blackout, if any (checked separately: next_trade() has no opinion on
    it, and a "ready" rule during an EIA/weekend/event blackout is an
    order place_bracket would refuse).

Pure: no I/O, no network, no clock reads. Backs the `entry` field of
GET /api/desks/{desk_id}/levels.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Machine-stable status keys (Desk page / entry_rows.js badge lookup) and
# the short, human text the operator asked to see on the card.
STATUS_NO_THESIS = "no_thesis"
STATUS_READY = "ready"
STATUS_WAITING = "waiting"
STATUS_BLACKOUT = "blackout"
STATUS_CHASE = "chase"
STATUS_BREAK = "break"

_CHASE_MARKER = "never chase"
_BREAK_MARKER = "no entry after a break"


def _distance(mid: Optional[float], entry_px: Optional[float], atr14: Optional[float]) -> Dict[str, Optional[float]]:
    """How far `mid` sits from `entry_px`: price, fraction of mid, ATRs.
    None for any leg that cannot be computed. Pure arithmetic over numbers
    `next_trade()`/`levels.summarize_coin()` already produced -- nothing
    here reads a candle or a rule."""
    if mid is None or entry_px is None:
        return {"price": None, "pct": None, "atr": None}
    mid, entry_px = float(mid), float(entry_px)
    price = abs(mid - entry_px)
    pct = (price / mid) if mid else None
    atr = (price / float(atr14)) if atr14 else None
    return {"price": round(price, 6), "pct": round(pct, 6) if pct is not None else None,
            "atr": round(atr, 3) if atr is not None else None}


def build(*, mid: Optional[float], atr14: Optional[float], side: Optional[str],
         thesis_entry_px: Optional[float], next_trade_result: Optional[Dict[str, Any]],
         blackout: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The Levels card's `entry` field.

    `side` / `thesis_entry_px`: off the thesis's own `call`, shown even on
    a `next_trade()` branch that does not echo them back (its early exits
    do not). `next_trade_result`: `next_trade()`'s own answer for this
    `side` (None when there is no side to trade, i.e. no thesis).
    `blackout`: `event_blackout(coin, side=side)`'s own answer, checked
    separately and given priority over the rule's own verdict -- the rule
    does not know about EIA/weekend/event/headline blackouts; place_bracket
    refuses during one regardless of what next_trade() says.
    """
    if side not in ("long", "short"):
        return {
            "ok": False, "has_thesis": False, "side": None, "thesis_entry_px": None,
            "live": {"status": STATUS_NO_THESIS, "status_text": "no thesis", "ok": False,
                    "entry_px": None, "stop_px": None, "tp_px": None, "r_multiple": None,
                    "rule_verdict": None},
            "distance": _distance(None, None, None),
        }

    nt = next_trade_result or {}
    entry_px = nt.get("entry_px")
    stop_px = nt.get("stop_px")
    tp_px = nt.get("tp_px")
    r_to_targets = nt.get("r_to_targets") or []
    r_mult = r_to_targets[0].get("r_multiple") if r_to_targets else None

    rule_ok = bool(nt.get("ok"))
    blockers = nt.get("blockers") or []
    chase = any(_CHASE_MARKER in b for b in blockers)
    broke = any(_BREAK_MARKER in b for b in blockers)

    if blackout:
        until = blackout.get("until_utc")
        status = STATUS_BLACKOUT
        status_text = f"blackout until {until}" if until else "blackout"
    elif rule_ok:
        status, status_text = STATUS_READY, "ready"
    elif chase:
        # Plain words: the desk is not chasing, it is waiting for price to
        # come back to its level (operator read "chase" as the opposite).
        status, status_text = STATUS_CHASE, "waiting, price past thesis entry"
    elif broke:
        status, status_text = STATUS_BREAK, "no entry, level broke"
    else:
        status, status_text = STATUS_WAITING, "waiting"

    live_ok = rule_ok and not blackout
    return {
        "ok": live_ok, "has_thesis": True, "side": side, "thesis_entry_px": thesis_entry_px,
        "live": {
            "status": status, "status_text": status_text, "ok": live_ok,
            "entry_px": entry_px, "stop_px": stop_px, "tp_px": tp_px, "r_multiple": r_mult,
            "rule_verdict": nt.get("verdict") or nt.get("reason"),
        },
        "distance": _distance(mid, entry_px, atr14),
    }


__all__ = ["build", "STATUS_NO_THESIS", "STATUS_READY", "STATUS_WAITING", "STATUS_BLACKOUT",
          "STATUS_CHASE", "STATUS_BREAK"]
