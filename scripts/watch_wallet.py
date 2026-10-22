"""Read-only check of any Hyperliquid wallet: positions on the main dex and
every HIP-3 dex it holds margin on, plus the reduce-only orders protecting
them. Flags a position with no stop. Used by the oil-tick skill to watch
Julien's own 20x wallet, which pathiel never trades.

  python3 scripts/watch_wallet.py 0x8debbedbdc603769549a6277fed38dae65185019
  python3 scripts/watch_wallet.py ADDR --dexes xyz        # skip the main dex

Public info API only: no key, no account, nothing is placed or cancelled.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional

INFO_URL = "https://api.hyperliquid.xyz/info"


def _post(body: Dict[str, Any]) -> Any:
    # curl, not urllib: the system Python has no CA bundle on this machine.
    out = subprocess.run(["curl", "-sS", "-m", "10", INFO_URL, "-H", "Content-Type: application/json",
                          "-d", json.dumps(body)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def fetch(user: str, dex: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {"type": "clearinghouseState", "user": user}
    orders: Dict[str, Any] = {"type": "frontendOpenOrders", "user": user}
    if dex:
        body["dex"] = dex
        orders["dex"] = dex
    return {"state": _post(body), "orders": _post(orders)}


# ----------------------------------------------------------------- pure part
def assess(state: Dict[str, Any], orders: List[Dict[str, Any]], dex: str = "") -> Dict[str, Any]:
    """Positions with their protection. `naked` is true for any position whose
    reduce-only stop orders cover less than its size."""
    ms = state.get("marginSummary") or {}
    positions = []
    for row in state.get("assetPositions") or []:
        p = (row or {}).get("position") or {}
        coin = p.get("coin")
        try:
            szi = float(p.get("szi") or 0)
        except (TypeError, ValueError):
            continue
        if not coin or szi == 0:
            continue
        mine = [o for o in orders if o.get("coin") == coin and o.get("reduceOnly")]
        stops = [o for o in mine if "Stop" in str(o.get("orderType", ""))]
        tps = [o for o in mine if "Take Profit" in str(o.get("orderType", ""))]
        covered = sum(float(o.get("sz") or 0) for o in stops)
        lev = p.get("leverage") or {}
        entry = float(p.get("entryPx") or 0)
        liq = p.get("liquidationPx")
        liq_f = float(liq) if liq not in (None, "") else None
        positions.append({
            "coin": coin, "side": "long" if szi > 0 else "short", "size": abs(szi), "entry_px": entry,
            "leverage": lev.get("value") if isinstance(lev, dict) else lev,
            "margin_type": lev.get("type") if isinstance(lev, dict) else None,
            "notional_usd": round(abs(float(p.get("positionValue") or 0)), 2),
            "upnl_usd": round(float(p.get("unrealizedPnl") or 0), 2),
            "liq_px": liq_f,
            "liq_dist_pct": (round(abs(entry - liq_f) / entry * 100, 3) if liq_f and entry else None),
            "stop_px": [o.get("triggerPx") for o in stops], "tp_px": [o.get("triggerPx") for o in tps],
            "stop_covers": covered, "naked": covered + 1e-9 < abs(szi),
        })
    return {"dex": dex or "main", "equity_usd": float(ms.get("accountValue") or 0),
            "notional_usd": float(ms.get("totalNtlPos") or 0), "positions": positions,
            "any_naked": any(p["naked"] for p in positions)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("user")
    ap.add_argument("--dexes", default="main,xyz", help="comma list; 'main' is the main perp dex")
    a = ap.parse_args(argv)
    out = {"user": a.user, "accounts": []}
    for d in [x.strip() for x in a.dexes.split(",") if x.strip()]:
        dex = "" if d == "main" else d
        got = fetch(a.user, dex)
        out["accounts"].append(assess(got["state"], got["orders"] if isinstance(got["orders"], list) else [], dex))
    out["any_naked"] = any(acc["any_naked"] for acc in out["accounts"])
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
