#!/usr/bin/env python3
"""Alpha hunt W6 — vol-managed momentum (Moreira-Muir) + Amihud illiquidity + dispersion timing.

Methodology bar (ALL required):
  - Lookahead-safe: signal from data <= t, enter t+1 open
  - Cost-aware: >= 10bps/leg
  - Survivorship-free: whole liquid universe
  - OOS-robust: both halves of trade stream mean-positive

Run with: BT_CACHE_ONLY=1 python3 scripts/edge_volmgd_amihud.py
"""

import os, sys, math, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ── Constants ────────────────────────────────────────────────────────────────
TOPN = 50
VOL_FLOOR = 5e6
K = 8            # legs per side
COST = 10.0 / 1e4  # 10 bps per name round-trip

# Validated config from ALPHA-PLAN.md (LB=7, hold=10 best OOS)
MOM_LB = 7
MOM_HOLD = 10

# Vol-management params (Moreira-Muir)
VOLMGD_WINDOW = 20    # trailing days of strategy returns for realized-vol estimate
TARGET_VOL = 0.02     # annualized target daily vol (≈ 2% daily = ~38% ann; generous for crypto)
TARGET_VOL_DAILY = TARGET_VOL  # already in daily terms (strategy return stdev target)

# Amihud params
AMIHUD_WINDOW = 30    # days for per-coin Amihud ratio
AMIHUD_HOLD = 10      # hold period for Amihud L/S positions

# Dispersion params: use same MOM_LB/MOM_HOLD for the momentum signal, gate on trailing dispersion
DISP_WINDOW = 10      # trailing days of cross-section stdev for dispersion estimate

ANNUALIZE = math.sqrt(365)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    """Load universe + candles. Cache-only; returns dict coin -> list[bar_dict]."""
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            data[c] = bars
    return data


def sharpe(rets):
    """Annualized Sharpe (mean/stdev * sqrt(365)). None if too short."""
    if len(rets) < 10:
        return None
    m = statistics.mean(rets)
    s = statistics.pstdev(rets)
    if s <= 0:
        return None
    return m / s * ANNUALIZE


def max_drawdown(rets):
    """Max peak-to-trough drawdown of cumulative return stream."""
    if not rets:
        return 0.0
    cum = 1.0
    peak = 1.0
    worst = 0.0
    for r in rets:
        cum *= (1 + r)
        if cum > peak:
            peak = cum
        dd = (peak - cum) / peak
        if dd > worst:
            worst = dd
    return worst


def rep(name, arr, width=32):
    """Print OOS-robust report for a return stream."""
    if not arr or len(arr) < 15:
        print(f"  {name:{width}} n={len(arr) if arr else 0} (thin)")
        return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else 0
    h2 = statistics.mean(arr[mid:]) * 100 if n - mid else 0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    mu = statistics.mean(arr) * 100
    sh = sharpe(arr)
    mdd = max_drawdown(arr) * 100
    flag = "  <<< +EV" if mu > 0 and rob == "ROBUST" else ""
    sh_str = f"{sh:>+5.2f}" if sh is not None else "  N/A"
    print(f"  {name:{width}} n={n:>4} win {w/n*100:>3.0f}%  "
          f"mean {mu:>+6.2f}%  Sharpe {sh_str}  maxDD {mdd:>5.1f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")


