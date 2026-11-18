"""Must a desk's resting entry on COIN be cancelled right now for a blackout?

  python3 scripts/resting_blackout.py --coin xyz:BRENTOIL --side long
  -> {"coin": "xyz:BRENTOIL", "side": "long", "blocked": false, "reason": null, "utc": "..."}

Operator, 2026-11-18 ("yeah", resting the live rule's trigger instead of
waiting for a tick): a resting limit sits on the venue continuously between
ticks, so it can fill on a touch inside a blackout window the tick itself
would have refused to enter. This is read-only and pure given the clock:
`blocked` is true when a blackout (EIA, the oil weekend, a scheduled event,
or a headline hold on `side`) is ACTIVE now, or starts before the next tick
(services/strategies/event_blackout.resting_entry_blackout, default horizon
15 minutes, the tick cadence). The desk-tick agent cancels the resting
entry when `blocked` is true and does not re-rest in the same tick (the
rule reapplies next tick, after the window: place_bracket's own blackout
check would refuse a re-rest anyway). Never reaches an open position, its
stop or its target -- call this only for a flat coin with a resting entry.
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


def answer(coin: str, side: Optional[str], at_ms: int, horizon_ms: Optional[int] = None) -> Dict[str, Any]:
    """Pure: the agent's one answer for `coin`/`side` at `at_ms`."""
    from services.strategies.event_blackout import RESTING_ENTRY_HORIZON_MS, resting_entry_blackout
    kw = {} if horizon_ms is None else {"horizon_ms": horizon_ms}
    hit = resting_entry_blackout(coin, side, at_ms, **kw)
    utc = datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    out: Dict[str, Any] = {"coin": coin, "side": side, "blocked": bool(hit), "reason": None, "utc": utc,
                           "horizon_min": (horizon_ms if horizon_ms is not None else RESTING_ENTRY_HORIZON_MS) / 60_000}
    if hit:
        out["reason"] = hit["reason"]
        out["until_utc"] = hit.get("until_utc")
        out["event"] = hit.get("event")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coin", required=True)
    ap.add_argument("--side", choices=("long", "short"), default=None)
    ap.add_argument("--at", default=None, help="ISO UTC time instead of now (tests): 2026-11-04T13:16Z")
    ap.add_argument("--horizon-min", type=float, default=None, help="default 15 (the tick cadence)")
    a = ap.parse_args(argv)
    at_ms = (int(datetime.strptime(a.at, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
             if a.at else int(time.time() * 1000))
    horizon_ms = int(a.horizon_min * 60_000) if a.horizon_min is not None else None
    print(json.dumps(answer(a.coin, a.side, at_ms, horizon_ms)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
