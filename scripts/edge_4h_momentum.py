#!/usr/bin/env python3
"""Alpha hunt — 4h cross-sectional RESIDUAL momentum.

Tests whether the validated daily xs-residual-momentum edge (LB=7d/hold=10d, +2.37%/rebal)
holds on the 4h timeframe.  Uses the same methodology: BTC-neutral residual score, top-K long
/ bottom-K short, enter-next-bar open, exit-at-hold close.

DATA CONSTRAINT WARNING (honest accounting):
  Cache holds 28 coins × 241 bars × 4h = ~40 calendar days (2026-05-14 to 2026-06-23).
  The daily edge was validated on ~260 bars (~1 year).  40 days covers ONE market regime
  (a strong up-trend).  OOS halves are only ~20 days each — far below the ~130 days per
  half the daily version used.  Any "ROBUST" label here is regime-scoped, not multi-cycle.
  A ROBUST label on 40 days of 4h data does NOT clear the methodology bar on its own.

COST IS HIGHER on 4h: LB=12/H=6 rebalances ~31×/40d vs daily LB=7/H=10 rebalancing ~4×/40d.
  => much larger total friction drag even if the per-rebal return looks similar.

Run with:  BT_CACHE_ONLY=1 python3 scripts/edge_4h_momentum.py
"""
import os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── constants ──────────────────────────────────────────────────────────────────
VOL_FLOOR = 5e6
K = 8                    # names per leg (top-K long, bottom-K short)
BETA_WIN = 48            # 4h bars for beta estimation (= 8 trading days)
CONFIGS = [(lb, h) for lb in (12, 24, 48) for h in (6, 12, 24)]
COSTS = [10.0, 20.0]    # bps/leg round-trip


# ── helpers ────────────────────────────────────────────────────────────────────
def _ts(ms):
    """Integer bar index key — keep bars in strict arrival order."""
    return int(ms)


def _bar_rets(bars, window):
    """Bar-over-bar returns for the last `window` bars. Lookahead-safe: only uses passed bars."""
    closes = [b["c"] for b in bars[-(window + 1):]]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