# ── Part A: Build the base xs-momentum L/S daily return stream ───────────────
def build_base_ls_stream(data):
    """Build the validated xs-momentum long-short DAILY return stream.

    Each rebalance period (MOM_HOLD days) produces one return. Returns list of
    (rebal_returns, date_of_entry) sorted chronologically.
    Uses MOM_LB=7, MOM_HOLD=10, K=8, cost=10bps — the validated config.
    """
    # Build date-indexed close dict per coin
    closes = {}
    all_days_set = set()
    for coin, bars in data.items():
        cl = {_ymd(b["t"]): b["c"] for b in bars}
        opens = {_ymd(b["t"]): b["o"] for b in bars}
        closes[coin] = {"c": cl, "o": opens}
        all_days_set.update(cl.keys())

    all_days = sorted(all_days_set)
    results = []  # list of (entry_ymd, exit_ymd, ls_ret)

    for t in range(MOM_LB, len(all_days) - MOM_HOLD - 1):
        d = all_days[t]           # signal date (rank on close[d])
        d_lb = all_days[t - MOM_LB]
        d_entry = all_days[t + 1]
        d_exit_idx = t + 1 + MOM_HOLD
        if d_exit_idx >= len(all_days):
            continue
        d_exit = all_days[d_exit_idx]

        ranked = []
        for coin, oc in closes.items():
            cl = oc["c"]
            if d in cl and d_lb in cl and d_entry in oc["o"] and d_exit in cl:
                c_now, c_past = cl[d], cl[d_lb]
                if c_past > 0:
                    ranked.append((coin, c_now / c_past - 1))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        longs = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            o = closes[coin]["o"].get(d_entry, 0)
            c = closes[coin]["c"].get(d_exit, 0)
            return (c - o) / o if o > 0 else 0.0

        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        ls_ret = (lr - sr) - 2 * COST
        results.append((d_entry, d_exit, ls_ret))

    return results


# ── Part A: Vol-managed momentum (Moreira-Muir) ──────────────────────────────
def vol_managed_momentum(stream):
    """Scale each rebalance's exposure by inverse realized vol of the strategy returns.

    w_t = target_vol / realized_vol_t

    realized_vol_t = pstdev of last VOLMGD_WINDOW strategy returns (before t).
    Cap weight at 2.0 (no more than 2x leverage on the strategy).
    Compare scaled vs raw stream: Sharpe, maxDD, OOS both halves.
    """
    raw_rets = [r for _, _, r in stream]
    if len(raw_rets) < VOLMGD_WINDOW + 10:
        print(f"  (too thin for vol-managed: n={len(raw_rets)})")
        return raw_rets, []

    scaled_rets = []
    for i in range(VOLMGD_WINDOW, len(raw_rets)):
        window = raw_rets[i - VOLMGD_WINDOW:i]
        rv = statistics.pstdev(window)
        if rv <= 0:
            w = 1.0
        else:
            # target_vol is in per-period units (same as raw_rets)
            # raw_rets are rebal-period returns, not daily — target accordingly
            # Use a per-rebalance vol target: TARGET_VOL_DAILY * sqrt(MOM_HOLD) for hold-period
            per_period_target = TARGET_VOL_DAILY * math.sqrt(MOM_HOLD)
            w = min(per_period_target / rv, 2.0)  # cap at 2x
        scaled_rets.append(raw_rets[i] * w)

    return raw_rets, scaled_rets


