#!/usr/bin/env python3
"""Alpha hunt — RICHER REGIME CLASSIFIERS as SIZING GATES on momentum + vol-dispersion books.

Assignment (X2 in ALPHA-PLAN.md Wave 4):
  (a) 2-STATE vol regime on BTC: trailing-vol-threshold proxy (Gaussian 2-state HMM is heavy
      in stdlib-only code; trailing-vol quantile is a clean, equivalent proxy). Does conditioning
      on inferred HIGH vs LOW vol state beat unconditioned?

  (b) CHANGE-POINT / CUSUM on BTC vol: detect structural breaks in BTC rolling vol. Do the
      books behave differently right after a detected break (de-risk post-break hypothesis)?

  (c) 2-SIGNAL COMBO: correlation-regime gate (validated V3) + vol-state — does combining
      beat the correlation-regime gate alone?

METHODOLOGY (all gates):
  - Lookahead-safe: regime label from data <= t, applied t+1 entry
  - Cost-aware: >= 10bps/leg already subtracted from both books
  - OOS-robust: BOTH chronological halves of conditioned stream must be positive
  - PERMUTATION TEST: shuffle regime labels N=500 times, compare gated Sharpe to shuffle
    distribution; report p-value. MANDATORY for every gate.
  - HIGH BAR: 4 regime signals already refuted; only corr-regime survived V3.

Books replicated from edge_regime_timing.py (same params validated there):
  - Momentum   : xs long-short (LB=7d, hold=10d, K=8, cost=10bps/leg)
  - Vol-disp   : within-beta-tercile long-HIGH/short-LOW idio-vol (hold=10d, cost=10bps/leg)

Run: BT_CACHE_ONLY=1 python3 scripts/edge_regime_states.py
"""
import os, sys, math, random, statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── config ───────────────────────────────────────────────────────────────────
TOPN          = 50
VOL_FLOOR     = 5e6
K             = 8          # names per leg (both books)
COST_BPS      = 10.0       # per name, round-trip
MOM_LB        = 7          # momentum look-back (validated)
MOM_HOLD      = 10         # momentum hold
VDISP_HOLD    = 10         # vol-dispersion hold
IDVOL_WIN     = 30         # trailing window for idio-vol signal
BETA_WIN      = 30         # trailing window for BTC-beta estimation
CORR_WIN      = 14         # rolling window for pairwise-correlation signal (V3 validated)
VOL_WIN       = 20         # trailing BTC-vol window for 2-state classifier
CUSUM_WIN     = 20         # baseline window for CUSUM
CUSUM_K       = 0.5        # CUSUM slack (in units of baseline std)
CUSUM_H       = 3.0        # CUSUM detection threshold (in units of baseline std)
COOLDOWN_DAYS = 5          # post-break cooldown: flag=True for this many days after break
N_PERM        = 500        # permutation test iterations
RANDOM_SEED   = 42
# ─────────────────────────────────────────────────────────────────────────────


