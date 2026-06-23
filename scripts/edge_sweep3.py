#!/usr/bin/env python3
"""Alpha hunt batch #4 — calendar seasonality + volatility-regime momentum (cached daily, fast).

Lookahead-safe, cost-aware, OOS-robust gate.
  A. day-of-week     — is any weekday's cross-coin mean daily return robustly +/- (calendar edge)?
  B. turn-of-month   — last-2 / first-3 trading days of the month vs the rest (TOM effect)
  C. vol-regime momo — does xs-momentum's edge concentrate in HIGH or LOW BTC-vol regimes?
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


def _dt(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
def _ymd(ms): return _dt(ms).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 80:
            data[m["coin"]] = bars
    return data


def rep(name, arr):
    if not arr or len(arr) < 15:
        print(f"  {name:28} n={len(arr) if arr else 0} (thin)"); return
    n = len(arr); w = sum(1 for r in arr if r > 0); mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
    h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if statistics.mean(arr) > 0 and rob == "ROBUST" else ""
    print(f"  {name:28} n={n:>4} win {w/n*100:>3.0f}%  mean {statistics.mean(arr)*100:>+6.3f}%  "
          f"OOS {h1:>+5.3f}/{h2:>+5.3f} {rob}{flag}")


def day_of_week(data):
    """Mean daily return by weekday (no cost — it's a directional bias probe, not a round-trip)."""
    buckets = {i: [] for i in range(7)}
    for coin, bars in data.items():
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 > 0:
                buckets[_dt(bars[k]["t"]).weekday()].append(p1 / p0 - 1)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i in range(7):
        rep(f"  {names[i]}", buckets[i])


def turn_of_month(data):
    tom, rest = [], []
    for coin, bars in data.items():
        for k in range(1, len(bars)):
            p0, p1 = bars[k - 1]["c"], bars[k]["c"]
            if p0 <= 0:
                continue
            dom = _dt(bars[k]["t"]).day
            r = p1 / p0 - 1
            (tom if (dom <= 3 or dom >= 27) else rest).append(r)
    rep("  turn-of-month (long)", tom)
    rep("  rest-of-month", rest)


def vol_regime_momentum(data, lb=7, h=10):
    """Split xs-momentum long-short rebalances by BTC trailing-vol regime (median split)."""
    if "BTC" not in data:
        print("  (no BTC)"); return
    btc = {_ymd(b["t"]): b["c"] for b in data["BTC"]}
    closes = {c: {_ymd(b["t"]): b["c"] for b in bars} for c, bars in data.items()}
    all_days = sorted({d for cl in closes.values() for d in cl})
    btc_days = sorted(btc)

    def btc_vol(d_idx):
        ds = btc_days[max(0, d_idx - 14):d_idx]
        rets = [btc[ds[k]] / btc[ds[k - 1]] - 1 for k in range(1, len(ds)) if btc[ds[k - 1]] > 0]
        return statistics.pstdev(rets) if len(rets) > 3 else None

    # precompute BTC vol per day-index in btc_days
    spread_with_vol = []
    bidx = {d: i for i, d in enumerate(btc_days)}
    for t in range(lb, len(all_days) - h - 1):
        d, d_lb, d_en = all_days[t], all_days[t - lb], all_days[t + 1]
        d_ex = all_days[min(t + 1 + h, len(all_days) - 1)]
        if d not in bidx:
            continue
        v = btc_vol(bidx[d])
        if v is None:
            continue
        ranked = []
        for c, cl in closes.items():
            if d in cl and d_lb in cl and d_en in cl and d_ex in cl and cl[d_lb] > 0:
                ranked.append((c, cl[d] / cl[d_lb] - 1))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        L = [c for c, _ in ranked[:K]]; S = [c for c, _ in ranked[-K:]]
        def fwd(c): return closes[c][d_ex] / closes[c][d_en] - 1 if closes[c][d_en] > 0 else 0.0
        sp = (statistics.mean(map(fwd, L)) - statistics.mean(map(fwd, S))) - 2 * COST
        spread_with_vol.append((v, sp))
    if len(spread_with_vol) < 30:
        print("  (thin)"); return
    med = statistics.median(v for v, _ in spread_with_vol)
    lo = [s for v, s in spread_with_vol if v <= med]
    hi = [s for v, s in spread_with_vol if v > med]
    rep("  momo · LOW BTC-vol", lo)
    rep("  momo · HIGH BTC-vol", hi)


def main():
    print("# Alpha sweep #4 | cached daily | seasonality + vol-regime | lookahead-safe, OOS")
    data = load()
    print(f"# {len(data)} coins\n")
    print("# A. day-of-week (cross-coin mean daily return, directional bias):")
    day_of_week(data)
    print("\n# B. turn-of-month:")
    turn_of_month(data)
    print("\n# C. xs-momentum conditioned on BTC vol regime:")
    vol_regime_momentum(data)


if __name__ == "__main__":
    main()
