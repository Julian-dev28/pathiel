#!/usr/bin/env python3
"""Alpha hunt batch #5 — STACK: do momentum + pairs combined beat each alone? (cached daily)

Builds DAILY return streams for (a) the xs-momentum long-short book (rebalanced, held) and (b) the
pairs stat-arb book (active spread positions), aligns them by date, and compares Sharpe of each alone
vs a 50/50 blend. Low correlation between two robust +EV streams ⇒ the blend has a higher Sharpe
(diversification). Lookahead-safe (signals from data ≤ d).
"""
import os, sys, math, statistics, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

TOPN = 40
VOL_FLOOR = 5e6
COST = 10.0 / 1e4
K = 8
MOM_LB, MOM_HOLD = 7, 7
PAIR_LB, Z_ENTRY, Z_EXIT, MIN_CORR = 30, 2.0, 0.5, 0.6


def _ymd(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        bars = get_candles(m["coin"], "1d", 260)
        if len(bars) >= 90:
            data[m["coin"]] = {_ymd(b["t"]): b["c"] for b in bars}
    return data


def momentum_daily(data):
    """Daily LS-book return: rebalance every MOM_HOLD days, hold; mean(long dret) - mean(short dret)."""
    all_days = sorted({d for cl in data.values() for d in cl})
    out = {}
    longs, shorts = [], []
    for t in range(MOM_LB, len(all_days)):
        d = all_days[t]
        if (t - MOM_LB) % MOM_HOLD == 0:                       # rebalance day
            d_lb = all_days[t - MOM_LB]
            ranked = [(c, cl[d] / cl[d_lb] - 1) for c, cl in data.items()
                      if d in cl and d_lb in cl and cl[d_lb] > 0]
            if len(ranked) >= 2 * K + 4:
                ranked.sort(key=lambda x: x[1], reverse=True)
                longs = [c for c, _ in ranked[:K]]; shorts = [c for c, _ in ranked[-K:]]
        dp = all_days[t - 1]
        def dret(names):
            rs = [data[c][d] / data[c][dp] - 1 for c in names if d in data[c] and dp in data[c] and data[c][dp] > 0]
            return statistics.mean(rs) if rs else 0.0
        if longs and shorts:
            out[d] = dret(longs) - dret(shorts)
    return out


def pairs_daily(data):
    """Daily aggregate spread P&L of active pair positions (open z>2, close on reversion)."""
    coins = list(data)
    all_days = sorted({d for cl in data.values() for d in cl})
    state = {}                                                 # pair -> (side, mu, sd) while active
    daily = {d: [] for d in all_days}
    pairs = [(a, b) for a, b in itertools.combinations(coins, 2)
             if len(set(data[a]) & set(data[b])) >= PAIR_LB + 30]
    for a, b in pairs:
        common = sorted(set(data[a]) & set(data[b]))
        la = {d: math.log(data[a][d]) for d in common}
        lb = {d: math.log(data[b][d]) for d in common}
        spread = {d: la[d] - lb[d] for d in common}
        key = (a, b)
        for i in range(PAIR_LB, len(common)):
            d, dp = common[i], common[i - 1]
            win = [spread[common[j]] for j in range(i - PAIR_LB, i)]
            mu, sd = statistics.mean(win), statistics.pstdev(win)
            if sd <= 0:
                continue
            if key in state:                                   # active → accrue daily P&L, maybe close
                side, _mu, _sd = state[key]
                daily[d].append(side * (spread[dp] - spread[d]))   # convergence P&L for the day
                if abs((spread[d] - _mu) / _sd) <= Z_EXIT:
                    del state[key]
            else:
                z = (spread[d] - mu) / sd
                ra = [la[common[j]] - la[common[j - 1]] for j in range(i - PAIR_LB + 1, i)]
                rb = [lb[common[j]] - lb[common[j - 1]] for j in range(i - PAIR_LB + 1, i)]
                if abs(z) >= Z_ENTRY and _corr(ra, rb) >= MIN_CORR:
                    state[key] = (-1 if z > 0 else 1, mu, sd)
    return {d: statistics.mean(v) for d, v in daily.items() if v}


def _corr(xs, ys):
    n = len(xs)
    if n < 5:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs)); sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def stats(name, series):
    if len(series) < 20:
        print(f"  {name:16} n={len(series)} (thin)"); return None
    mu, sd = statistics.mean(series), statistics.pstdev(series)
    sharpe = (mu / sd * math.sqrt(365)) if sd > 0 else 0.0     # annualized (daily series)
    print(f"  {name:16} n={len(series):>4} dailyμ {mu*100:>+6.3f}%  σ {sd*100:>5.2f}%  Sharpe(ann) {sharpe:>+5.2f}")
    return sharpe


def main():
    print(f"# STACK: momentum + pairs combined | top{TOPN} | mom LB={MOM_LB}/h={MOM_HOLD}, pairs z>{Z_ENTRY}")
    data = load()
    print(f"# {len(data)} coins\n")
    m = momentum_daily(data)
    p = pairs_daily(data)
    common = sorted(set(m) & set(p))
    mser = [m[d] for d in common]
    pser = [p[d] for d in common]
    blend = [0.5 * a + 0.5 * b for a, b in zip(mser, pser)]
    print(f"# aligned days: {len(common)}")
    sm = stats("momentum", mser)
    sp = stats("pairs", pser)
    sb = stats("50/50 blend", blend)
    print(f"\n  correlation(momentum, pairs) = {_corr(mser, pser):+.2f}")
    if sm and sp and sb:
        best_solo = max(sm, sp)
        verdict = "STACK WINS (diversifies)" if sb > best_solo + 0.05 else "no stacking benefit"
        print(f"  blend Sharpe {sb:+.2f} vs best-solo {best_solo:+.2f} → {verdict}")


if __name__ == "__main__":
    main()
