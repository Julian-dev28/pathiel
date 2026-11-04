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
SILVER = "xyz:SILVER"
# Silver joined 2026-11-04 with the breakout (operator: "turn on silver
# breakout, chase OK on silver"): the live silver rule with 20/15 scored the
# same as the tested combo_best2 on every thesis dataset ($273.91 vs live
# $132.92 on the 30-theses replay, journal tie) and tied live on the controls
# (docs/strategy/SILVER_STRATEGY_TEST_2026-11-04.md, "Live config").
LEVERAGE_FIT_COINS = frozenset({BRENT, SILVER})

# Breakout entry (silver only, since 2026-11-04). The backtest's
# `pullback_entry="a5"` (desk_tick_sim.pullback_setup), as tested in
# combo_best2: when the main rule refuses (mid past the thesis entry, or
# under 2R), sell a short thesis at the mid once the last CLOSED 1h bar
# closed under the low of the 24 bars before it (a long: over their high).
# Stop: the stop buffer beyond the last 4 closed bars' high (a long: low),
# or beyond the mid if the mid is already past it. Target: the broken level
# minus the height of the range that broke (a measured move), with the
# usual 0.5% offset. 2R and the 20x line at the configured leverage; no
# leverage fit on this entry (the test never fitted it). This is the one
# place a new entry may sell under a short thesis's entry ("chase OK on
# silver"); Brent keeps never-chase.
# OFF since 2026-11-04 evening (operator: "DO WHAT U THINK IS MORE
# PROFITABLE"): the breakout sells AFTER the 24h low broke, which is the
# review's one LEAK (range=past own edge, see NO_BREAK_ENTRY). Silver's two
# breakout-day shorts stopped. The code stays for the backtest's parity
# tests; an empty set means no live coin takes it.
BREAKOUT_COINS: frozenset = frozenset()
BREAKOUT_LOOKBACK_H = 24

# No new entry once the mid is past the 24h range's own edge (a long under
# the 24h low, a short over the 24h high). Operator, 2026-11-04: "go", then
# "DO WHAT U THINK IS MORE PROFITABLE". The trade review's first LEAK:
# range=past own edge, 10 real trades, 1 win, -$117.26, mean -0.97R,
# p=0.002 (docs/strategy/learning/REVIEW_2026-11-04.md); every other trade
# together made +$62. Market entries inside the range are unchanged. The
# backtest (LOSS_AUDIT_2026-11-02.md, "No buy under the 24h low") scored it
# Brent 1/8 geo 0.66, silver 3/8 geo 1.26, and a resting limit at the edge
# worse on both (2/8 0.52, 2/8 0.40); the real fills since the 10-30 switch
# to market entries disagree with the Brent replay, so the live rule
# follows the fills. desk_tick_sim.live_params() sets no_break_entry to match.
NO_BREAK_ENTRY = True
BREAKOUT_STOP_H = 4


def leverage_fit_for(coin: str) -> Tuple[float, ...]:
    """The candidates a coin's new entries fit their leverage from; () = the
    fixed configured leverage (silver and every other coin, untested there)."""
    return LEVERAGE_FIT if coin in LEVERAGE_FIT_COINS else ()


def breakout_for(coin: str) -> bool:
    """True when `coin`'s flat ticks may take the breakout entry."""
    return coin in BREAKOUT_COINS


def breakout_levels(closed_1h: Sequence[Dict[str, Any]], lookback_h: int = BREAKOUT_LOOKBACK_H,
                    stop_h: int = BREAKOUT_STOP_H) -> Optional[Dict[str, float]]:
    """The numbers the breakout entry reads, from CLOSED 1h candles (oldest
    first, HL rows with h/l/c): the low and high of the `lookback_h` bars
    before the last one, the last close, and the last `stop_h` bars' high
    and low (the last bar included). None with too little history. Pure."""
    if len(closed_1h) < max(lookback_h + 1, stop_h):
        return None
    prior = closed_1h[-(lookback_h + 1):-1]
    recent = closed_1h[-stop_h:]
    return {"prior_low": min(float(c["l"]) for c in prior), "prior_high": max(float(c["h"]) for c in prior),
            "last_close": float(closed_1h[-1]["c"]),
            "recent_high": max(float(c["h"]) for c in recent), "recent_low": min(float(c["l"]) for c in recent),
            "lookback_h": lookback_h, "stop_h": stop_h}


