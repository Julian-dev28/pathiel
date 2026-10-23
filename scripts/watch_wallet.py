"""Read-only check of any Hyperliquid wallet: positions on the main dex and
every HIP-3 dex it holds margin on, plus the reduce-only orders protecting
them. Flags a position with no stop. Used by the oil-tick skill to watch
Julien's own 20x wallet, which pathiel never trades.

  python3 scripts/watch_wallet.py 0x8debbedbdc603769549a6277fed38dae65185019
  python3 scripts/watch_wallet.py ADDR --dexes xyz        # skip the main dex

Public info API only: no key, no account, nothing is placed or cancelled.

This is a thin CLI: the assessment logic (`assess`) lives in
services/accounts/positions.py, which the dashboard's
GET /api/accounts/{id}/positions route also calls, so the two never drift
apart. Only the network leg (curl-over-subprocess, needed because the
system Python here has no CA bundle) stays in this file.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.accounts.positions import assess  # noqa: E402

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
