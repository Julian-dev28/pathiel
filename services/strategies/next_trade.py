"""The desk's next trade, by the tick's own rules, for the Desk page.

Operator, 2026-10-30: "is the call part of the fe still valid after the thesis
and desk tick" -> "fix it make the fe be in sync with the ticks". Since the
switch to market entries (docs/strategy/OIL_STRATEGY.md) the thesis's own
entry/stop/targets are its plan, not the order the tick sends. The tick
(`.claude/agents/desk-tick.md` step 5, flat) builds its bracket from the 24h
range:

  long:  stop 0.75 under the 24h low (under the mid if price broke it),
         target the 24h high with place_bracket's 0.5% offset,
         2R or better (bracket.DEFAULT_MIN_R), the 20x rule, and never
         above the thesis entry.
  short: the mirror.

`next_trade` answers, from the same numbers the tick reads: would it buy at
the mid right now, and if not, at what price it would (the highest mid that
pays the minimum R with these levels, capped at the thesis entry), and does
the 20x rule pass there. Pure: no I/O. The gates are the live ones from
pathiel/agents/bracket.py, so the page and the tick cannot disagree.

Backs `GET /api/desks/{desk_id}/next`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pathiel.agents.bracket import (DEFAULT_MIN_R, TARGET_OFFSET_FRAC, check_leverage,
                                    check_levels, offset_target)

# Stop distance beyond the 24h edge on Brent (operator, 2026-10-29); the
# tick uses the same distance in ATR terms elsewhere: 0.75 / Brent's 1h ATR14,
# about 1.3 ATR. Kept equal to desk_tick_sim.live_params().stop_buffer by a test.
BRENT_STOP_BUFFER = 0.75
BRENT = "xyz:BRENTOIL"
ATR_BUFFER_MULT = 1.3


def stop_buffer_for(coin: str, atr14: Optional[float]) -> Optional[float]:
    """0.75 on Brent; about 1.3 x the 1h ATR14 on any other desk's coin."""
    if coin == BRENT:
        return BRENT_STOP_BUFFER
    return round(ATR_BUFFER_MULT * atr14, 6) if atr14 else None


def next_trade(side: str, mid: float, low_24h: float, high_24h: float, *,
               thesis_entry: Optional[float], leverage: float, coin_max_leverage: Optional[float],
               stop_buffer: float, min_r: float = DEFAULT_MIN_R,
               offset_frac: float = TARGET_OFFSET_FRAC) -> Dict[str, Any]:
    """The tick's next trade for a `side` thesis. `ok` is True when the tick
    would send the market order at `mid` right now. Every other answer says
    why not (`blockers`) and where it would trade (`trigger_px`)."""
    if side not in ("long", "short"):
        return {"ok": False, "source": "desk_rules", "reason": f"the thesis is {side}: no trade until it calls a side"}
    if not coin_max_leverage:
        return {"ok": False, "source": "desk_rules", "reason": "venue max leverage is unavailable right now"}
    long = side == "long"
    struct = min(low_24h, mid) if long else max(high_24h, mid)
    stop = struct - stop_buffer if long else struct + stop_buffer
    structure_tp = high_24h if long else low_24h
    tp = round(offset_target(structure_tp, side, offset_frac), 6)
    if (tp <= mid) if long else (tp >= mid):
        return {"ok": False, "source": "desk_rules", "side": side, "mid": mid, "stop_px": stop,
                "tp_px": tp, "reason": "price is at the target already: nothing to buy into" if long
                else "price is at the target already: nothing to sell into"}

    now_r = check_levels(mid, stop, tp, min_r)
    now_lev = check_leverage(mid, stop, leverage, coin_max_leverage)
    chase = thesis_entry is not None and ((mid > thesis_entry) if long else (mid < thesis_entry))

    # The furthest mid that still pays min_r with these levels (the stop sits
    # under the 24h low, so it does not move while price stays above it):
    # long: (tp - p) = min_r (p - stop)  ->  p = (tp + min_r stop) / (1 + min_r)
    edge_stop = (low_24h - stop_buffer) if long else (high_24h + stop_buffer)
    trigger = (tp + min_r * edge_stop) / (1.0 + min_r)
    if thesis_entry is not None:
        trigger = min(trigger, thesis_entry) if long else max(trigger, thesis_entry)
    trig_lev = check_leverage(trigger, edge_stop, leverage, coin_max_leverage)

    blockers: List[str] = []
    if not now_r.get("ok"):
        blockers.append(f"pays {now_r.get('r_multiple', 0):.2f}R at {mid:g}, needs {min_r:g}R")
    if not now_lev.get("ok"):
        blockers.append(f"stop too wide for {leverage:g}x (max {now_lev.get('max_leverage_ok')}x)")
    if chase:
        blockers.append(f"mid {'above' if long else 'below'} the thesis entry {thesis_entry:g}: never chase")
    ok = not blockers

    # The leverage box prices the order the tick would send: at the mid when it
    # trades now, else at the trigger price.
    at_px, at_stop, at_lev = (mid, stop, now_lev) if ok else (trigger, edge_stop, trig_lev)
    r_at = abs(tp - at_px) / abs(at_px - at_stop) if at_px != at_stop else None
    verdict = ("buys now" if long else "sells now") if ok else (
        f"waits: {'buy' if long else 'sell'} at {trigger:.2f} or {'lower' if long else 'higher'}"
        + ("" if trig_lev.get("ok") else f", but no {leverage:g}x there (max {trig_lev.get('max_leverage_ok')}x)"))
    return {
        "ok": ok, "source": "desk_rules", "side": side, "leverage": leverage, "min_r": min_r,
        "mid": mid, "range_24h": {"low": low_24h, "high": high_24h}, "stop_buffer": stop_buffer,
        "stop_px": round(at_stop, 6), "tp_px": tp, "structure_tp_px": structure_tp, "targets": [tp],
        "entry_px": round(at_px, 6), "trigger_px": round(trigger, 6), "trigger_leverage_ok": bool(trig_lev.get("ok")),
        "r_now": now_r.get("r_multiple"), "blockers": blockers, "verdict": verdict,
        "liq_px": at_lev.get("liq_px"), "stop_frac_of_liq": at_lev.get("stop_frac_of_liq"),
        "max_leverage_ok": at_lev.get("max_leverage_ok"),
        "r_to_targets": [{"px": tp, "r_multiple": round(r_at, 2)}] if r_at is not None else [],
        "thesis_entry": thesis_entry,
    }


__all__ = ["next_trade", "stop_buffer_for", "BRENT_STOP_BUFFER"]
