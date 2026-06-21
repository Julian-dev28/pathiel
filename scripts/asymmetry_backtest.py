#!/usr/bin/env python3
"""LEGACY HEURISTIC asymmetry sweep for exit-shape experiments.

This does not replay logged AI verdicts or current runner/portfolio gates. It
reuses the legacy heuristic primitives in backtest.py/reentry_backtest.py, so
treat results as directional only. Use strategy_grid_search.py --profile exit or
backtest_logged.py for current config evidence.

Usage: python3 scripts/asymmetry_backtest.py --days 21 --coins 30 --interval 1h
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reentry_backtest import simulate
from hermes_trader.agents.config import get_config
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.client.universe import get_universe


def stats(trades):
    n = len(trades)
    if not n:
        return None
    p = [t["pnl_usd"] for t in trades]
    w = [x for x in p if x > 0]; l = [x for x in p if x < 0]
    net = sum(p); aw = sum(w)/len(w) if w else 0; al = sum(l)/len(l) if l else 0
    pf = (sum(w)/abs(sum(l))) if l else float('inf')
    cum = peak = mdd = 0.0
    for x in p:
        cum += x; peak = max(peak, cum); mdd = min(mdd, cum-peak)
    payoff = abs(aw/al) if al else float('inf')
    be = abs(al)/(aw+abs(al))*100 if (aw+abs(al)) else 0
    return dict(n=n, win=len(w)/n*100, net=net, aw=aw, al=al, pf=pf,
                payoff=payoff, be=be, mdd=mdd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--coins", type=int, default=30)
    ap.add_argument("--interval", default="1h", choices=["15m", "1h", "4h"])
    ap.add_argument("--equity", type=float, default=180.0)
    ap.add_argument("--equity-fraction", type=float, default=0.20)
    ap.add_argument("--lev", type=int, default=12)
    ap.add_argument("--max-loss", type=float, default=0.40)
    args = ap.parse_args()

    cfg = get_config()
    bars = {"15m": 96, "1h": 24, "4h": 6}[args.interval] * args.days + 110
    perps = [m for m in get_universe() if m["type"] == "perp" and not m["coin"].startswith("@")]
    coins = sorted(perps, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[:args.coins]

    # (protect_pct, retrace) grid. Row 1 matches the current tight scalp exit,
    # but the entry model remains legacy heuristic.
    grid = [
        ("current tight  ", 1.25, 0.20),
        ("old scalp      ", 1.50, 0.30),
        ("scalp+wide-give", 1.50, 0.45),
        ("scalp+wider    ", 1.50, 0.60),
        ("mid protect    ", 2.50, 0.40),
        ("mid+wide       ", 2.50, 0.55),
        ("run protect    ", 4.00, 0.45),
        ("run+wide       ", 4.00, 0.60),
    ]
    print(f"=== LEGACY ASYMMETRY sweep | {args.days}d {args.interval} top-{args.coins} "
          f"lev{args.lev}x | LOSS stop HELD at {args.max_loss}% ===")
    print(f"{'config':17s} {'n':>4} {'win%':>5} {'net$':>9} {'avgW':>7} {'avgL':>7} "
          f"{'pay':>5} {'BE%':>5} {'PF':>5} {'maxDD$':>8}")

    # fetch once per coin, replay every grid row on the same candles
    cache = {}
    for m in coins:
        try:
            c = fetch_hl_candles(m["coin"], args.interval, bars)
            if len(c) >= 110:
                cache[m["coin"]] = (c, int(m.get("maxLeverage", 5)))
        except Exception:
            pass

    for label, pp, rt in grid:
        trades = []
        for coin, (candles, max_lev) in cache.items():
            trades += simulate(coin, candles, max_lev, policy="BLOCK", cfg=cfg,
                               equity=args.equity, equity_fraction=args.equity_fraction,
                               lev_ceiling=args.lev, max_loss_pct=args.max_loss,
                               protect_pct=pp, retrace_threshold=rt,
                               cooldown_bars=4, reclaim_pct=1.0, min_composite=30,
                               exit_mode="fixed")
        s = stats(trades)
        if s:
            print(f"{label:17s} {s['n']:>4} {s['win']:>5.0f} {s['net']:>+9.1f} "
                  f"{s['aw']:>+7.2f} {s['al']:>+7.2f} {s['payoff']:>5.2f} {s['be']:>5.0f} "
                  f"{s['pf']:>5.2f} {s['mdd']:>+8.1f}")
    print("\nRead: use this only for exit-shape intuition. Confirm any promising row "
          "with logged/portfolio replays before changing live config.")


if __name__ == "__main__":
    main()
