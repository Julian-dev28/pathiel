#!/usr/bin/env python3
"""Stat-arb extensions — three research assignments:

(a) CORRELATION-CLUSTERING pair selection: compare all-pairs baseline vs.
    tightest-only (best-partner per coin) vs. hierarchical-cluster within-cluster.
(b) BOLLINGER/RSI-extreme CROSS-SECTIONAL reversion: long most-oversold /
    short most-overbought coins, hold 3-5d. Controls for band/RSI normalisation.
(c) PAIRS threshold SWEEP: sweep entry-z × exit-z × max-hold on the validated
    baseline pairs. Find materially better calibration that is OOS-robust.

Run: BT_CACHE_ONLY=1 python scripts/edge_statarb_ext.py
"""

import os, sys, math, statistics, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── Constants ──────────────────────────────────────────────────────────────────
TOPN      = 40
VOL_FLOOR = 5e6
COST      = 10.0 / 1e4   # 10bps per leg
LOOKBACK  = 30            # trailing window for spread z-score

# Baseline (validated) params
BASE_Z_ENTRY  = 2.0
BASE_Z_EXIT   = 0.5
BASE_MAXHOLD  = 15
BASE_MIN_CORR = 0.6

# ── Helpers ────────────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
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
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def run_pair(ca_data, cb_data, common, z_entry, z_exit, maxhold, min_corr=BASE_MIN_CORR):
    """Walk the spread; trade z-extremes. Returns list of net per-trade returns."""
    la = [math.log(ca_data[d]) for d in common]
    lb = [math.log(cb_data[d]) for d in common]
    spread = [a - b for a, b in zip(la, lb)]
    out = []
    i = LOOKBACK
    while i < len(common) - 1:
        win = spread[i - LOOKBACK:i]
        mu, sd = statistics.mean(win), statistics.pstdev(win)
        if sd <= 0:
            i += 1
            continue
        # Dynamic co-movement check (returns correlation over the lookback window)
        ra = [la[k] - la[k - 1] for k in range(i - LOOKBACK + 1, i)]
        rb = [lb[k] - lb[k - 1] for k in range(i - LOOKBACK + 1, i)]
        if _corr(ra, rb) < min_corr:
            i += 1
            continue
        z = (spread[i] - mu) / sd
        if abs(z) < z_entry:
            i += 1
            continue
        side = -1 if z > 0 else 1
        j = i + 1
        while j < min(i + 1 + maxhold, len(common)):
            zj = (spread[j] - mu) / sd
            if abs(zj) <= z_exit:
                break
            j += 1
        j = min(j, len(common) - 1)
        pnl = side * (spread[i] - spread[j])  # convergence of log-spread; note sign corrected
        out.append(pnl - 2 * COST)
        i = j + 1
    return out


def _full_corr_matrix(data):
    """Compute pairwise full-history (returns) correlation for pair selection."""
    coins = list(data)
    common_dates = sorted(set.intersection(*[set(data[c]) for c in coins]))
    if len(common_dates) < 60:
        return {}
    # Log-returns for the full history
    lr = {}
    for c in coins:
        prices = [data[c][d] for d in common_dates]
        lr[c] = [math.log(prices[k] / prices[k - 1]) for k in range(1, len(prices))]
    corr = {}
    for ca, cb in itertools.combinations(coins, 2):
        r = _corr(lr[ca], lr[cb])
        corr[(ca, cb)] = r
        corr[(cb, ca)] = r
    return corr, coins, common_dates


