"""Who holds the account's position slot, from this desk's point of view.

  python3 scripts/desk_slot.py --desk metals
  -> {"slot": "held", "holder": "xyz:BRENTOIL", "mine": false, "kind": "position", ...}

The desk-tick skill runs this FIRST. A desk whose instruments do not hold
the slot stops right there with one log line: no brief, no levels, no MCP
calls (operator rule, 2026-10-23: "the occupied slot should be the only
tick"). Read-only: the public info API for positions and resting entries
on every dex the account holds margin on; no key.

Per-desk slots (2026-10-31): with `desk_equity_share` saved (oil 95%,
silver 5%, services/strategies/desk_share.py) every desk with a share owns
its own slot, so only THIS desk's coins can hold it ("mode": "per_desk") and
oil holding Brent never stops the silver tick. A desk with no share while
shares are saved reads "slot": "inactive": the tick skips it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def slot_view(desk_coins: List[str], raws: List[Dict[str, Any]], own_slot: bool = False) -> Dict[str, Any]:
    """Pure: given this desk's instrument coins and the raw {state, orders}
    per dex, say whether the slot is held, by which coin, and whether that
    coin is one of ours. A position outranks a resting entry. `own_slot`
    (per-desk slots): only this desk's coins can hold its slot."""
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
    mode = {"mode": "per_desk"} if own_slot else {}
    mine_set = set(desk_coins)
    cand_pos = [c for c in positions if c in mine_set] if own_slot else positions
    cand_ent = [c for c in entries if c in mine_set] if own_slot else entries
    if cand_pos:
        holder, kind = cand_pos[0], "position"
    elif cand_ent:
        holder, kind = cand_ent[0], "resting_entry"
    else:
        return {"slot": "free", "holder": None, "mine": False, "kind": None,
                "positions": positions, "resting_entries": entries, **mode}
    return {"slot": "held", "holder": holder, "mine": holder in mine_set, "kind": kind,
            "positions": positions, "resting_entries": entries, **mode}


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
    from pathiel.agents.config_store import read_agent_config
    from services.strategies import desk_share
    try:
        shares = desk_share.desk_shares(read_agent_config())
    except desk_share.DeskShareError as e:
        print(json.dumps({"error": f"desk share: {e}", "desk": a.desk})); return 1
    if shares and a.desk not in shares:
        print(json.dumps({"slot": "inactive", "holder": None, "mine": False, "desk": a.desk,
                          "desk_coins": coins, "reason": "no desk_equity_share for this desk"}))
        return 0
    from services.accounts import positions as acc_pos
    fetch_raw = fetch_raw or acc_pos._fetch_raw
    dexes = acc_pos._dexes_with_margin(user) if dexes is None else dexes
    out = slot_view(coins, [fetch_raw(user, d) for d in dexes], own_slot=bool(shares))
    out.update({"desk": a.desk, "desk_coins": coins})
    if shares:
        out["equity_share"] = shares[a.desk]
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
