#!/usr/bin/env python3
"""Alpha blend tests — two experiments in one script.

(A) MULTI-LOOKBACK MOMENTUM: cross-sectional residual-momentum L/S that BLENDS lookbacks
    LB ∈ {3, 7, 14, 30}d.  Each coin gets a cross-sectional z-score per LB, the four z-scores are
    AVERAGED into a composite, then top-K long / bottom-K short. Compare composite vs each single
    LB alone (hold=10d, K=8, cost 10bps/name round-trip).

(B) OPTIMAL BLEND WEIGHT: sweep w ∈ {0.0, 0.1, …, 1.0} for w*momentum + (1-w)*pairs.  Report
    the Sharpe at each w and the w* that maximises Sharpe.  Determine whether any w beats
    momentum-alone (w=1).  Annualise as daily_mean/daily_std*sqrt(365).

Run with BT_CACHE_ONLY=1 (pre-warmed cache only — no network).

Methodology: lookahead-safe (signal from data ≤ t, enter t+1 open); cost-aware (≥10bps/leg);
survivorship-free (whole top-50 liquid universe); OOS-robust (split trade stream in halves, both
must be mean-positive to validate).
"""
import os, sys, math, statistics, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── shared constants ──────────────────────────────────────────────────────────
TOPN = 50
VOL_FLOOR = 5e6
K = 8
COST = 10.0 / 1e4          # per leg, per name

# (A) multi-lookback
LOOKBACKS = [3, 7, 14, 30]
HOLD = 10

# (B) blend sweep — reuse edge_stack.py params so results are comparable
MOM_LB, MOM_HOLD = 7, 7
PAIR_LB, Z_ENTRY, Z_EXIT, MIN_CORR, MAXHOLD = 30, 2.0, 0.5, 0.6, 15


# ── helpers ───────────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 90:
            data[c] = {_ymd(b["t"]): b["c"] for b in bars}
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


def _zscore_row(vals):
    """Cross-sectional z-score a dict {coin: raw_score}. Returns {coin: z}."""
    coins = list(vals)
    vs = [vals[c] for c in coins]
    if len(vs) < 3:
        return {}
    mu = statistics.mean(vs)
    sd = statistics.pstdev(vs)
    if sd <= 0:
        return {c: 0.0 for c in coins}
    return {c: (vals[c] - mu) / sd for c in coins}


def sharpe(series):
    if len(series) < 20:
        return None
    mu = statistics.mean(series)
    sd = statistics.pstdev(series)
    return (mu / sd * math.sqrt(365)) if sd > 0 else 0.0


def rep(label, arr):
    if not arr:
        print(f"  {label:32s} n=0  (no data)")
        return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else float("nan")
    h2 = statistics.mean(arr[mid:]) * 100 if n - mid else float("nan")
    mu = statistics.mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    flag = "  <<< +EV" if mu > 0 and rob == "ROBUST" else ""
    sh = sharpe(arr)
    sh_str = f"{sh:>+5.2f}" if sh is not None else "  N/A"
    print(f"  {label:32s} n={n:>4} win={w/n*100:>3.0f}%  "
          f"mean={mu:>+6.2f}%  OOS={h1:>+5.2f}/{h2:>+5.2f}  Sharpe={sh_str}  {rob}{flag}")


# ═══════════════════════════════════════════════════════════════════════════════
# (A) MULTI-LOOKBACK MOMENTUM
# ═══════════════════════════════════════════════════════════════════════════════

def _single_lb_ls(data, all_days, lb, hold=HOLD):
    """Long-short trade returns for one lookback length (enter t+1 open, exit t+1+h open).
    Returns list of per-rebalance LS returns."""
    out = []
    for t in range(lb, len(all_days) - hold - 1):
        d = all_days[t]
        d_lb = all_days[t - lb]
        d_entry = all_days[t + 1]
        d_exit = all_days[t + 1 + hold] if t + 1 + hold < len(all_days) else all_days[-1]
        raw = {}
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_entry in oc and d_exit in oc and oc[d_lb][1] > 0:
                raw[coin] = oc[d][1] / oc[d_lb][1] - 1   # trailing return (raw score)
        if len(raw) < 2 * K + 4:
            continue
        ranked = sorted(raw, key=raw.get, reverse=True)
        longs = ranked[:K]
        shorts = ranked[-K:]

        def fwd(coin):
            o, _ = data[coin][d_entry]
            _, c = data[coin][d_exit]
            return (c - o) / o if o > 0 else 0.0

        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        out.append((lr - sr) - 2 * COST)
    return out


