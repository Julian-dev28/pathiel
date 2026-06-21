"""Open-interest time-series logger. HL exposes only CURRENT OI (metaAndAssetCtxs /
get_universe's `openInterest`), no history — so to ever backtest the OI/price four-quadrant
positioning filter we must self-collect a time-series going forward. This APPENDS a
snapshot each call (distinct from whale_index's single overwriting .oi-history.json).
Throttled + size-capped so it's cheap and bounded. Piggybacks the universe the loop
already fetches — no extra API calls.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".oi-timeseries.jsonl")
_MIN_INTERVAL_S = 600.0     # one snapshot per ~10min (OI moves slowly; keeps file small)
_MAX_LINES = 40_000         # ~9 months at 10min; rotate oldest beyond this
_last_log_ts = 0.0


def append_oi(universe: List[Dict[str, Any]], min_oi_usd: float = 5e6) -> int:
    """Append one OI snapshot {ts, oi:{coin:[oi_coins, px]}} for liquid coins. Throttled
    to _MIN_INTERVAL_S. Returns number of coins logged (0 if throttled/failed). Wrapped by
    the caller; must never raise into the loop."""
    global _last_log_ts
    now = time.time()
    if now - _last_log_ts < _MIN_INTERVAL_S:
        return 0
    snap: Dict[str, list] = {}
    for m in universe or []:
        coin = m.get("coin")
        oi_coins = float(m.get("openInterest", 0) or 0)
        px = float(m.get("midPx", 0) or m.get("markPx", 0) or 0)
        if coin and oi_coins > 0 and px > 0 and oi_coins * px >= min_oi_usd:
            snap[coin] = [round(oi_coins, 4), px]      # coin-units OI (price-independent) + px
    if not snap:
        return 0
    try:
        with open(_FILE, "a") as f:
            f.write(json.dumps({"ts": int(now), "oi": snap}) + "\n")
        _last_log_ts = now
        # cheap size cap: rotate when oversized (keep most recent _MAX_LINES)
        if os.path.getsize(_FILE) > 20_000_000:        # ~20MB guard
            lines = open(_FILE).readlines()[-_MAX_LINES:]
            with open(_FILE, "w") as f:
                f.writelines(lines)
    except Exception:
        return 0
    return len(snap)
