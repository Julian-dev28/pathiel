"""Bracket trades: one entry with an exact stop and take-profit, placed as
asked. The operator's direct-trade path (Claude places it via the MCP
`place_bracket` tool), separate from the bot's `execute`, which picks its own
ATR stop, ATR target and config leverage.

Everything here is deterministic: the R gate, the side, the one-position rule
and the post-close sweep. The judgment (which entry, which stop) comes in as
arguments, never from in here.

Exchange calls go through `ex`, the pathiel.client.exchange module by
default, so tests pass a fake.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_MIN_R = 1.5


def check_levels(entry_px: float, stop_px: float, tp_px: float,
                 min_r: float = DEFAULT_MIN_R) -> Dict[str, Any]:
    """Side and reward:risk from the three prices. ok False with a plain
    reason when they don't form a valid bracket or pay under `min_r`."""
    if min(entry_px, stop_px, tp_px) <= 0:
        return {"ok": False, "reason": "prices must be positive"}
    if stop_px < entry_px < tp_px:
        side = "long"
    elif tp_px < entry_px < stop_px:
        side = "short"
    else:
        return {"ok": False, "reason": "entry must sit between stop and target"}
    r = abs(tp_px - entry_px) / abs(entry_px - stop_px)
    out = {"ok": r + 1e-9 >= min_r, "side": side, "r_multiple": round(r, 2), "min_r": min_r}
    if not out["ok"]:
        out["reason"] = f"pays {r:.2f}R, under the {min_r}R minimum"
    return out


def place_bracket(coin: str, *, entry_px: Optional[float], stop_px: float, tp_px: float,
                  size: float, leverage: int, min_r: float = DEFAULT_MIN_R,
                  dry_run: bool = False, ex: Any = None) -> Dict[str, Any]:
    """Place one bracket. entry_px None = market now (IOC), checked against
    the live mid; otherwise a resting limit with stop and target attached.

    Refuses when the coin already has a position or a resting entry, so a
    repeated call (a loop tick, a retry) can never stack a second trade.
    """
    if ex is None:
        from pathiel.client import exchange as ex   # noqa: N813
    market = entry_px is None
    ref_px = float(ex.get_hl_price(coin)) if market else float(entry_px)
    gate = check_levels(ref_px, stop_px, tp_px, min_r)
    base = {"coin": coin, "mode": "market" if market else "limit", "entry_px": ref_px,
            "stop_px": stop_px, "tp_px": tp_px, "size": size, "leverage": leverage, **gate}
    if not gate["ok"]:
        return {**base, "placed": False}
    if size <= 0:
        return {**base, "ok": False, "placed": False, "reason": "size must be positive"}

    user = ex.resolve_user_address()
    pos = ex._position_size_on_coin(user, coin)
    if pos is None:
        return {**base, "ok": False, "placed": False, "reason": "position read failed; not trading blind"}
    if pos != 0:
        return {**base, "ok": False, "placed": False, "reason": f"already in a position ({pos}) on {coin}"}
    entries = [o for o in ex._resting_orders_for_coin(user, coin) if not o.get("reduceOnly")]
    if entries:
        return {**base, "ok": False, "placed": False,
                "reason": f"{len(entries)} entry order(s) already resting on {coin}; cancel first"}
    if dry_run:
        return {**base, "placed": False, "dry_run": True}

    ex.cancel_stranded_reduce_only_orders(coin)   # old triggers must not hit the new trade
    lev = ex.set_leverage(coin, int(leverage), is_cross=False, strict=True)
    if not lev.get("ok"):
        return {**base, "ok": False, "placed": False, "reason": f"set_leverage failed: {lev.get('error')}"}
    is_buy = gate["side"] == "long"

    if not market:
        res = ex.place_hl_bracket_order(coin, is_buy, size, float(entry_px), stop_px, tp_px)
        return {**base, "ok": bool(res.get("ok")), "placed": bool(res.get("entry", {}).get("ok")),
                "result": res}

    entry = ex.place_hl_order(is_buy, size, ref_px, coin)
    filled = float(entry.get("total_sz") or 0)
    if not entry.get("ok") or filled <= 0:
        return {**base, "ok": False, "placed": False, "result": {"entry": entry}}
    sl = ex.place_hl_trigger_order(is_buy, filled, stop_px, "sl", coin)
    tp = ex.place_hl_trigger_order(is_buy, filled, tp_px, "tp", coin)
    out = {**base, "ok": bool(sl.get("ok")), "placed": True, "filled_sz": filled,
           "avg_px": entry.get("avg_px"), "result": {"entry": entry, "sl": sl, "tp": tp}}
    if not sl.get("ok"):
        out["reason"] = "FILLED WITHOUT A STOP: place one now"
    return out


def sweep(coin: str, ex: Any = None) -> Dict[str, Any]:
    """One tick of housekeeping for `coin`: cancel reduce-only orders left on a
    flat coin (the sibling of a stop or target that fired), and report whether
    an open position has a stop. Never opens or closes a position."""
    if ex is None:
        from pathiel.client import exchange as ex   # noqa: N813
    user = ex.resolve_user_address()
    pos = ex._position_size_on_coin(user, coin)
    if pos is None:
        return {"coin": coin, "ok": False, "reason": "position read failed"}
    orders = ex._resting_orders_for_coin(user, coin)
    if pos == 0:
        n = ex.cancel_stranded_reduce_only_orders(coin)
        entries = [o for o in orders if not o.get("reduceOnly")]
        return {"coin": coin, "ok": True, "position": 0.0, "cancelled_stranded": n,
                "resting_entries": [{"oid": o.get("oid"), "px": o.get("limitPx"), "sz": o.get("sz")}
                                    for o in entries]}
    stops = [o for o in orders if o.get("reduceOnly") and "Stop" in str(o.get("orderType", ""))]
    tps = [o for o in orders if o.get("reduceOnly") and "Take Profit" in str(o.get("orderType", ""))]
    covered = sum(float(o.get("sz") or 0) for o in stops)
    return {"coin": coin, "ok": covered + 1e-9 >= abs(pos), "position": pos,
            "stop_px": [o.get("triggerPx") for o in stops], "tp_px": [o.get("triggerPx") for o in tps],
            "stop_covers": covered,
            **({} if covered + 1e-9 >= abs(pos) else {"reason": "POSITION NOT FULLY COVERED BY A STOP"})}
