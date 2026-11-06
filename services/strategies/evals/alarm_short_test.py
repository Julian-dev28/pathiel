"""What-if: short Brent when the price alarms fire on a down move.

Operator, 2026-11-06: "why cant we short the bearish news again?" then "test
shorting i guess". We have two days of headline history but 50+ days of the
price alarms that fire with the news (services/intel/headline_watch.py:
price_shock 1% in 15 min, price_drift 1.75% in 4h). This replays them on real
xyz:BRENTOIL 15m candles and trades the alarm direction. Read only.

    python3 -m services.strategies.evals.alarm_short_test [--days 52]

Rule per variant:
- Entry: at the 15m close where a DOWN alarm fires (and, as a check, the
  mirror: a long on UP alarms).
- Stop: 0.75 beyond the highest high of the last 4h (a long: the lowest low).
- Target: the 24h low less 0.5% (a long: the 24h high), or a fixed R
  multiple.
- Gates: the 2R gate on or off. One trade at a time, out after 24h.
- Fees: 4.5 bps taker each way.
- Result: in R (risk = |entry - stop|), and in $ at a fixed $10 risk a trade.

Pass bar, stated before the run: the short variant must be net positive in R
over the window, win in at least 2 of 3 equal sub-windows, and beat doing
nothing (0R).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

Q = 900_000            # 15 minutes
H = 3_600_000
FEE = 0.00045
STOP_BUFFER = 0.75
SHOCK = 0.010          # headline_watch.PRICE_SHOCK for Brent
SHOCK_BARS = 1         # 15 minutes = one 15m bar
DRIFT = 0.0175         # headline_watch.PRICE_DRIFT for Brent
DRIFT_BARS = 16        # 4 hours of 15m bars
REARM_BARS = 16
RISK_USD = 10.0


def alarms(bars: Sequence[Dict[str, float]]) -> List[Dict[str, Any]]:
    """[{i, dir, kind}] at each 15m close where shock or drift fires (dir +1/-1),
    re-armed per kind like the watcher. bars: oldest first, keys t,o,h,l,c. Pure."""
    out: List[Dict[str, Any]] = []
    last = {"shock": -10**9, "drift": -10**9}
    for i in range(1, len(bars)):
        c = bars[i]["c"]
        for kind, n, thr in (("shock", SHOCK_BARS, SHOCK), ("drift", DRIFT_BARS, DRIFT)):
            if i - n < 0 or i - last[kind] < REARM_BARS:
                continue
            ref = max((bars[j]["c"] for j in range(i - n, i)), key=lambda p: abs(c - p))
            move = (c - ref) / ref
            if abs(move) >= thr:
                last[kind] = i
                out.append({"i": i, "dir": 1 if move > 0 else -1, "kind": kind, "move": move})
    return out


def trade(bars: Sequence[Dict[str, float]], i: int, side: int, *, min_r: Optional[float],
          target_r: Optional[float]) -> Optional[Dict[str, Any]]:
    """One trade from bar i's close. None when the setup is refused. Pure."""
    entry = bars[i]["c"]
    lo4 = min(b["l"] for b in bars[max(0, i - 15):i + 1])
    hi4 = max(b["h"] for b in bars[max(0, i - 15):i + 1])
    lo24 = min(b["l"] for b in bars[max(0, i - 95):i + 1])
    hi24 = max(b["h"] for b in bars[max(0, i - 95):i + 1])
    if side < 0:
        stop = hi4 + STOP_BUFFER
        risk = stop - entry
        target = entry - target_r * risk if target_r else lo24 * 0.995
        if target >= entry:
            return None
    else:
        stop = lo4 - STOP_BUFFER
        risk = entry - stop
        target = entry + target_r * risk if target_r else hi24 * 1.005
        if target <= entry:
            return None
    if risk <= 0:
        return None
    r_plan = abs(target - entry) / risk
    if min_r and r_plan < min_r:
        return None
    exit_px, reason, j = None, "time", i
    for j in range(i + 1, min(len(bars), i + 97)):
        b = bars[j]
        hit_stop = b["h"] >= stop if side < 0 else b["l"] <= stop
        hit_tgt = b["l"] <= target if side < 0 else b["h"] >= target
        if hit_stop:                       # stop first when both touch: the conservative read
            exit_px, reason = stop, "stop"
            break
        if hit_tgt:
            exit_px, reason = target, "target"
            break
    if exit_px is None:
        exit_px = bars[j]["c"]
    gross = (entry - exit_px) if side < 0 else (exit_px - entry)
    fees = FEE * (entry + exit_px)
    r = (gross - fees) / risk
    return {"i": i, "exit_i": j, "side": side, "entry": entry, "stop": stop, "target": target,
            "exit": exit_px, "reason": reason, "r": r, "r_plan": r_plan}


def run(bars: Sequence[Dict[str, float]], side: int, *, min_r: Optional[float],
        target_r: Optional[float]) -> Dict[str, Any]:
    trades = []
    busy_until = -1
    for a in alarms(bars):
        if a["dir"] != side or a["i"] <= busy_until:
            continue
        tr = trade(bars, a["i"], side, min_r=min_r, target_r=target_r)
        if tr:
            trades.append(tr)
            busy_until = tr["exit_i"]
    n = len(bars)
    thirds = [sum(t["r"] for t in trades if k * n // 3 <= t["i"] < (k + 1) * n // 3) for k in range(3)]
    total = sum(t["r"] for t in trades)
    return {"trades": len(trades), "wins": sum(1 for t in trades if t["r"] > 0),
            "total_r": round(total, 2), "usd_at_10_risk": round(total * RISK_USD, 2),
            "thirds_r": [round(x, 2) for x in thirds],
            "pass": total > 0 and sum(1 for x in thirds if x > 0) >= 2}


VARIANTS = {"edge_target_2R_gate": {"min_r": 2.0, "target_r": None},
            "edge_target_no_gate": {"min_r": None, "target_r": None},
            "fixed_1.5R": {"min_r": None, "target_r": 1.5},
            "fixed_1R": {"min_r": None, "target_r": 1.0}}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=52)
    a = ap.parse_args(argv)
    sys.path.insert(0, "scripts")
    import oil_levels
    now = int(time.time() * 1000)
    raw = sorted(oil_levels.fetch_candles("xyz:BRENTOIL", "15m", a.days * 24, now), key=lambda b: int(b["t"]))
    bars = [{"t": int(b["t"]), "o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]), "c": float(b["c"])}
            for b in raw]
    out: Dict[str, Any] = {"bars": len(bars), "alarms_down": sum(1 for x in alarms(bars) if x["dir"] < 0),
                           "alarms_up": sum(1 for x in alarms(bars) if x["dir"] > 0),
                           "brent_move_pct": round(100 * (bars[-1]["c"] / bars[0]["c"] - 1), 2)}
    for name, kw in VARIANTS.items():
        out[f"short/{name}"] = run(bars, -1, **kw)
        out[f"long/{name}"] = run(bars, +1, **kw)
    out["short_passes"] = [k for k, v in out.items() if k.startswith("short/") and v["pass"]]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
