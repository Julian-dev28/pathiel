#!/usr/bin/env python3
"""Alpha hunt batch #2 — classic quant factors on the cached daily universe (fast, no refetch).

Lookahead-safe, cost-aware, OOS-robust gate. Drawing from the literature:
  A. TSMOM (Moskowitz/AQR)   — time-series/ABSOLUTE momentum: long if own trailing return>0, else short
  B. skip-momentum (12-1)    — cross-sectional momentum ranking on return[t-LB .. t-skip] (skip the
                                last `skip` days to dodge short-term reversal — beat plain momentum in equities)
  C. vol-scaled XS-momentum  — the validated xs-momentum, but inverse-realized-vol weight each leg (risk parity)
  D. BTC lead-lag            — does BTC's day-t move predict ALTS' day t+1 (beta-timing)?
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

TOPN = 50
VOL_FLOOR = 5e6
COST = 10.0 / 1e4
K = 8


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 80:
            data[m["coin"]] = {_ymd(b["t"]): b["c"] for b in bars}
    return data


def rep(name, arr):
    if not arr or len(arr) < 15:
        print(f"  {name:30} n={len(arr) if arr else 0} (thin)"); return
    n = len(arr); w = sum(1 for r in arr if r > 0); mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
    h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if statistics.mean(arr) > 0 and rob == "ROBUST" else ""
    print(f"  {name:30} n={n:>4} win {w/n*100:>3.0f}%  mean {statistics.mean(arr)*100:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")


def _ret(closes, a, b):
    return closes[b] / closes[a] - 1 if (a in closes and b in closes and closes[a] > 0) else None


def tsmom(data, lb, h, thresh):
    """Absolute momentum: sign of trailing return → direction. Directional (has market beta)."""
    out = []
    for coin, cl in data.items():
        days = sorted(cl)
        for t in range(lb, len(days) - h - 1):
            r = _ret(cl, days[t - lb], days[t])
            if r is None or abs(r) < thresh:
                continue
            side = 1 if r > 0 else -1
            f = _ret(cl, days[t + 1], days[min(t + 1 + h, len(days) - 1)])
            if f is not None:
                out.append(side * f - COST)
    return out


def xs_skip(data, lb, skip, h):
    """Cross-sectional momentum, ranking on return[t-lb .. t-skip] (skip recent reversal). Long-short."""
    all_days = sorted({d for c in data.values() for d in c})
    out = []
    for t in range(lb, len(all_days) - h - 1):
        d_score, d_lb, d_en = all_days[t - skip], all_days[t - lb], all_days[t + 1]
        d_ex = all_days[min(t + 1 + h, len(all_days) - 1)]
        ranked = []
        for coin, cl in data.items():
            r = _ret(cl, d_lb, d_score)
            if r is not None and d_en in cl and d_ex in cl:
                ranked.append((coin, r))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        L = [c for c, _ in ranked[:K]]; S = [c for c, _ in ranked[-K:]]
        def fwd(c): return _ret(data[c], d_en, d_ex) or 0.0
        out.append((statistics.mean(map(fwd, L)) - statistics.mean(map(fwd, S))) - 2 * COST)
    return out


def xs_volscaled(data, lb, h):
    """XS-momentum with inverse-realized-vol weights per leg (risk parity)."""
    all_days = sorted({d for c in data.values() for d in c})
    out = []
    for t in range(lb, len(all_days) - h - 1):
        d, d_lb, d_en = all_days[t], all_days[t - lb], all_days[t + 1]
        d_ex = all_days[min(t + 1 + h, len(all_days) - 1)]
        ranked = []
        for coin, cl in data.items():
            r = _ret(cl, d_lb, d)
            ds = [x for x in sorted(cl) if x <= d]
            if r is None or len(ds) <= lb or d_en not in cl or d_ex not in cl:
                continue
            rets = [_ret(cl, ds[k - 1], ds[k]) for k in range(len(ds) - lb, len(ds))]
            rets = [x for x in rets if x is not None]
            vol = statistics.pstdev(rets) if len(rets) > 2 else None
            if vol and vol > 0:
                ranked.append((coin, r, 1.0 / vol))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        L = ranked[:K]; S = ranked[-K:]
        def wfwd(rows):
            tw = sum(w for _, _, w in rows) or 1e-9
            return sum((_ret(data[c], d_en, d_ex) or 0.0) * w for c, _, w in rows) / tw
        out.append((wfwd(L) - wfwd(S)) - 2 * COST)
    return out


def btc_leadlag(data, thresh, h):
    """When BTC has a big day-t move, do ALTS follow on day t+1? (beta-timing, directional)."""
    if "BTC" not in data:
        return []
    btc = data["BTC"]; all_days = sorted(btc)
    out = []
    for t in range(1, len(all_days) - h - 1):
        rb = _ret(btc, all_days[t - 1], all_days[t])
        if rb is None or abs(rb) < thresh:
            continue
        side = 1 if rb > 0 else -1
        d_en = all_days[t + 1]; d_ex = all_days[min(t + 1 + h, len(all_days) - 1)]
        alt = [(_ret(cl, d_en, d_ex)) for c, cl in data.items() if c != "BTC"]
        alt = [x for x in alt if x is not None]
        if alt:
            out.append(side * statistics.mean(alt) - COST)
    return out


def main():
    print("# Alpha sweep #2 | cached daily universe | cost 10bps | lookahead-safe, OOS")
    data = load()
    print(f"# {len(data)} coins\n")
    print("# A. TSMOM (absolute time-series momentum, directional):")
    for lb in (20, 40):
        for th in (0.0, 0.05):
            rep(f"  tsmom LB={lb} h=5 thr={th}", tsmom(data, lb, 5, th))
    print("\n# B. skip-momentum (12-1 style, xs long-short):")
    for lb, sk in ((14, 3), (21, 5)):
        rep(f"  xs-skip LB={lb} skip={sk} h=5", xs_skip(data, lb, sk, 5))
    print("\n# C. vol-scaled xs-momentum (risk parity legs):")
    for lb in (7, 14):
        rep(f"  xs-volscaled LB={lb} h=10", xs_volscaled(data, lb, 10))
    print("\n# D. BTC lead-lag (alts follow BTC next day?):")
    for th in (0.03, 0.05):
        rep(f"  btc-leadlag thr={th} h=1", btc_leadlag(data, th, 1))


if __name__ == "__main__":
    main()
