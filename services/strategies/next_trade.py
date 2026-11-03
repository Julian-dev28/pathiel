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

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import (DEFAULT_MIN_R, TARGET_OFFSET_FRAC, check_leverage,
                                    check_levels, offset_target)

# Stop distance beyond the 24h edge on Brent (operator, 2026-10-29); the
# tick uses the same distance in ATR terms elsewhere: 0.75 / Brent's 1h ATR14,
# about 1.3 ATR. Kept equal to desk_tick_sim.live_params().stop_buffer by a test.
BRENT_STOP_BUFFER = 0.75
BRENT = "xyz:BRENTOIL"
ATR_BUFFER_MULT = 1.3
# Never under 0.8% of the mid on a non-Brent coin (operator, 2026-11-02:
# "yeah"). The live per-tick 1.3 x ATR buffer ran a median 0.11 on silver
# (10-31 to 11-02) where the 30-day test used about 0.41: mid-range entries
# passed 2R and noise stopped them (two shorts that night). The floor beat the
# live rule on 8 of 8 silver backtests ($253 vs $143 on 30 theses from $64)
# and leaves Brent trade-for-trade unchanged
# (docs/strategy/SILVER_STOP_FLOOR_TEST_2026-11-02.md).
STOP_BUFFER_FLOOR_FRAC = 0.008


def stop_buffer_for(coin: str, atr14: Optional[float],
                    mid: Optional[float] = None) -> Optional[float]:
    """0.75 on Brent; on any other desk's coin the larger of 1.3 x the 1h
    ATR14 and 0.8% of `mid` (when a mid is given)."""
    if coin == BRENT:
        return BRENT_STOP_BUFFER
    if not atr14:
        return None
    buf = ATR_BUFFER_MULT * atr14
    if mid:
        buf = max(buf, STOP_BUFFER_FLOOR_FRAC * float(mid))
    return round(buf, 6)


# Leverage fit (operator, 2026-11-04: "TURN ON LEVERAGE FIT"). A Brent entry
# whose stop is too far for 20x trades at 15x instead of being skipped; every
# trade that fits 20x stays 20x, and dollar risk stays the 15% cap (size is
# the same risk at any leverage). The final test (FINAL_STRATEGY_TEST_2026-11-04.md,
# lev_fit_eia) beat live on 4 of 4 thesis datasets with candidates 20/15/10/8/5
# but never used anything under 15x; the operator wants no lower, so 20/15.
LEVERAGE_FIT = (20.0, 15.0)
LEVERAGE_FIT_COINS = frozenset({BRENT})


def leverage_fit_for(coin: str) -> Tuple[float, ...]:
    """The candidates a coin's new entries fit their leverage from; () = the
    fixed configured leverage (silver and every other coin, untested there)."""
    return LEVERAGE_FIT if coin in LEVERAGE_FIT_COINS else ()


def fit_leverage(entry: float, stop: float, leverage: float, coin_max_leverage: Optional[float],
                 candidates: Sequence[float] = ()) -> Tuple[float, Dict[str, Any]]:
    """(leverage, check_leverage gate) for a new entry. With no candidates,
    the configured `leverage`. Otherwise the highest candidate, capped at
    `leverage` and the venue max, whose gate passes; when none passes, the
    lowest candidate and its failing gate. Mirrors desk_tick_sim.
    _fit_leverage_setup. Pure."""
    if not coin_max_leverage:
        return leverage, {"ok": False, "reason": "venue max leverage is unavailable"}
    if not candidates:
        return leverage, check_leverage(entry, stop, leverage, coin_max_leverage)
    cap = min(leverage, coin_max_leverage) if coin_max_leverage else leverage
    cands = sorted({float(c) for c in candidates if 0 < c <= cap}, reverse=True) or [float(leverage)]
    gate: Dict[str, Any] = {}
    for lev in cands:
        gate = check_leverage(entry, stop, lev, coin_max_leverage)
        if gate.get("ok"):
            return lev, gate
    return cands[-1], gate


