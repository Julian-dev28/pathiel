"""Is the oil futures weekend open right now, and must the desk go flat?

  python3 scripts/weekend.py --coin xyz:BRENTOIL
  -> {"coin": "xyz:BRENTOIL", "flatten": true, "window": {"start_utc": "2026-11-06 21:00Z",
      "until_utc": "2026-11-08 23:00Z", ...}, "next": null, "utc": "2026-11-07 12:00Z"}

Operator, 2026-11-05: "weekend flat". The desk-tick agent runs this every
tick (pure, no network, no key) and closes an open oil position with
close_position only when `flatten` is true AND it holds one; `place_bracket`
refuses new oil entries inside the same window by itself. `flatten` is
false for every non-oil coin (silver and gold trade the weekend). `next`
is the coming window when it starts inside --warn-hours (default 24), for
the operator sentence. The window is services/strategies/weekend.py's,
the same one the backtest's `weekend_off="flatten"` used.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOUR_MS = 3_600_000


def answer(coin: str, at_ms: int, warn_hours: float = 24.0) -> Dict[str, Any]:
    """Pure: the agent's one answer for `coin` at `at_ms`."""
    from services.strategies.weekend import OIL_COINS, next_oil_weekend, oil_weekend
    utc = datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    out: Dict[str, Any] = {"coin": coin, "oil": coin in OIL_COINS, "flatten": False, "window": None,
                           "next": None, "utc": utc}
    active = oil_weekend(coin, at_ms)
    if active:
        out["flatten"] = True
        out["window"] = {k: active[k] for k in ("start_ms", "end_ms", "start_utc", "until_utc", "reason")}
        out["what"] = ("close an open position with close_position, cancel a resting entry, "
                       "then bracket_sweep; no new entry or top-up until " + active["until_utc"])
        return out
    nxt = next_oil_weekend(coin, at_ms)
    if nxt and nxt["start_ms"] - at_ms <= warn_hours * HOUR_MS:
        out["next"] = {k: nxt[k] for k in ("start_ms", "end_ms", "start_utc", "until_utc")}
        out["next"]["in_hours"] = round((nxt["start_ms"] - at_ms) / HOUR_MS, 2)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coin", required=True, help="xyz:BRENTOIL, xyz:CL, xyz:GOLD, ...")
    ap.add_argument("--at", default=None, help="ISO UTC time instead of now (tests, what-ifs): 2026-11-07T12:00Z")
    ap.add_argument("--warn-hours", type=float, default=24.0)
    a = ap.parse_args(argv)
    if a.at:
        at_ms = int(datetime.strptime(a.at, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    else:
        at_ms = int(time.time() * 1000)
    print(json.dumps(answer(a.coin, at_ms, a.warn_hours)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
