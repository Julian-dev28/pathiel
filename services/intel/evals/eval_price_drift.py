"""Eval: how often the drift alarm (headline_watch.price_drift) would fire on
real candles, and whether it catches the 2026-11-05 evening Brent climb.

    python3 -m services.intel.evals.eval_price_drift [--days 60]

Replays public Hyperliquid 1h closes as a one-a-minute mid (each hour's close
held for 60 minutes: coarse, it can only under-count intrahour moves), with
the watcher's re-arm rule. Pass: Brent fires on the 11-05 evening and no
coin averages more than 2 alerts a day. Read only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from services.intel import headline_watch as HW

M = HW.MIN_MS


def replay(coin: str, closes: Sequence[tuple]) -> List[int]:
    """Alert times (ms) for (open_ms, close) 1h bars, oldest first. Pure."""
    hist: List[tuple] = []
    last = 0
    fired: List[int] = []
    for t0, c in closes:
        for k in range(60):
            now = int(t0) + 3_600_000 + k * M
            d = HW.price_drift(coin, hist, now, float(c))
            hist.append((now, float(c)))
            del hist[:-HW.HISTORY_KEEP_MIN]
            if d and now - last >= HW.DRIFT_REARM_MIN * M:
                last = now
                fired.append(now)
    return fired


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args(argv)
    sys.path.insert(0, "scripts")
    import oil_levels
    now = int(time.time() * 1000)
    out: Dict[str, dict] = {}
    ok = True
    for coin in ("xyz:BRENTOIL", "xyz:GOLD"):
        bars = sorted(oil_levels.fetch_candles(coin, "1h", a.days * 24, now), key=lambda b: int(b["t"]))
        fired = replay(coin, [(int(b["t"]), float(b["c"])) for b in bars])
        days = max(1.0, (int(bars[-1]["t"]) - int(bars[0]["t"])) / 86_400_000) if bars else 1.0
        per_day = len(fired) / days
        stamps = [datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%m-%d %H:%MZ") for t in fired]
        out[coin] = {"alerts": len(fired), "per_day": round(per_day, 2), "last": stamps[-6:]}
        ok &= per_day <= 2.0
        if coin == "xyz:BRENTOIL":
            caught = any(s.startswith("11-05") and s[6:8] >= "18" for s in stamps)
            out[coin]["caught_11_05_evening"] = caught
            ok &= caught
    out["pass"] = ok
    print(json.dumps(out, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
