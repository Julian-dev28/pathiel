#!/usr/bin/env python3
"""Edge research #2: cross-sectional momentum vs reversal. Lookahead-free, cost-aware.

At each rebalance t, rank the liquid universe by trailing-LB return (data <= t),
then measure forward-H return (data > t). Top decile outperforming forward = MOMENTUM;
underperforming = REVERSAL. Non-overlapping holding (rebalance every H bars) so we
don't inflate the signal with autocorrelation. Reports long-top-only (our edge is
long) and long-short, both net of round-trip cost.
"""
import statistics
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

VOL_FLOOR = 5e6
TOPN = 100
BARS = 1000          # ~30d of 1h
COST_BPS = 18.0     # round-trip taker+slippage


def main():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    coins = [m.get("name") or m.get("coin") for m in uni]
    data = {}
    for c in coins:
        try:
            cd = fetch_hl_candles(c, "1h", BARS)
            if len(cd) >= 240:
                data[c] = [candle_val(x, "c") for x in cd]
        except Exception:
            pass
    if not data:
        print("no candle data"); return
    L = min(len(v) for v in data.values())
    closes = {c: v[-L:] for c, v in data.items()}
    cost = COST_BPS / 1e4
    half = L // 2  # out-of-sample split: bars before/after = train/test
    print(f"# {len(closes)} liquid coins | {L} 1h bars (~{L//24}d) | cost {COST_BPS:.0f}bps round-trip")
    print(f"# OOS split at bar {half} (~{half//24}d each). A real edge holds in BOTH halves.")
    print(f"# {'lookbk':>6s} {'hold':>4s} | {'longTop net 1stHALF':>19s} | {'longTop net 2ndHALF':>19s} | {'robust?':>8s}")
    for LB in (6, 12, 24, 48, 72):
        for H in (6, 12, 24):
            halves = [[], []]   # longTop net per rebalance, split by half
            t = LB
            while t + H < L:
                rets = {c: closes[c][t] / closes[c][t - LB] - 1 for c in closes if closes[c][t - LB] > 0}
                fwd = {c: closes[c][t + H] / closes[c][t] - 1 for c in closes if closes[c][t] > 0}
                common = [c for c in rets if c in fwd]
                if len(common) >= 10:
                    ranked = sorted(common, key=lambda c: rets[c])
                    k = max(1, len(ranked) // 10)
                    top_fwd = statistics.mean(fwd[c] for c in ranked[-k:])
                    halves[0 if t < half else 1].append(top_fwd - cost)
                t += H
            if not halves[0] or not halves[1]:
                continue
            h1, h2 = statistics.mean(halves[0]), statistics.mean(halves[1])
            robust = "YES" if (h1 > 0 and h2 > 0) else "no"
            print(f"  {LB:>6d} {H:>4d} | {h1*100:+12.2f}% (n={len(halves[0]):>3d}) | "
                  f"{h2*100:+12.2f}% (n={len(halves[1]):>3d}) | {robust:>8s}")


if __name__ == "__main__":
    main()