def _beta(cr, br):
    """OLS beta.  Returns 1.0 if degenerate."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = sum(br) / n
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = sum(cr) / n
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def load():
    """Load universe (same filter as edge_xsectional) + 4h candle arrays per coin."""
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:50]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "4h", 240)
        if len(bars) >= BETA_WIN + 4:   # minimum viable history
            data[c] = bars              # list[dict] in ascending time order
    return data


def run(data, lb, h, cost_frac):
    """Walk every 4h bar (overlapping windows, same as edge_xsectional daily walk).
    Signal: residual = coin_lb_ret − beta × btc_lb_ret.
    Entry: bar[t+1] open.  Exit: bar[t+1+h] close (or last bar if short).
    Returns list of per-rebalance long-short net returns (after 2 legs × cost_frac).
    """
    btc_bars = data.get("BTC")
    if not btc_bars:
        return []

    n_bars = max(len(b) for b in data.values())   # align all coins by index
    out = []
    start = max(lb, BETA_WIN)

    for t in range(start, n_bars - h - 1):
        # --- compute residual scores at bar t ---
        ranked = []
        # BTC bars for beta window (indices t-BETA_WIN .. t)
        btc_win = btc_bars[max(0, t - BETA_WIN): t + 1]
        br = _bar_rets(btc_win, len(btc_win))

        # BTC lb-return for residual (close[t] / close[t-lb] - 1)
        if t < lb or btc_bars[t - lb]["c"] <= 0:
            continue
        rb = btc_bars[t]["c"] / btc_bars[t - lb]["c"] - 1.0

        for coin, bars in data.items():
            if len(bars) < t + 1:        # coin bar count may differ
                continue
            if bars[t - lb]["c"] <= 0:
                continue
            # residual = coin_lb_ret - beta * btc_lb_ret  (lookahead-safe: only bars[0..t])
            coin_win = bars[max(0, t - BETA_WIN): t + 1]
            cr = _bar_rets(coin_win, len(coin_win))
            if len(cr) < 8 or len(br) < 8:
                continue
            beta = _beta(cr, br)
            rc = bars[t]["c"] / bars[t - lb]["c"] - 1.0
            score = rc - beta * rb
            ranked.append((coin, score))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        # --- forward return: enter bar[t+1] open, exit bar[t+1+h] close ---
        t_en = t + 1
        t_ex = min(t + 1 + h, len(btc_bars) - 1)

        def fwd(coin):
            bars = data[coin]
            if t_en >= len(bars) or t_ex >= len(bars):
                return 0.0
            o = bars[t_en]["o"]
            c = bars[t_ex]["c"]
            return (c - o) / o if o > 0 else 0.0

        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        out.append((lr - sr) - 2 * cost_frac)   # long + short both cost 1 leg each

    return out


def rep(name, arr):
    """Report: n, win%, mean, OOS h1/h2, quartile stability."""
    if not arr:
        print(f"  {name:40} n=0 (no data)")
        return
    n = len(arr)
    if n < 12:
        print(f"  {name:40} n={n} (too thin to interpret)")
        return
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100
    h2 = statistics.mean(arr[mid:]) * 100
    mu = statistics.mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    flag = "  <<< +EV*" if mu > 0 and rob == "ROBUST" else ""
    print(f"  {name:40} n={n:>4} win {w/n*100:>3.0f}%  mean {mu:>+6.2f}%  OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{flag}")

    # 4-quartile sub-period stability
    q = n // 4
    if q >= 3:
        qs = [statistics.mean(arr[i * q: (i + 1) * q if i < 3 else n]) * 100 for i in range(4)]
        nneg = sum(1 for x in qs if x <= 0)
        smooth = "SMOOTH(0 neg Q)" if nneg == 0 else f"({nneg}/4 Q<=0)"
        print(f"  {'':40} Q1-4: {qs[0]:>+5.2f}/{qs[1]:>+5.2f}/{qs[2]:>+5.2f}/{qs[3]:>+5.2f}  {smooth}")


def main():
    print("# 4h Cross-sectional RESIDUAL Momentum Backtest")
    print("# Universe: top-50 liquid perps (no HIP-3), K=8/leg, BTC-neutral residual score")
    print("# Costs swept: 10 and 20 bps/leg.  Lookahead-safe, OOS-split.")
    print("#")
    print("# DATA CONSTRAINT: 28 coins × 241 bars × 4h = ~40 calendar days (2026-05-14 to 2026-06-23).")
    print("# OOS halves are ~20 days each.  This is ONE up-regime.  ROBUST here ≠ multi-cycle.")
    print("#")

    data = load()
    print(f"# {len(data)} coins loaded with 4h candles\n")

    if "BTC" not in data:
        print("FATAL: BTC 4h candles not in cache. Run with live fetch or re-warm cache.")
        return

    # Report daily turn count vs 4h for cost context
    n_bars = len(data["BTC"])
    print(f"# Rebalance count comparison (approx, ~{n_bars} bars / 40 days):")
    for lb, h in CONFIGS:
        approx_rebal = max(0, n_bars - max(lb, BETA_WIN) - h - 1) // h
        print(f"#   LB={lb:2}h / H={h:2}h:  ~{approx_rebal} rebalances in {n_bars} bars "
              f"(daily LB=7d/H=10d had ~{260//10} rebalances in 260 bars)")
    print()

    for cost_bps in COSTS:
        cost = cost_bps / 1e4
        print(f"{'='*80}")
        print(f"# COST = {cost_bps:.0f} bps/leg  (round-trip = {2*cost_bps:.0f} bps per rebalance)")
        print(f"{'='*80}")
        for lb, h in CONFIGS:
            arr = run(data, lb, h, cost)
            name = f"LB={lb:2}h / H={h:2}h"
            rep(name, arr)
        print()

    print("# * ROBUST = both OOS halves mean>0 after cost.  On 40d/20d-per-half this is")
    print("#   regime-scoped, NOT a multi-cycle validation.  Methodology bar requires")
    print("#   OOS-robust across independently-distributed subperiods — not met here.")
    print("#   Compare daily edge: LB=7d/H=10d, +2.37%/rebal, validated on ~1 year.")


if __name__ == "__main__":
    main()