def run_part_a(data):
    """Load candle data as (open, close) pairs; data structure expected by _single_lb_ls."""
    print("=" * 72)
    print("(A) MULTI-LOOKBACK MOMENTUM COMPOSITE  (hold=10d, K=8, cost=10bps/leg)")
    print("=" * 72)

    # Need open + close for the entry/exit model used in edge_xsectional.py
    # Rebuild data with (open, close) tuples keyed by ymd
    oc_data = {}
    for coin, close_map in data.items():
        # We only have close in the shared data dict — load raw bars to get open
        bars = get_candles(coin, "1d", 260)
        if len(bars) >= 90:
            oc_data[coin] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}

    all_days = sorted({d for oc in oc_data.values() for d in oc})

    # --- single-LB results ---
    single_results = {}
    for lb in LOOKBACKS:
        rets = _single_lb_ls(oc_data, all_days, lb)
        single_results[lb] = rets

    # --- composite: z-score each LB cross-sectionally, average ---
    composite_rets = []
    for t in range(max(LOOKBACKS), len(all_days) - HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[t + 1 + HOLD] if t + 1 + HOLD < len(all_days) else all_days[-1]

        # For each LB build a raw score map {coin: trailing_return}
        lb_scores = {}   # lb -> {coin: raw}
        for lb in LOOKBACKS:
            if t - lb < 0:
                continue
            d_lb = all_days[t - lb]
            raw = {}
            for coin, oc in oc_data.items():
                if d in oc and d_lb in oc and d_entry in oc and d_exit in oc and oc[d_lb][1] > 0:
                    raw[coin] = oc[d][1] / oc[d_lb][1] - 1
            lb_scores[lb] = raw

        if len(lb_scores) < 2:   # need at least 2 lookbacks to blend
            continue

        # Cross-sectional z-score per LB then average across LBs
        z_maps = [_zscore_row(raw) for raw in lb_scores.values()]
        # Only coins present in ALL lookback z-maps
        common_coins = set(z_maps[0]) if z_maps else set()
        for zm in z_maps[1:]:
            common_coins &= set(zm)
        if len(common_coins) < 2 * K + 4:
            continue

        composite = {c: statistics.mean(zm[c] for zm in z_maps) for c in common_coins}
        ranked = sorted(composite, key=composite.get, reverse=True)
        longs = ranked[:K]
        shorts = ranked[-K:]

        def fwd(coin):
            o, _ = oc_data[coin][d_entry]
            _, c = oc_data[coin][d_exit]
            return (c - o) / o if o > 0 else 0.0

        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        composite_rets.append((lr - sr) - 2 * COST)

    # --- print results ---
    print()
    for lb in LOOKBACKS:
        rep(f"single LB={lb}d", single_results[lb])
    print()
    rep("composite (avg z-score, all LBs)", composite_rets)

    # Which LB is the best single?
    best_lb = max(LOOKBACKS, key=lambda lb: statistics.mean(single_results[lb]) if single_results[lb] else -1)
    best_single_mu = statistics.mean(single_results[best_lb]) * 100 if single_results[best_lb] else float("nan")
    comp_mu = statistics.mean(composite_rets) * 100 if composite_rets else float("nan")
    delta = comp_mu - best_single_mu

    print(f"\n  Best single LB: LB={best_lb}d  mean={best_single_mu:+.2f}%/rebal")
    print(f"  Composite:              mean={comp_mu:+.2f}%/rebal   delta={delta:+.2f}%")

    comp_oos = False
    if composite_rets:
        mid = len(composite_rets) // 2
        ch1 = statistics.mean(composite_rets[:mid]) * 100
        ch2 = statistics.mean(composite_rets[mid:]) * 100
        comp_oos = ch1 > 0 and ch2 > 0

    verdict = ""
    if comp_mu > best_single_mu and comp_oos:
        verdict = "COMPOSITE WINS — blending lookbacks HELPS and is OOS-robust"
    elif comp_mu > best_single_mu and not comp_oos:
        verdict = "composite mean > best single but NOT OOS-robust — not validated"
    else:
        verdict = "NO BENEFIT — blending lookbacks does NOT consistently beat the best single LB"

    print(f"\n  VERDICT (A): {verdict}")
    return single_results, composite_rets


# ═══════════════════════════════════════════════════════════════════════════════
# (B) OPTIMAL BLEND WEIGHT
# ═══════════════════════════════════════════════════════════════════════════════

def momentum_daily(data):
    """Daily LS-book return (identical logic to edge_stack.py, param MOM_LB/MOM_HOLD)."""
    close_data = {coin: close_map for coin, close_map in data.items()}
    all_days = sorted({d for cl in close_data.values() for d in cl})
    out = {}
    longs, shorts = [], []
    for t in range(MOM_LB, len(all_days)):
        d = all_days[t]
        if (t - MOM_LB) % MOM_HOLD == 0:
            d_lb = all_days[t - MOM_LB]
            ranked = [(c, cl[d] / cl[d_lb] - 1) for c, cl in close_data.items()
                      if d in cl and d_lb in cl and cl[d_lb] > 0]
            if len(ranked) >= 2 * K + 4:
                ranked.sort(key=lambda x: x[1], reverse=True)
                longs = [c for c, _ in ranked[:K]]
                shorts = [c for c, _ in ranked[-K:]]
        dp = all_days[t - 1]

        def dret(names):
            rs = [close_data[c][d] / close_data[c][dp] - 1
                  for c in names if d in close_data[c] and dp in close_data[c]
                  and close_data[c][dp] > 0]
            return statistics.mean(rs) if rs else 0.0

        if longs and shorts:
            out[d] = dret(longs) - dret(shorts)
    return out


def pairs_daily(data):
    """Daily aggregate spread P&L of active pair positions (identical logic to edge_stack.py)."""
    close_data = data
    coins = list(close_data)
    all_days = sorted({d for cl in close_data.values() for d in cl})
    state = {}
    daily = {d: [] for d in all_days}
    pairs = [(a, b) for a, b in itertools.combinations(coins, 2)
             if len(set(close_data[a]) & set(close_data[b])) >= PAIR_LB + 30]
    for a, b in pairs:
        common = sorted(set(close_data[a]) & set(close_data[b]))
        la = {d: math.log(close_data[a][d]) for d in common}
        lb_map = {d: math.log(close_data[b][d]) for d in common}
        spread = {d: la[d] - lb_map[d] for d in common}
        key = (a, b)
        for i in range(PAIR_LB, len(common)):
            d, dp = common[i], common[i - 1]
            win = [spread[common[j]] for j in range(i - PAIR_LB, i)]
            mu, sd = statistics.mean(win), statistics.pstdev(win)
            if sd <= 0:
                continue
            if key in state:
                side, _mu, _sd = state[key]
                daily[d].append(side * (spread[dp] - spread[d]))
                if abs((spread[d] - _mu) / _sd) <= Z_EXIT:
                    del state[key]
            else:
                z = (spread[d] - mu) / sd
                ra = [la[common[j]] - la[common[j - 1]] for j in range(i - PAIR_LB + 1, i)]
                rb = [lb_map[common[j]] - lb_map[common[j - 1]] for j in range(i - PAIR_LB + 1, i)]
                if abs(z) >= Z_ENTRY and _corr(ra, rb) >= MIN_CORR:
                    state[key] = (-1 if z > 0 else 1, mu, sd)
    return {d: statistics.mean(v) for d, v in daily.items() if v}


def run_part_b(data):
    print()
    print("=" * 72)
    print("(B) OPTIMAL BLEND WEIGHT  w*momentum + (1-w)*pairs")
    print(f"    mom LB={MOM_LB}/hold={MOM_HOLD}, pairs z>{Z_ENTRY}/exit<{Z_EXIT}/corr>{MIN_CORR}")
    print("=" * 72)

    m = momentum_daily(data)
    p = pairs_daily(data)
    common = sorted(set(m) & set(p))
    if len(common) < 30:
        print(f"  INSUFFICIENT aligned days ({len(common)}); cannot run blend sweep.")
        return

    mser = [m[d] for d in common]
    pser = [p[d] for d in common]
    corr = _corr(mser, pser)

    print(f"\n  Aligned days: {len(common)}")
    print(f"  Correlation(momentum, pairs): {corr:+.3f}")
    print()

    # Per-stream stats
    def stream_stats(label, ser):
        mu = statistics.mean(ser)
        sd = statistics.pstdev(ser)
        sh = mu / sd * math.sqrt(365) if sd > 0 else 0.0
        mid = len(ser) // 2
        h1 = statistics.mean(ser[:mid]) * 100
        h2 = statistics.mean(ser[mid:]) * 100
        rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
        print(f"  {label:16s}  dailyμ={mu*100:>+6.3f}%  σ={sd*100:>5.2f}%  Sharpe={sh:>+5.2f}  OOS {h1:>+5.2f}/{h2:>+5.2f}  {rob}")
        return sh

    sm = stream_stats("momentum", mser)
    sp = stream_stats("pairs", pser)

    # Blend sweep
    weights = [round(w * 0.1, 1) for w in range(11)]   # 0.0 … 1.0
    results = []
    print()
    print(f"  {'w(mom)':>8}  {'w(pairs)':>9}  {'dailyμ%':>8}  {'σ%':>6}  {'Sharpe':>7}")
    print("  " + "-" * 48)
    for w in weights:
        blended = [w * a + (1 - w) * b for a, b in zip(mser, pser)]
        mu_b = statistics.mean(blended)
        sd_b = statistics.pstdev(blended)
        sh_b = mu_b / sd_b * math.sqrt(365) if sd_b > 0 else 0.0
        results.append((w, sh_b, mu_b, sd_b))
        marker = "  <-- w=1 momentum alone" if w == 1.0 else (
                 "  <-- w=0 pairs alone" if w == 0.0 else "")
        print(f"  {w:>8.1f}  {1-w:>9.1f}  {mu_b*100:>+8.3f}  {sd_b*100:>6.3f}  {sh_b:>+7.3f}{marker}")

    best_w, best_sh, best_mu, best_sd = max(results, key=lambda r: r[1])
    mom_sh = next(sh for w, sh, _, _ in results if w == 1.0)

    print(f"\n  Optimal w* = {best_w:.1f}  (Sharpe={best_sh:+.3f}  dailyμ={best_mu*100:+.3f}%)")
    print(f"  Momentum-alone (w=1) Sharpe = {mom_sh:+.3f}")
    delta_sh = best_sh - mom_sh

    # OOS check on optimal blend
    blended_best = [best_w * a + (1 - best_w) * b for a, b in zip(mser, pser)]
    mid = len(blended_best) // 2
    bh1 = statistics.mean(blended_best[:mid]) * 100
    bh2 = statistics.mean(blended_best[mid:]) * 100
    oos_blend = bh1 > 0 and bh2 > 0

    if best_w == 1.0:
        verdict = "MOMENTUM-ALONE IS ALREADY OPTIMAL — no blend improves Sharpe"
    elif delta_sh > 0.05 and oos_blend:
        verdict = (f"OPTIMAL BLEND WINS: w*={best_w:.1f} beats momentum-alone by +{delta_sh:.3f} Sharpe "
                   f"and is OOS-robust ({bh1:+.2f}/{bh2:+.2f})")
    elif delta_sh > 0 and not oos_blend:
        verdict = (f"w*={best_w:.1f} gains +{delta_sh:.3f} Sharpe over momentum-alone "
                   f"BUT is NOT OOS-robust ({bh1:+.2f}/{bh2:+.2f}) — not validated")
    elif delta_sh > 0 and oos_blend:
        verdict = (f"w*={best_w:.1f} gains +{delta_sh:.3f} Sharpe but improvement is MARGINAL "
                   f"(< 0.05 threshold); OOS-robust ({bh1:+.2f}/{bh2:+.2f})")
    else:
        verdict = f"NO BENEFIT — w*={best_w:.1f} does not beat momentum-alone"

    print(f"\n  VERDICT (B): {verdict}")


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    print("# edge_blend.py — Multi-lookback blend (A) + Optimal-weight blend (B)")
    print(f"# universe: top{TOPN} liquid perps, vol≥{VOL_FLOOR/1e6:.0f}M, cost={COST*1e4:.0f}bps/leg")
    print()
    data = load()
    print(f"# {len(data)} coins loaded: {', '.join(sorted(data))}\n")

    run_part_a(data)
    run_part_b(data)

    print()
    print("# Done.")


if __name__ == "__main__":
    main()
