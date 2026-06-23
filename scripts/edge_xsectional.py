#!/usr/bin/env python3
"""Alpha hunt #1 — CROSS-SECTIONAL momentum/reversal (rank the universe, not single coins).

Different structure than everything tested so far: don't predict a coin in isolation — each
rebalance, rank all liquid coins by trailing return, go LONG the top quantile and SHORT the
bottom (market-neutral). Long-short spread > 0 ⇒ momentum (winners keep winning); < 0 ⇒
reversal (losers bounce). Lookahead-safe (rank on close[t], enter t+1 open), cost-aware, OOS.
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

TOPN = 50
VOL_FLOOR = 5e6
K = 8                      # names per leg (top-K long, bottom-K short)
COST_BPS = 10.0            # per name, round-trip
CONFIGS = [(lb, h) for lb in (7, 14, 30) for h in (5, 10)]


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            data[c] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}
    return data


def run(data, lb, h, cost):
    all_days = sorted({d for oc in data.values() for d in oc})
    ls_rets, lo_rets = [], []                     # long-short spread, long-only
    for t in range(lb, len(all_days) - h - 1):
        d = all_days[t]
        d_lb = all_days[t - lb]
        d_entry = all_days[t + 1]
        d_exit = all_days[t + 1 + h] if t + 1 + h < len(all_days) else all_days[-1]
        ranked = []
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_entry in oc and d_exit in oc:
                c_now, c_past = oc[d][1], oc[d_lb][1]
                if c_past > 0:
                    ranked.append((coin, c_now / c_past - 1))
        if len(ranked) < 2 * K + 4:               # need enough names to rank
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        longs = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):                              # enter t+1 open, exit t+1+h close
            o, _ = data[coin][d_entry]; _, c = data[coin][d_exit]
            return (c - o) / o if o > 0 else 0.0
        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        ls_rets.append((lr - sr) - 2 * cost)        # both legs cost
        lo_rets.append(lr - cost)
    return ls_rets, lo_rets


def rep(name, arr):
    if not arr:
        print(f"  {name:28} n=0"); return
    n = len(arr); w = sum(1 for r in arr if r > 0); mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
    h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if statistics.mean(arr) > 0 and rob == "ROBUST" else ""
    print(f"  {name:28} n={n:>4} win {w/n*100:>3.0f}%  mean {statistics.mean(arr)*100:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")


def main():
    print(f"# Cross-sectional momentum | top{TOPN} liquid crypto, K={K}/leg | cost {COST_BPS:.0f}bps/name | "
          f"lookahead-safe, OOS")
    data = load()
    print(f"# {len(data)} coins loaded\n")
    cost = COST_BPS / 1e4
    for lb, h in CONFIGS:
        ls, lo = run(data, lb, h, cost)
        print(f"# LB={lb}d hold={h}d:")
        rep(f"  long-short (momentum)", ls)
        rep(f"  long-only top-{K}", lo)
    print("\n# long-short>0 ⇒ momentum edge; <0 ⇒ reversal edge. Only ROBUST +EV cuts get wired.")


if __name__ == "__main__":
    main()