def breakout_trade(side: str, mid: float, bo: Optional[Dict[str, float]], *, leverage: float,
                   coin_max_leverage: Optional[float], stop_buffer: float, min_r: float = DEFAULT_MIN_R,
                   offset_frac: float = TARGET_OFFSET_FRAC) -> Dict[str, Any]:
    """The breakout entry at `mid` from `breakout_levels()`: `ok` with the
    stop, target and R, or `ok: False` with the reason. Mirrors
    desk_tick_sim.pullback_setup mode a5 with pb_target="measured" and no
    pb_fit_leverage (a parity test keeps them equal). Pure."""
    if side not in ("long", "short"):
        return {"ok": False, "reason": f"the thesis is {side}"}
    if not bo:
        return {"ok": False, "reason": "not enough closed 1h candles for the breakout"}
    long = side == "long"
    level = bo["prior_high"] if long else bo["prior_low"]
    if not ((bo["last_close"] > level) if long else (bo["last_close"] < level)):
        return {"ok": False, "level": level,
                "reason": f"no 1h close {'over' if long else 'under'} the prior {bo['lookback_h']}h "
                          f"{'high' if long else 'low'} {level:g} (last close {bo['last_close']:g})"}
    height = bo["prior_high"] - bo["prior_low"]
    struct = min(bo["recent_low"], mid) if long else max(bo["recent_high"], mid)
    stop = struct - stop_buffer if long else struct + stop_buffer
    structure_tp = level + height if long else level - height
    tp = round(offset_target(structure_tp, side, offset_frac), 6)
    base = {"level": level, "stop_px": round(stop, 6), "tp_px": tp, "structure_tp_px": round(structure_tp, 6)}
    if (tp <= mid) if long else (tp >= mid):
        return {**base, "ok": False, "reason": "the measured target is already behind the mid"}
    if not coin_max_leverage:
        return {**base, "ok": False, "reason": "venue max leverage is unavailable"}
    lev_gate = check_leverage(mid, stop, leverage, coin_max_leverage)
    if not lev_gate.get("ok"):
        return {**base, "ok": False, "lev_gate": lev_gate,
                "reason": f"breakout stop too wide for {leverage:g}x (max {lev_gate.get('max_leverage_ok')}x)"}
    r = check_levels(mid, stop, tp, min_r)
    if not r.get("ok"):
        return {**base, "ok": False, "lev_gate": lev_gate,
                "reason": f"breakout pays {r.get('r_multiple', 0):.2f}R, needs {min_r:g}R"}
    return {**base, "ok": True, "lev_gate": lev_gate, "r_multiple": r.get("r_multiple"), "leverage": leverage}


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
               leverage_fit: Sequence[float] = (),
               breakout: Optional[Dict[str, float]] = None,
               no_break: bool = NO_BREAK_ENTRY) -> Dict[str, Any]:
    """The tick's next trade for a `side` thesis. `ok` is True when the tick
    would send the market order at `mid` right now. Every other answer says
    why not (`blockers`) and where it would trade (`trigger_px`).
    `breakout`: `breakout_levels()` for a coin with the breakout entry
    (`breakout_for`), None elsewhere. When the main rule refuses for chase or
    R (the backtest's PB_TRIGGERS, in its gate order: chase, then the
    leverage line, then R), the breakout is tried; `source: "breakout"`
    when it trades. `no_break` (NO_BREAK_ENTRY, live): no entry while the
    mid is past the 24h range's own edge."""
    out = _main_rule(side, mid, low_24h, high_24h, thesis_entry=thesis_entry, leverage=leverage,
                     coin_max_leverage=coin_max_leverage, stop_buffer=stop_buffer, min_r=min_r,
                     offset_frac=offset_frac, leverage_fit=leverage_fit, no_break=no_break)
    pb_trigger = out.pop("_pb_trigger", False)
    trigger_lev = out.pop("_trigger_lev", None) or leverage
    if breakout is None or out.get("ok") or not pb_trigger:
        return out
    bo = breakout_trade(side, mid, breakout, leverage=trigger_lev, coin_max_leverage=coin_max_leverage,
                        stop_buffer=stop_buffer, min_r=min_r, offset_frac=offset_frac)
    if not bo.get("ok"):
        out["breakout"] = {"ok": False, "reason": bo["reason"], "level": bo.get("level")}
        if bo.get("level") is not None and "stop_px" not in bo:
            long = side == "long"
            out["verdict"] = (out.get("verdict") or "") + (
                f"; or {'buys' if long else 'sells'} a 1h close {'over' if long else 'under'} {bo['level']:g}")
        return out
    gate = bo["lev_gate"]
    long = side == "long"
    return {
        "ok": True, "source": "breakout", "side": side, "leverage": bo["leverage"], "min_r": min_r,
        "mid": mid, "range_24h": {"low": low_24h, "high": high_24h}, "stop_buffer": stop_buffer,
        "stop_px": bo["stop_px"], "tp_px": bo["tp_px"], "structure_tp_px": bo["structure_tp_px"],
        "targets": [bo["tp_px"]], "entry_px": mid, "trigger_px": mid, "trigger_leverage_ok": True,
        "r_now": bo["r_multiple"], "blockers": [], "rule_blockers": out.get("blockers") or [out.get("reason")],
        "verdict": (f"{'buys' if long else 'sells'} now: breakout, 1h close "
                    f"{'over' if long else 'under'} the prior 24h {'high' if long else 'low'} {bo['level']:g}"),
        "liq_px": gate.get("liq_px"), "stop_frac_of_liq": gate.get("stop_frac_of_liq"),
        "max_leverage_ok": gate.get("max_leverage_ok"),
        "r_to_targets": [{"px": bo["tp_px"], "r_multiple": bo["r_multiple"]}], "thesis_entry": thesis_entry,
        "breakout": {"ok": True, "level": bo["level"]},
    }


