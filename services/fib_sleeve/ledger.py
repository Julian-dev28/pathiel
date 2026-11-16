"""The sleeve's record, rebuilt from the sub-account's own fills every tick,
and the kill switch read off it. Pure: fills in, trades and a verdict out.

A trade is one round trip on the coin: from flat to flat. Its net is the
venue's closedPnl summed over its fills less every fee. A trade that ends
under zero is a loss, a break-even ratchet exit included (it pays the fees).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.fib_sleeve.config import SleeveConfig

FLAT = 1e-9


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def coin_names(coin: str) -> Tuple[str, ...]:
    return (coin, coin.split(":", 1)[1]) if ":" in coin else (coin,)


def round_trips(fills: Sequence[Dict[str, Any]], coin: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(closed trades oldest first, the open trade or None) from
    userFillsByTime rows. Position after a fill = startPosition + the signed
    size; a trade closes when that returns to zero. A fill that crosses
    through zero closes one trade and opens the next with the remainder's
    fee left on the closing trade (the venue reports one fee per fill)."""
    names = coin_names(coin)
    rows = sorted((f for f in fills if isinstance(f, dict) and f.get("coin") in names),
                  key=lambda f: (int(f.get("time") or 0), int(f.get("tid") or 0)))
    closed: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for f in rows:
        signed = _f(f.get("sz")) * (1.0 if f.get("side") == "B" else -1.0)
        before = _f(f.get("startPosition"))
        after = before + signed
        t = int(f.get("time") or 0)
        if cur is None:
            cur = {"open_ms": t, "side": "long" if (before if abs(before) > FLAT else signed) > 0 else "short",
                   "entry_px": _f(f.get("px")), "size": 0.0, "closed_pnl": 0.0, "fees": 0.0, "fills": 0,
                   "partial_history": abs(before) > FLAT}
        cur["closed_pnl"] += _f(f.get("closedPnl"))
        cur["fees"] += _f(f.get("fee"))
        cur["fills"] += 1
        cur["size"] = round(max(cur["size"], abs(before), abs(after)), 8)
        flipped = abs(before) > FLAT and abs(after) > FLAT and (before > 0) != (after > 0)
        if abs(after) <= FLAT or flipped:
            cur.update({"close_ms": t, "exit_px": _f(f.get("px")),
                        "net_usd": round(cur["closed_pnl"] - cur["fees"], 6)})
            closed.append(cur)
            cur = None
            if flipped:
                cur = {"open_ms": t, "side": "long" if after > 0 else "short", "entry_px": _f(f.get("px")),
                       "size": abs(after), "closed_pnl": 0.0, "fees": 0.0, "fills": 0, "partial_history": False}
    return closed, cur


def summary(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    nets = [t["net_usd"] for t in trades]
    streak = 0
    for n in reversed(nets):
        if n < 0:
            streak += 1
        else:
            break
    return {"trades": len(nets), "wins": sum(1 for n in nets if n > 0), "net_usd": round(sum(nets), 4),
            "consecutive_losses": streak}


def kill_reason(cfg: SleeveConfig, trades: Sequence[Dict[str, Any]], starting_equity: Optional[float],
                equity_now: Optional[float], flat: bool) -> Optional[str]:
    """Why the sleeve must stop taking new trades, or None.

    1. `max_consecutive_losses` losing round trips in a row.
    2. Realized loss since the start of `max_drawdown_frac` of the starting
       equity, from the fills (independent of how the account mode reports
       margin while a position is open).
    3. Flat, and the account's equity sits `max_drawdown_frac` under the
       starting equity (catches funding and anything the fills miss). Read
       only when flat: a flat account's equity is one unambiguous number.
    """
    s = summary(trades)
    if s["consecutive_losses"] >= cfg.max_consecutive_losses:
        return f"{s['consecutive_losses']} consecutive losses (limit {cfg.max_consecutive_losses})"
    if starting_equity and starting_equity > 0:
        if -s["net_usd"] >= cfg.max_drawdown_frac * starting_equity - 1e-9:
            return (f"realized loss ${-s['net_usd']:.2f} is {-s['net_usd'] / starting_equity:.1%} of the "
                    f"${starting_equity:.2f} starting equity (limit {cfg.max_drawdown_frac:.0%})")
        if flat and equity_now is not None and equity_now <= (1.0 - cfg.max_drawdown_frac) * starting_equity + 1e-9:
            return (f"equity ${equity_now:.2f} is {1.0 - equity_now / starting_equity:.1%} under the "
                    f"${starting_equity:.2f} starting equity (limit {cfg.max_drawdown_frac:.0%})")
    return None
