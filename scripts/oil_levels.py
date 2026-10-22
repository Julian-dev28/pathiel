"""Deterministic levels for the Claude-run oil loop (.claude/skills/oil-tick).

Reads Hyperliquid's public candles and prints the numbers a tick needs, so
the model never eyeballs ranges or does R arithmetic in its head:

  python3 scripts/oil_levels.py                       # xyz:BRENTOIL
  python3 scripts/oil_levels.py --coin xyz:CL
  python3 scripts/oil_levels.py --check 107.20 106.70 108.45   # entry stop tp -> R

Output is one JSON object: mid, the last CLOSED 1h candle, 8h/24h/48h
range, ATR14 on 1h, where the mid sits in the 24h range, and the last four
closed 15m candles' range. Read-only; no key, no account.
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

INFO_URL = "https://api.hyperliquid.xyz/info"
HOUR_MS = 3_600_000


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


# ----------------------------------------------------------------- pure part
def closed_only(candles: List[Dict[str, Any]], now_ms: int) -> List[Dict[str, Any]]:
    """Candles whose close time has passed. The open candle's high/low are
    still moving and must not anchor a stop."""
    return [c for c in candles if int(c["T"]) <= now_ms]


def true_range(prev_close: float, c: Dict[str, Any]) -> float:
    h, lo = float(c["h"]), float(c["l"])
    return max(h - lo, abs(h - prev_close), abs(lo - prev_close))


def atr(candles: List[Dict[str, Any]], n: int = 14) -> Optional[float]:
    """Simple-average ATR over the last n closed candles (needs n+1)."""
    if len(candles) < n + 1:
        return None
    window = candles[-(n + 1):]
    trs = [true_range(float(window[i - 1]["c"]), window[i]) for i in range(1, len(window))]
    return sum(trs) / n


def _range(candles: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    if not candles:
        return None
    return {"high": max(float(c["h"]) for c in candles), "low": min(float(c["l"]) for c in candles)}


def summarize(c1h: List[Dict[str, Any]], c15: List[Dict[str, Any]], mid: float, now_ms: int) -> Dict[str, Any]:
    closed = closed_only(c1h, now_ms)
    last = closed[-1] if closed else None
    r24 = _range(closed[-24:])
    out: Dict[str, Any] = {
        "mid": mid,
        "last_closed_1h": ({"open": float(last["o"]), "high": float(last["h"]), "low": float(last["l"]),
                            "close": float(last["c"]), "closed_at_utc": time.strftime(
                                # HL's T is the last ms of the candle (xx:59:59.999); +1 shows the hour it closed on.
                                "%Y-%m-%dT%H:%M", time.gmtime((int(last["T"]) + 1) / 1000))} if last else None),
        "range_8h": _range(closed[-8:]),
        "range_24h": r24,
        "range_48h": _range(closed[-48:]),
        "atr14_1h": atr(closed),
        "range_15m_last4": _range(closed_only(c15, now_ms)[-4:]),
        "n_closed_1h": len(closed),
    }
    if r24 and r24["high"] > r24["low"]:
        out["mid_pos_in_24h"] = round((mid - r24["low"]) / (r24["high"] - r24["low"]), 3)
    if out["atr14_1h"]:
        out["atr14_pct"] = round(out["atr14_1h"] / mid * 100, 3)
    return out


def check(entry: float, stop: float, tp: float, leverage: Optional[float] = None,
          max_leverage: float = 20.0) -> Dict[str, Any]:
    """The R gate, plus the leverage gate when --lev is given: liquidation
    price, how much of the liquidation distance the stop uses, and the
    highest leverage this stop allows (the "20x line" for Julien's wallet)."""
    from pathiel.agents.bracket import check_levels, check_leverage
    out = {"entry": entry, "stop": stop, "tp": tp, **check_levels(entry, stop, tp)}
    if leverage:
        lev = check_leverage(entry, stop, leverage, max_leverage)
        out["leverage"] = lev
        out["ok"] = bool(out.get("ok")) and bool(lev.get("ok"))
        out["verdict"] = (f"{leverage:g}x OK" if lev.get("ok")
                          else f"no {leverage:g}x: max {lev.get('max_leverage_ok')}x for this stop")
    return out


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