# ── Part B: Amihud illiquidity factor ────────────────────────────────────────
def amihud_factor(data):
    """Cross-sectional long-short on Amihud ratio.

    Amihud_i = mean(|daily_ret_i| / daily_dollar_vol_i) over trailing AMIHUD_WINDOW days
    where daily_dollar_vol = v * close (v=volume in base units from candle).

    Test BOTH:
    - Long high-Amihud / short low-Amihud (illiquidity PREMIUM — less liquid should earn more)
    - Long low-Amihud / short high-Amihud (liquidity PREMIUM — standard)

    Enter t+1 open, exit t+1+AMIHUD_HOLD close. Cost=10bps/name.
    """
    # Build per-coin arrays indexed by ymd
    coin_data = {}
    all_days_set = set()
    for coin, bars in data.items():
        # need at least AMIHUD_WINDOW + AMIHUD_HOLD + a few days
        if len(bars) < AMIHUD_WINDOW + AMIHUD_HOLD + 10:
            continue
        d = {}
        for b in bars:
            ymd = _ymd(b["t"])
            d[ymd] = {"o": b["o"], "c": b["c"], "v": b.get("v", 0) or 0}
        coin_data[coin] = d
        all_days_set.update(d.keys())

    all_days = sorted(all_days_set)
    n_days = len(all_days)

    # Pre-compute per-coin daily |ret| / dollar_vol for each day
    # (use close[t]/close[t-1]-1 for daily ret, dollar_vol = v[t]*close[t])
    coin_illiq = {}  # coin -> list of (ymd, amihud_ratio)
    for coin, d in coin_data.items():
        days_sorted = sorted(d.keys())
        ratios = {}
        for i in range(1, len(days_sorted)):
            d0 = days_sorted[i - 1]
            d1 = days_sorted[i]
            p0, p1 = d[d0]["c"], d[d1]["c"]
            v1 = d[d1]["v"]
            if p0 > 0 and p1 > 0 and v1 > 0:
                ret = abs(p1 / p0 - 1)
                dollar_vol = v1 * p1
                if dollar_vol > 0:
                    ratios[d1] = ret / dollar_vol
        coin_illiq[coin] = ratios

    # Build Amihud signal per rebalance day
    results_illiq = []   # long-illiquid / short-liquid (illiquidity premium)
    results_liq = []     # long-liquid / short-illiquid (liquidity premium)

    for t in range(AMIHUD_WINDOW + 1, n_days - AMIHUD_HOLD - 1):
        d_signal = all_days[t]    # signal date: compute Amihud up to and including d_signal
        d_entry = all_days[t + 1]
        d_exit_idx = t + 1 + AMIHUD_HOLD
        if d_exit_idx >= n_days:
            continue
        d_exit = all_days[d_exit_idx]

        # Compute Amihud for each coin using last AMIHUD_WINDOW days up to d_signal
        window_days = all_days[t - AMIHUD_WINDOW + 1:t + 1]

        scored = []
        for coin, ratios in coin_illiq.items():
            vals = [ratios[d] for d in window_days if d in ratios]
            if len(vals) < AMIHUD_WINDOW // 2:  # need at least half the window
                continue
            if coin not in coin_data:
                continue
            cd = coin_data[coin]
            if d_entry not in cd or d_exit not in cd:
                continue
            amihud_score = statistics.mean(vals)
            scored.append((coin, amihud_score))

        if len(scored) < 2 * K + 4:
            continue

        scored.sort(key=lambda x: x[1], reverse=True)  # highest Amihud = most illiquid
        illiq_longs = [c for c, _ in scored[:K]]   # most illiquid → long
        illiq_shorts = [c for c, _ in scored[-K:]]  # most liquid → short

        def fwd(coin):
            cd = coin_data[coin]
            o = cd[d_entry]["o"]
            c = cd[d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        # Illiquidity premium: long illiquid, short liquid
        lr_illiq = statistics.mean(fwd(c) for c in illiq_longs)
        sr_illiq = statistics.mean(fwd(c) for c in illiq_shorts)
        results_illiq.append((lr_illiq - sr_illiq) - 2 * COST)

        # Liquidity premium: long liquid, short illiquid (reverse)
        results_liq.append((sr_illiq - lr_illiq) - 2 * COST)  # reverse the spread

    return results_illiq, results_liq


# ── Part C: Dispersion-conditioned momentum ───────────────────────────────────
def dispersion_momentum(data, stream):
    """Condition xs-momentum on trailing cross-sectional return dispersion.

    Cross-sectional dispersion on day d = stdev of daily returns across all coins on d.
    Trailing dispersion = mean of daily dispersions over last DISP_WINDOW days (up to signal date).

    Split the momentum L/S return stream into:
    - HIGH trailing dispersion (above median) → does momentum pay more?
    - LOW trailing dispersion (below median)

    Signal is lookahead-safe: dispersion computed from data up to rank-signal date (same as momentum signal).
    """
    # Build per-coin daily returns
    coin_daily = {}
    all_days_set = set()
    for coin, bars in data.items():
        rets = {}
        days_sorted = sorted(bars, key=lambda b: b["t"])
        for i in range(1, len(days_sorted)):
            b0, b1 = days_sorted[i - 1], days_sorted[i]
            p0, p1 = b0["c"], b1["c"]
            if p0 > 0:
                rets[_ymd(b1["t"])] = p1 / p0 - 1
        coin_daily[coin] = rets
        all_days_set.update(rets.keys())

    all_days = sorted(all_days_set)
    day_idx = {d: i for i, d in enumerate(all_days)}

    # Compute cross-sectional dispersion per day
    daily_disp = {}
    for d in all_days:
        day_rets = [r for coin_r in coin_daily.values()
                    for day, r in [(d, coin_r.get(d))] if r is not None]
        if len(day_rets) >= 5:
            daily_disp[d] = statistics.pstdev(day_rets)

    # For each rebalance in the stream, compute trailing dispersion at the signal date
    # stream is list of (entry_ymd, exit_ymd, ls_ret)
    # We need the signal_date = one day before entry_ymd in all_days
    entry_to_signal = {}
    for i, d in enumerate(all_days):
        if i > 0:
            entry_to_signal[d] = all_days[i - 1]  # previous day = signal date

    conditioned = []  # list of (trailing_disp, ls_ret)
    for (d_entry, d_exit, ls_ret) in stream:
        if d_entry not in entry_to_signal:
            continue
        d_signal = entry_to_signal[d_entry]
        if d_signal not in day_idx:
            continue
        sig_idx = day_idx[d_signal]
        # trailing DISP_WINDOW days up to and including d_signal
        window_days = all_days[max(0, sig_idx - DISP_WINDOW + 1):sig_idx + 1]
        disp_vals = [daily_disp[d] for d in window_days if d in daily_disp]
        if len(disp_vals) < DISP_WINDOW // 2:
            continue
        trail_disp = statistics.mean(disp_vals)
        conditioned.append((trail_disp, ls_ret))

    if len(conditioned) < 20:
        return [], [], []

    # Median split
    med_disp = statistics.median(d for d, _ in conditioned)
    high_disp = [r for d, r in conditioned if d > med_disp]
    low_disp = [r for d, r in conditioned if d <= med_disp]
    all_rets = [r for _, r in conditioned]

    return all_rets, high_disp, low_disp, med_disp


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print("# Alpha W6: Vol-managed momentum + Amihud illiquidity + Dispersion timing")
    print("# Methodology: lookahead-safe | cost>=10bps/leg | OOS-robust | BT_CACHE_ONLY")
    print("=" * 72)

    data = load()
    print(f"\n# Universe: {len(data)} coins loaded\n")

    # ─── Part A: Build base momentum stream + vol-managed variant ─────────────
    print("=" * 72)
    print("# PART A: VOL-MANAGED MOMENTUM (Moreira-Muir)")
    print(f"# Xs-momentum LB={MOM_LB}d / hold={MOM_HOLD}d / K={K}/leg / cost={int(COST*1e4)}bps")
    print(f"# Vol-target window={VOLMGD_WINDOW} rebalances; per-period target={TARGET_VOL_DAILY*math.sqrt(MOM_HOLD)*100:.1f}%")
    print("=" * 72)

    stream = build_base_ls_stream(data)
    print(f"\n  Built {len(stream)} rebalances")

    raw_rets, scaled_rets = vol_managed_momentum(stream)

    print(f"\n  {'Strategy':{32}} n={'n':>4} {'win':>6}  {'mean':>9}  {'Sharpe':>9}  {'maxDD':>8}  OOS h1/h2")
    print(f"  {'-'*32} {'-'*4} {'-'*6}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*20}")

    # Report raw (trimmed to same length for fair comparison)
    raw_trim = raw_rets[VOLMGD_WINDOW:]  # align with scaled (both start after window)
    rep("xs-momentum (raw, trim-aligned)", raw_trim, width=34)
    rep("xs-momentum (vol-managed, w_t)", scaled_rets, width=34)

    # Full raw for reference
    print()
    rep("xs-momentum (full raw, all rebals)", raw_rets, width=34)

    # ── Detailed comparison ──────────────────────────────────────────────────
    print("\n  ── Detailed Moreira-Muir diagnostics ──")
    if scaled_rets and len(scaled_rets) >= 10:
        raw_sh = sharpe(raw_trim)
        scl_sh = sharpe(scaled_rets)
        raw_dd = max_drawdown(raw_trim) * 100
        scl_dd = max_drawdown(scaled_rets) * 100
        raw_mu = statistics.mean(raw_trim) * 100
        scl_mu = statistics.mean(scaled_rets) * 100

        print(f"  Sharpe:  raw={raw_sh:>+.2f}  vol-managed={scl_sh:>+.2f}  delta={scl_sh-raw_sh:>+.2f}")
        print(f"  maxDD:   raw={raw_dd:>5.1f}%  vol-managed={scl_dd:>5.1f}%  delta={scl_dd-raw_dd:>+.1f}%")
        print(f"  mean/rebal: raw={raw_mu:>+.2f}%  vol-managed={scl_mu:>+.2f}%")

        # Compute per-period weights for diagnostics
        weights = []
        for i in range(VOLMGD_WINDOW, len(raw_rets)):
            window = raw_rets[i - VOLMGD_WINDOW:i]
            rv = statistics.pstdev(window)
            per_period_target = TARGET_VOL_DAILY * math.sqrt(MOM_HOLD)
            w = min(per_period_target / rv, 2.0) if rv > 0 else 1.0
            weights.append(w)
        if weights:
            print(f"  weights: mean={statistics.mean(weights):.2f}  "
                  f"min={min(weights):.2f}  max={max(weights):.2f}  "
                  f"pct>1={sum(1 for w in weights if w > 1)/len(weights)*100:.0f}%")

    # ─── Part B: Amihud illiquidity factor ────────────────────────────────────
    print()
    print("=" * 72)
    print(f"# PART B: AMIHUD ILLIQUIDITY FACTOR")
    print(f"# Amihud=mean(|ret|/dollar_vol) over {AMIHUD_WINDOW}d; hold={AMIHUD_HOLD}d; K={K}/leg; cost={int(COST*1e4)}bps")
    print(f"# dollar_vol = candle.v * close (v=volume in base coin units)")
    print("=" * 72)

    results_illiq, results_liq = amihud_factor(data)

    print(f"\n  {'Strategy':{42}} n={'n':>4} {'win':>6}  {'mean':>9}  {'Sharpe':>9}  {'maxDD':>8}  OOS h1/h2")
    print(f"  {'-'*42} {'-'*4} {'-'*6}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*20}")

    rep("long-ILLIQUID / short-LIQUID (illiq prem)", results_illiq, width=44)
    rep("long-LIQUID / short-ILLIQUID (liq prem)", results_liq, width=44)

    print()
    if results_illiq:
        mu_i = statistics.mean(results_illiq) * 100
        mu_l = statistics.mean(results_liq) * 100
        print(f"  EV summary: illiq_prem={mu_i:>+.2f}%  liq_prem={mu_l:>+.2f}%  "
              f"spread_direction={'ILLIQ' if mu_i > mu_l else 'LIQ'}")

    # ─── Part C: Dispersion-conditioned momentum ──────────────────────────────
    print()
    print("=" * 72)
    print(f"# PART C: DISPERSION-CONDITIONED MOMENTUM")
    print(f"# Trailing cross-section stdev over {DISP_WINDOW}d → median split → high vs low dispersion")
    print(f"# Using the same xs-momentum stream (LB={MOM_LB}/hold={MOM_HOLD}); gate value assessed")
    print("=" * 72)

    disp_result = dispersion_momentum(data, stream)
    if len(disp_result) == 4:
        all_rets, high_disp_rets, low_disp_rets, med_disp = disp_result
    else:
        all_rets, high_disp_rets, low_disp_rets, med_disp = [], [], [], 0

    print(f"\n  Trailing dispersion median: {med_disp*100:.3f}%")
    print(f"  {'Strategy':{36}} n={'n':>4} {'win':>6}  {'mean':>9}  {'Sharpe':>9}  {'maxDD':>8}  OOS h1/h2")
    print(f"  {'-'*36} {'-'*4} {'-'*6}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*20}")

    rep("xs-momentum (unconditioned, same n)", all_rets, width=38)
    rep("xs-momentum | HIGH dispersion", high_disp_rets, width=38)
    rep("xs-momentum | LOW dispersion", low_disp_rets, width=38)

    print()
    if high_disp_rets and low_disp_rets and len(high_disp_rets) >= 5 and len(low_disp_rets) >= 5:
        mu_hi = statistics.mean(high_disp_rets) * 100
        mu_lo = statistics.mean(low_disp_rets) * 100
        print(f"  HIGH disp EV={mu_hi:>+.2f}%  LOW disp EV={mu_lo:>+.2f}%  "
              f"lift={mu_hi-mu_lo:>+.2f}% (positive = dispersion gate helps)")
        gate_useful = (mu_hi > mu_lo and mu_hi > 0)
        print(f"  Dispersion gate verdict: {'USEFUL (run in high-disp only)' if gate_useful else 'NOT useful (no reliable conditional lift)'}")

    # ─── Summary verdict ───────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("# SUMMARY VERDICTS")
    print("=" * 72)

    # A: vol-managed
    if scaled_rets and raw_trim:
        raw_sh = sharpe(raw_trim)
        scl_sh = sharpe(scaled_rets)
        raw_dd = max_drawdown(raw_trim) * 100
        scl_dd = max_drawdown(scaled_rets) * 100
        sh_delta = scl_sh - raw_sh if raw_sh and scl_sh else None
        dd_delta = scl_dd - raw_dd
        if sh_delta is not None:
            verdict_a = ("HELPS (Sharpe up, DD improved)"
                         if sh_delta > 0.1 and dd_delta < -1
                         else "MIXED (Sharpe up but DD not improved)"
                         if sh_delta > 0.1
                         else "HURTS (Sharpe down) or NEUTRAL"
                         if sh_delta < -0.05
                         else "NEUTRAL")
        else:
            verdict_a = "N/A"
        print(f"\n  A) Vol-managed momentum: {verdict_a}")
        print(f"     Sharpe delta={sh_delta:>+.2f}  maxDD delta={dd_delta:>+.1f}%")

    # B: Amihud
    if results_illiq and results_liq:
        mu_i = statistics.mean(results_illiq) * 100
        mu_l = statistics.mean(results_liq) * 100
        rob_i = "ROBUST" if (statistics.mean(results_illiq[:len(results_illiq)//2]) > 0 and
                              statistics.mean(results_illiq[len(results_illiq)//2:]) > 0) else "fragile/neg"
        rob_l = "ROBUST" if (statistics.mean(results_liq[:len(results_liq)//2]) > 0 and
                              statistics.mean(results_liq[len(results_liq)//2:]) > 0) else "fragile/neg"
        print(f"\n  B) Amihud illiquidity factor:")
        print(f"     long-illiquid/short-liquid: {mu_i:>+.2f}% ({rob_i})")
        print(f"     long-liquid/short-illiquid: {mu_l:>+.2f}% ({rob_l})")
        if mu_i > 0 and rob_i == "ROBUST":
            print(f"     => ILLIQUIDITY PREMIUM VALIDATED — orthogonal alpha candidate")
        elif mu_l > 0 and rob_l == "ROBUST":
            print(f"     => LIQUIDITY PREMIUM VALIDATED (reverse: liquid outperforms)")
        else:
            print(f"     => NEITHER direction OOS-robust — REFUTED as standalone edge")

    # C: dispersion
    if high_disp_rets and low_disp_rets and len(high_disp_rets) >= 5 and len(low_disp_rets) >= 5:
        mu_hi = statistics.mean(high_disp_rets) * 100
        mu_lo = statistics.mean(low_disp_rets) * 100
        rob_hi = "ROBUST" if (len(high_disp_rets) >= 10 and
                               statistics.mean(high_disp_rets[:len(high_disp_rets)//2]) > 0 and
                               statistics.mean(high_disp_rets[len(high_disp_rets)//2:]) > 0) else "fragile/neg"
        print(f"\n  C) Dispersion-conditioned momentum:")
        print(f"     high-disp EV={mu_hi:>+.2f}%  low-disp EV={mu_lo:>+.2f}%  lift={mu_hi-mu_lo:>+.2f}%")
        if mu_hi > mu_lo and mu_hi > 0 and rob_hi == "ROBUST":
            print(f"     => DISPERSION GATE VALIDATED — trade momentum only in high-disp regimes")
        else:
            print(f"     => NOT validated as reliable gate — both periods need retesting on larger OOS")

    print()
    print("# END W6 REPORT")


if __name__ == "__main__":
    main()
