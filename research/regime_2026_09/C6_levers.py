#!/usr/bin/env python3
"""Which change actually moves the monthly P&L?

The operator asked what to change to increase returns. Ranking levers by
edge-per-trade is the mistake C4 already caught once: a book with a 4% edge that
fires twice a month loses to a 1% edge that fires daily on the same capital.
What matters is

    $/month = edge_per_trade x notional_per_trade x trades_per_day x 30

and the levers move DIFFERENT terms, sometimes in opposite directions. Holding
longer raises the edge and cuts the turnover. Leverage raises the notional and
the drawdown together. Only one lever raises a term without lowering another.

So every candidate is scored on the same three outputs: monthly dollars, the
correlated drawdown it implies, and whether the account survives one bad day.
A lever that raises P&L and ends the account on a normal Tuesday is not an
improvement, and the ranking says so.

Edges are measured on the panel, SECOND HALF ONLY. Using the pooled average
would flatter every row by the same decaying period.

    python research/regime_2026_09/C6_levers.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from C3_hold_length import HORIZONS, build, spans_weekend  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EQUITY = float(sys.argv[1]) if len(sys.argv) > 1 else 34.67


def second_half(rows: List[dict], key: str) -> tuple[float, int]:
    """Mean of the RECENT half. The planning number, not the flattering one."""
    if not rows:
        return 0.0, 0
    times = sorted({o["t"] for o in rows})
    cut = times[len(times) // 2]
    h2 = [o[key] for o in rows if o["t"] >= cut]
    return (st.mean(h2) if h2 else 0.0), len(h2)


def main() -> int:
    cfg = json.loads((ROOT / ".agent-config.json").read_text())
    slots = int(cfg["max_concurrent"])
    frac = float(cfg["strategy_book_equity_frac"])
    lev = int(cfg["leverage"])
    stop = float(cfg["default_stop_pct"])
    floor = float(cfg["min_tradable_equity_usd"])
    hold_now = float(cfg["xs_reversal"]["hold_hours"])

    obs = build()
    if not obs:
        print("  not enough panel history")
        return 1
    clean = [o for o in obs if not spans_weekend(o["t"], 24.0)]

    def row(label, hold_h, rows, n_slots=slots, n_frac=frac, n_lev=lev,
            n_stop=stop, keep=1.0):
        ev, n = second_half(rows, f"r{hold_h:.0f}")
        if n < 100:
            return None
        notional = EQUITY * n_frac * n_lev
        trades = n_slots * (24.0 / hold_h) * keep
        month = notional * ev / 100.0 * trades * 30
        dd = n_slots * n_frac * n_lev * n_stop / 100.0
        survives = EQUITY * (1 - dd) >= floor
        return {"label": label, "ev": ev, "n": n, "trades": trades,
                "month": month, "dd": dd, "survives": survives, "hold": hold_h}

    cands = []
    base = row(f"NOW: {hold_now:.0f}h hold, 92% deployed", 24.0, obs)
    cands.append(base)
    for h in HORIZONS:
        if h == 24.0:
            continue
        cands.append(row(f"hold {h:.0f}h instead of 24h", h, obs))
    cands.append(row("skip weekend-spanning entries", 24.0, clean,
                     keep=len(clean) / len(obs)))
    # Deployment that survives one correlated stop-out with the floor intact.
    safe_frac = (1 - floor / EQUITY) / (slots * lev * stop / 100.0)
    cands.append(row(f"cut deployment to {slots*safe_frac:.0%} (survives a bad day)",
                     24.0, obs, n_frac=safe_frac))
    cands.append(row(f"BOTH: {72:.0f}h hold + {slots*safe_frac:.0%} deployment",
                     72.0, obs, n_frac=safe_frac))
    # Leverage looks free - double the notional on the same margin - and is not.
    # backup_sl_max_frac_of_liq clamps the stop to 60/leverage percent, so 6x
    # forces the 15% stop down to 10%. The panel measures a 24h hold with NO
    # stop, so it cannot see the extra stop-outs a 10% band causes: the EV below
    # is therefore an OVERSTATEMENT, and the drawdown is not.
    hi_lev = int(cfg.get("hip3_max_leverage", 6))
    clamped_stop = 100.0 * float(cfg["backup_sl_max_frac_of_liq"]) / hi_lev
    cands.append(row(f"raise leverage to {hi_lev}x (stop clamps to {clamped_stop:.0f}%)",
                     24.0, obs, n_lev=hi_lev, n_stop=clamped_stop))
    cands = [c for c in cands if c]

    print(f"LEVERS, scored at ${EQUITY:.2f} equity, second-half edges only\n")
    print(f"  {'':<44}{'EV/trade':>9}{'trades/d':>9}{'$/month':>9}"
          f"{'drawdown':>10}{'survives':>10}")
    for c in sorted(cands, key=lambda x: -x["month"]):
        mark = "  <- now" if c is base else ""
        print(f"  {c['label']:<44}{c['ev']:>+8.2f}%{c['trades']:>9.2f}"
              f"{c['month']:>9.2f}{c['dd']:>9.1%}{'yes' if c['survives'] else 'NO':>10}{mark}")

    best = max(cands, key=lambda x: x["month"])
    safe = [c for c in cands if c["survives"]]
    best_safe = max(safe, key=lambda x: x["month"]) if safe else None

    print("\nTHE READ")
    print(f"  Highest raw P&L: {best['label']} at ${best['month']:.2f}/month.")
    if not best["survives"]:
        print("  It does NOT survive a correlated stop-out - one bad day drops the")
        print(f"  account under the ${floor:.0f} floor and trading stops for good.")
    if best_safe:
        print(f"\n  Best that survives a bad day: {best_safe['label']}")
        print(f"  ${best_safe['month']:.2f}/month at a {best_safe['dd']:.1%} drawdown.")
        if base:
            d = best_safe["month"] - base["month"]
            print(f"  vs today: {d:+.2f}/month ({d/base['month']*100:+.0f}%) "
                  f"and a drawdown {base['dd']-best_safe['dd']:.1%} smaller.")
    print("\n  Note what is NOT on this list: more slots. Supply is 38 candidates/day")
    print("  against 3 slots, so the constraint is capital, not signal. The one")
    print("  lever that raises P&L without raising drawdown is more equity -")
    print("  every row above scales linearly with it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