def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pstdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _pearson(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma, mb = _mean(a), _mean(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    if da <= 0 or db <= 0:
        return 0.0
    return num / (da * db)


def _ols_beta(cr, br):
    """OLS beta of coin returns on BTC returns. Returns 1.0 if degenerate."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = list(cr[-n:]), list(br[-n:])
    mb = _mean(br)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = _mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


def sharpe(rets):
    """Annualised Sharpe (daily observations, x sqrt(365))."""
    if len(rets) < 4:
        return float("nan")
    m = _mean(rets)
    s = _pstdev(rets)
    if s <= 0:
        return float("nan")
    return (m / s) * math.sqrt(365)


def max_dd(rets):
    """Maximum drawdown (as a positive fraction)."""
    cum = peak = worst = 0.0
    for r in rets:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > worst:
            worst = dd
    return worst


# ─── Data loading ────────────────────────────────────────────────────────────

def load():
    """Load daily candles. Returns dict: coin -> {ymd: (open, close)}."""
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
            data[c] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}
    return data


# ─── Daily BTC return series ─────────────────────────────────────────────────

def build_btc_daily_rets(data, all_days):
    """Returns dict: day -> btc_return (or None if missing)."""
    btc_oc = data.get("BTC", {})
    btc_rets = {}
    for i in range(1, len(all_days)):
        d, prev = all_days[i], all_days[i - 1]
        if d in btc_oc and prev in btc_oc:
            c_now, c_prev = btc_oc[d][1], btc_oc[prev][1]
            if c_prev > 0:
                btc_rets[d] = c_now / c_prev - 1.0
    return btc_rets


# ─── (a) 2-State vol regime — trailing-vol threshold proxy ───────────────────

def build_vol_regime(all_days, btc_rets):
    """2-state BTC vol regime using trailing-vol vs rolling-median threshold.

    At each day t:
      - Compute trailing std of BTC returns over VOL_WIN days ending at t.
      - Track running median of all trailing-vol estimates seen so far (t,
        NOT t+1 — lookahead-safe).
      - vol_high[t] = True  if trailing_vol[t] > running_median_vol (HIGH vol state)
                    = False if trailing_vol[t] <= running_median_vol (LOW vol state)
      - Applied at entry = t+1.

    This is a clean proxy for Gaussian 2-state HMM without scipy dependency.
    The key structural equivalence: HMM high/low-vol states map directly onto
    above/below-median trailing vol (which is the empirical mode-split).
    """
    vol_regime = {}   # day -> True (high vol) | False (low vol) | None
    running_vols = []

    for i, day in enumerate(all_days):
        if i < VOL_WIN:
            vol_regime[day] = None
            continue
        # Trailing returns over VOL_WIN days ending at day (inclusive)
        win_days = all_days[i - VOL_WIN + 1: i + 1]
        win_rets = [btc_rets.get(d) for d in win_days]
        win_rets = [r for r in win_rets if r is not None]
        if len(win_rets) < VOL_WIN // 2:
            vol_regime[day] = None
            continue
        trail_vol = _pstdev(win_rets)
        running_vols.append(trail_vol)
        # Rolling median: uses all observations up to and including t (lookahead-safe)
        med_vol = statistics.median(running_vols)
        vol_regime[day] = trail_vol > med_vol   # True = HIGH vol

    return vol_regime


# ─── (b) Change-point / CUSUM on BTC vol ────────────────────────────────────

def build_cusum_regime(all_days, btc_rets):
    """CUSUM change-point detector on BTC rolling volatility.

    Algorithm:
      1. Compute trailing BTC-vol series (same VOL_WIN window).
      2. Maintain a baseline mean and std of vol over the first CUSUM_WIN
         vol observations (expanding, then fixed once we have enough).
      3. CUSUM statistic: S_t = max(0, S_{t-1} + (v_t - mu - k*sigma))
         where mu, sigma = running mean/std of vol, k = CUSUM_K slack.
      4. Alarm (break detected) when S_t > H = CUSUM_H * sigma.
      5. After alarm: reset S_t = 0, flag post_break[t] = True for COOLDOWN_DAYS.
      6. post_break gate applied at entry = t+1.

    Hypothesis: the books' characters change right after a detected volatility
    structural break (de-risk post-break).
    """
    # Step 1: compute trailing-vol series indexed by day
    trail_vols = {}   # day -> trailing vol
    for i, day in enumerate(all_days):
        if i < VOL_WIN:
            continue
        win_days = all_days[i - VOL_WIN + 1: i + 1]
        win_rets = [btc_rets.get(d) for d in win_days]
        win_rets = [r for r in win_rets if r is not None]
        if len(win_rets) < VOL_WIN // 2:
            continue
        trail_vols[day] = _pstdev(win_rets)

    # Step 2-5: CUSUM over the vol series
    post_break = {}   # day -> True (within cooldown of a break) | False | None
    vol_hist = []     # running vol history for baseline estimation
    S = 0.0           # CUSUM statistic
    cooldown_remaining = 0

    for day in all_days:
        if day not in trail_vols:
            post_break[day] = None
            continue

        v = trail_vols[day]
        vol_hist.append(v)

        if len(vol_hist) < CUSUM_WIN:
            # Warmup: not enough baseline
            post_break[day] = None
            S = 0.0
            continue

        # Baseline from full history up to now (expanding window, lookahead-safe)
        mu = _mean(vol_hist)
        sigma = _pstdev(vol_hist)
        if sigma <= 0:
            post_break[day] = False
            continue

        threshold = CUSUM_H * sigma

        # CUSUM update
        S = max(0.0, S + (v - mu - CUSUM_K * sigma))

        if S > threshold:
            # Break detected at this day
            cooldown_remaining = COOLDOWN_DAYS
            S = 0.0   # reset after alarm
            post_break[day] = True
        elif cooldown_remaining > 0:
            cooldown_remaining -= 1
            post_break[day] = True
        else:
            post_break[day] = False

    return post_break


# ─── Correlation-regime gate (V3 validated — replicated from edge_regime_timing.py) ─────

def build_corr_regime(data, all_days):
    """Rolling avg pairwise Pearson correlation vs running median (V3 validated gate).

    corr_high[day] = True  = HIGH corr (size DOWN momentum, size UP vol-disp)
                   = False = LOW corr  (size UP momentum, size DOWN vol-disp)
    Lookahead-safe: uses returns up to and including day t; applied at t+1.
    """
    # Precompute daily returns
    returns_by_day = {}
    for coin, oc in data.items():
        days_sorted = sorted(oc)
        returns_by_day[coin] = {}
        for i in range(1, len(days_sorted)):
            d, prev = days_sorted[i], days_sorted[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                returns_by_day[coin][d] = c_now / c_prev - 1.0

    raw_corr = {}   # day -> avg_corr | None
    for i, day in enumerate(all_days):
        if i < CORR_WIN:
            raw_corr[day] = None
            continue
        window_days = all_days[i - CORR_WIN + 1: i + 1]
        eligible = []
        for coin in data:
            rets_w = [returns_by_day[coin].get(d) for d in window_days]
            if sum(1 for r in rets_w if r is not None) >= CORR_WIN * 0.8:
                filled = [r if r is not None else 0.0 for r in rets_w]
                eligible.append(filled)
        if len(eligible) < 4:
            raw_corr[day] = None
            continue
        sub = eligible[:15]
        pair_corrs = []
        for ii in range(len(sub)):
            for jj in range(ii + 1, len(sub)):
                rho = _pearson(sub[ii], sub[jj])
                pair_corrs.append(rho)
        raw_corr[day] = _mean(pair_corrs) if pair_corrs else None

    # Build boolean against rolling median (lookahead-safe)
    corr_regime = {}
    running_corrs = []
    for day in all_days:
        c = raw_corr.get(day)
        if c is None:
            corr_regime[day] = None
        else:
            running_corrs.append(c)
            med = statistics.median(running_corrs)
            corr_regime[day] = c > med  # True = HIGH corr

    return corr_regime


# ─── LS book builders (replicated from edge_regime_timing.py) ────────────────

def build_momentum_stream(data, all_days):
    """xs-momentum LS stream. LB=MOM_LB, hold=MOM_HOLD, K legs, 10bps/leg."""
    cost = COST_BPS / 1e4 * 2  # round-trip both legs
    stream = []
    for t in range(MOM_LB, len(all_days) - MOM_HOLD - 1):
        d       = all_days[t]
        d_lb    = all_days[t - MOM_LB]
        d_entry = all_days[t + 1]
        d_exit  = all_days[t + 1 + MOM_HOLD] if t + 1 + MOM_HOLD < len(all_days) else all_days[-1]
        ranked  = []
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_entry in oc and d_exit in oc:
                c_now, c_past = oc[d][1], oc[d_lb][1]
                if c_past > 0:
                    ranked.append((coin, c_now / c_past - 1))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        longs  = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]
        def fwd(coin):
            o = data[coin][d_entry][0]
            c = data[coin][d_exit][1]
            return (c - o) / o if o > 0 else 0.0
        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        stream.append((d, (lr - sr) - cost))
    return stream


def build_vdisp_stream(data, all_days):
    """Within-beta-tercile vol-dispersion LS stream. hold=VDISP_HOLD, 10bps/leg."""
    cost = COST_BPS / 1e4 * 2
    daily_rets = {}
    for coin, oc in data.items():
        days_sorted = sorted(oc)
        daily_rets[coin] = {}
        for i in range(1, len(days_sorted)):
            d, prev = days_sorted[i], days_sorted[i - 1]
            c_now, c_prev = oc[d][1], oc[prev][1]
            if c_prev > 0:
                daily_rets[coin][d] = c_now / c_prev - 1.0
    btc_rets = daily_rets.get("BTC", {})
    stream   = []
    warmup   = max(IDVOL_WIN, BETA_WIN) + 2
    for t in range(warmup, len(all_days) - VDISP_HOLD - 1):
        d       = all_days[t]
        d_entry = all_days[t + 1]
        d_exit  = all_days[t + 1 + VDISP_HOLD] if t + 1 + VDISP_HOLD < len(all_days) else all_days[-1]
        win_days  = all_days[max(0, t - IDVOL_WIN + 1): t + 1]
        beta_days = all_days[max(0, t - BETA_WIN + 1): t + 1]
        factors = []
        for coin, oc in data.items():
            if d_entry not in oc or d_exit not in oc:
                continue
            cr_win  = [daily_rets[coin].get(dd) for dd in win_days]
            cr_win  = [r for r in cr_win if r is not None]
            cr_beta = [daily_rets[coin].get(dd) for dd in beta_days]
            br_beta = [btc_rets.get(dd, 0.0) for dd in beta_days]
            cr_beta = [r for r in cr_beta if r is not None]
            if len(cr_win) < IDVOL_WIN // 2:
                continue
            beta    = _ols_beta(cr_beta, br_beta)
            btc_win = [btc_rets.get(dd, 0.0) for dd in win_days]
            residuals = [cr_win[i] - beta * btc_win[i]
                         for i in range(min(len(cr_win), len(btc_win)))]
            if len(residuals) < 4:
                continue
            idvol = _pstdev(residuals)
            factors.append((coin, beta, idvol))
        if len(factors) < 2 * K + 4:
            continue
        factors.sort(key=lambda x: x[1])
        n_f = len(factors)
        t1, t2 = n_f // 3, 2 * n_f // 3
        terciles = [factors[:t1], factors[t1:t2], factors[t2:]]
        k_per = max(1, K // 3)
        longs, shorts = [], []
        for terc in terciles:
            if len(terc) < 2:
                continue
            ts = sorted(terc, key=lambda x: x[2], reverse=True)
            longs.extend([c for c, _, _ in ts[:k_per]])
            shorts.extend([c for c, _, _ in ts[-k_per:]])
        if len(longs) < 2 or len(shorts) < 2:
            continue
        def fwd(coin):
            o = data[coin][d_entry][0]
            c = data[coin][d_exit][1]
            return (c - o) / o if o > 0 else 0.0
        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        stream.append((d, (lr - sr) - cost))
    return stream


# ─── Permutation test ────────────────────────────────────────────────────────

def permutation_test(stream, gate_dict, gate_key, use_true, n_perm=N_PERM, seed=RANDOM_SEED):
    """Permutation p-value: shuffle regime labels, re-compute gated Sharpe N times.

    gate_dict: day -> bool|None for gate_key (or plain dict day->bool|None)
    use_true: if True, test the "gate=True" leg; if False, test "gate=False" leg.
    Returns (observed_sharpe, p_value, n_eligible, perm_sharpes).

    p = fraction of permuted Sharpes >= observed Sharpe.
    Low p (<0.05) = regime label carries real information (not noise).
    """
    # Collect (index, return) pairs where the gate fires (use_true / ~use_true)
    eligible = []   # list of (stream_idx, ret)
    for i, (d, r) in enumerate(stream):
        g = gate_dict.get(d) if isinstance(next(iter(gate_dict)), str) else gate_dict.get(d)
        if g is True:
            eligible.append((i, r, True))
        elif g is False:
            eligible.append((i, r, False))
        # None -> skip

    if use_true:
        obs_rets = [r for _, r, g in eligible if g]
    else:
        obs_rets = [r for _, r, g in eligible if not g]

    obs_sh = sharpe(obs_rets)
    if len(obs_rets) < 6 or math.isnan(obs_sh):
        return obs_sh, float("nan"), len(obs_rets), []

    # Permutation: shuffle the True/False labels among eligible days, re-compute Sharpe
    rng = random.Random(seed)
    labels = [g for _, _, g in eligible]
    rets_all = [r for _, r, _ in eligible]

    perm_sharpes = []
    for _ in range(n_perm):
        shuffled = labels[:]
        rng.shuffle(shuffled)
        if use_true:
            pret = [rets_all[i] for i, g in enumerate(shuffled) if g]
        else:
            pret = [rets_all[i] for i, g in enumerate(shuffled) if not g]
        sh = sharpe(pret)
        if not math.isnan(sh):
            perm_sharpes.append(sh)

    if not perm_sharpes:
        return obs_sh, float("nan"), len(obs_rets), []

    p_val = sum(1 for s in perm_sharpes if s >= obs_sh) / len(perm_sharpes)
    return obs_sh, p_val, len(obs_rets), perm_sharpes


# ─── Reporting helpers ────────────────────────────────────────────────────────

def rep_stream(label, rets, indent=6):
    """Print stats for a return stream."""
    pad = " " * indent
    if not rets:
        print(f"{pad}{label:40s}  n=0  (no observations)")
        return
    n   = len(rets)
    mid = n // 2
    h1  = rets[:mid]
    h2  = rets[mid:]
    m   = _mean(rets) * 100
    m1  = _mean(h1) * 100 if h1 else float("nan")
    m2  = _mean(h2) * 100 if h2 else float("nan")
    sh  = sharpe(rets)
    sh1 = sharpe(h1)
    sh2 = sharpe(h2)
    mdd = max_dd(rets) * 100
    w   = sum(1 for r in rets if r > 0)
    robust  = m1 > 0 and m2 > 0
    rob_tag = "ROBUST" if robust else ("fragile" if (m1 > 0) != (m2 > 0) else "neg")
    ev_tag  = "  <<< +EV" if m > 0 and robust else ""
    print(f"{pad}{label:40s}  n={n:>4}  win={w/n*100:>3.0f}%  mean={m:>+6.2f}%  "
          f"OOS {m1:>+5.2f}/{m2:>+5.2f}  Sh={sh:>+5.2f}({sh1:>+5.2f}/{sh2:>+5.2f})  "
          f"maxDD={mdd:>5.2f}%  {rob_tag}{ev_tag}")


def print_gate_section(title, stream, gate_dict, true_label, false_label,
                       test_leg_true=None, book_label="BOOK"):
    """Full gate breakdown: unconditioned, two legs, permutation p for the best leg."""
    print(f"\n  ─── {title} ───")
    all_rets   = [r for _, r in stream]
    true_rets  = [r for d, r in stream if gate_dict.get(d) is True]
    false_rets = [r for d, r in stream if gate_dict.get(d) is False]
    none_rets  = [r for d, r in stream if gate_dict.get(d) is None]

    rep_stream("Unconditioned",           all_rets)
    rep_stream(true_label,                true_rets)
    rep_stream(false_label,               false_rets)
    if none_rets:
        rep_stream("No signal (skipped)",     none_rets)

    base_sh = sharpe(all_rets)
    # Determine which leg to run permutation test on (higher Sharpe leg)
    sh_true  = sharpe(true_rets)  if len(true_rets)  >= 6 else float("-inf")
    sh_false = sharpe(false_rets) if len(false_rets) >= 6 else float("-inf")
    if test_leg_true is None:
        use_true = sh_true >= sh_false
    else:
        use_true = test_leg_true
    best_rets  = true_rets  if use_true else false_rets
    best_label = true_label if use_true else false_label
    best_sh    = sh_true    if use_true else sh_false

    obs_sh, p_val, n_elig, perm_sharpes = permutation_test(
        stream, gate_dict, None, use_true
    )
    delta = best_sh - base_sh

    # OOS of best leg
    mid = len(best_rets) // 2
    h1b = _mean(best_rets[:mid]) * 100 if mid else float("nan")
    h2b = _mean(best_rets[mid:]) * 100 if (len(best_rets) - mid) else float("nan")
    oos_robust = h1b > 0 and h2b > 0

    if perm_sharpes:
        pct_5 = sorted(perm_sharpes)[int(0.95 * len(perm_sharpes))]
    else:
        pct_5 = float("nan")

    print(f"\n      Best leg: [{best_label}]")
    print(f"        Sharpe lift: {base_sh:>+.2f} -> {best_sh:>+.2f}  (Δ={delta:>+.2f})")
    print(f"        OOS halves: {h1b:>+.2f}% / {h2b:>+.2f}%  robust={oos_robust}")
    print(f"        maxDD gated: {max_dd(best_rets)*100:.2f}%  vs  {max_dd(all_rets)*100:.2f}% uncond.")
    print(f"        Permutation p={p_val:.3f} (n_perm={N_PERM})  "
          f"95th-pct perm Sharpe={pct_5:.2f}  obs={obs_sh:.2f}")

    # Verdict
    sig_tag = "p<0.05 (sig)" if (not math.isnan(p_val) and p_val < 0.05) else "p>=0.05 (NOT sig)"
    if oos_robust and delta > 0.5 and not math.isnan(p_val) and p_val < 0.05:
        verd = "USEFUL GATE — OOS-robust, Sharpe lift, permutation significant"
    elif oos_robust and delta > 0.0:
        verd = "MARGINAL — OOS-robust but small lift or not permutation-significant"
    else:
        verd = "NOISE — fails OOS robustness or no Sharpe lift"
    print(f"      >>> VERDICT [{title} | {book_label}]: {verd}  [{sig_tag}]")
    return base_sh, best_sh, best_label, oos_robust, p_val


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 100)
    print("# RICHER REGIME CLASSIFIERS AS SIZING GATES — edge_regime_states.py")
    print(f"# K={K}/leg | cost {COST_BPS:.0f}bps/name | lookahead-safe | OOS chronological split")
    print(f"# Permutation N={N_PERM} | seed={RANDOM_SEED}")
    print("=" * 100)

    data = load()
    n_coins  = len(data)
    all_days = sorted({d for oc in data.values() for d in oc})
    print(f"\n# {n_coins} coins | {len(all_days)} trading days ({all_days[0]} – {all_days[-1]})")

    for req in ["BTC"]:
        if req not in data:
            print(f"ERROR: {req} not in cache — cannot build vol/CUSUM signals. Aborting.")
            return

    # ── Build signals ───────────────────────────────────────────────────────
    print("\n# Building BTC daily returns...")
    btc_rets = build_btc_daily_rets(data, all_days)
    n_btc = sum(1 for v in btc_rets.values() if v is not None)
    print(f"  {n_btc} BTC return observations")

    print("\n# Building regime signals...")
    print(f"  (a) 2-state vol regime  (trailing vol window={VOL_WIN}d vs running median)...")
    vol_regime = build_vol_regime(all_days, btc_rets)
    vol_high_days = sum(1 for v in vol_regime.values() if v is True)
    vol_low_days  = sum(1 for v in vol_regime.values() if v is False)
    print(f"      HIGH-vol days: {vol_high_days}  LOW-vol days: {vol_low_days}  "
          f"(none: {len(all_days)-vol_high_days-vol_low_days})")

    print(f"  (b) CUSUM change-point  (vol window={VOL_WIN}d, baseline={CUSUM_WIN}d, "
          f"k={CUSUM_K}, H={CUSUM_H}, cooldown={COOLDOWN_DAYS}d)...")
    cusum_regime = build_cusum_regime(all_days, btc_rets)
    post_break_days = sum(1 for v in cusum_regime.values() if v is True)
    stable_days     = sum(1 for v in cusum_regime.values() if v is False)
    print(f"      Post-break days: {post_break_days}  Stable days: {stable_days}  "
          f"(none: {len(all_days)-post_break_days-stable_days})")

    print(f"  (c) Correlation regime  (CORR_WIN={CORR_WIN}d, same as V3 validated gate)...")
    corr_regime = build_corr_regime(data, all_days)
    corr_high_days = sum(1 for v in corr_regime.values() if v is True)
    corr_low_days  = sum(1 for v in corr_regime.values() if v is False)
    print(f"      HIGH-corr days: {corr_high_days}  LOW-corr days: {corr_low_days}  "
          f"(none: {len(all_days)-corr_high_days-corr_low_days})")

    # ── Build LS books ──────────────────────────────────────────────────────
    print(f"\n# Building momentum book (LB={MOM_LB}d, hold={MOM_HOLD}d)...")
    mom_stream = build_momentum_stream(data, all_days)
    print(f"  {len(mom_stream)} rebalance periods")

    print(f"# Building vol-dispersion book (hold={VDISP_HOLD}d, within-beta-tercile)...")
    vdisp_stream = build_vdisp_stream(data, all_days)
    print(f"  {len(vdisp_stream)} rebalance periods")

    # ── (c) Build combo signal: corr-regime AND vol-state ───────────────────
    # Combo: corr_low (False) AND vol_low (False) — the "sweet spot" hypothesis
    combo_low_corr_low_vol = {}
    for d in all_days:
        c = corr_regime.get(d)
        v = vol_regime.get(d)
        if c is None or v is None:
            combo_low_corr_low_vol[d] = None
        elif c is False and v is False:   # low corr AND low vol
            combo_low_corr_low_vol[d] = True
        elif c is True and v is True:     # high corr AND high vol
            combo_low_corr_low_vol[d] = False
        else:
            combo_low_corr_low_vol[d] = None   # mixed regime — skip

    # Alternative combo: any vol state within corr-regime
    # Tested below as (c1) pure corr vs (c2) corr+vol combo

    combo_stats_days = sum(1 for v in combo_low_corr_low_vol.values() if v is not None)
    combo_good_days  = sum(1 for v in combo_low_corr_low_vol.values() if v is True)
    combo_bad_days   = sum(1 for v in combo_low_corr_low_vol.values() if v is False)
    print(f"\n  Combo (low-corr AND low-vol): good={combo_good_days}  bad={combo_bad_days}  "
          f"mixed/none={len(all_days)-combo_stats_days}")

    # ─────────────────────────────────────────────────────────────────────────
    # RESULTS BY BOOK
    # ─────────────────────────────────────────────────────────────────────────

    summary = []  # (book, gate_name, base_sh, best_sh, best_label, oos_rob, p_val)

    for book_label, book_stream in [("MOMENTUM", mom_stream), ("VOL-DISPERSION", vdisp_stream)]:
        print(f"\n{'═' * 100}")
        print(f"# BOOK: {book_label}   (n={len(book_stream)} periods)")
        print(f"{'═' * 100}")

        all_rets  = [r for _, r in book_stream]
        uncond_sh = sharpe(all_rets)
        uncond_mdd = max_dd(all_rets) * 100
        mid_u = len(all_rets) // 2
        h1_u  = _mean(all_rets[:mid_u]) * 100 if mid_u else float("nan")
        h2_u  = _mean(all_rets[mid_u:]) * 100 if (len(all_rets) - mid_u) else float("nan")
        print(f"\n  UNCONDITIONED baseline:  Sh={uncond_sh:>+.2f}  "
              f"mean={_mean(all_rets)*100:>+.3f}%  OOS {h1_u:>+.2f}/{h2_u:>+.2f}%  "
              f"maxDD={uncond_mdd:.2f}%")

        # ── (a) 2-state vol regime ─────────────────────────────────────────
        # Hypothesis: momentum better in LOW vol; vol-disp better in HIGH vol
        # (same directional claim as original vol-regime gate in ALPHA-PLAN + V3 corr-gate logic)
        if book_label == "MOMENTUM":
            # LOW vol = better for momentum (validated direction)
            b, bsh, bl, rob, pv = print_gate_section(
                "(a) 2-STATE VOL REGIME",
                book_stream, vol_regime,
                "HIGH-vol state (trailing vol > median)",
                "LOW-vol state  (trailing vol <= median)",
                test_leg_true=False,   # test low-vol leg for momentum
                book_label=book_label
            )
        else:
            # HIGH vol = maybe better for vol-dispersion (dispersion itself higher)
            b, bsh, bl, rob, pv = print_gate_section(
                "(a) 2-STATE VOL REGIME",
                book_stream, vol_regime,
                "HIGH-vol state (trailing vol > median)",
                "LOW-vol state  (trailing vol <= median)",
                test_leg_true=True,    # test high-vol leg for vol-disp
                book_label=book_label
            )
        summary.append((book_label, "(a) vol-regime", b, bsh, bl, rob, pv))

        # Also show the OTHER leg for completeness
        alt_rets = ([r for d, r in book_stream if vol_regime.get(d) is True]
                    if book_label == "MOMENTUM"
                    else [r for d, r in book_stream if vol_regime.get(d) is False])
        alt_label = ("HIGH-vol (other leg)" if book_label == "MOMENTUM"
                     else "LOW-vol (other leg)")
        print(f"      Alt leg [{alt_label}]: ", end="")
        if alt_rets:
            mid_a = len(alt_rets) // 2
            a1 = _mean(alt_rets[:mid_a]) * 100 if mid_a else float("nan")
            a2 = _mean(alt_rets[mid_a:]) * 100 if (len(alt_rets) - mid_a) else float("nan")
            print(f"n={len(alt_rets)}  mean={_mean(alt_rets)*100:>+.2f}%  "
                  f"OOS {a1:>+.2f}/{a2:>+.2f}%  Sh={sharpe(alt_rets):>+.2f}")
        else:
            print("(no observations)")

        # ── (b) CUSUM change-point ─────────────────────────────────────────
        # Hypothesis: post_break=True → de-risk (both books weaker after a break)
        # So we test stable (False) leg as the "good" period
        b2, bsh2, bl2, rob2, pv2 = print_gate_section(
            "(b) CUSUM CHANGE-POINT",
            book_stream, cusum_regime,
            "Post-break period (de-risk hypothesis)",
            "Stable period    (no recent break)",
            test_leg_true=False,   # always test stable leg
            book_label=book_label
        )
        summary.append((book_label, "(b) CUSUM", b2, bsh2, bl2, rob2, pv2))

        # ── (c) Corr-regime ALONE (V3 baseline to beat) ───────────────────
        # For momentum: LOW-corr = good
        # For vol-disp: HIGH-corr = good (V3 finding)
        corr_test_true = (book_label == "VOL-DISPERSION")   # True=high-corr for vd, False for mom
        b3, bsh3, bl3, rob3, pv3 = print_gate_section(
            "(c1) CORR-REGIME ALONE (V3 baseline)",
            book_stream, corr_regime,
            "HIGH-corr (flat-mom / good-vd hypothesis)",
            "LOW-corr  (good-mom / flat-vd hypothesis)",
            test_leg_true=corr_test_true,
            book_label=book_label
        )
        summary.append((book_label, "(c1) corr-alone", b3, bsh3, bl3, rob3, pv3))

        # ── (c2) Corr + Vol combo ─────────────────────────────────────────
        # Test the "good" leg: low-corr AND low-vol for momentum; high-corr AND high-vol for vol-disp
        if book_label == "MOMENTUM":
            combo_test = combo_low_corr_low_vol  # True = low-corr+low-vol
            combo_true_lbl  = "LOW-corr AND LOW-vol (sweet spot)"
            combo_false_lbl = "HIGH-corr AND HIGH-vol (bad combo)"
            combo_leg_true  = True   # test the sweet spot
        else:
            # For vol-disp: high-corr AND high-vol
            combo_vd = {}
            for d in all_days:
                c = corr_regime.get(d)
                v = vol_regime.get(d)
                if c is None or v is None:
                    combo_vd[d] = None
                elif c is True and v is True:
                    combo_vd[d] = True
                elif c is False and v is False:
                    combo_vd[d] = False
                else:
                    combo_vd[d] = None
            combo_test = combo_vd
            combo_true_lbl  = "HIGH-corr AND HIGH-vol (sweet spot vd)"
            combo_false_lbl = "LOW-corr AND LOW-vol (bad combo vd)"
            combo_leg_true  = True

        b4, bsh4, bl4, rob4, pv4 = print_gate_section(
            "(c2) CORR + VOL COMBO",
            book_stream, combo_test,
            combo_true_lbl,
            combo_false_lbl,
            test_leg_true=combo_leg_true,
            book_label=book_label
        )
        summary.append((book_label, "(c2) corr+vol combo", b4, bsh4, bl4, rob4, pv4))

    # ─── Sensitivity sweep: vol regime window ────────────────────────────────
    print(f"\n{'═' * 100}")
    print("# SENSITIVITY SWEEP: vol-regime window vs momentum book")
    print(f"{'═' * 100}")
    print(f"  (testing LOW-vol leg for momentum; VOL_WIN in {{10, 15, 20, 30}})")
    print(f"  {'VOL_WIN':>8}  {'n_low':>6}  {'Sh_uncond':>10}  {'Sh_low':>8}  "
          f"{'OOS h1':>8}  {'OOS h2':>8}  {'p_val':>8}  {'verdict'}")
    print(f"  {'-'*8}  {'-'*6}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*20}")

    base_sh_mom = sharpe([r for _, r in mom_stream])
    for vw in [10, 15, 20, 30]:
        vr_tmp = build_vol_regime(all_days, btc_rets)  # uses VOL_WIN (can't parametrize without refactor)
        # Quick recompute for this window
        vr_w = {}
        running_v = []
        for i, day in enumerate(all_days):
            if i < vw:
                vr_w[day] = None
                continue
            win_d = all_days[i - vw + 1: i + 1]
            wr    = [btc_rets.get(d) for d in win_d]
            wr    = [r for r in wr if r is not None]
            if len(wr) < vw // 2:
                vr_w[day] = None
                continue
            tv = _pstdev(wr)
            running_v.append(tv)
            med = statistics.median(running_v)
            vr_w[day] = tv > med

        low_rets = [r for d, r in mom_stream if vr_w.get(d) is False]
        low_sh   = sharpe(low_rets)
        mid_l    = len(low_rets) // 2
        h1_l     = _mean(low_rets[:mid_l]) * 100 if mid_l else float("nan")
        h2_l     = _mean(low_rets[mid_l:]) * 100 if (len(low_rets) - mid_l) else float("nan")
        _, pv_l, _, _ = permutation_test(mom_stream, vr_w, None, False)
        verd_s   = "ok" if (h1_l > 0 and h2_l > 0 and low_sh > base_sh_mom) else "no"
        print(f"  {vw:>8}  {len(low_rets):>6}  {base_sh_mom:>+10.2f}  {low_sh:>+8.2f}  "
              f"{h1_l:>+8.2f}%  {h2_l:>+8.2f}%  {pv_l:>8.3f}  {verd_s}")

    # ─── CUSUM sensitivity: H threshold ─────────────────────────────────────
    print(f"\n{'═' * 100}")
    print("# SENSITIVITY SWEEP: CUSUM H-threshold vs momentum book (stable leg)")
    print(f"{'═' * 100}")
    print(f"  {'H_thresh':>8}  {'n_stable':>8}  {'n_break':>8}  {'Sh_stable':>10}  "
          f"{'OOS h1':>8}  {'OOS h2':>8}  {'p_val':>8}  {'verdict'}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*20}")

    for H in [2.0, 3.0, 4.0, 5.0]:
        # Recompute CUSUM with different H
        cusum_h = {}
        vol_hist2 = []
        S2 = 0.0
        cooldown2 = 0
        trail_v2 = {}
        for i, day in enumerate(all_days):
            if i < VOL_WIN:
                continue
            win_d2 = all_days[i - VOL_WIN + 1: i + 1]
            wr2    = [btc_rets.get(d) for d in win_d2]
            wr2    = [r for r in wr2 if r is not None]
            if len(wr2) < VOL_WIN // 2:
                continue
            trail_v2[day] = _pstdev(wr2)

        vol_hist2 = []
        S2 = 0.0
        cooldown2 = 0
        for day in all_days:
            if day not in trail_v2:
                cusum_h[day] = None
                continue
            v2 = trail_v2[day]
            vol_hist2.append(v2)
            if len(vol_hist2) < CUSUM_WIN:
                cusum_h[day] = None
                S2 = 0.0
                continue
            mu2    = _mean(vol_hist2)
            sigma2 = _pstdev(vol_hist2)
            if sigma2 <= 0:
                cusum_h[day] = False
                continue
            thr2 = H * sigma2
            S2 = max(0.0, S2 + (v2 - mu2 - CUSUM_K * sigma2))
            if S2 > thr2:
                cooldown2 = COOLDOWN_DAYS
                S2 = 0.0
                cusum_h[day] = True
            elif cooldown2 > 0:
                cooldown2 -= 1
                cusum_h[day] = True
            else:
                cusum_h[day] = False

        stable_rets2 = [r for d, r in mom_stream if cusum_h.get(d) is False]
        break_rets2  = [r for d, r in mom_stream if cusum_h.get(d) is True]
        stab_sh2     = sharpe(stable_rets2)
        mid_s2       = len(stable_rets2) // 2
        h1_s2        = _mean(stable_rets2[:mid_s2]) * 100 if mid_s2 else float("nan")
        h2_s2        = _mean(stable_rets2[mid_s2:]) * 100 if (len(stable_rets2) - mid_s2) else float("nan")
        _, pv_s2, _, _ = permutation_test(mom_stream, cusum_h, None, False)
        verd_c = "ok" if (h1_s2 > 0 and h2_s2 > 0 and stab_sh2 > base_sh_mom) else "no"
        print(f"  {H:>8.1f}  {len(stable_rets2):>8}  {len(break_rets2):>8}  {stab_sh2:>+10.2f}  "
              f"{h1_s2:>+8.2f}%  {h2_s2:>+8.2f}%  {pv_s2:>8.3f}  {verd_c}")

    # ─── Summary table ───────────────────────────────────────────────────────
    print(f"\n{'═' * 100}")
    print("# SUMMARY TABLE — all gates vs unconditioned (both books)")
    print(f"{'═' * 100}")
    print(f"  {'Book':<16} {'Gate':<24} {'Base Sh':>8} {'Best Sh':>8} {'Best leg':<30} "
          f"{'OOS-rob':>8} {'perm-p':>8} {'Verdict'}")
    print(f"  {'-'*16} {'-'*24} {'-'*8} {'-'*8} {'-'*30} {'-'*8} {'-'*8} {'-'*25}")
    for book, gate, base, bsh, bl, rob, pv in summary:
        delta = bsh - base
        if rob and delta > 0.5 and not math.isnan(pv) and pv < 0.05:
            vt = "USEFUL"
        elif rob and delta > 0.0:
            vt = "marginal"
        else:
            vt = "NOISE"
        pv_s = f"{pv:.3f}" if not math.isnan(pv) else " N/A"
        print(f"  {book:<16} {gate:<24} {base:>+8.2f} {bsh:>+8.2f} {bl[:30]:<30} "
              f"{'yes' if rob else 'no':>8} {pv_s:>8} {vt}")

    # ─── Final verdicts ──────────────────────────────────────────────────────
    print(f"\n{'═' * 100}")
    print("# FINAL VERDICTS")
    print(f"{'═' * 100}")
    print("""
  Methodology bar (all MANDATORY to call "USEFUL GATE"):
    1. Best-leg Sharpe > base unconditioned Sharpe
    2. BOTH OOS halves of best-leg positive (chronological split)
    3. Permutation p < 0.05 (regime label adds real signal, not noise)
    4. HIGH BAR: 4 prior regime tests refuted; only corr-regime (V3) survived

  (a) 2-STATE VOL REGIME (trailing-vol-threshold proxy for HMM):
      Does conditioning on inferred HIGH/LOW vol state beat unconditioned?
      Directional priors: momentum -> LOW-vol better; vol-disp -> HIGH-vol better.
      Verdict: see table above.

  (b) CUSUM CHANGE-POINT on BTC vol:
      Do the books behave differently right after a detected structural break?
      De-risk hypothesis: stable periods outperform post-break periods.
      Verdict: see table above.

  (c1) CORR-REGIME ALONE (V3 replication, baseline to beat):
      The ONLY previously validated gate. Reproduced here for direct comparison.
      Momentum: LOW-corr; vol-disp: HIGH-corr.

  (c2) CORR + VOL COMBO:
      Does combining the corr-regime gate WITH the vol-state gate improve on
      corr-regime alone? Restricts eligible periods further — fewer n, higher bar.

  KEY QUESTION: does ANYTHING here beat the corr-regime gate alone?
  See table row "(c1) corr-alone" as the benchmark for (a), (b), (c2).
""")


if __name__ == "__main__":
    main()