def _main_rule(side: str, mid: float, low_24h: float, high_24h: float, *,
               thesis_entry: Optional[float], leverage: float, coin_max_leverage: Optional[float],
               stop_buffer: float, min_r: float, offset_frac: float,
               leverage_fit: Sequence[float], no_break: bool = NO_BREAK_ENTRY) -> Dict[str, Any]:
    """The 24h-range rule (the docstring of `next_trade`). Adds `_pb_trigger`
    (would the backtest try its breakout after this refusal) and
    `_trigger_lev` (the leverage that refusal was judged at) for next_trade."""
    if side not in ("long", "short"):
        return {"ok": False, "source": "desk_rules", "reason": f"the thesis is {side}: no trade until it calls a side"}
    if not coin_max_leverage:
        return {"ok": False, "source": "desk_rules", "reason": "venue max leverage is unavailable right now"}
    long = side == "long"
    struct = min(low_24h, mid) if long else max(high_24h, mid)
    stop = struct - stop_buffer if long else struct + stop_buffer
    structure_tp = high_24h if long else low_24h
    tp = round(offset_target(structure_tp, side, offset_frac), 6)
    chase = thesis_entry is not None and ((mid > thesis_entry) if long else (mid < thesis_entry))
    now_x, now_lev = fit_leverage(mid, stop, leverage, coin_max_leverage, leverage_fit)
    # The backtest's gate order (desk_tick_sim._setup): chase first, then the
    # leverage line, then R. Its breakout answers a chase or an R refusal only.
    pb_trigger = chase or bool(now_lev.get("ok"))
    trigger_lev = leverage if chase else now_x
    if leverage_fit and chase:
        trigger_lev = max([c for c in leverage_fit if c <= min(leverage, coin_max_leverage)] or [leverage])
    if (tp <= mid) if long else (tp >= mid):
        return {"ok": False, "source": "desk_rules", "side": side, "mid": mid, "stop_px": stop,
                "tp_px": tp, "reason": "price is at the target already: nothing to buy into" if long
                else "price is at the target already: nothing to sell into",
                "_pb_trigger": pb_trigger, "_trigger_lev": trigger_lev}

    now_r = check_levels(mid, stop, tp, min_r)

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
    edge = low_24h if long else high_24h
    broke = no_break and ((mid < edge) if long else (mid > edge))
    if broke:
        blockers.append(f"mid {mid:g} {'under the 24h low' if long else 'over the 24h high'} "
                        f"{edge:g}: no entry after a break")
    ok = not blockers

    # The leverage box prices the order the tick would send: at the mid when it
    # trades now, else at the trigger price.
    at_px, at_stop, at_lev, at_x = (mid, stop, now_lev, now_x) if ok else (trigger, edge_stop, trig_lev, trig_x)
    r_at = abs(tp - at_px) / abs(at_px - at_stop) if at_px != at_stop else None
    verdict = ("buys now" if long else "sells now") if ok else (
        f"waits: {'buy' if long else 'sell'} at {trigger:.2f} or {'lower' if long else 'higher'}"
        + ("" if trig_lev.get("ok") else f", but no {floor_x:g}x there (max {trig_lev.get('max_leverage_ok')}x)"))
    if broke:
        verdict = (f"waits: price broke the 24h {'low' if long else 'high'} {edge:g}; no entry until it "
                   f"trades back {'above' if long else 'below'} it, then {'buy' if long else 'sell'} "
                   f"at {trigger:.2f} or {'lower' if long else 'higher'}")
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
        "_pb_trigger": (not ok) and pb_trigger, "_trigger_lev": trigger_lev,
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
           "breakout_for", "breakout_levels", "breakout_trade",
           "BRENT_STOP_BUFFER", "LEVERAGE_FIT", "BREAKOUT_COINS", "BREAKOUT_LOOKBACK_H", "BREAKOUT_STOP_H",
           "NO_BREAK_ENTRY"]
