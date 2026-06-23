#!/usr/bin/env python3
"""Alpha hunt batch #3 — PAIRS / cointegration stat-arb (a genuinely independent edge family).

Mean-reversion of a market-neutral SPREAD between two correlated coins. For each pair, spread =
log(A) - log(B); z-score it over a trailing window (lookahead-safe). When |z| is extreme the pair
has diverged → trade the convergence (short the rich, long the cheap); exit when z reverts. This is
orthogonal to momentum: it profits from RELATIVE mean-reversion, not trend.
"""
import os, sys, math, statistics, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

TOPN = 40
VOL_FLOOR = 5e6
COST = 10.0 / 1e4
LOOKBACK = 30           # trailing window for the spread z-score
Z_ENTRY = 2.0
Z_EXIT = 0.5
MAXHOLD = 15
MIN_CORR = 0.6          # only trade pairs that are actually co-moving (else the spread is noise)


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


def _corr(xs, ys):
    n = len(xs)
    if n < 5:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs)); sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def run_pair(ca, cb, common):
    """Walk the spread; trade z-extremes. Returns list of net per-trade returns (market-neutral)."""
    la = [math.log(ca[d]) for d in common]
    lb = [math.log(cb[d]) for d in common]
    spread = [a - b for a, b in zip(la, lb)]
    out = []
    i = LOOKBACK
    while i < len(common) - 1:
        win = spread[i - LOOKBACK:i]                       # strictly before i (lookahead-safe)
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        if sd <= 0:
            i += 1; continue
        # only trade pairs currently co-moving (returns correlation over the window)
        ra = [la[k] - la[k - 1] for k in range(i - LOOKBACK + 1, i)]
        rb = [lb[k] - lb[k - 1] for k in range(i - LOOKBACK + 1, i)]
        if _corr(ra, rb) < MIN_CORR:
            i += 1; continue
        z = (spread[i] - mu) / sd
        if abs(z) < Z_ENTRY:
            i += 1; continue
        side = -1 if z > 0 else 1                          # z>0: A rich → short A/long B (side on A)
        entry_spread = spread[i]
        # hold until reversion (|z|<exit) or maxhold; pnl = side * (entry_spread - exit_spread)
        j = i + 1
        while j < min(i + 1 + MAXHOLD, len(common)):
            zj = (spread[j] - mu) / sd
            if abs(zj) <= Z_EXIT:
                break
            j += 1
        j = min(j, len(common) - 1)
        pnl = side * (entry_spread - spread[j])            # spread convergence (log-return of the L/S pair)
        out.append(pnl - 2 * COST)                         # two legs
        i = j + 1                                          # non-overlapping
    return out


def main():
    print(f"# Pairs / cointegration stat-arb | top{TOPN} liquid | z-entry {Z_ENTRY} exit {Z_EXIT} | "
          f"corr>{MIN_CORR} | cost {COST*1e4:.0f}bps/leg | lookahead-safe, OOS")
    data = load()
    coins = list(data)
    print(f"# {len(coins)} coins → {len(coins)*(len(coins)-1)//2} candidate pairs\n")
    all_trades = []
    npairs = 0
    for ca, cb in itertools.combinations(coins, 2):
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        npairs += 1
        all_trades += run_pair(data[ca], data[cb], common)
    if not all_trades:
        print("no trades"); return
    n = len(all_trades); w = sum(1 for r in all_trades if r > 0); mid = n // 2
    h1 = statistics.mean(all_trades[:mid]) * 100; h2 = statistics.mean(all_trades[mid:]) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if statistics.mean(all_trades) > 0 and rob == "ROBUST" else ""
    print(f"# {npairs} co-moving pairs traded, {n} spread-reversion trades")
    print(f"  pairs stat-arb        n={n:>4} win {w/n*100:>3.0f}%  mean {statistics.mean(all_trades)*100:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")
    print(f"  median {statistics.median(all_trades)*100:+.2f}% | per-trade log-spread convergence, market-neutral")


if __name__ == "__main__":
    main()
