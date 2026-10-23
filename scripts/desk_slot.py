"""Who holds the account's position slot, from this desk's point of view.

  python3 scripts/desk_slot.py --desk metals
  -> {"slot": "held", "holder": "xyz:BRENTOIL", "mine": false, "kind": "position", ...}

The desk-tick skill runs this FIRST. A desk whose instruments do not hold
the slot stops right there with one log line: no brief, no levels, no MCP
calls (operator rule, 2026-10-23: "the occupied slot should be the only
tick"). Read-only: the public info API for positions and resting entries
on every dex the account holds margin on; no key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def slot_view(desk_coins: List[str], raws: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: given this desk's instrument coins and the raw {state, orders}
    per dex, say whether the slot is held, by which coin, and whether that
    coin is one of ours. A position outranks a resting entry."""
    positions: List[str] = []
    entries: List[str] = []
    for raw in raws:
        for row in (raw.get("state") or {}).get("assetPositions") or []:
            pos = (row or {}).get("position") or {}
            try:
                if pos.get("coin") and float(pos.get("szi") or 0) != 0:
                    positions.append(pos["coin"])
            except (TypeError, ValueError):
                continue
        for o in raw.get("orders") or []:
            if o.get("coin") and not o.get("reduceOnly"):
                entries.append(o["coin"])
    positions, entries = sorted(set(positions)), sorted(set(entries))
    if positions:
        holder, kind = positions[0], "position"
    elif entries:
        holder, kind = entries[0], "resting_entry"
    else:
        return {"slot": "free", "holder": None, "mine": False, "kind": None,
                "positions": positions, "resting_entries": entries}
    return {"slot": "held", "holder": holder, "mine": holder in set(desk_coins), "kind": kind,
            "positions": positions, "resting_entries": entries}


def main(argv: Optional[List[str]] = None, fetch_raw: Optional[Callable[[str, str], Dict[str, Any]]] = None,
         dexes: Optional[List[str]] = None, user: Optional[str] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desk", required=True)
    a = ap.parse_args(argv)
    from services.strategies.desks import get_spec
    coins = [i.coin for i in get_spec(a.desk).instruments]
    if user is None:
        from services.accounts.view import get_effective_active_account
        acct = get_effective_active_account()
        if acct is None:
            print(json.dumps({"error": "no active account"})); return 1
        user = acct.main_address
    from services.accounts import positions as acc_pos
    fetch_raw = fetch_raw or acc_pos._fetch_raw
    dexes = acc_pos._dexes_with_margin(user) if dexes is None else dexes
    out = slot_view(coins, [fetch_raw(user, d) for d in dexes])
    out.update({"desk": a.desk, "desk_coins": coins})
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
