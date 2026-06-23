#!/usr/bin/env python3
"""Cross-sectional RISK FACTOR sweep — beta-neutralized from the start.

Assignment: 4 new risk factors, each tested both directions, ALL evaluated with:
  - Raw L-S spread (unneutralized, for comparison)
  - Beta-neutralized L-S (scale-short method, matching edge_beta_neutral_factor.py)
  - Within-beta-tercile L-S (gold standard neutralization, BY CONSTRUCTION beta-neutral)
  - Down-regime (worst Q1 BTC-forward) subset
  - Net-beta of raw spread
  - Pearson correlation to the validated momentum stream
  - Pearson correlation to the vol-dispersion (idio-vol) stream

Factors:
  (a) KURTOSIS: trailing 60d return kurtosis — long LOW-kurt / short HIGH (and reverse)
  (b) DOWNSIDE DEVIATION / SORTINO: trailing downside semi-dev; Sortino = mean/downside_dev
  (c) TRAILING MAX-DRAWDOWN: 60d max-DD; long LOW-MDD / short HIGH (and reverse)
  (d) BETA-ROTATION: trailing 30d OLS beta vs BTC; long LOW-beta / short HIGH (and reverse)

Methodology bar:
  - Lookahead-safe: signal ≤ t, enter t+1 open, exit t+1+HOLD close
  - Cost-aware: 10bps/name round-trip
  - Survivorship-free: whole liquid universe (top-50 by volume, no HIP-3, no spot/index)
  - OOS-robust: both halves (H1, H2) of the trade stream must both be positive

Run: BT_CACHE_ONLY=1 python3 scripts/edge_risk_factors.py
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── constants ─────────────────────────────────────────────────────────────────
VOL_FLOOR = 5e6
TOPN = 50
K = 8               # names per leg
HOLD = 10           # holding period (days) — matches validated xs-momentum
COST = 10.0 / 1e4  # 10 bps per name round-trip
BURN_IN = 70        # minimum warm-up bars before first rebalance

# Factor windows
KURT_WIN = 60       # (a) kurtosis trailing window
DD_WIN = 60         # (b) downside deviation trailing window
MDD_WIN = 60        # (c) max-drawdown trailing window
BETA_WIN = 30       # (d) beta-rotation / beta-neutralization window

# Baseline streams — recomputed inside main, references below
MOMENTUM_LB = 7     # LB for momentum baseline (matches ALPHA-PLAN validated LB=7/hold=10)
IDVOL_WIN = 30      # idio-vol window (matches edge_beta_neutral_factor.py)


# ─── pure-python stat helpers ──────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def _var(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

def _std(xs):
    v = _var(xs)
    return math.sqrt(v) if v > 0 else 0.0

def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 10:
        return float("nan")
    xs, ys = xs[-n:], ys[-n:]
    mx, my = _mean(xs), _mean(ys)
    sx, sy = _std(xs), _std(ys)
    if sx <= 0 or sy <= 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / ((n - 1) * sx * sy)

def _ols_beta(cr, br):
    """OLS beta of coin returns on BTC returns. 1.0 if degenerate."""
    n = min(len(cr), len(br))
    if n < 8:
        return 1.0
    cr, br = cr[-n:], br[-n:]
    mb = _mean(br)
    vb = sum((x - mb) ** 2 for x in br)
    if vb <= 0:
        return 1.0
    mc = _mean(cr)
    return sum((a - mc) * (b - mb) for a, b in zip(cr, br)) / vb


# ─── data ──────────────────────────────────────────────────────────────────────
def load():
    """Top-50 liquid perps (no HIP-3, no spot/index/colon coins) from cache."""
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
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ─── daily return helpers ──────────────────────────────────────────────────────
def _get_rets(coin_data, days):
    """Daily returns from closes on the given ordered day list."""
    closes = [coin_data[d]["c"] for d in days if d in coin_data and coin_data[d]["c"] > 0]
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]


# ─── factor score functions ────────────────────────────────────────────────────

def _score_kurtosis(coin_data, btc_data, win_days):
    """Excess kurtosis of trailing 60d returns (fat-tailedness).
    Low kurtosis = thin-tailed / steady; high kurtosis = fat-tailed / explosive."""
    wd = win_days[-KURT_WIN:]
    rets = _get_rets(coin_data, wd)
    n = len(rets)
    if n < 15:
        return None
    m = _mean(rets)
    s = _std(rets)
    if s <= 0:
        return None
    # excess kurtosis (Fisher): kurt - 3
    k4 = sum((r - m) ** 4 for r in rets) / n / (s ** 4)
    return k4 - 3.0   # excess kurtosis (normal=0)


def _score_downside_dev(coin_data, btc_data, win_days):
    """Downside semi-deviation (returns below 0).
    Low = small downside risk; high = fat lower tail."""
    wd = win_days[-DD_WIN:]
    rets = _get_rets(coin_data, wd)
    down = [r for r in rets if r < 0.0]
    if len(down) < 5:
        return None
    # semi-variance: mean of squared negative deviations from zero (Sortino denominator)
    sv = sum(r ** 2 for r in down) / len(down)
    return math.sqrt(sv)  # downside deviation


def _score_sortino(coin_data, btc_data, win_days):
    """Sortino ratio = mean_return / downside_dev (trailing DD_WIN days).
    High Sortino = better risk-adj upside.  Low = poor return relative to downside risk."""
    wd = win_days[-DD_WIN:]
    rets = _get_rets(coin_data, wd)
    if len(rets) < 10:
        return None
    m = _mean(rets)
    down = [r for r in rets if r < 0.0]
    if len(down) < 4:
        # no downside = perfect sortino → rank very high or None
        return None
    sv = sum(r ** 2 for r in down) / len(down)
    dd = math.sqrt(sv)
    if dd <= 0:
        return None
    return m / dd


def _score_max_drawdown(coin_data, btc_data, win_days):
    """Trailing MDD_WIN-day max-drawdown (as a negative fraction, larger = worse DD).
    We return the drawdown magnitude as a POSITIVE number → high score = big drawdown."""
    wd = win_days[-MDD_WIN:]
    closes = [coin_data[d]["c"] for d in wd if d in coin_data and coin_data[d]["c"] > 0]
    if len(closes) < 10:
        return None
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return mdd   # positive; 0=no DD, 1=wiped out


def _score_total_beta(coin_data, btc_data, win_days):
    """Trailing BETA_WIN-day OLS beta vs BTC.
    Low beta = more defensive; high beta = more aggressive."""
    wd = win_days[-BETA_WIN:]
    cr = _get_rets(coin_data, wd)
    br = _get_rets(btc_data, wd)
    if len(cr) < 8 or len(br) < 8:
        return None
    return _ols_beta(cr, br)


# ─── per-coin beta at signal time (for beta-neutralization) ───────────────────
def _coin_beta_at_t(coin_data, btc_data, win_days):
    """30d trailing beta for beta-neutralization. Same BETA_WIN as factor (d)."""
    cr = _get_rets(coin_data, win_days[-BETA_WIN:])
    br = _get_rets(btc_data, win_days[-BETA_WIN:])
    if len(cr) < 8 or len(br) < 8:
        return 1.0
    return _ols_beta(cr, br)


# ─── idio-vol score for the vol-dispersion baseline stream ────────────────────
def _score_idio_vol(coin_data, btc_data, win_days):
    """Residual (BTC-neutral) volatility. Matches edge_beta_neutral_factor.py."""
    wd = win_days[-IDVOL_WIN:]
    cr = _get_rets(coin_data, wd)
    br = _get_rets(btc_data, wd)
    if len(cr) < 10 or len(br) < 10:
        return None
    n = min(len(cr), len(br))
    cr, br = cr[-n:], br[-n:]
    beta = _ols_beta(cr, br)
    residuals = [c - beta * b for c, b in zip(cr, br)]
    return _std(residuals)


# ─── core backtest engine with beta infrastructure ─────────────────────────────
def run_factor_full(data, btc_data, score_fn, higher_is_long=True):
    """
    Run one factor direction. Returns a list of per-rebalance records:
      ls       : raw long-short return (cost-adjusted)
      bn       : beta-neutralized LS (scale-short method)
      btc_fwd  : BTC forward return over same hold
      net_beta : avg-long-beta minus avg-short-beta of raw book
    """
    all_days = sorted({d for cd in data.values() for d in cd})
    records = []

    for t in range(BURN_IN, len(all_days) - HOLD - 1):
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70): t + 1]

        ranked = []
        for coin, cd in data.items():
            score = score_fn(cd, btc_data, win_days)
            if score is None:
                continue
            if d_entry not in cd or d_exit not in cd:
                continue
            beta = _coin_beta_at_t(cd, btc_data, win_days)
            ranked.append((coin, score, beta))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=higher_is_long)
        longs = ranked[:K]
        shorts = ranked[-K:]

        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c = data[coin][d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c, _, _ in longs])
        sr = _mean([fwd(c) for c, _, _ in shorts])
        ls_ret = (lr - sr) - 2 * COST

        avg_long_beta = _mean([b for _, _, b in longs])
        avg_short_beta = _mean([b for _, _, b in shorts])
        net_beta = avg_long_beta - avg_short_beta

        # Beta-neutralized: scale short leg by (avg_long_beta / avg_short_beta)
        scale = avg_long_beta / avg_short_beta if avg_short_beta > 0.01 else 1.0
        bn_ret = (lr - scale * sr) - (1 + scale) * COST / 2

        # BTC forward return for down-regime subsets
        if d_entry in btc_data and d_exit in btc_data:
            o_b = btc_data[d_entry]["o"]
            c_b = btc_data[d_exit]["c"]
            btc_fwd = (c_b - o_b) / o_b if o_b > 0 else None
        else:
            btc_fwd = None

        records.append({
            "ls": ls_ret,
            "bn": bn_ret,
            "btc_fwd": btc_fwd,
            "net_beta": net_beta,
        })

    return records


def run_within_tercile(data, btc_data, score_fn, higher_is_long=True):
    """
    Gold-standard beta-neutralization: sort universe into beta terciles,
    apply top/bottom within each tercile, average. By construction beta-neutral.
    Returns list of per-rebalance L-S returns.
    """
    all_days = sorted({d for cd in data.values() for d in cd})
    ls_rets = []

    for t in range(BURN_IN, len(all_days) - HOLD - 1):
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70): t + 1]

        ranked = []
        for coin, cd in data.items():
            score = score_fn(cd, btc_data, win_days)
            if score is None:
                continue
            if d_entry not in cd or d_exit not in cd:
                continue
            beta = _coin_beta_at_t(cd, btc_data, win_days)
            ranked.append((coin, score, beta))

        if len(ranked) < 9:
            continue

        # Split into beta terciles
        by_beta = sorted(ranked, key=lambda x: x[2])
        n_per_t = len(by_beta) // 3
        if n_per_t < 3:
            continue

        tercile_spreads = []
        for ti in range(3):
            start = ti * n_per_t
            end = start + n_per_t if ti < 2 else len(by_beta)
            tc = sorted(by_beta[start:end], key=lambda x: x[1], reverse=higher_is_long)
            k_t = max(1, min(3, len(tc) // 3))
            longs_t = [c for c, _, _ in tc[:k_t]]
            shorts_t = [c for c, _, _ in tc[-k_t:]]
            if not longs_t or not shorts_t:
                continue

            def fwd(coin):
                o = data[coin][d_entry]["o"]
                c = data[coin][d_exit]["c"]
                return (c - o) / o if o > 0 else 0.0

            lr_t = _mean([fwd(c) for c in longs_t])
            sr_t = _mean([fwd(c) for c in shorts_t])
            tercile_spreads.append((lr_t - sr_t) - 2 * COST)

        if tercile_spreads:
            ls_rets.append(_mean(tercile_spreads))

    return ls_rets


# ─── baseline streams ──────────────────────────────────────────────────────────
def run_momentum_stream(data):
    """Total-return xs-momentum (LB=7, hold=10) for correlation baseline."""
    all_days = sorted({d for cd in data.values() for d in cd})
    ls_rets = []
    for t in range(MOMENTUM_LB + 5, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_lb = all_days[t - MOMENTUM_LB]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        ranked = []
        for coin, cd in data.items():
            if d in cd and d_lb in cd and d_entry in cd and d_exit in cd and cd[d_lb]["c"] > 0:
                score = cd[d]["c"] / cd[d_lb]["c"] - 1.0
                ranked.append((coin, score))
        if len(ranked) < 2 * K + 4:
            continue
        ranked.sort(key=lambda x: x[1], reverse=True)
        longs = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]
        lr = _mean([(data[c][d_exit]["c"] - data[c][d_entry]["o"]) / data[c][d_entry]["o"]
                    for c in longs if data[c][d_entry]["o"] > 0])
        sr = _mean([(data[c][d_exit]["c"] - data[c][d_entry]["o"]) / data[c][d_entry]["o"]
                    for c in shorts if data[c][d_entry]["o"] > 0])
        ls_rets.append((lr - sr) - 2 * COST)
    return ls_rets


def run_idvol_stream(data, btc_data):
    """HIGH-idio-vol L-S (matches edge_beta_neutral_factor.py, higher_is_long=True) for vol-disp correlation."""
    def score_fn(cd, btcd, win_days):
        return _score_idio_vol(cd, btcd, win_days)
    records = run_factor_full(data, btc_data, score_fn, higher_is_long=True)
    return [r["ls"] for r in records]


# ─── reporting ─────────────────────────────────────────────────────────────────
def _oos_str(arr):
    if not arr:
        return "n/a"
    mid = len(arr) // 2
    h1 = _mean(arr[:mid]) * 100
    h2 = _mean(arr[mid:]) * 100
    robust = h1 > 0 and h2 > 0
    return f"H1={h1:>+5.2f}% H2={h2:>+5.2f}%  {'ROBUST' if robust else 'FAILS-OOS'}"


def _win_pct(arr):
    if not arr:
        return 0.0
    return 100.0 * sum(1 for r in arr if r > 0) / len(arr)


def analyse_factor(name, records_fwd, records_rev,
                   wt_fwd, wt_rev,
                   mom_stream, idvol_stream,
                   label_fwd="long LOW", label_rev="long HIGH"):
    """
    Full analysis for one factor (both directions).
    records_fwd / records_rev: output of run_factor_full.
    wt_fwd / wt_rev: output of run_within_tercile.
    """
    print(f"\n{'═'*72}")
    print(f"  FACTOR: {name}")
    print(f"{'═'*72}")

    def analyse_dir(label, records, wt_arr):
        if not records:
            print(f"  [{label}] no data")
            return None

        ls_arr = [r["ls"] for r in records]
        bn_arr = [r["bn"] for r in records]
        nb_arr = [r["net_beta"] for r in records]

        avg_nb = _mean(nb_arr)
        ls_mean = _mean(ls_arr) * 100
        bn_mean = _mean(bn_arr) * 100

        # OOS
        mid = len(ls_arr) // 2
        ls_h1 = _mean(ls_arr[:mid]) * 100;  ls_h2 = _mean(ls_arr[mid:]) * 100
        bn_h1 = _mean(bn_arr[:mid]) * 100;  bn_h2 = _mean(bn_arr[mid:]) * 100
        wt_h1 = (_mean(wt_arr[:len(wt_arr)//2]) * 100) if wt_arr else float("nan")
        wt_h2 = (_mean(wt_arr[len(wt_arr)//2:]) * 100) if wt_arr else float("nan")
        wt_mean = _mean(wt_arr) * 100 if wt_arr else float("nan")

        ls_robust = ls_h1 > 0 and ls_h2 > 0
        bn_robust = bn_h1 > 0 and bn_h2 > 0
        wt_robust = (not math.isnan(wt_h1)) and wt_h1 > 0 and wt_h2 > 0

        # Down-regime (worst Q1 BTC-forward)
        with_btc = [(r["btc_fwd"], r) for r in records if r["btc_fwd"] is not None]
        if with_btc:
            with_btc.sort(key=lambda x: x[0])
            q25 = len(with_btc) // 4
            down_recs = [r for _, r in with_btc[:q25]]
            down_ls_mean = _mean([r["ls"] for r in down_recs]) * 100
            down_bn_mean = _mean([r["bn"] for r in down_recs]) * 100
            btc_thr = with_btc[q25][0] * 100 if q25 < len(with_btc) else float("nan")
        else:
            down_ls_mean = down_bn_mean = btc_thr = float("nan")

        # Correlations to baseline streams (time-align: use trailing n of mom/idvol)
        corr_mom = _pearson(ls_arr, mom_stream)
        corr_idvol = _pearson(ls_arr, idvol_stream)

        print(f"\n  Direction: {label} (K={K}/leg, hold={HOLD}d, cost={COST*1e4:.0f}bps)")
        print(f"  {'Metric':<32} {'Raw L-S':>10}  {'BN (scale)':>10}  {'BN (tercile)':>12}")
        print(f"  {'':-<66}")
        print(f"  {'n (rebalances)':<32} {len(ls_arr):>10}")
        print(f"  {'Mean return/rebal':<32} {ls_mean:>+9.2f}%  {bn_mean:>+9.2f}%  {wt_mean:>+11.2f}%")
        print(f"  {'Win rate':<32} {_win_pct(ls_arr):>9.1f}%  {_win_pct(bn_arr):>9.1f}%  {_win_pct(wt_arr):>11.1f}%")
        print(f"  {'OOS H1':<32} {ls_h1:>+9.2f}%  {bn_h1:>+9.2f}%  {wt_h1:>+11.2f}%")
        print(f"  {'OOS H2':<32} {ls_h2:>+9.2f}%  {bn_h2:>+9.2f}%  {wt_h2:>+11.2f}%")
        print(f"  {'OOS robust?':<32} {'ROBUST' if ls_robust else 'FAILS':>10}  {'ROBUST' if bn_robust else 'FAILS':>10}  {'ROBUST' if wt_robust else 'FAILS':>12}")
        print(f"  {'Net spread beta':<32} {avg_nb:>+10.3f}")
        print(f"  {'Down-regime raw mean':<32} {down_ls_mean:>+9.2f}%  (BTC<{btc_thr:>+.2f}%)")
        print(f"  {'Down-regime BN mean':<32} {down_bn_mean:>+9.2f}%")
        print(f"  {'Corr to momentum':<32} {corr_mom:>+10.3f}")
        print(f"  {'Corr to vol-dispersion (idvol)':<32} {corr_idvol:>+10.3f}")

        # Verdict
        # Use within-tercile as the arbiter (gold standard; scale-short as secondary)
        if wt_mean > 0 and wt_robust:
            if abs(corr_mom) > 0.65:
                verdict = "REDUNDANT — within-tercile +EV but high momentum correlation"
            elif abs(corr_idvol) > 0.65:
                verdict = "REDUNDANT — within-tercile +EV but high vol-dispersion correlation"
            else:
                verdict = "GENUINE +EV beta-neutral"
        elif bn_mean > 0 and bn_robust:
            if wt_mean <= 0:
                verdict = "MARGINAL — BN(scale) robust but within-tercile fails"
            else:
                verdict = "CANDIDATE — scale-BN robust; tercile borderline"
        elif ls_mean > 0 and ls_robust and avg_nb > 0.15:
            verdict = "BETA BET — raw +EV disappears on beta-neutralization"
        else:
            verdict = "REFUTED — not +EV after beta-neutralization"

        print(f"\n  *** VERDICT: {verdict} ***")
        print(f"      Net-beta={avg_nb:+.3f} | Raw={ls_mean:>+5.2f}% | BN(scale)={bn_mean:>+5.2f}% "
              f"| BN(tercile)={wt_mean:>+5.2f}%")

        return {
            "label": label,
            "n": len(ls_arr),
            "raw_mean": ls_mean, "raw_h1": ls_h1, "raw_h2": ls_h2, "raw_robust": ls_robust,
            "bn_scale_mean": bn_mean, "bn_scale_h1": bn_h1, "bn_scale_h2": bn_h2, "bn_scale_robust": bn_robust,
            "wt_mean": wt_mean, "wt_h1": wt_h1, "wt_h2": wt_h2, "wt_robust": wt_robust,
            "net_beta": avg_nb,
            "down_raw": down_ls_mean, "down_bn": down_bn_mean,
            "corr_mom": corr_mom, "corr_idvol": corr_idvol,
            "verdict": verdict,
        }

    r_fwd = analyse_dir(label_fwd, records_fwd, wt_fwd)
    r_rev = analyse_dir(label_rev, records_rev, wt_rev)
    return r_fwd, r_rev


# ─── main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print("  RISK FACTOR SWEEP — beta-neutralized cross-sectional (K=8, hold=10d)")
    print(f"  Factors: kurtosis / downside-dev / max-drawdown / beta-rotation")
    print(f"  K={K}/leg | hold={HOLD}d | cost={COST*1e4:.0f}bps/name | BT_CACHE_ONLY safe")
    print("=" * 72)

    data = load()
    print(f"  {len(data)} coins loaded\n")

    btc_data = data.get("BTC")
    if btc_data is None:
        print("ERROR: BTC not in cache — cannot compute betas / idio-vol.")
        sys.exit(1)

    print("  Computing baseline streams (momentum + vol-dispersion)…", flush=True)
    mom_stream = run_momentum_stream(data)
    idvol_stream = run_idvol_stream(data, btc_data)
    print(f"  Momentum baseline  : n={len(mom_stream)}, mean={_mean(mom_stream)*100:+.2f}%")
    print(f"  IdioVol baseline   : n={len(idvol_stream)}, mean={_mean(idvol_stream)*100:+.2f}%")

    results = {}   # factor_name -> (r_fwd, r_rev)

    # ───────────────────────────────────────────────────────────────────────────
    # (a) KURTOSIS
    # ───────────────────────────────────────────────────────────────────────────
    print("\n\n>>> (a) KURTOSIS — trailing 60d excess kurtosis", flush=True)
    print("    Low kurtosis = thin-tailed / stable; High = fat-tailed / explosive.")
    print("    Classic risk hypothesis: long low-kurt (stable) / short high-kurt (explosive).")
    print("    Crypto flip candidate: high-kurt coins are lottery coins — may OUTPERFORM in bull tape.")

    recs_kurt_low = run_factor_full(data, btc_data, _score_kurtosis, higher_is_long=False)
    recs_kurt_hi  = run_factor_full(data, btc_data, _score_kurtosis, higher_is_long=True)
    wt_kurt_low   = run_within_tercile(data, btc_data, _score_kurtosis, higher_is_long=False)
    wt_kurt_hi    = run_within_tercile(data, btc_data, _score_kurtosis, higher_is_long=True)

    results["kurtosis"] = analyse_factor(
        "(a) KURTOSIS (60d excess kurtosis)",
        recs_kurt_low, recs_kurt_hi,
        wt_kurt_low, wt_kurt_hi,
        mom_stream, idvol_stream,
        label_fwd="long LOW-kurt / short HIGH-kurt (classic)",
        label_rev="long HIGH-kurt / short LOW-kurt (crypto flip)",
    )

    # ───────────────────────────────────────────────────────────────────────────
    # (b) DOWNSIDE DEVIATION / SORTINO
    # ───────────────────────────────────────────────────────────────────────────
    print("\n\n>>> (b) DOWNSIDE DEVIATION — trailing 60d semi-deviation (below-zero)", flush=True)
    print("    Low downside-dev = small loss magnitude; High = big loss tails.")
    print("    Classic: long low-DD / short high-DD (risk-managed picks).")

    recs_dd_low = run_factor_full(data, btc_data, _score_downside_dev, higher_is_long=False)
    recs_dd_hi  = run_factor_full(data, btc_data, _score_downside_dev, higher_is_long=True)
    wt_dd_low   = run_within_tercile(data, btc_data, _score_downside_dev, higher_is_long=False)
    wt_dd_hi    = run_within_tercile(data, btc_data, _score_downside_dev, higher_is_long=True)

    results["downside_dev"] = analyse_factor(
        "(b) DOWNSIDE DEVIATION (60d semi-dev, returns below 0)",
        recs_dd_low, recs_dd_hi,
        wt_dd_low, wt_dd_hi,
        mom_stream, idvol_stream,
        label_fwd="long LOW-DD / short HIGH-DD (classic risk-avoid)",
        label_rev="long HIGH-DD / short LOW-DD (crypto flip)",
    )

    print("\n\n>>> (b-ii) SORTINO RATIO — trailing 60d (mean / downside-dev)", flush=True)
    print("    High Sortino = strong mean with small downside; Low = poor or loss-heavy.")
    print("    Hypothesis: high-Sortino coins reward; low-Sortino coins punished.")

    recs_so_hi  = run_factor_full(data, btc_data, _score_sortino, higher_is_long=True)
    recs_so_low = run_factor_full(data, btc_data, _score_sortino, higher_is_long=False)
    wt_so_hi    = run_within_tercile(data, btc_data, _score_sortino, higher_is_long=True)
    wt_so_low   = run_within_tercile(data, btc_data, _score_sortino, higher_is_long=False)

    results["sortino"] = analyse_factor(
        "(b-ii) SORTINO RATIO (60d, mean/downside-dev)",
        recs_so_hi, recs_so_low,
        wt_so_hi, wt_so_low,
        mom_stream, idvol_stream,
        label_fwd="long HIGH-Sortino / short LOW-Sortino (reward quality)",
        label_rev="long LOW-Sortino / short HIGH-Sortino (reversal)",
    )

    # ───────────────────────────────────────────────────────────────────────────
    # (c) TRAILING MAX-DRAWDOWN
    # ───────────────────────────────────────────────────────────────────────────
    print("\n\n>>> (c) TRAILING MAX-DRAWDOWN — 60d MDD", flush=True)
    print("    High MDD = coin fell hard from its recent peak.")
    print("    Classic: long low-MDD (resilient) / short high-MDD (damaged).")
    print("    Crypto flip: high-MDD may be 'beaten-down' bounce candidates.")

    recs_mdd_low = run_factor_full(data, btc_data, _score_max_drawdown, higher_is_long=False)
    recs_mdd_hi  = run_factor_full(data, btc_data, _score_max_drawdown, higher_is_long=True)
    wt_mdd_low   = run_within_tercile(data, btc_data, _score_max_drawdown, higher_is_long=False)
    wt_mdd_hi    = run_within_tercile(data, btc_data, _score_max_drawdown, higher_is_long=True)

    results["max_drawdown"] = analyse_factor(
        "(c) TRAILING MAX-DRAWDOWN (60d MDD)",
        recs_mdd_low, recs_mdd_hi,
        wt_mdd_low, wt_mdd_hi,
        mom_stream, idvol_stream,
        label_fwd="long LOW-MDD / short HIGH-MDD (resilient long)",
        label_rev="long HIGH-MDD / short LOW-MDD (beaten-down bounce)",
    )

    # ───────────────────────────────────────────────────────────────────────────
    # (d) BETA-ROTATION
    # ───────────────────────────────────────────────────────────────────────────
    print("\n\n>>> (d) BETA-ROTATION — trailing 30d OLS beta vs BTC", flush=True)
    print("    Low beta = defensive / decoupled from BTC; High beta = highly coupled.")
    print("    Classic: long low-beta (less crash risk); defensive rotation timing.")
    print("    Question: is beta timing +EV in crypto, or just inverse-momentum?")

    recs_beta_low = run_factor_full(data, btc_data, _score_total_beta, higher_is_long=False)
    recs_beta_hi  = run_factor_full(data, btc_data, _score_total_beta, higher_is_long=True)
    wt_beta_low   = run_within_tercile(data, btc_data, _score_total_beta, higher_is_long=False)
    wt_beta_hi    = run_within_tercile(data, btc_data, _score_total_beta, higher_is_long=True)

    results["beta_rotation"] = analyse_factor(
        "(d) BETA-ROTATION (30d OLS beta vs BTC)",
        recs_beta_low, recs_beta_hi,
        wt_beta_low, wt_beta_hi,
        mom_stream, idvol_stream,
        label_fwd="long LOW-beta / short HIGH-beta (defensive rotation)",
        label_rev="long HIGH-beta / short LOW-beta (high-beta momentum)",
    )

    # ─── SUMMARY TABLE ────────────────────────────────────────────────────────
    print(f"\n\n{'═'*72}")
    print("  FINAL SUMMARY — ALL FACTORS, BOTH DIRECTIONS")
    print(f"{'═'*72}")
    print(f"  {'Factor / Direction':<42} {'Raw':>7} {'BN(sc)':>8} {'BN(3il)':>8} {'β':>6}  {'Verdict'}")
    print(f"  {'':-<90}")

    for factor_key, (r_fwd, r_rev) in results.items():
        for r in (r_fwd, r_rev):
            if r is None:
                continue
            wt_s = f"{r['wt_mean']:>+6.2f}%" if not math.isnan(r["wt_mean"]) else "  n/a  "
            print(f"  {r['label']:<42} {r['raw_mean']:>+6.2f}%  {r['bn_scale_mean']:>+6.2f}%  {wt_s}  {r['net_beta']:>+5.3f}  {r['verdict']}")

    print(f"\n{'═'*72}")
    print("  VERDICTS BY FACTOR")
    print(f"{'═'*72}")
    for factor_key, (r_fwd, r_rev) in results.items():
        print(f"\n  [{factor_key.upper()}]")
        for r in (r_fwd, r_rev):
            if r is None:
                continue
            corr_s = f"corr_mom={r['corr_mom']:+.2f}  corr_idvol={r['corr_idvol']:+.2f}"
            oos_fwd = f"H1={r['raw_h1']:>+5.2f}%/H2={r['raw_h2']:>+5.2f}% (raw)  H1={r['bn_scale_h1']:>+5.2f}%/H2={r['bn_scale_h2']:>+5.2f}% (BN)"
            print(f"    {r['label']}")
            print(f"      OOS: {oos_fwd}")
            print(f"      {corr_s}")
            print(f"      DOWN-REGIME: raw={r['down_raw']:>+5.2f}%  BN={r['down_bn']:>+5.2f}%")
            print(f"      *** {r['verdict']} ***")

    print()


if __name__ == "__main__":
    main()
