#!/usr/bin/env python3
"""Does the odds-alert volume floor drop the noise and keep the signal?

2026-11-22: two thin Polymarket markets woke the HYPE desk overnight and
forced thesis rewrites. "Will Hyperliquid reach $76 in November?" had $146 of
lifetime volume and went 19% -> 45% on one trade; "$88 in November?" ($377)
went 4% -> 27%. headline_watch.odds_alerts now skips a market under
ODDS_ALERT_MIN_VOLUME_USD.

This eval reads the Polymarket payloads the desks last fetched (the thesis's
own cache under $PATHIEL_STATE_DIR/intel/<desk>/; no network, no LLM) and
checks:

  1. both incident markets are blocked (they never alert), and
  2. the floor keeps most of the oil desk's markets alert-eligible
     (oil is the desk that trades on war and sanctions odds).

It also prints, per desk, how many markets stay alert-eligible, so a floor
change shows what it costs each desk.

    PATHIEL_STATE_DIR=... .venv/bin/python -m services.intel.evals.eval_odds_volume_floor

Exit 0 when both checks pass, 1 otherwise (2 when no cached payload exists).
"""
from __future__ import annotations

import glob
import json
import os
import sys
from typing import Dict, List, Tuple

from services.intel import headline_watch as HW

INCIDENTS = [  # (question, lifetime volume USD, from, to) as seen on 2026-11-21
    ("Will Hyperliquid reach $76 in November?", 146.55, 0.19, 0.45),
    ("Will Hyperliquid reach $88 in November?", 376.72, 0.04, 0.27),
]
OIL_MIN_KEPT = 0.5   # at least half of oil's markets must stay alert-eligible


def _state_dir() -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or os.path.expanduser("~/.pathiel/state")


def cached_volumes(state_dir: str) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for path in glob.glob(os.path.join(state_dir, "intel", "*", "predictions_fetch_polymarket_*.json")):
        desk = path.split(os.sep + "intel" + os.sep)[1].split(os.sep)[0]
        try:
            payload = json.load(open(path)).get("payload")
        except (OSError, ValueError):
            continue
        rows = payload[0] if payload and isinstance(payload[0], list) else (payload or [])
        vols = [float(r.get("volume_usd") or 0) for r in rows if isinstance(r, dict)]
        out.setdefault(desk, []).extend(vols)
    return out


def incident_blocked() -> List[Tuple[str, bool]]:
    res = []
    for i, (q, vol, a, b) in enumerate(INCIDENTS):
        prev = {str(i): a}
        alerts = HW.odds_alerts("hype", [{"id": str(i), "question": q, "probability": b,
                                          "volume_usd": vol}], prev, 0)
        res.append((q, not alerts))
    return res


def main() -> int:
    floor = HW.ODDS_ALERT_MIN_VOLUME_USD
    print(f"odds alert volume floor: ${floor:,.0f}")
    ok = True
    for q, blocked in incident_blocked():
        print(f"  incident {'BLOCKED' if blocked else 'STILL ALERTS'}: {q}")
        ok &= blocked
    vols = cached_volumes(_state_dir())
    if not vols:
        print(f"no cached Polymarket payloads under {_state_dir()}/intel")
        return 2
    for desk in sorted(vols):
        v = vols[desk]
        kept = sum(1 for x in v if x >= floor)
        print(f"  {desk:7s} markets={len(v):4d} alert-eligible={kept:4d} ({kept / len(v):.0%})")
    oil = vols.get("oil") or []
    if oil:
        share = sum(1 for x in oil if x >= floor) / len(oil)
        good = share >= OIL_MIN_KEPT
        print(f"  oil keeps {share:.0%} of its markets (need >= {OIL_MIN_KEPT:.0%}): {'PASS' if good else 'FAIL'}")
        ok &= good
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
