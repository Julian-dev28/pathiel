#!/usr/bin/env python3
"""Expected dollars from the CURRENT live config at the CURRENT equity.

EV per trade is not the answer to "what will this make". Dollars per month is

    edge_per_trade  x  notional_per_trade  x  trades_per_day  x  30

and every one of those four comes from a different place: the edge from the
panel (C4), the notional and the slots from .agent-config.json, the turnover
from the hold length capped by slots. Quoting the edge alone hides that a 1.4%
edge on $32 four times a week is a different business from the same edge on
$11 once a day.

WHAT THIS DELIBERATELY DOES NOT DO
It does not compound. Reinvesting the gain would raise the monthly figure and
is exactly the assumption that turns a projection into a sales pitch, because
it is the assumption most likely to be wrong first - the fraction sizing does
scale with equity, but so does the drawdown.

    python research/regime_2026_09/ev_projection.py [equity]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Measured by C4_ev_split.py on the xyz panel: top-decile 3d cross-sectional
# return, funding awake >= 67%, 24h hold, NET of 0.25% slippage and of the
# funding actually paid or received. n=2562, clustered bootstrap p<0.0001.
EV_POOLED, EV_H1, EV_H2 = 1.408, 1.78, 0.98
N_OBS, CANDIDATES_PER_DAY = 2562, 38.3


def main() -> int:
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    equity = float(sys.argv[1]) if len(sys.argv) > 1 else 34.67

    slots = int(cfg["max_concurrent"])
    frac = float(cfg["strategy_book_equity_frac"])
    lev = int(cfg["leverage"])
    hold_h = float(cfg["xs_reversal"]["hold_hours"])
    stop = float(cfg["default_stop_pct"])

    notional = equity * frac * lev
    trades_day = slots * (24.0 / hold_h)
    dd = float(cfg["max_correlated_drawdown_pct"])

    print(f"THE LIVE BOOK at ${equity:.2f} equity\n")
    print(f"  {slots} slots x {frac:.5f} x {lev}x   = ${notional:.2f} notional per position")
    print(f"  total deployed              ${notional*slots:.2f} notional, "
          f"${equity*frac*slots:.2f} margin ({frac*slots:.0%})")
    print(f"  {hold_h:.0f}h hold, {slots} slots       = {trades_day:.1f} trades/day maximum")
    print(f"  supply is {CANDIDATES_PER_DAY:.0f} candidates/day = "
          f"{CANDIDATES_PER_DAY/trades_day:.0f}x capacity, so slots bind, not signal\n")

    print("EXPECTED DOLLARS, no compounding")
    print(f"  {'edge/trade':<26}{'$/trade':>9}{'$/day':>8}{'$/month':>10}{'%/mo':>8}")
    rows = [("pooled  (n=%d)" % N_OBS, EV_POOLED),
            ("first half", EV_H1),
            ("second half  <- plan on", EV_H2)]
    for label, ev in rows:
        per_trade = notional * ev / 100.0
        per_day = per_trade * trades_day
        print(f"  {label:<20}{ev:>+6.2f}%{per_trade:>9.3f}{per_day:>8.2f}"
              f"{per_day*30:>10.2f}{per_day*30/equity*100:>7.0f}%")

    print("\n  Plan on the SECOND half, not the pooled number. The two halves are")
    print(f"  {EV_H1:+.2f}% and {EV_H2:+.2f}% - both positive, which is why the book is live,")
    print("  but a decaying edge is the normal life of one, and the recent half is")
    print("  the better estimate of the next month than an average that includes")
    print("  a period that is over.")

    print(f"\nWHAT IT COSTS TO EARN THAT")
    one = frac * lev * stop / 100.0
    print(f"  one position stopping        {one:>6.1%} of equity = ${equity*one:.2f}")
    print(f"  daily kill halts new entries {float(cfg['max_daily_loss_pct']):>6.1%} = "
          f"${equity*float(cfg['max_daily_loss_pct']):.2f}")
    print(f"  all {slots} stopping together    {dd:>6.1%} of equity = ${equity*dd:.2f}")
    print(f"\n  The monthly figure and that drawdown are the SAME position sizing.")
    print(f"  At a ~50% win rate and 0.83 correlation between xyz shorts, a month")
    print(f"  that returns the projection still contains days near -${equity*dd:.2f}.")
    print(f"  Equity below ${float(cfg['min_tradable_equity_usd']):.0f} stops trading entirely, and from "
          f"${equity:.2f} that")
    print(f"  is {(equity-float(cfg['min_tradable_equity_usd']))/equity:.0%} of the account - roughly "
          f"{(equity-float(cfg['min_tradable_equity_usd']))/(equity*dd):.1f} correlated stop-outs away.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
