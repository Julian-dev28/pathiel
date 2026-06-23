#!/usr/bin/env python3
"""Cross-sectional momentum rebalancer — SHADOW runner.

Builds the live target book (long top-K / short bottom-K by trailing LB-day return) on CURRENT
data and prints the rebalance plan vs the current account — WITHOUT placing any orders. This is
the validation preview before the engine is wired into the live loop. Pure engine = xs_momentum.py.

  python3 scripts/xs_momentum_run.py            # LB=14, K=8 (validated config)
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.universe import get_universe
from hermes_trader.agents.xs_momentum import rank_universe, rebalance_plan
from _bt_candles import get as get_candles

LB = int(os.environ.get("XS_LB", "14"))
K = int(os.environ.get("XS_K", "8"))
HOLD = int(os.environ.get("XS_HOLD", "5"))
TOPN = int(os.environ.get("XS_TOPN", "50"))
VOL_FLOOR = 5e6


def current_book():
    """Current long/short coins from the positions snapshot (no live API call)."""
    longs, shorts = [], []
    try:
        d = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         ".positions-snapshot.json")))
        for p in d.get("asset_positions", []):
            pos = p.get("position", p)
            coin = pos.get("coin")
            szi = float(pos.get("szi", 0) or 0)
            if not coin or szi == 0:
                continue
            (longs if szi > 0 else shorts).append(coin)
    except Exception:
        pass
    return longs, shorts


def main():
    print(f"# XS-momentum rebalancer SHADOW preview | LB={LB}d K={K}/leg hold={HOLD}d | top{TOPN} liquid")
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    cbc = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 60)
        if len(bars) >= LB + 5:
            cbc[m["coin"]] = bars
    print(f"# {len(cbc)} coins with enough history\n")

    book = rank_universe(cbc, LB, K)
    if not book.longs:
        print("no target book (too few coins)"); return

    print(f"TARGET LONGS (top {K} by {LB}d return):")
    for c in book.longs:
        print(f"  +  {c:10} {book.scores[c]*100:+7.1f}%")
    print(f"TARGET SHORTS (bottom {K}):")
    for c in book.shorts:
        print(f"  -  {c:10} {book.scores[c]*100:+7.1f}%")

    cl, cs = current_book()
    plan = rebalance_plan(book, cl, cs)
    print(f"\ncurrent book: {len(cl)} long, {len(cs)} short")
    print("REBALANCE PLAN (SHADOW — no orders placed):")
    for action in ("open_long", "open_short", "close_long", "close_short", "hold_long", "hold_short"):
        if plan[action]:
            print(f"  {action:12} {', '.join(plan[action])}")
    gross = len(book.longs) + len(book.shorts)
    print(f"\n# would hold {len(book.longs)}L / {len(book.shorts)}S (market-neutral, gross {gross} names)")
    print("# SHADOW only. Live wiring (loop timer + executor diff) is the next step, with sign-off.")


if __name__ == "__main__":
    main()
