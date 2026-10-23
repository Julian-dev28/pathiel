"""Deterministic levels for the Claude-run oil loop (.claude/skills/oil-tick).

Reads Hyperliquid's public candles and prints the numbers a tick needs, so
the model never eyeballs ranges or does R arithmetic in its head:

  python3 scripts/oil_levels.py                       # xyz:BRENTOIL
  python3 scripts/oil_levels.py --coin xyz:CL
  python3 scripts/oil_levels.py --check 107.20 106.70 108.45   # entry stop tp -> R

Output is one JSON object: mid, the last CLOSED 1h candle, 8h/24h/48h
range, ATR14 on 1h, where the mid sits in the 24h range, and the last four
closed 15m candles' range. Read-only; no key, no account.

This is a thin CLI: the actual math (closed_only/atr/summarize/check) lives
in services/strategies/levels.py, which the dashboard's
GET /api/desks/oil/levels route also calls, so the two never drift apart.
Only the network leg (curl-over-subprocess, needed because the system
Python here has no CA bundle) stays in this file.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategies.levels import HOUR_MS, check, summarize  # noqa: E402

INFO_URL = "https://api.hyperliquid.xyz/info"


def _post(body: Dict[str, Any]) -> Any:
    # curl, not urllib: the system Python has no CA bundle on this machine.
    out = subprocess.run(["curl", "-sS", "-m", "10", INFO_URL, "-H", "Content-Type: application/json",
                          "-d", json.dumps(body)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def fetch_candles(coin: str, interval: str, hours: int, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    now_ms = now_ms or int(time.time() * 1000)
    return _post({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval, "startTime": now_ms - hours * HOUR_MS, "endTime": now_ms}})


def fetch_mid(coin: str) -> float:
    dex = coin.split(":")[0] if ":" in coin else ""
    body: Dict[str, Any] = {"type": "allMids"}
    if dex:
        body["dex"] = dex
    return float(_post(body)[coin])


# ------------------------------------------------------------------ CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coin", default="xyz:BRENTOIL")
    ap.add_argument("--check", nargs=3, type=float, metavar=("ENTRY", "STOP", "TP"))
    ap.add_argument("--lev", type=float, help="with --check: leverage to judge (e.g. 20)")
    ap.add_argument("--max-lev", type=float, default=20.0, help="venue max leverage for the coin (Brent/WTI 20)")
    a = ap.parse_args(argv)
    if a.check:
        print(json.dumps(check(*a.check, leverage=a.lev, max_leverage=a.max_lev)))
        return 0
    now_ms = int(time.time() * 1000)
    out = summarize(fetch_candles(a.coin, "1h", 50, now_ms), fetch_candles(a.coin, "15m", 3, now_ms),
                    fetch_mid(a.coin), now_ms)
    out["coin"] = a.coin
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