def next_trade(side: str, mid: float, low_24h: float, high_24h: float, *,
               thesis_entry: Optional[float], leverage: float, coin_max_leverage: Optional[float],
               stop_buffer: float, min_r: float = DEFAULT_MIN_R,
               offset_frac: float = TARGET_OFFSET_FRAC,
               leverage_fit: Sequence[float] = ()) -> Dict[str, Any]:
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
    now_x, now_lev = fit_leverage(mid, stop, leverage, coin_max_leverage, leverage_fit)
    chase = thesis_entry is not None and ((mid > thesis_entry) if long else (mid < thesis_entry))

    # The furthest mid that still pays min_r with these levels (the stop sits
    # under the 24h low, so it does not move while price stays above it):
    # long: (tp - p) = min_r (p - stop)  ->  p = (tp + min_r stop) / (1 + min_r)
    edge_stop = (low_24h - stop_buffer) if long else (high_24h + stop_buffer)
    trigger = (tp + min_r * edge_stop) / (1.0 + min_r)
    if thesis_entry is not None:
        trigger = min(trigger, thesis_entry) if long else max(trigger, thesis_entry)
    trig_x, trig_lev = fit_leverage(trigger, edge_stop, leverage, coin_max_leverage, leverage_fit)
    floor_x = min([c for c in leverage_fit if c <= leverage] or [leverage])   # the lowest leverage the rule uses

    blockers: List[str] = []
    if not now_r.get("ok"):
        blockers.append(f"pays {now_r.get('r_multiple', 0):.2f}R at {mid:g}, needs {min_r:g}R")
    if not now_lev.get("ok"):
        blockers.append(f"stop too wide for {floor_x:g}x (max {now_lev.get('max_leverage_ok')}x)")
    if chase:
        blockers.append(f"mid {'above' if long else 'below'} the thesis entry {thesis_entry:g}: never chase")
    ok = not blockers

    # The leverage box prices the order the tick would send: at the mid when it
    # trades now, else at the trigger price.
    at_px, at_stop, at_lev, at_x = (mid, stop, now_lev, now_x) if ok else (trigger, edge_stop, trig_lev, trig_x)
    r_at = abs(tp - at_px) / abs(at_px - at_stop) if at_px != at_stop else None
    verdict = ("buys now" if long else "sells now") if ok else (
        f"waits: {'buy' if long else 'sell'} at {trigger:.2f} or {'lower' if long else 'higher'}"
        + ("" if trig_lev.get("ok") else f", but no {floor_x:g}x there (max {trig_lev.get('max_leverage_ok')}x)"))
    return {
        "ok": ok, "source": "desk_rules", "side": side, "leverage": at_x, "min_r": min_r,
        "mid": mid, "range_24h": {"low": low_24h, "high": high_24h}, "stop_buffer": stop_buffer,
        "stop_px": round(at_stop, 6), "tp_px": tp, "structure_tp_px": structure_tp, "targets": [tp],
        "entry_px": round(at_px, 6), "trigger_px": round(trigger, 6), "trigger_leverage_ok": bool(trig_lev.get("ok")),
        "r_now": now_r.get("r_multiple"), "blockers": blockers, "verdict": verdict,
        "liq_px": at_lev.get("liq_px"), "stop_frac_of_liq": at_lev.get("stop_frac_of_liq"),
        "max_leverage_ok": at_lev.get("max_leverage_ok"),
        "r_to_targets": [{"px": tp, "r_multiple": round(r_at, 2)}] if r_at is not None else [],
        "thesis_entry": thesis_entry,
    }


def live_rule_line(nt: Dict[str, Any]) -> str:
    """One markdown line: where the tick actually trades, from a next_trade()
    answer. Operator, 2026-11-02: the thesis said "short at 109.30" while the
    live rule (stop past the 24h edge, 2R to the far edge) only sells near
    111.62, and the operator was told the wrong price. This line sits under
    the thesis in desk_brief so the two can never be confused again."""
    if not nt.get("side"):
        return f"Live rule: no trade ({nt.get('reason') or 'no side'})."
    if nt.get("reason") and nt.get("trigger_px") is None:
        return f"Live rule: no trade now ({nt['reason']})."
    stop, tp = nt.get("stop_px"), nt.get("tp_px")
    r = (nt.get("r_to_targets") or [{}])[0].get("r_multiple")
    r_s = f", {r:g}R" if r is not None else ""
    head = f"Live rule: {nt.get('verdict')} (stop {stop:g}, target {tp:g}{r_s})"
    te = nt.get("thesis_entry")
    if not nt.get("ok") and te is not None and nt.get("trigger_px") is not None \
            and abs(float(nt["trigger_px"]) - float(te)) > 1e-9:
        head += f". The tick trades there, not at the thesis price {te:g}"
    return head + "."


__all__ = ["next_trade", "stop_buffer_for", "live_rule_line", "fit_leverage", "leverage_fit_for",
           "BRENT_STOP_BUFFER", "LEVERAGE_FIT"]
