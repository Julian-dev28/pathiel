#!/usr/bin/env python3
"""Alpha hunt batch #6 — residual (BTC-neutral) momentum + acceleration (cached daily).

The audit showed total xs-momentum is REGIME-DEPENDENT (lumpy, ~2-month dead stretches), likely
when the BTC market factor swamps the cross-sectional signal. So test the residual:
  A. residual momentum — rank by coin return MINUS beta×BTC return (idiosyncratic). Smoother? (Blitz)
  B. acceleration       — rank by recent-momentum minus older-momentum (2nd derivative)
Each reported with the SAME 4-quartile sub-period stability check used in the audit (is it less lumpy?).
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

VOL_FLOOR = 5e6
COST = 10.0 / 1e4
K = 8
LB, HOLD, BETA_WIN = 7, 10, 30


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load(topn=50):
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:topn]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 80:
            data[m["coin"]] = {_ymd(b["t"]): b["c"] for b in bars}
    return data


def _beta(cr, br):
    if len(cr) < 8:
        return 1.0
    mb = statistics.mean(br); vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = statistics.mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def run(data, mode):
    btc = data.get("BTC")
    if not btc:
        return []
    all_days = sorted({d for cl in data.values() for d in cl})
    out = []
    for t in range(max(LB, BETA_WIN), len(all_days) - HOLD - 1):
        d, d_lb, d_en = all_days[t], all_days[t - LB], all_days[t + 1]
        d_ex = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[t - BETA_WIN:t]
        br = [btc[win_days[k]] / btc[win_days[k - 1]] - 1 for k in range(1, len(win_days))
              if win_days[k] in btc and win_days[k - 1] in btc and btc[win_days[k - 1]] > 0]
        ranked = []
        for c, cl in data.items():
            if not all(x in cl for x in (d, d_lb, d_en, d_ex)) or cl[d_lb] <= 0:
                continue
            if mode == "residual":
                cr = [cl[win_days[k]] / cl[win_days[k - 1]] - 1 for k in range(1, len(win_days))
                      if win_days[k] in cl and win_days[k - 1] in cl and cl[win_days[k - 1]] > 0]
                if len(cr) != len(br) or len(br) < 8:
                    continue
                beta = _beta(cr, br)
                rc = cl[d] / cl[d_lb] - 1
                rb = btc[d] / btc[d_lb] - 1 if d in btc and d_lb in btc and btc[d_lb] > 0 else 0
                score = rc - beta * rb
            elif mode == "accel":
                d_mid = all_days[t - LB // 2] if t - LB // 2 < len(all_days) else d
                if d_mid not in cl or cl[d_mid] <= 0:
                    continue
                recent = cl[d] / cl[d_mid] - 1
                older = cl[d_mid] / cl[d_lb] - 1
                score = recent - older
            else:
                score = cl[d] / cl[d_lb] - 1
            ranked.append((c, score))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        L = [c for c, _ in ranked[:K]]; S = [c for c, _ in ranked[-K:]]
        def fwd(c): return data[c][d_ex] / data[c][d_en] - 1 if data[c][d_en] > 0 else 0.0
        out.append((statistics.mean(map(fwd, L)) - statistics.mean(map(fwd, S))) - 2 * COST)
    return out


def report(name, arr):
    if not arr or len(arr) < 20:
        print(f"  {name:22} n={len(arr) if arr else 0} (thin)"); return
    mid = len(arr) // 2
    h1 = statistics.mean(arr[:mid]) * 100; h2 = statistics.mean(arr[mid:]) * 100
    q = len(arr) // 4
    qs = [statistics.mean(arr[i * q:(i + 1) * q if i < 3 else len(arr)]) * 100 for i in range(4)]
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    nneg = sum(1 for x in qs if x <= 0)
    flag = "  <<< +EV" if statistics.mean(arr) > 0 and rob == "ROBUST" else ""
    smooth = " SMOOTH(0 neg Q)" if nneg == 0 else f" ({nneg}/4 Q<=0)"
    print(f"  {name:22} mean {statistics.mean(arr)*100:>+6.2f}%  OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")
    print(f"  {'':22} quartiles {qs[0]:>+5.2f}/{qs[1]:>+5.2f}/{qs[2]:>+5.2f}/{qs[3]:>+5.2f}{smooth}")


def main():
    print(f"# Alpha sweep #6 | residual + acceleration momentum | LB={LB}/hold={HOLD} | lookahead-safe, OOS+quartiles")
    data = load(50)
    print(f"# {len(data)} coins\n")
    print("# baseline TOTAL momentum (for comparison — audit showed it's lumpy):")
    report("total momentum", run(data, "total"))
    print("\n# A. residual (BTC-neutral) momentum — smoother across regimes?:")
    report("residual momentum", run(data, "residual"))
    print("\n# B. acceleration (momentum-of-momentum):")
    report("acceleration", run(data, "accel"))


if __name__ == "__main__":
    main()
