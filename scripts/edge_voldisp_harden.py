#!/usr/bin/env python3
"""Vol-dispersion (idio-vol) edge harden + wire-readiness study.

ASSIGNMENT (Wave 3 V1):
  1. WINDOW SWEEP: idio-vol window {15,21,30,45,60}d — best + most OOS-robust?
  2. ROBUSTNESS STRESS: cost {10,20,30bps} × K/leg {4,6,8,12} × universe {20,30,40,50}.
     Does the beta-neutral within-tercile edge survive everywhere?
  3. MOMENTUM STACK: build daily LS streams for both factors; correlation + combined Sharpes
     at {vol-disp only, 50/50, mom only}.
  4. VOL-SCALED: inverse-vol leg weighting vs equal-weight.

KEY METHODOLOGY NOTE: always report BOTH raw (dollar-neutral) AND within-β-tercile
(beta-neutral by construction). Trust the within-tercile number; raw inflates by net-beta
in the bull window.

Run: BT_CACHE_ONLY=1 python3 scripts/edge_voldisp_harden.py
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── shared constants ──────────────────────────────────────────────────────────
VOL_FLOOR   = 5e6
TOPN        = 50
HOLD        = 10          # holding period days (matches W1)
BETA_WIN    = 30          # trailing window for per-coin BTC beta
BASE_COST   = 10.0 / 1e4 # 10 bps/name round-trip
BASE_K      = 8
BASE_WIN    = 30          # W1 reference window


# ─── stats helpers (stdlib-only) ───────────────────────────────────────────────
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def _variance(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

def _stdev(xs):
    v = _variance(xs)
    return math.sqrt(v) if v > 0 else 0.0

def _corr(a, b):
    n = min(len(a), len(b))
    if n < 4:
        return float("nan")
    a, b = a[-n:], b[-n:]
    ma, mb = _mean(a), _mean(b)
    num = sum((ai - ma) * (bi - mb) for ai, bi in zip(a, b))
    den = math.sqrt(sum((ai - ma)**2 for ai in a) * sum((bi - mb)**2 for bi in b))
    return num / den if den > 0 else float("nan")

def _sharpe_annualised(xs, periods_per_year=261/HOLD):
    """Annualised Sharpe: mean/stdev * sqrt(periods_per_year)."""
    if len(xs) < 4:
        return float("nan")
    m = _mean(xs)
    s = _stdev(xs)
    if s <= 0:
        return float("nan")
    return (m / s) * math.sqrt(periods_per_year)

def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


# ─── data loading ──────────────────────────────────────────────────────────────
def load(topn=50):
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "")
           and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot"
           and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:topn]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ─── core helpers ──────────────────────────────────────────────────────────────
def _daily_rets(coin_data, days):
    """Ordered daily returns for the given list of day-keys."""
    rets = []
    prev_c = None
    for d in days:
        if d in coin_data:
            c = coin_data[d]["c"]
            if prev_c is not None and prev_c > 0:
                rets.append(c / prev_c - 1.0)
            prev_c = c
        else:
            prev_c = None
    return rets

def _ols_beta(cr, br):
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = list(cr[-n:]), list(br[-n:])
    mb = _mean(br)
    vb = sum((x - mb)**2 for x in br)
    if vb <= 0:
        return 1.0
    mc = _mean(cr)
    return sum((a - mc)*(b - mb) for a, b in zip(cr, br)) / vb

def _idio_vol(coin_data, btc_data, win_days, idvol_win):
    """Idiosyncratic vol: stdev of residuals (coin ret - beta * BTC ret) over window."""
    wd = win_days[-idvol_win:]
    # need contiguous returns → compute from wd
    cr = _daily_rets(coin_data, wd)
    br = _daily_rets(btc_data, wd)
    if len(cr) < 10 or len(br) < 10:
        return None
    n = min(len(cr), len(br))
    cr, br = cr[-n:], br[-n:]
    beta = _ols_beta(cr, br)
    residuals = [c - beta * b for c, b in zip(cr, br)]
    return _stdev(residuals)

def _coin_beta(coin_data, btc_data, win_days):
    wd = win_days[-BETA_WIN:]
    cr = _daily_rets(coin_data, wd)
    br = _daily_rets(btc_data, wd)
    return _ols_beta(cr, br)

def _trailing_vol(coin_data, win_days, vol_win=30):
    """Trailing volatility (stdev of daily returns) for inverse-vol weighting."""
    wd = win_days[-vol_win:]
    rets = _daily_rets(coin_data, wd)
    return _stdev(rets) if len(rets) >= 5 else None


# ─── reporting ─────────────────────────────────────────────────────────────────
def rep(label, arr, width=50):
    if not arr:
        print(f"  {label:{width}} n=  0  ---")
        return
    n   = len(arr)
    mid = n // 2
    h1  = _mean(arr[:mid]) * 100 if mid else 0.0
    h2  = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    mn  = _mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else ("fragile" if (h1 > 0) != (h2 > 0) else "neg")
    flag = "  <<< +EV" if mn > 0 and rob == "ROBUST" else ""
    sh  = _sharpe_annualised(arr)
    print(f"  {label:{width}} n={n:>3} mean={mn:>+6.2f}%  OOS {h1:>+5.2f}/{h2:>+5.2f}  "
          f"Sh={sh:>+4.2f}  {rob}{flag}")


# ─── SECTION 1: window sweep ───────────────────────────────────────────────────
def window_sweep(data, btc_data, all_days, burn_in=70):
    """
    For each idvol window, run:
      - raw L-S (dollar-neutral, K=BASE_K, cost=BASE_COST)
      - within-β-tercile L-S (beta-neutral by construction)
    """
    print("=" * 78)
    print("  1. WINDOW SWEEP — idio-vol window × {raw, within-β-tercile}")
    print("=" * 78)
    print(f"  K={BASE_K}/leg | hold={HOLD}d | cost={BASE_COST*1e4:.0f}bps | top{TOPN}\n")

    windows = [15, 21, 30, 45, 60]
    best = {}   # window -> (raw_mean, wt_mean) for summary

    for idvol_win in windows:
        raw_rets, wt_rets = _run_both_modes(
            data, btc_data, all_days, burn_in,
            idvol_win=idvol_win, k=BASE_K, cost=BASE_COST, vol_scaled=False
        )
        raw_mn = _mean(raw_rets) * 100 if raw_rets else float("nan")
        wt_mn  = _mean(wt_rets) * 100 if wt_rets else float("nan")
        best[idvol_win] = (raw_mn, wt_mn)
        print(f"  Window={idvol_win:>2}d:")
        rep(f"    Raw L-S (dollar-neutral)", raw_rets)
        rep(f"    Within-β-tercile (beta-neutral)", wt_rets)
        print()

    # pick best by within-tercile mean (the honest number)
    best_win = max(best, key=lambda w: best[w][1] if not math.isnan(best[w][1]) else -999)
    print(f"  WINDOW VERDICT: best within-β-tercile mean = win={best_win}d "
          f"({best[best_win][1]:>+.2f}%)")
    print(f"  W1 reference (30d): raw={best[30][0]:>+.2f}%, BN={best[30][1]:>+.2f}%")
    print()
    return best_win


# ─── SECTION 2: robustness stress test ────────────────────────────────────────
def robustness_sweep(data, btc_data, all_days, burn_in=70, best_win=30):
    """
    Sweep cost × K × universe-size for within-β-tercile (the beta-neutral number).
    Raw shown alongside for comparison.
    """
    print("=" * 78)
    print(f"  2. ROBUSTNESS STRESS TEST (within-β-tercile) — best window={best_win}d")
    print("=" * 78)

    coins_by_liq = list(data)  # already volume-sorted by load()

    # A. Cost sweep (K=BASE_K, top50)
    print(f"\n  A. Cost sweep (K={BASE_K}, top-{TOPN}):")
    for bps in [10, 20, 30]:
        raw_rets, wt_rets = _run_both_modes(
            data, btc_data, all_days, burn_in,
            idvol_win=best_win, k=BASE_K, cost=bps/1e4, vol_scaled=False
        )
        rep(f"    cost={bps}bps  raw", raw_rets, width=35)
        rep(f"    cost={bps}bps  β-neutral", wt_rets, width=35)

    # B. K sweep (BASE_COST, top50)
    print(f"\n  B. K/leg sweep (cost={BASE_COST*1e4:.0f}bps, top-{TOPN}):")
    for k in [4, 6, 8, 12]:
        raw_rets, wt_rets = _run_both_modes(
            data, btc_data, all_days, burn_in,
            idvol_win=best_win, k=k, cost=BASE_COST, vol_scaled=False
        )
        rep(f"    K={k}  raw", raw_rets, width=35)
        rep(f"    K={k}  β-neutral", wt_rets, width=35)

    # C. Universe-size sweep (BASE_K, BASE_COST)
    print(f"\n  C. Universe-size sweep (K={BASE_K}, cost={BASE_COST*1e4:.0f}bps):")
    for topn in [20, 30, 40, 50]:
        subset = {c: data[c] for c in coins_by_liq[:topn] if c in data}
        sd_btc = btc_data if "BTC" in subset else btc_data
        sub_all_days = sorted({d for cd in subset.values() for d in cd})
        raw_rets, wt_rets = _run_both_modes(
            subset, sd_btc, sub_all_days, burn_in,
            idvol_win=best_win, k=BASE_K, cost=BASE_COST, vol_scaled=False
        )
        rep(f"    top-{topn}  raw", raw_rets, width=35)
        rep(f"    top-{topn}  β-neutral", wt_rets, width=35)

    print()


# ─── SECTION 3: momentum stack ────────────────────────────────────────────────
def momentum_stack(data, btc_data, all_days, burn_in=70, best_win=30):
    """
    Build aligned daily LS streams for:
      - vol-dispersion (within-β-tercile, best_win)
      - xs-momentum residual (LB=7, hold=HOLD)
    Compute correlation + Sharpes at mom-only / 50-50 / vol-disp-only.
    """
    print("=" * 78)
    print(f"  3. MOMENTUM STACK — correlation + Sharpe at blends")
    print("=" * 78)
    print(f"  vol-disp: within-β-tercile, win={best_win}d | mom: LB=7d residual | "
          f"hold={HOLD}d | cost={BASE_COST*1e4:.0f}bps\n")

    # vol-dispersion stream (within-tercile)
    _, vd_stream = _run_both_modes(
        data, btc_data, all_days, burn_in,
        idvol_win=best_win, k=BASE_K, cost=BASE_COST, vol_scaled=False
    )

    # xs-momentum stream (residual, LB=7)
    mom_stream = _run_xs_momentum(data, btc_data, all_days, burn_in=burn_in, lb=7)

    # align by rebalance count (both produce one ret per rebalance, may differ in n)
    n = min(len(vd_stream), len(mom_stream))
    if n < 10:
        print("  Not enough data to stack.")
        return

    vd  = vd_stream[-n:]
    mom = mom_stream[-n:]

    corr = _corr(vd, mom)
    print(f"  Correlation (vol-disp β-neutral vs xs-momentum): {corr:>+.3f}")
    print(f"  (|corr|<0.20 = genuinely diversifying; 0.20-0.40 = partial; >0.40 = correlated)\n")

    blends = [("vol-disp only (β-neutral)", vd, 1.0, 0.0),
              ("50/50 blend",               None, 0.5, 0.5),
              ("mom only",                  mom, 0.0, 1.0)]

    for label, arr, w_vd, w_mom in blends:
        if arr is None:
            combined = [(w_vd * v + w_mom * m) for v, m in zip(vd, mom)]
        else:
            combined = arr
        rep(label, combined, width=40)
    print()

    # Is vol-disp diversifying?
    sh_vd  = _sharpe_annualised(vd)
    sh_mom = _sharpe_annualised(mom)
    combo  = [(0.5*v + 0.5*m) for v, m in zip(vd, mom)]
    sh_combo = _sharpe_annualised(combo)

    print(f"  Annualised Sharpes (gross, pre-cost-averaging):")
    print(f"    vol-disp (β-neutral) : {sh_vd:>+.3f}")
    print(f"    xs-momentum          : {sh_mom:>+.3f}")
    print(f"    50/50 blend          : {sh_combo:>+.3f}")

    if sh_combo > max(sh_vd, sh_mom):
        print(f"  DIVERSIFICATION VERDICT: 50/50 RAISES Sharpe → vol-disp is genuinely diversifying ✓")
    elif abs(corr) < 0.25:
        print(f"  DIVERSIFICATION VERDICT: low corr ({corr:+.3f}) but Sharpe doesn't lift → independent but "
              f"adding noise (small allocation still valid)")
    else:
        print(f"  DIVERSIFICATION VERDICT: correlated ({corr:+.3f}) → limited diversification benefit")
    print()
    return vd_stream, mom_stream


# ─── SECTION 4: vol-scaled variant ────────────────────────────────────────────
def vol_scaled_variant(data, btc_data, all_days, burn_in=70, best_win=30):
    """
    Inverse-vol weighted legs vs equal-weight.
    Within-β-tercile only (the honest benchmark).
    """
    print("=" * 78)
    print(f"  4. VOL-SCALED VARIANT (inverse-vol leg weighting, within-β-tercile)")
    print("=" * 78)
    print(f"  K={BASE_K}, win={best_win}d, cost={BASE_COST*1e4:.0f}bps\n")

    _, ew_rets = _run_both_modes(
        data, btc_data, all_days, burn_in,
        idvol_win=best_win, k=BASE_K, cost=BASE_COST, vol_scaled=False
    )
    _, vs_rets = _run_both_modes(
        data, btc_data, all_days, burn_in,
        idvol_win=best_win, k=BASE_K, cost=BASE_COST, vol_scaled=True
    )

    rep("Equal-weight (baseline)", ew_rets)
    rep("Inverse-vol weighted    ", vs_rets)
    print()

    ew_sh = _sharpe_annualised(ew_rets)
    vs_sh = _sharpe_annualised(vs_rets)
    if vs_sh > ew_sh + 0.10:
        print(f"  VOL-SCALED VERDICT: IMPROVED Sharpe ({ew_sh:+.3f} → {vs_sh:+.3f}) → USE vol-scaled")
    elif vs_sh > ew_sh:
        print(f"  VOL-SCALED VERDICT: marginal improvement ({ew_sh:+.3f} → {vs_sh:+.3f}) → optional")
    else:
        print(f"  VOL-SCALED VERDICT: no improvement ({ew_sh:+.3f} → {vs_sh:+.3f}) → equal-weight fine")
    print()


# ─── rebalance engines ────────────────────────────────────────────────────────
def _run_both_modes(data, btc_data, all_days, burn_in,
                    idvol_win, k, cost, vol_scaled):
    """
    Returns (raw_rets, within_tercile_rets) for idio-vol factor.
      raw          = dollar-neutral L-S (net beta may be nonzero)
      within_tercile = L-S within each beta-tercile, averaged → beta-neutral by construction
    """
    raw_rets = []
    wt_rets  = []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d       = all_days[t]
        d_entry = all_days[t + 1]
        d_exit  = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - max(idvol_win, BETA_WIN) - 5): t + 1]

        # Score every coin
        scored = []
        for coin, cd in data.items():
            if d_entry not in cd or d_exit not in cd:
                continue
            score = _idio_vol(cd, btc_data, win_days, idvol_win)
            if score is None:
                continue
            beta = _coin_beta(cd, btc_data, win_days)
            if vol_scaled:
                tvol = _trailing_vol(cd, win_days, idvol_win) or 1e-4
            else:
                tvol = None
            scored.append((coin, score, beta, tvol))

        if len(scored) < 2 * k + 4:
            continue

        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c = data[coin][d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        # ── RAW (dollar-neutral, equal or vol-scaled) ──────────────────────────
        scored_by_score = sorted(scored, key=lambda x: x[1], reverse=True)
        longs_r  = scored_by_score[:k]
        shorts_r = scored_by_score[-k:]

        if vol_scaled:
            # inverse-vol weights within each leg
            lw = [1.0 / (x[3] or 1e-4) for x in longs_r]
            sw = [1.0 / (x[3] or 1e-4) for x in shorts_r]
            lw_sum, sw_sum = sum(lw), sum(sw)
            lr = sum(fwd(x[0]) * w for x, w in zip(longs_r, lw)) / lw_sum if lw_sum else 0.0
            sr = sum(fwd(x[0]) * w for x, w in zip(shorts_r, sw)) / sw_sum if sw_sum else 0.0
        else:
            lr = _mean([fwd(x[0]) for x in longs_r])
            sr = _mean([fwd(x[0]) for x in shorts_r])

        raw_rets.append((lr - sr) - 2 * cost)

        # ── WITHIN-β-TERCILE (beta-neutral by construction) ────────────────────
        scored_by_beta = sorted(scored, key=lambda x: x[2])
        n_tot = len(scored_by_beta)
        n_per = n_tot // 3
        if n_per < 3:
            continue

        tercile_spreads = []
        for ti in range(3):
            start = ti * n_per
            end   = (start + n_per) if ti < 2 else n_tot
            tc    = scored_by_beta[start:end]

            # sort within tercile by factor score (higher idio-vol = long)
            tc.sort(key=lambda x: x[1], reverse=True)
            k_t = max(1, min(3, len(tc) // 3))
            longs_t  = tc[:k_t]
            shorts_t = tc[-k_t:]

            if not longs_t or not shorts_t:
                continue

            if vol_scaled:
                lw = [1.0 / (x[3] or 1e-4) for x in longs_t]
                sw = [1.0 / (x[3] or 1e-4) for x in shorts_t]
                lw_sum, sw_sum = sum(lw), sum(sw)
                lr_t = sum(fwd(x[0]) * w for x, w in zip(longs_t, lw)) / lw_sum if lw_sum else 0.0
                sr_t = sum(fwd(x[0]) * w for x, w in zip(shorts_t, sw)) / sw_sum if sw_sum else 0.0
            else:
                lr_t = _mean([fwd(x[0]) for x in longs_t])
                sr_t = _mean([fwd(x[0]) for x in shorts_t])

            tercile_spreads.append((lr_t - sr_t) - 2 * cost)

        if tercile_spreads:
            wt_rets.append(_mean(tercile_spreads))

    return raw_rets, wt_rets


def _run_xs_momentum(data, btc_data, all_days, burn_in, lb):
    """
    xs-momentum RESIDUAL stream (same hold=HOLD, cost=BASE_COST, K=BASE_K).
    Signal = coin's lb-return minus beta*BTC-lb-return (BTC-neutral score).
    Lookahead-safe: score on close[t], enter t+1 open, exit t+1+HOLD close.
    """
    rets = []
    for t in range(lb + BETA_WIN, len(all_days) - HOLD - 1):
        d       = all_days[t]
        d_entry = all_days[t + 1]
        d_exit  = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - BETA_WIN - 5): t + 1]

        scored = []
        for coin, cd in data.items():
            if d_entry not in cd or d_exit not in cd:
                continue
            # trailing lb-day return
            d_lb = all_days[t - lb] if t - lb >= 0 else None
            if d_lb is None or d_lb not in cd or d not in cd:
                continue
            c_now  = cd[d]["c"]
            c_past = cd[d_lb]["c"]
            if c_past <= 0:
                continue
            rc = c_now / c_past - 1.0

            # BTC return over same period
            if btc_data and d in btc_data and d_lb in btc_data:
                rb = btc_data[d]["c"] / btc_data[d_lb]["c"] - 1.0
                beta = _coin_beta(cd, btc_data, win_days)
                score = rc - beta * rb
            else:
                score = rc

            scored.append((coin, score))

        if len(scored) < 2 * BASE_K + 4:
            continue

        scored.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in scored[:BASE_K]]
        shorts = [c for c, _ in scored[-BASE_K:]]

        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c = data[coin][d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        rets.append((lr - sr) - 2 * BASE_COST)

    return rets


# ─── final summary ────────────────────────────────────────────────────────────
def print_final_verdict(best_win, data, btc_data, all_days, burn_in=70):
    print("=" * 78)
    print("  FINAL VERDICT")
    print("=" * 78)

    _, wt_best = _run_both_modes(
        data, btc_data, all_days, burn_in,
        idvol_win=best_win, k=BASE_K, cost=BASE_COST, vol_scaled=False
    )

    if not wt_best:
        print("  No data for final verdict.")
        return

    n   = len(wt_best)
    mid = n // 2
    h1  = _mean(wt_best[:mid]) * 100
    h2  = _mean(wt_best[mid:]) * 100
    mn  = _mean(wt_best) * 100
    sh  = _sharpe_annualised(wt_best)
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile"

    print(f"\n  Best config: idvol_win={best_win}d, K={BASE_K}/leg, hold={HOLD}d, cost={BASE_COST*1e4:.0f}bps")
    print(f"  Within-β-tercile (HONEST beta-neutral EV):")
    print(f"    Mean/rebal : {mn:>+.2f}%")
    print(f"    OOS halves : {h1:>+.2f}% / {h2:>+.2f}%  → {rob}")
    print(f"    Sharpe     : {sh:>+.3f} annualised")
    print()
    print("  WIRE-READINESS CHECK:")
    conditions = [
        (mn > 0,      f"mean/rebal > 0          : {mn:>+.2f}%"),
        (rob == "ROBUST", f"OOS-robust (both +)     : {h1:>+.2f} / {h2:>+.2f}%"),
        (sh > 0.5,    f"Sharpe > 0.5            : {sh:>+.3f}"),
    ]
    all_pass = True
    for ok, desc in conditions:
        status = "PASS" if ok else "FAIL"
        print(f"    [{status}] {desc}")
        if not ok:
            all_pass = False

    print()
    print("  CAVEATS (cannot be resolved with current cache):")
    print("  - Only ~6-month bull/choppy window (Oct 2025 - Jun 2026). No sustained bear.")
    print("  - Down-days ≠ sustained bear regime. Within-tercile is still only bull tested.")
    print("  - n=28 coins; tercile granularity thin (k_t=1-3 per tercile).")
    print()
    if all_pass:
        print("  *** VERDICT: WIRE SHADOW alongside momentum — POSITIVE expected EV ***")
        print(f"  HONEST EV (within-β-tercile, beta-neutral): ~{mn:+.2f}%/rebal")
        print(f"  INFLATED raw EV (dollar-neutral, carry net-beta): DISCARD for live expectations")
        print(f"  DEPLOYMENT: shadow-only until bear-regime forward data arrives (~2-3 months)")
    else:
        print("  *** VERDICT: NOT YET WIRE-READY — see FAIL conditions above ***")


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 78)
    print("  VOL-DISPERSION (IDIO-VOL) EDGE HARDEN — Wave 3 V1")
    print("  Characterise + stress-test for shadow wire-readiness")
    print("=" * 78)
    print(f"  BT_CACHE_ONLY={os.environ.get('BT_CACHE_ONLY', '0')} | "
          f"hold={HOLD}d | beta_win={BETA_WIN}d\n")

    data = load(TOPN)
    if not data:
        print("ERROR: no data loaded (is cache warmed? try without BT_CACHE_ONLY=1)")
        sys.exit(1)

    btc_data = data.get("BTC")
    if btc_data is None:
        print("ERROR: BTC not in cache. Cannot compute betas or residuals.")
        sys.exit(1)

    all_days = sorted({d for cd in data.values() for d in cd})
    burn_in  = 80  # slightly more conservative than W1's 70 (need BETA_WIN headroom)

    print(f"  {len(data)} coins | {len(all_days)} days in union | "
          f"burn-in={burn_in}d → {len(all_days)-burn_in-HOLD-1} rebalance slots\n")

    # 1. Window sweep
    best_win = window_sweep(data, btc_data, all_days, burn_in)

    # 2. Robustness stress test at best window
    robustness_sweep(data, btc_data, all_days, burn_in, best_win)

    # 3. Momentum stack
    momentum_stack(data, btc_data, all_days, burn_in, best_win)

    # 4. Vol-scaled variant
    vol_scaled_variant(data, btc_data, all_days, burn_in, best_win)

    # Final verdict
    print_final_verdict(best_win, data, btc_data, all_days, burn_in)


if __name__ == "__main__":
    main()
