#!/usr/bin/env python3
"""Edge research #3: funding-extreme reversal/squeeze. Lookahead-free, cost-aware.

Mechanism: extreme funding = crowded positioning. Very negative funding (shorts pay
longs) = crowded shorts = squeeze fuel -> bias forward UP. Very positive = crowded
longs -> bias forward DOWN. At each hour t, cross-sectionally rank the liquid universe
by funding[t] and measure forward-H return (data > t). If the most-negative-funding
decile outperforms forward, the squeeze edge is real.
"""
import statistics
import time
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles, fetch_funding_history
from hermes_trader.indicators.math import candle_val

VOL_FLOOR = 5e6
TOPN = 45
DAYS = 30
COST_BPS = 12.0


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    coins = [m.get("name") or m.get("coin") for m in uni]
    start = int(time.time() * 1000) - DAYS * 86_400_000
    # hourly close + hourly funding, aligned by hour bucket
    closes, fund = {}, {}
    for c in coins:
        try:
            cd = fetch_hl_candles(c, "1h", DAYS * 24 + 12)
            fh = fetch_funding_history(c, start)
            if len(cd) < 240 or not fh:
                continue
            closes[c] = {int(x.t) // 3_600_000: candle_val(x, "c") for x in cd}
            fund[c] = {int(f["time"]) // 3_600_000: float(f.get("fundingRate", 0)) for f in fh}
        except Exception:
            pass
    if not closes:
        print("no data"); return
    hours = sorted(set.intersection(*[set(closes[c]) & set(fund[c]) for c in closes]))
    cost = COST_BPS / 1e4
    print(f"# {len(closes)} coins | {len(hours)} aligned hours (~{len(hours)//24}d) | cost {COST_BPS:.0f}bps")
    print(f"# {'hold':>4s} {'rebals':>6s} | {'lowFund(sqz)':>12s} {'hiFund':>8s} {'market':>7s} | "
          f"{'longLow net':>11s} {'L-S net':>8s} {'verdict':>16s}")
    for H in (6, 12, 24, 48):
        lowr, hir, mkt = [], [], []
        for i in range(0, len(hours) - H, H):
            t, tf = hours[i], hours[i] + H
            fwd = {c: closes[c][tf] / closes[c][t] - 1
                   for c in closes if t in closes[c] and tf in closes[c] and closes[c][t] > 0}
            f_t = {c: fund[c][t] for c in fwd if t in fund[c]}
            common = [c for c in fwd if c in f_t]
            if len(common) < 10:
                continue
            ranked = sorted(common, key=lambda c: f_t[c])
            k = max(1, len(ranked) // 5)            # quintiles (funding is noisier than returns)
            lowr.append(statistics.mean(fwd[c] for c in ranked[:k]))   # most-negative funding
            hir.append(statistics.mean(fwd[c] for c in ranked[-k:]))   # most-positive funding
            mkt.append(statistics.mean(fwd[c] for c in common))
        if not lowr:
            continue
        lo, hi, m = statistics.mean(lowr), statistics.mean(hir), statistics.mean(mkt)
        long_net = lo - cost                  # long the crowded-short (squeeze) names
        ls_net = (lo - hi) - 2 * cost
        verdict = "SQUEEZE-EDGE" if lo > m else "no-edge"
        print(f"  {H:>4d} {len(lowr):>6d} | {lo*100:+11.2f}% {hi*100:+7.2f}% {m*100:+6.2f}% | "
              f"{long_net*100:+10.2f}% {ls_net*100:+7.2f}% {verdict:>16s}")


if __name__ == "__main__":
    main()
