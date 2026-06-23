#!/usr/bin/env python3
"""Alpha hunt — batch sweep of more candidate edges on the cached daily universe (fast, no refetch).

Each is lookahead-safe, cost-aware, OOS. Adds to the validated-edge tally toward the target of 10.
Signals:
  A. single-bar reversal     — after a day < -X%, LONG next day (bounce); after > +X%, SHORT (fade)
  B. xs short-term reversal   — cross-sectional: long the 2-3d LOSERS, short the winners (mean-rev)
  C. low-vol anomaly          — cross-sectional: long lowest realized-vol quantile, short highest
  D. xs momentum (confirm)    — LB=21d hold=5d, long-short (sanity vs edge_xsectional)
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

TOPN = 50
VOL_FLOOR = 5e6
COST = 10.0 / 1e4


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 80:
            data[m["coin"]] = {_ymd(b["t"]): (b["o"], b["h"], b["l"], b["c"]) for b in bars}
    return data


def rep(name, arr):
    if not arr or len(arr) < 10:
        print(f"  {name:30} n={len(arr) if arr else 0} (thin)"); return
    n = len(arr); w = sum(1 for r in arr if r > 0); mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
    h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if statistics.mean(arr) > 0 and rob == "ROBUST" else ""
    print(f"  {name:30} n={n:>4} win {w/n*100:>3.0f}%  mean {statistics.mean(arr)*100:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")


def single_bar_reversal(data, thresh):
    """After a >thresh move, fade it next day. Returns signed net returns."""
    out = []
    for coin, oc in data.items():
        days = sorted(oc)
        for i in range(1, len(days) - 2):
            d, dn = days[i], days[i + 1]
            c0, c1 = oc[days[i - 1]][3], oc[d][3]
            if c0 <= 0:
                continue
            ret = c1 / c0 - 1
            if abs(ret) < thresh:
                continue
            side = -1 if ret > 0 else +1                # fade: big up→short, big down→long
            o, c = oc[dn][0], oc[dn][3]
            if o > 0:
                out.append(side * (c - o) / o - COST)
    return out


def xs_rank(data, lb, h, long_winners):
    """Cross-sectional long-short. long_winners=True → momentum; False → reversal."""
    all_days = sorted({d for oc in data.values() for d in oc})
    K = 8
    out = []
    for t in range(lb, len(all_days) - h - 1):
        d, d_lb, d_en = all_days[t], all_days[t - lb], all_days[t + 1]
        d_ex = all_days[min(t + 1 + h, len(all_days) - 1)]
        ranked = []
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_en in oc and d_ex in oc and oc[d_lb][3] > 0:
                ranked.append((coin, oc[d][3] / oc[d_lb][3] - 1))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=long_winners)   # winners first if momentum
        longs = [c for c, _ in ranked[:K]]; shorts = [c for c, _ in ranked[-K:]]
        def fwd(c):
            o = data[c][d_en][0]; cl = data[c][d_ex][3]
            return (cl - o) / o if o > 0 else 0.0
        out.append((statistics.mean(map(fwd, longs)) - statistics.mean(map(fwd, shorts))) - 2 * COST)
    return out


def low_vol_anomaly(data, lb, h):
    """Cross-sectional: long lowest realized-vol quantile, short highest. Long-short net."""
    all_days = sorted({d for oc in data.values() for d in oc})
    K = 8
    out = []
    for t in range(lb, len(all_days) - h - 1):
        d_en = all_days[t + 1]; d_ex = all_days[min(t + 1 + h, len(all_days) - 1)]
        vols = []
        for coin, oc in data.items():
            ds = sorted(x for x in oc if x <= all_days[t])
            if len(ds) <= lb or all_days[t] not in oc or d_en not in oc or d_ex not in oc:
                continue
            rets = []
            for k in range(len(ds) - lb, len(ds)):
                p0, p1 = oc[ds[k - 1]][3], oc[ds[k]][3]
                if p0 > 0: rets.append(p1 / p0 - 1)
            if len(rets) >= lb - 2:
                vols.append((coin, statistics.pstdev(rets)))
        if len(vols) < 2 * K + 4:
            continue
        vols.sort(key=lambda x: x[1])                  # lowest vol first
        longs = [c for c, _ in vols[:K]]; shorts = [c for c, _ in vols[-K:]]
        def fwd(c):
            o = data[c][d_en][0]; cl = data[c][d_ex][3]
            return (cl - o) / o if o > 0 else 0.0
        out.append((statistics.mean(map(fwd, longs)) - statistics.mean(map(fwd, shorts))) - 2 * COST)
    return out


def main():
    print("# Alpha sweep | cached daily universe | cost 10bps | lookahead-safe, OOS")
    data = load()
    print(f"# {len(data)} coins\n")
    print("# A. single-bar reversal (fade the extreme day):")
    for th in (0.08, 0.12, 0.18):
        rep(f"  fade >|{int(th*100)}%| · next-1d", single_bar_reversal(data, th))
    print("\n# B. cross-sectional SHORT-TERM reversal (long losers):")
    for lb, h in ((2, 3), (3, 5), (5, 5)):
        rep(f"  xs-reversal LB={lb} hold={h}", xs_rank(data, lb, h, long_winners=False))
    print("\n# C. low-volatility anomaly (long low-vol / short high-vol):")
    for lb, h in ((14, 5), (30, 10)):
        rep(f"  low-vol LB={lb} hold={h}", low_vol_anomaly(data, lb, h))
    print("\n# D. xs momentum confirm (LB=21 hold=5):")
    rep("  xs-momentum LB=21 hold=5", xs_rank(data, 21, 5, long_winners=True))


if __name__ == "__main__":
    main()
