#!/usr/bin/env python3
"""W-U2 — the re-run W-U1 said the run-in cell required before promotion.

`unlock_short_runin` is LIVE. Its evidence is W-U1's `EXPL_runin_Tm3_T` cell,
which W-U1's own docstring labels "EXPLORATORY (reported, not promotable without
a re-run)". Reading the code, that label is earned: the cell is a raw
close-to-close drift with NO trade construction — no stop, no fees, no matched
null, no OOS split. Its headline -2.105% is a price move, not a strategy return.

W-U1's PRIMARY pre-registered cell (short T-1d -> T+2d) came back p_mc = 0.10
and does NOT clear the 0.05 bar. So the one cell that WAS tested properly
failed, and the one the live book trades was never tested at all.

This is that test: the run-in window as an actual trade — short at close(T-3d),
exit close(T), 15% stop checked on the daily HIGH (pessimistic), 25bps round
trip — against the same matched same-coin random-day null W-U1 uses, plus OOS
time halves.

Usage: .venv/bin/python research/alpha_swarm/hypotheses/W-U2_runin_rerun.py
"""
from __future__ import annotations

import importlib.util
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("wu1", HERE / "W-U1_unlock_backtest.py")
wu1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wu1)

FEE_PCT = 0.25          # 25bps round trip, same as W-U1's primary cell
STOP_PCT = 15.0


def runin_trade(days: dict, d0: int) -> float | None:
    """Short close(T-3d) -> close(T). Stop on the daily HIGH, checked before
    the exit day, so a spike through the stop counts even if the close is fine.
    Net of fees. Returns the SHORT's return in percent."""
    entry_day = days.get(d0 - 3)
    exit_day = days.get(d0)
    if not entry_day or not exit_day:
        return None
    entry = entry_day.get("c")
    if not entry or entry <= 0:
        return None
    stop_px = entry * (1 + STOP_PCT / 100)
    for off in (-2, -1, 0):
        d = days.get(d0 + off)
        if d and d.get("h") and d["h"] >= stop_px:
            return -STOP_PCT - FEE_PCT
    exit_px = exit_day.get("c")
    if not exit_px or exit_px <= 0:
        return None
    return (entry - exit_px) / entry * 100 - FEE_PCT


def main() -> int:
    idx = wu1.load_index()
    import json
    name_to_sym = json.load(open(wu1.MAP_FILE))
    events = wu1.extract_events(idx, name_to_sym, 1.0)
    coins = sorted({e["coin"] for e in events})
    candles = wu1.load_candles(coins)
    days_by_coin = {c: wu1.day_index(v) for c, v in candles.items() if v}

    rets, ev_coins = [], []
    for e in events:
        days = days_by_coin.get(e["coin"])
        if not days:
            continue
        r = runin_trade(days, e["t_ms"] // wu1.DAY_MS)
        if r is not None:
            rets.append(r)
            ev_coins.append(e["coin"])

    if len(rets) < 30:
        print(f"only {len(rets)} tradable events — cannot judge")
        return 1

    n = len(rets)
    mean = statistics.mean(rets)
    win = sum(1 for r in rets if r > 0) / n
    mid = n // 2
    h1, h2 = statistics.mean(rets[:mid]), statistics.mean(rets[mid:])
    p = wu1.mc_pvalue(days_by_coin, rets, ev_coins, runin_trade)

    print("W-U2 — unlock run-in as an actual trade")
    print(f"  short close(T-3d) -> close(T), {STOP_PCT:.0f}% stop on daily high, "
          f"{FEE_PCT:.2f}% round trip\n")
    print(f"  n            {n}")
    print(f"  mean         {mean:+.3f}%")
    print(f"  win          {win:.3f}")
    print(f"  OOS first    {h1:+.3f}%")
    print(f"  OOS second   {h2:+.3f}%")
    print(f"  mc_p         {p:.4f}")
    print()
    both = h1 > 0 and h2 > 0
    passes = mean > 0 and both and p < 0.05
    print(f"  both halves + : {'YES' if both else 'NO'}")
    print(f"  p < 0.05      : {'YES' if p < 0.05 else 'NO'}")
    print(f"\n  VERDICT: {'VALIDATED' if passes else 'NOT VALIDATED'}")
    print("  (W-U1 PRIMARY pre-registered cell for reference: p_mc = 0.10, fails)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
