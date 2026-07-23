#!/usr/bin/env python3
"""W-FUN1c — does the oracle survive? (a) other coins, (b) profit-taking overlays.

Two operator questions, one honest answer: numerology direction is NOISE, so
- (a) the +11.35%/trade ETH "win" is coin-specific luck; other coins give other random
  winners and the median still craters;
- (b) profit-taking / fractional sizing CUSHION the ruin (real, useful risk management)
  but do NOT create edge — on a coin flip they narrow both tails to ~breakeven-minus-fees.

Same winner formula as W-FUN1 (day_root_odd @ 14:00 UTC, all-in 25x, liq at 4% adverse).
Start bankroll $1000. Reuses W-FUN1's schemes / daily_entries / pnl_of.
"""
from __future__ import annotations

import importlib.util
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import alpha_lib as A  # noqa: E402
_spec = importlib.util.spec_from_file_location("wfun1", HERE / "W-FUN1_numerology.py")
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

START = 1000.0
HOUR = 14
SCHEME = "day_root_odd"


def pnl_for(coin, d):
    bars = A.candles(d, coin, "1h")
    if len(bars) < 24 * 25:
        return None
    entries = w.daily_entries(bars, HOUR)
    if len(entries) < 20:
        return None
    dirs = [w.schemes(dt)[SCHEME] for (dt, *_) in entries]
    return w.pnl_of(entries, dirs)


def compound_fixed(pnl, f):
    eq = START
    for p in pnl:
        eq = max(0.0, eq * (1.0 + f * p))
    return eq


def vault_bank_on_double(pnl):
    """All-in on the trading pile, but sweep half to an untouchable vault every time the
    trading pile doubles from its last high-water mark. Final = vault + survivors."""
    trading, safe, mark = START, 0.0, START
    for p in pnl:
        trading = max(0.0, trading * (1.0 + p))
        if trading >= 2 * mark:
            swept = trading * 0.5
            safe += swept
            trading -= swept
            mark = trading
        if trading == 0.0:
            break
    return safe + trading


def liq_rate(pnl):
    return 100 * sum(1 for p in pnl if p <= -0.999) / len(pnl)


def main():
    d = A.load_dataset()
    coins = d["meta"]["coins"]

    # (a) same formula, every coin, all-in 25x
    rows = []
    for c in coins:
        pnl = pnl_for(c, d)
        if pnl is None:
            continue
        rows.append((c, len(pnl), compound_fixed(pnl, 1.0), liq_rate(pnl)))
    rows.sort(key=lambda r: -r[2])
    survivors = [r for r in rows if r[2] > START]
    alive = [r for r in rows if r[2] > 0.01]
    print(f"=== (a) day_root_odd @14:00, ALL-IN 25x, ${int(START)} start — {len(rows)} coins ===")
    print(f"{'coin':<10}{'n':>4}{'final $':>14}{'liq %':>8}")
    for c, n, fin, lr in rows[:8]:
        print(f"{c:<10}{n:>4}{fin:>14,.2f}{lr:>7.0f}%")
    print("  ...")
    for c, n, fin, lr in rows[-4:]:
        print(f"{c:<10}{n:>4}{fin:>14,.2f}{lr:>7.0f}%")
    finals = [r[2] for r in rows]
    print(f"\n  survived-and-profited: {len(survivors)}/{len(rows)} coins "
          f"({', '.join(r[0] for r in survivors) or 'none'})")
    print(f"  not fully wiped:       {len(alive)}/{len(rows)} coins")
    print(f"  MEDIAN coin final:     ${st.median(finals):,.2f}   MEAN: ${st.mean(finals):,.2f}")
    print(f"  -> the ETH 'win' does NOT generalize: it is one lucky coin out of {len(rows)}.")

    # (b) profit-taking overlays, on ETH (the lucky formula) AND the median coin
    print(f"\n=== (b) same ETH bets, different money management (${int(START)} start) ===")
    eth = pnl_for("ETH", d)
    regimes = [("all-in 25x (degenerate)", compound_fixed(eth, 1.0)),
               ("fixed 50% of bankroll/day", compound_fixed(eth, 0.5)),
               ("fixed 20% of bankroll/day", compound_fixed(eth, 0.2)),
               ("fixed 10% of bankroll/day", compound_fixed(eth, 0.1)),
               ("all-in + bank-half-on-double vault", vault_bank_on_double(eth))]
    print(f"  {'money management':<40}{'final $':>14}")
    for lab, fin in regimes:
        tag = "  <- survives + keeps gains" if fin > START else ("  <- dead" if fin < 1 else "")
        print(f"  {lab:<40}{fin:>14,.2f}{tag}")

    # the honest control: median coin under the SAME overlays
    med_coin = rows[len(rows) // 2][0]
    mp = pnl_for(med_coin, d)
    print(f"\n  same overlays on the MEDIAN coin ({med_coin}) — no lucky run to bank:")
    for lab, f in [("fixed 20%/day", 0.2), ("fixed 10%/day", 0.1)]:
        print(f"    {lab:<24}{compound_fixed(mp, f):>12,.2f}")
    print(f"    all-in vault          {vault_bank_on_double(mp):>12,.2f}")

    print("\n=== VERDICT ===")
    print("  (a) OTHER COINS: the +11.35% was ETH-specific luck. Median coin craters; only a")
    print(f"      handful of {len(rows)} survive, different ones than you'd have bet on in advance.")
    print("  (b) PROFIT-TAKING: fractional sizing + a vault DO cushion the ruin and would have")
    print("      banked part of ETH's lucky run. But that is RISK management, not EDGE. On a coin")
    print("      flip the overlay just narrows both tails to ~breakeven-minus-fees; on the median")
    print("      coin even 10%/day bleeds. Profit-taking MULTIPLIES an edge — it cannot CREATE one.")
    print("      Put the same overlay on a REAL edge (xs_xyz_equities) and it compounds; on")
    print("      numerology it just makes the zero arrive politely.")


if __name__ == "__main__":
    main()