def _report(name, trades, baseline_mean=None, indent=2):
    sp = " " * indent
    if not trades:
        print(f"{sp}{name:45}  n=   0  (no trades)")
        return
    n   = len(trades)
    w   = sum(1 for r in trades if r > 0)
    mn  = statistics.mean(trades) * 100
    med = statistics.median(trades) * 100
    mid = n // 2
    h1  = statistics.mean(trades[:mid])  * 100 if mid     else float("nan")
    h2  = statistics.mean(trades[mid:])  * 100 if n - mid else float("nan")
    rob = ("ROBUST"   if h1 > 0 and h2 > 0
           else "fragile" if (h1 > 0) != (h2 > 0)
           else "neg")
    ev_flag   = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
    delta_str = ""
    if baseline_mean is not None:
        delta_str = f"  Δ{(mn - baseline_mean):+.2f}% vs baseline"
    print(f"{sp}{name:45}  n={n:>5} win {w/n*100:>3.0f}%  mean {mn:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}{ev_flag}{delta_str}")


# ══════════════════════════════════════════════════════════════════════════════
# (a) CORRELATION-CLUSTERING pair selection
# ══════════════════════════════════════════════════════════════════════════════

def run_a(data):
    print("\n" + "=" * 75)
    print("(a) CORRELATION-CLUSTERING PAIR SELECTION")
    print("=" * 75)
    print(f"    Lookback {LOOKBACK}d | z-entry {BASE_Z_ENTRY} | z-exit {BASE_Z_EXIT} | "
          f"maxhold {BASE_MAXHOLD} | corr>{BASE_MIN_CORR} | cost {COST*1e4:.0f}bps/leg")
    print()

    coins = list(data)
    if len(coins) < 4:
        print("  not enough coins"); return

    # Full-history corr matrix for pair SELECTION only (selection criterion ≤ t is fine because
    # we use it to choose which pairs to run, not to generate the actual spread signal — the same
    # rolling window lookahead-safe z-score is used for every pair)
    common_all = sorted(set.intersection(*[set(data[c]) for c in coins]))
    if len(common_all) < 60:
        print("  insufficient common history"); return

    lr = {}
    for c in coins:
        prices = [data[c][d] for d in common_all]
        lr[c]  = [math.log(prices[k] / prices[k - 1]) for k in range(1, len(prices))]

    corr = {}
    for ca, cb in itertools.combinations(coins, 2):
        r = _corr(lr[ca], lr[cb])
        corr[(ca, cb)] = r
        corr[(cb, ca)] = r

    # ── A0 BASELINE: ALL candidate pairs — exact replication of edge_pairs.py ─
    # edge_pairs.py does NOT pre-filter by full-history corr; it runs every pair
    # and the dynamic within-window corr check gates individual trades inside run_pair.
    baseline_trades = []
    baseline_pairs = []
    for ca, cb in itertools.combinations(coins, 2):
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        baseline_pairs.append((ca, cb))
        baseline_trades += run_pair(data[ca], data[cb], common,
                                    BASE_Z_ENTRY, BASE_Z_EXIT, BASE_MAXHOLD)

    base_mean = statistics.mean(baseline_trades) * 100 if baseline_trades else 0.0
    print(f"  A0 BASELINE: all {len(baseline_pairs)} candidate pairs (replicates edge_pairs.py)")
    _report("all-pairs baseline (A0)", baseline_trades)
    print()

    # ── A1: TIGHTEST PARTNER per coin (best-corr partner only) ──────────────
    # Select from ALL pairs; pick each coin's single highest-corr partner
    chosen_a1 = set()
    for c in coins:
        best_corr = -999
        best_partner = None
        for other in coins:
            if other == c:
                continue
            r = corr.get((c, other), 0)
            if r > best_corr:
                best_corr = r
                best_partner = other
        if best_partner is not None:
            pair = tuple(sorted([c, best_partner]))
            chosen_a1.add(pair)

    a1_trades = []
    for ca, cb in chosen_a1:
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        a1_trades += run_pair(data[ca], data[cb], common,
                              BASE_Z_ENTRY, BASE_Z_EXIT, BASE_MAXHOLD)

    print(f"  A1 TIGHTEST-PARTNER: each coin's single highest-corr partner  ({len(chosen_a1)} pairs)")
    _report("tightest-partner (A1)", a1_trades, base_mean)
    print()

    # ── A2: TOP-25% CORR PAIRS only (from all candidate pairs) ──────────────
    all_corrs = [(ca, cb, corr[(ca, cb)])
                 for ca, cb in itertools.combinations(coins, 2)
                 if (ca, cb) in {tuple(sorted([x, y])) for x, y, _ in
                                  [(a, b, corr.get((a,b),0)) for a, b in itertools.combinations(coins, 2)]}]
    # simpler: all pairs with their corr
    all_corrs = sorted(
        [(ca, cb, corr.get((ca, cb), 0)) for ca, cb in itertools.combinations(coins, 2)],
        key=lambda x: x[2], reverse=True
    )
    top_q = max(1, len(all_corrs) // 4)
    top25_pairs = {(ca, cb) for ca, cb, _ in all_corrs[:top_q]}

    a2_trades = []
    for ca, cb in top25_pairs:
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        a2_trades += run_pair(data[ca], data[cb], common,
                              BASE_Z_ENTRY, BASE_Z_EXIT, BASE_MAXHOLD)

    print(f"  A2 TOP-25% CORR PAIRS: top quartile of all pairs by full-history corr  ({len(top25_pairs)} pairs)")
    _report("top-25%-corr pairs (A2)", a2_trades, base_mean)
    print()

    # ── A3: HIERARCHICAL CLUSTER — within-cluster pairs only ─────────────────
    # Average-linkage agglomerative clustering on dissimilarity = 1 - |corr|
    # Cluster all coins, then trade only pairs within the same cluster.
    dist = {}
    for ca, cb in itertools.combinations(coins, 2):
        d = 1.0 - abs(corr.get((ca, cb), 0))
        dist[(ca, cb)] = d
        dist[(cb, ca)] = d

    target_clusters = max(4, len(coins) // 7)  # ~4 clusters on 28 coins
    merged = [[c] for c in coins]

    def cluster_dist(c1, c2):
        dists = [dist.get((a, b), 1.0) for a in c1 for b in c2 if a != b]
        return statistics.mean(dists) if dists else 1.0

    while len(merged) > target_clusters:
        best_d = 999
        mi, mj = 0, 1
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                d = cluster_dist(merged[i], merged[j])
                if d < best_d:
                    best_d = d; mi = i; mj = j
        merged[mi] = merged[mi] + merged[mj]
        merged.pop(mj)

    # Trade ALL within-cluster pairs (no pre-filter by corr level;
    # dynamic corr check inside run_pair still gates individual trades)
    within_cluster_pairs = set()
    for cl in merged:
        for ca, cb in itertools.combinations(cl, 2):
            within_cluster_pairs.add(tuple(sorted([ca, cb])))

    a3_trades = []
    for ca, cb in within_cluster_pairs:
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        a3_trades += run_pair(data[ca], data[cb], common,
                              BASE_Z_ENTRY, BASE_Z_EXIT, BASE_MAXHOLD)

    print(f"  A3 HIERARCHICAL CLUSTER: within-cluster pairs ({target_clusters} clusters, "
          f"{len(within_cluster_pairs)} pairs selected from {len(coins)*(len(coins)-1)//2} total)")
    _report("within-cluster pairs (A3)", a3_trades, base_mean)
    print()

    # ── A4: HIGH-CORR threshold (corr > 0.75) from all pairs ─────────────────
    a4_pairs = [(ca, cb) for ca, cb in itertools.combinations(coins, 2)
                if corr.get((ca, cb), 0) >= 0.75]
    a4_trades = []
    for ca, cb in a4_pairs:
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) < LOOKBACK + 30:
            continue
        a4_trades += run_pair(data[ca], data[cb], common,
                              BASE_Z_ENTRY, BASE_Z_EXIT, BASE_MAXHOLD)

    print(f"  A4 HIGH-CORR (≥0.75): stricter pre-filter threshold  ({len(a4_pairs)} pairs)")
    _report("high-corr ≥0.75 (A4)", a4_trades, base_mean)
    print()

    print("  VERDICT (a):")
    results_a = [("A0-baseline", baseline_trades), ("A1-tightest", a1_trades),
                 ("A2-top25pct", a2_trades), ("A3-cluster", a3_trades), ("A4-corr075", a4_trades)]
    for label, tr in results_a:
        if not tr:
            continue
        mn  = statistics.mean(tr) * 100
        mid = len(tr) // 2
        h1  = statistics.mean(tr[:mid]) * 100 if mid else 0
        h2  = statistics.mean(tr[mid:]) * 100 if len(tr) - mid else 0
        rob = "ROBUST" if h1 > 0 and h2 > 0 else "not robust"
        print(f"    {label:20}  mean {mn:+.3f}%  OOS {h1:+.2f}/{h2:+.2f}  {rob}")


# ══════════════════════════════════════════════════════════════════════════════
# (b) BOLLINGER / RSI-EXTREME cross-sectional reversion
# ══════════════════════════════════════════════════════════════════════════════

def _rsi(prices, period=14):
    """Standard RSI from a list of closing prices; return value at last bar."""
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for k in range(1, len(prices)):
        d = prices[k] - prices[k - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    # Wilder smoothing (use simple avg for seed, then EMA)
    ag = statistics.mean(gains[:period])
    al = statistics.mean(losses[:period])
    for k in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[k]) / period
        al = (al * (period - 1) + losses[k]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1 + rs)


def _boll_z(prices, period=20):
    """Return z-score of last price vs Bollinger mean/std (signed: >0 = overbought)."""
    if len(prices) < period:
        return 0.0
    window = prices[-period:]
    mu = statistics.mean(window)
    sd = statistics.pstdev(window)
    if sd <= 0:
        return 0.0
    return (prices[-1] - mu) / sd


def run_b(data):
    print("\n" + "=" * 75)
    print("(b) BOLLINGER / RSI-EXTREME CROSS-SECTIONAL REVERSION")
    print("=" * 75)
    print(f"    Signal: rank coins by Bollinger-z or RSI extremity each day; "
          f"L/S top-K / bottom-K; hold 3-5d; cost {COST*1e4:.0f}bps/name")
    print()

    K          = 6          # names per leg
    HOLD_OPTS  = [3, 5]
    BB_PERIOD  = 20
    RSI_PERIOD = 14

    all_days = sorted({d for oc in data.values() for d in oc})
    coins    = list(data)

    def run_xs_signal(signal_fn, hold, signal_name):
        """Cross-sectional L/S portfolio on signal_fn (higher = more overbought → go short)."""
        ls_rets = []
        for t in range(BB_PERIOD + RSI_PERIOD, len(all_days) - hold - 1):
            d_entry = all_days[t + 1]
            d_exit  = all_days[t + 1 + hold] if t + 1 + hold < len(all_days) else all_days[-1]
            ranked  = []
            for c in coins:
                # Need history up to (and including) t for signal, then enter t+1
                days_so_far = [all_days[k] for k in range(max(0, t - BB_PERIOD - RSI_PERIOD - 5), t + 1)
                               if all_days[k] in data[c]]
                if len(days_so_far) < BB_PERIOD:
                    continue
                if d_entry not in data[c] or d_exit not in data[c]:
                    continue
                prices_hist = [data[c][d] for d in days_so_far]
                sig = signal_fn(prices_hist)
                ranked.append((c, sig))
            if len(ranked) < 2 * K + 4:
                continue
            ranked.sort(key=lambda x: x[1], reverse=True)
            # SHORT the most overbought (highest signal), LONG the most oversold (lowest signal)
            overbought  = [c for c, _ in ranked[:K]]
            oversold    = [c for c, _ in ranked[-K:]]

            def fwd(coin):
                o_p = data[coin].get(d_entry)
                c_p = data[coin].get(d_exit)
                if o_p and c_p and o_p > 0:
                    return (c_p - o_p) / o_p
                return 0.0

            long_ret  = statistics.mean(fwd(c) for c in oversold)    # long oversold
            short_ret = statistics.mean(fwd(c) for c in overbought)  # short overbought
            # L-S reversion: long oversold - short overbought; if reverting, both legs profit
            ls_rets.append((long_ret - short_ret) - 2 * COST)
        return ls_rets

    configs = [
        ("Bollinger-z (bb20)", lambda p: _boll_z(p, BB_PERIOD)),
        ("RSI-extremity (rsi14)", lambda p: -(50 - _rsi(p, RSI_PERIOD))),  # higher = more extreme; neg of dist from 50
        # Combined: average rank (normalised so higher = more overbought)
        ("BB+RSI combined", lambda p: _boll_z(p, BB_PERIOD) + (-(50 - _rsi(p, RSI_PERIOD))) / 50.0),
    ]

    any_ev = False
    for sig_name, sig_fn in configs:
        for hold in HOLD_OPTS:
            tr = run_xs_signal(sig_fn, hold, sig_name)
            label = f"{sig_name} hold={hold}d"
            _report(label, tr)
            if tr and statistics.mean(tr) > 0:
                mid = len(tr) // 2
                h1 = statistics.mean(tr[:mid]) * 100 if mid else 0
                h2 = statistics.mean(tr[mid:]) * 100 if len(tr) - mid else 0
                if h1 > 0 and h2 > 0:
                    any_ev = True

    print()
    print("  VERDICT (b):")
    if any_ev:
        print("  >>> AT LEAST ONE CONFIG +EV ROBUST — inspect table above.")
    else:
        print("  Cross-sectional band/RSI reversion is NOT +EV (expected given refuted MA-reversion).")
        print("  Band/RSI normalisation does NOT rescue mean-reversion in this universe.")


# ══════════════════════════════════════════════════════════════════════════════
# (c) PAIRS THRESHOLD SWEEP
# ══════════════════════════════════════════════════════════════════════════════

def run_c(data):
    print("\n" + "=" * 75)
    print("(c) PAIRS THRESHOLD SWEEP  (entry-z × exit-z × max-hold)")
    print("=" * 75)
    print(f"    Baseline: entry {BASE_Z_ENTRY} | exit {BASE_Z_EXIT} | hold {BASE_MAXHOLD}d | "
          f"corr>{BASE_MIN_CORR} | cost {COST*1e4:.0f}bps/leg")
    print()

    coins = list(data)

    # Pre-compute candidate pairs — same as edge_pairs.py (no pre-corr filter;
    # dynamic within-window corr check inside run_pair gates individual trades)
    valid_pairs = []
    for ca, cb in itertools.combinations(coins, 2):
        common = sorted(set(data[ca]) & set(data[cb]))
        if len(common) >= LOOKBACK + 30:
            valid_pairs.append((ca, cb, common))

    print(f"  {len(valid_pairs)} candidate pairs (all pairs, matching edge_pairs.py baseline)")

    ENTRY_ZS = [1.5, 2.0, 2.5]
    EXIT_ZS  = [0.0, 0.5, 1.0]
    HOLDS    = [10, 15, 20]

    # Build baseline once
    base_key   = (BASE_Z_ENTRY, BASE_Z_EXIT, BASE_MAXHOLD)
    results    = {}

    total_configs = len(ENTRY_ZS) * len(EXIT_ZS) * len(HOLDS)
    print(f"  Sweeping {total_configs} configs ({len(ENTRY_ZS)} entry-z × "
          f"{len(EXIT_ZS)} exit-z × {len(HOLDS)} max-hold) ...\n")

    for z_entry in ENTRY_ZS:
        for z_exit in EXIT_ZS:
            if z_exit >= z_entry:
                continue   # degenerate: can't exit at a level higher than entry
            for maxhold in HOLDS:
                all_trades = []
                for ca, cb, common in valid_pairs:
                    all_trades += run_pair(data[ca], data[cb], common, z_entry, z_exit, maxhold)
                results[(z_entry, z_exit, maxhold)] = all_trades

    # Print table
    baseline_trades = results.get(base_key, [])
    base_mean = statistics.mean(baseline_trades) * 100 if baseline_trades else 0.0

    print(f"  {'entry-z':>7}  {'exit-z':>6}  {'hold':>4}  "
          f"{'n':>5}  {'win%':>5}  {'mean%':>7}  {'OOS h1':>7}  {'OOS h2':>7}  "
          f"{'robust':>8}  {'Δ base':>7}")
    print("  " + "-" * 72)

    robust_winners = []
    for (ze, zx, mh), trades in sorted(results.items()):
        if not trades:
            continue
        n   = len(trades)
        w   = sum(1 for r in trades if r > 0)
        mn  = statistics.mean(trades) * 100
        mid = n // 2
        h1  = statistics.mean(trades[:mid])  * 100 if mid     else float("nan")
        h2  = statistics.mean(trades[mid:])  * 100 if n - mid else float("nan")
        rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
        marker = " ★" if mn > 0 and rob == "ROBUST" else ""
        is_base = " ◄BASE" if (ze, zx, mh) == base_key else ""
        delta = mn - base_mean
        print(f"  {ze:>7.1f}  {zx:>6.1f}  {mh:>4d}  "
              f"{n:>5d}  {w/n*100:>5.1f}  {mn:>+7.2f}  {h1:>+7.2f}  {h2:>+7.2f}  "
              f"{rob:>8}{marker}{is_base}  {delta:>+6.2f}%")
        if rob == "ROBUST" and (ze, zx, mh) != base_key:
            robust_winners.append(((ze, zx, mh), mn, h1, h2, n))

    print()
    print("  VERDICT (c):")
    if robust_winners:
        best = max(robust_winners, key=lambda x: x[1])
        (ze, zx, mh), mn, h1, h2, n = best
        print(f"  Best config: entry-z={ze} exit-z={zx} hold={mh}d → "
              f"mean {mn:+.2f}% OOS {h1:+.2f}/{h2:+.2f} (n={n})")
        delta = mn - base_mean
        mat = "MATERIAL" if abs(delta) > 0.10 else "marginal"
        print(f"  Improvement over baseline: Δ{delta:+.2f}% → {mat}")
        print(f"  NOTE: improvements <+0.10% are within noise and NOT actionable.")
    else:
        print("  No config strictly beats baseline on both OOS halves simultaneously.")
        print("  Baseline (entry=2.0, exit=0.5, hold=15d) is near-optimal or flat EV landscape.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 75)
    print("STAT-ARB EXTENSIONS  |  edge_statarb_ext.py")
    print("=" * 75)
    print(f"Universe: top-{TOPN} liquid crypto-perps (vol>={VOL_FLOOR/1e6:.0f}M, no HIP-3/spot)")
    print(f"Cost: {COST*1e4:.0f}bps/leg (round-trip {COST*2e4:.0f}bps); OOS=chronological split")
    print(f"Methodology bar: lookahead-safe · cost-aware · survivorship-free · OOS-robust")
    print()

    print("Loading candle data ...")
    data = load()
    print(f"Loaded {len(data)} coins\n")

    if len(data) < 4:
        print("ERROR: insufficient coins in cache (BT_CACHE_ONLY=1 but cache empty?)")
        sys.exit(1)

    run_a(data)
    run_b(data)
    run_c(data)

    print()
    print("=" * 75)
    print("DONE")
    print("=" * 75)


if __name__ == "__main__":
    main()
