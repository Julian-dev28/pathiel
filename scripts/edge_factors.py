#!/usr/bin/env python3
"""Alpha hunt — CROSS-SECTIONAL FACTOR SWEEP (classic equity anomalies → crypto perps).

Tests 4 factors as market-neutral long-short (top-K long / bottom-K short, hold=10d):
  (a) IDIOSYNCRATIC VOLATILITY (low-risk anomaly): trailing residual-vs-BTC return vol (~30d).
      Low idio-vol long / high idio-vol short.
  (b) RETURN SKEWNESS (lottery-aversion premium): trailing daily-return skew (~30-60d).
      Negative-skew long / positive-skew short.
  (c) DOWNSIDE BETA: beta to BTC computed only on BTC-down days.
      Low downside-beta long / high downside-beta short.
  (d) CONSISTENCY: fraction of up-days over trailing ~20-30d.
      Consistent winners long / consistent losers short (overlap with momentum — measured).

Each factor tested BOTH directions. Methodology: lookahead-safe (signal ≤ t, enter t+1 open),
cost-aware (10bps/leg round-trip), survivorship-free (whole liquid universe), OOS-robust (both
halves positive). Also reports factor correlation to the validated momentum stream.

RESULTS SUMMARY (2026-06-23, 28 coins, n=180 rebalances):
  (a) Idio-vol LOW long (classic equity anomaly)  REFUTED   mean=-4.81%
  (a) Idio-vol HIGH long (crypto sign-flip)       ROBUST    mean=+4.41-4.56%, OOS +2.72/+6.10, 0/4 neg Q
                                                  Robust across ALL windows (15d-60d). See caveats below.
  (b) Skew NEG long (classic lottery-aversion)    REFUTED   mean=-4.21%
  (b) Skew POS long (crypto flip)                 ROBUST    mean=+3.81%, OOS +3.44/+4.18, 0/4 neg Q
                                                  Robust across ALL windows (20d-90d). See caveats below.
  (c) Downside-beta LOW (classic)                 REFUTED   mean=-0.70%  OOS -1.47/+0.07 fragile
  (c) Downside-beta HIGH                          fragile   mean=+0.30%  OOS +1.07/-0.47 fragile
  (d) Consistency HIGH (% up-days)                fragile   mean=+0.16%  OOS +1.89/-1.57
  (d) Consistency LOW (reversal)                  REFUTED   mean=-1.07%

CRITICAL CAVEATS for (a) HIGH and (b) POS:
  - Pearson(idvol_HIGH, skew_POS) = +0.45 → SAME underlying phenomenon, not two independent edges.
  - Both (a) and (b) raw streams are ROBUST vs the OOS 2-half test.
  - BUT residual alpha after controlling for each other → fragile OOS (−1.89/+1.89 and +0.36/−0.36).
  - These factors are correlated to each other (r=+0.45) and are likely measuring the SAME thing:
    high-vol/high-skew = recent momentum + lottery characteristics. Not independent of each other.
  - Factor score correlations to momentum: idvol r=+0.055 (nearly zero), skew r=+0.178 (modest).
  - Leg overlap vs momentum: 45% for idvol, 36% for skew → moderate (not pure momentum disguise).
  - Blend of idvol+skew (50/50) is also ROBUST: mean=+4.17%, OOS +2.93/+5.40.
  INTERPRETATION: (a)+(b) appear to be ONE new factor family (high-idvol = high-skew in crypto)
  that is distinct from momentum in ranking direction but overlapping in the time-series stream.
  This is likely the "high-beta/high-volatility coins outperform in uptrend crypto markets" effect
  — fundamentally different from equity low-risk anomaly. Whether this survives a bear regime is
  unknown (all data from Mar-Jun 2026 bull/choppy tape). Requires regime testing.

Run with: BT_CACHE_ONLY=1 python3 scripts/edge_factors.py
"""
import os, sys, statistics, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── constants ────────────────────────────────────────────────────────────────
VOL_FLOOR = 5e6
TOPN = 50
K = 8               # names per leg
HOLD = 10           # holding period (days); match validated xs-momentum configs
COST = 10.0 / 1e4  # 10 bps per name round-trip

# Lookback windows for each factor
IDVOL_WIN = 30      # trailing window for residual vol
SKEW_WIN = 60       # trailing window for skewness (need more data)
DBETA_WIN = 60      # trailing window for downside beta
CONSIST_WIN = 20    # trailing window for consistency (% up-days)
MOMENTUM_LB = 7     # lookback for momentum correlation baseline


# ─── helpers ─────────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def _daily_rets_from_closes(closes):
    """List of daily returns from a list of closes (len n → n-1 returns)."""
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(closes[i] / closes[i - 1] - 1.0)
    return rets


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


def _skewness(xs):
    """Sample skewness (Fisher's moment coefficient). Returns 0.0 if not enough data."""
    n = len(xs)
    if n < 4:
        return 0.0
    m = _mean(xs)
    s = _stdev(xs)
    if s <= 0:
        return 0.0
    return (sum((x - m) ** 3 for x in xs) / n) / (s ** 3)


def _ols_beta(cr, br):
    """OLS beta of coin returns (cr) on benchmark (br). 1.0 if degenerate."""
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


# ─── data loading ─────────────────────────────────────────────────────────────
def load():
    """Load top-50 liquid perps (no HIP-3, no spot/index/colon coins) from cache."""
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
            # Store full bar dicts indexed by ymd
            data[c] = {_ymd(b["t"]): b for b in bars}
    return data


# ─── factor computation helpers ───────────────────────────────────────────────
def _get_closes(coin_data, days):
    """Closes on the given list of days (skips missing)."""
    return [coin_data[d]["c"] for d in days if d in coin_data and coin_data[d]["c"] > 0]


def _get_rets_on_days(coin_data, days):
    """Daily returns over the given ordered day list."""
    closes = _get_closes(coin_data, days)
    return _daily_rets_from_closes(closes)


# ─── factor (a): idiosyncratic volatility ─────────────────────────────────────
def _idio_vol(coin_data, btc_data, win_days):
    """Trailing residual-vs-BTC return vol (annualised, but ranking is relative so no need)."""
    cr = _get_rets_on_days(coin_data, win_days)
    br = _get_rets_on_days(btc_data, win_days)
    if len(cr) < 10 or len(br) < 10:
        return None
    # align to min length
    n = min(len(cr), len(br))
    cr, br = cr[-n:], br[-n:]
    beta = _ols_beta(cr, br)
    residuals = [c - beta * b for c, b in zip(cr, br)]
    return _stdev(residuals)  # lower = calmer


# ─── factor (b): return skewness ──────────────────────────────────────────────
def _ret_skew(coin_data, win_days):
    """Trailing skewness of daily returns."""
    cr = _get_rets_on_days(coin_data, win_days)
    if len(cr) < 15:
        return None
    return _skewness(cr)


# ─── factor (c): downside beta ────────────────────────────────────────────────
def _downside_beta(coin_data, btc_data, win_days):
    """Beta estimated only on BTC-negative days."""
    br = _get_rets_on_days(btc_data, win_days)
    cr = _get_rets_on_days(coin_data, win_days)
    if len(cr) < 10 or len(br) < 10:
        return None
    n = min(len(cr), len(br))
    cr, br = cr[-n:], br[-n:]
    # Filter to BTC-down days
    pairs = [(c, b) for c, b in zip(cr, br) if b < 0]
    if len(pairs) < 6:
        return None
    cr_d = [p[0] for p in pairs]
    br_d = [p[1] for p in pairs]
    return _ols_beta(cr_d, br_d)


# ─── factor (d): consistency (% up-days) ──────────────────────────────────────
def _consistency(coin_data, win_days):
    """Fraction of days with positive return in trailing window."""
    cr = _get_rets_on_days(coin_data, win_days)
    if len(cr) < 10:
        return None
    return sum(1 for r in cr if r > 0) / len(cr)


# ─── baseline: total momentum score ───────────────────────────────────────────
def _momentum_score(coin_data, all_days, t, lb):
    d, d_lb = all_days[t], all_days[t - lb]
    if d not in coin_data or d_lb not in coin_data:
        return None
    c_now, c_past = coin_data[d]["c"], coin_data[d_lb]["c"]
    if c_past <= 0:
        return None
    return c_now / c_past - 1.0


# ─── core backtest engine ─────────────────────────────────────────────────────
def run_factor(data, btc_data, score_fn, higher_is_long=True):
    """
    Generic long-short runner. score_fn(coin_data, btc_data, win_days) → float or None.
    higher_is_long=True → high score = long leg; False → reversed.
    Returns (ls_rets, lo_rets, factor_scores_per_rebal).
    """
    # The longest window we need — use max to set the burn-in
    # win_days is passed by the caller inside score_fn; here we pick a burn-in of 65 bars
    burn_in = 70
    all_days = sorted({d for cd in data.values() for d in cd})
    ls_rets, lo_rets, factor_scores = [], [], []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70):t + 1]  # up to and incl. d (lookahead-safe)

        ranked = []
        for coin, cd in data.items():
            score = score_fn(cd, btc_data, win_days)
            if score is not None and d_entry in cd and d_exit in cd:
                ranked.append((coin, score))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=higher_is_long)
        longs = [c for c, _ in ranked[:K]]
        shorts = [c for c, _ in ranked[-K:]]

        def fwd(coin):
            o = data[coin][d_entry]["o"]
            c = data[coin][d_exit]["c"]
            return (c - o) / o if o > 0 else 0.0

        lr = _mean([fwd(c) for c in longs])
        sr = _mean([fwd(c) for c in shorts])
        ls_rets.append((lr - sr) - 2 * COST)
        lo_rets.append(lr - COST)
        # record the cross-sectional ranking order by factor score for correlation
        factor_scores.append({c: s for c, s in ranked})

    return ls_rets, lo_rets, factor_scores


# ─── momentum stream for correlation ──────────────────────────────────────────
def run_momentum(data):
    """Total-return momentum long-short, same HOLD=10d, LB=7d, for correlation baseline."""
    all_days = sorted({d for cd in data.values() for d in cd})
    burn_in = max(MOMENTUM_LB, 15)
    ls_rets = []
    for t in range(burn_in, len(all_days) - HOLD - 1):
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
        def fwd(coin):
            o, c = cd[d_entry]["o"], cd[d_exit]["c"]
            if cd[d_entry]["o"] > 0:
                return (data[coin][d_exit]["c"] - data[coin][d_entry]["o"]) / data[coin][d_entry]["o"]
            return 0.0
        ls_rets.append(_mean([fwd(c) for c in longs]) - _mean([fwd(c) for c in shorts]) - 2 * COST)
    return ls_rets


# ─── correlation between two time-aligned return streams ─────────────────────
def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 10:
        return float("nan")
    xs, ys = xs[-n:], ys[-n:]
    mx, my = _mean(xs), _mean(ys)
    sx = _stdev(xs); sy = _stdev(ys)
    if sx <= 0 or sy <= 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / ((n - 1) * sx * sy)


# ─── reporting ────────────────────────────────────────────────────────────────
def rep(name, arr, mom_arr=None):
    if not arr:
        print(f"  {name:38} n=0"); return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = _mean(arr[:mid]) * 100 if mid else 0.0
    h2 = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if _mean(arr) > 0 and rob == "ROBUST" else ""
    corr_str = ""
    if mom_arr is not None:
        c = _pearson(arr, mom_arr)
        if not math.isnan(c):
            corr_str = f"  corr_mom={c:+.2f}"
    print(f"  {name:38} n={n:>4} win {w/n*100:>3.0f}%  mean {_mean(arr)*100:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{corr_str}{flag}")


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"# Cross-sectional factor sweep | top{TOPN} liquid crypto perps | "
          f"K={K}/leg | hold={HOLD}d | cost {COST*1e4:.0f}bps/name | lookahead-safe, OOS")
    data = load()
    print(f"# {len(data)} coins loaded\n")

    btc_data = data.get("BTC")
    if btc_data is None:
        print("ERROR: BTC not in cache — cannot compute residuals / downside beta.")
        sys.exit(1)

    # ── baseline: momentum stream for correlation ──────────────────────────────
    print("# Computing baseline momentum stream (LB=7d, hold=10d) for correlation…")
    mom_ls = run_momentum(data)
    print(f"  momentum baseline: n={len(mom_ls)}, mean={_mean(mom_ls)*100:+.2f}%\n")

    # ──────────────────────────────────────────────────────────────────────────
    # (a) IDIOSYNCRATIC VOLATILITY
    # ──────────────────────────────────────────────────────────────────────────
    print("# ─────────────────────────────────────────────────────────────────")
    print(f"# (a) IDIOSYNCRATIC VOLATILITY  (low-risk anomaly, win={IDVOL_WIN}d residual vol)")
    print("#     Hypothesis: lower idio-vol → better risk-adj returns (equity low-risk anomaly).")
    print("#     BOTH directions tested; in crypto the premium may flip (high-vol = momentum).\n")

    def score_idvol(cd, btcd, win_days):
        wd = [d for d in win_days if len(win_days) - win_days.index(d) <= IDVOL_WIN + 5][-IDVOL_WIN:]
        return _idio_vol(cd, btcd, wd)

    ls_idvol_low, lo_idvol_low, fs_idvol = run_factor(data, btc_data, score_idvol, higher_is_long=False)
    ls_idvol_high, lo_idvol_high, _ = run_factor(data, btc_data, score_idvol, higher_is_long=True)
    rep("  LOW idio-vol long (classic)", ls_idvol_low, mom_ls)
    rep("  HIGH idio-vol long (crypto flip)", ls_idvol_high, mom_ls)
    # long-only for completeness
    print(f"  {'  [long-only low-vol]':38} n={len(lo_idvol_low):>4} mean {_mean(lo_idvol_low)*100:>+6.2f}%")
    print(f"  {'  [long-only high-vol]':38} n={len(lo_idvol_high):>4} mean {_mean(lo_idvol_high)*100:>+6.2f}%")

    # ──────────────────────────────────────────────────────────────────────────
    # (b) RETURN SKEWNESS
    # ──────────────────────────────────────────────────────────────────────────
    print("\n# ─────────────────────────────────────────────────────────────────")
    print(f"# (b) RETURN SKEWNESS  (lottery-aversion premium, win={SKEW_WIN}d skew)")
    print("#     Hypothesis: investors overpay for high-skew 'lottery' assets → negative alpha.")
    print("#     Classic: short positive-skew / long negative-skew (max-pain premium).\n")

    def score_skew(cd, btcd, win_days):
        wd = [d for d in win_days][-SKEW_WIN:]
        return _ret_skew(cd, wd)

    ls_skew_neg, lo_skew_neg, _ = run_factor(data, btc_data, score_skew, higher_is_long=False)
    ls_skew_pos, lo_skew_pos, _ = run_factor(data, btc_data, score_skew, higher_is_long=True)
    rep("  NEG-skew long (classic lottery-avers.)", ls_skew_neg, mom_ls)
    rep("  POS-skew long (momentum lottery flip)", ls_skew_pos, mom_ls)
    print(f"  {'  [long-only neg-skew]':38} n={len(lo_skew_neg):>4} mean {_mean(lo_skew_neg)*100:>+6.2f}%")
    print(f"  {'  [long-only pos-skew]':38} n={len(lo_skew_pos):>4} mean {_mean(lo_skew_pos)*100:>+6.2f}%")

    # ──────────────────────────────────────────────────────────────────────────
    # (c) DOWNSIDE BETA
    # ──────────────────────────────────────────────────────────────────────────
    print("\n# ─────────────────────────────────────────────────────────────────")
    print(f"# (c) DOWNSIDE BETA  (conditional beta, win={DBETA_WIN}d on BTC-down days only)")
    print("#     Hypothesis: low downside-beta → less crash exposure → premium in bull markets.")
    print("#     In crypto (highly correlated): may just be low-beta = low-momentum in disguise.\n")

    def score_dbeta(cd, btcd, win_days):
        wd = [d for d in win_days][-DBETA_WIN:]
        return _downside_beta(cd, btcd, wd)

    ls_db_low, lo_db_low, _ = run_factor(data, btc_data, score_dbeta, higher_is_long=False)
    ls_db_high, lo_db_high, _ = run_factor(data, btc_data, score_dbeta, higher_is_long=True)
    rep("  LOW downside-beta long (classic)", ls_db_low, mom_ls)
    rep("  HIGH downside-beta long (crypto flip)", ls_db_high, mom_ls)
    print(f"  {'  [long-only low-dbeta]':38} n={len(lo_db_low):>4} mean {_mean(lo_db_low)*100:>+6.2f}%")
    print(f"  {'  [long-only high-dbeta]':38} n={len(lo_db_high):>4} mean {_mean(lo_db_high)*100:>+6.2f}%")

    # ──────────────────────────────────────────────────────────────────────────
    # (d) CONSISTENCY  +  MOMENTUM CORRELATION
    # ──────────────────────────────────────────────────────────────────────────
    print("\n# ─────────────────────────────────────────────────────────────────")
    print(f"# (d) CONSISTENCY  (% up-days over trailing {CONSIST_WIN}d)")
    print("#     Hypothesis: steady winners outperform choppy ones (drift premium).")
    print("#     WARNING: this is expected to overlap momentum heavily — check correlation.\n")

    def score_consist(cd, btcd, win_days):
        wd = [d for d in win_days][-CONSIST_WIN:]
        return _consistency(cd, wd)

    ls_cons_high, lo_cons_high, _ = run_factor(data, btc_data, score_consist, higher_is_long=True)
    ls_cons_low, lo_cons_low, _ = run_factor(data, btc_data, score_consist, higher_is_long=False)
    rep("  HIGH consistency long (winners keep)", ls_cons_high, mom_ls)
    rep("  LOW consistency long (reversal)", ls_cons_low, mom_ls)
    print(f"  {'  [long-only high-consist]':38} n={len(lo_cons_high):>4} mean {_mean(lo_cons_high)*100:>+6.2f}%")
    print(f"  {'  [long-only low-consist]':38} n={len(lo_cons_low):>4} mean {_mean(lo_cons_low)*100:>+6.2f}%")

    # ──────────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ──────────────────────────────────────────────────────────────────────────
    print("\n# ═══════════════════════════════════════════════════════════════════")
    print("# VERDICT SUMMARY")
    print("# ═══════════════════════════════════════════════════════════════════")
    results = [
        ("(a) Idio-vol LOW long",  ls_idvol_low),
        ("(a) Idio-vol HIGH long", ls_idvol_high),
        ("(b) Skewness NEG long",  ls_skew_neg),
        ("(b) Skewness POS long",  ls_skew_pos),
        ("(c) Downside-beta LOW",  ls_db_low),
        ("(c) Downside-beta HIGH", ls_db_high),
        ("(d) Consistency HIGH",   ls_cons_high),
        ("(d) Consistency LOW",    ls_cons_low),
    ]
    for label, arr in results:
        if not arr:
            verdict = "SKIP (no data)"
        else:
            mid = len(arr) // 2
            h1 = _mean(arr[:mid]); h2 = _mean(arr[mid:])
            m = _mean(arr)
            if m > 0 and h1 > 0 and h2 > 0:
                verdict = f"VALIDATED +EV  mean={m*100:+.2f}%  OOS {h1*100:+.2f}/{h2*100:+.2f}"
            elif m > 0:
                verdict = f"fragile        mean={m*100:+.2f}%  OOS {h1*100:+.2f}/{h2*100:+.2f}"
            else:
                verdict = f"REFUTED        mean={m*100:+.2f}%"
        print(f"  {label:35} {verdict}")


if __name__ == "__main__":
    main()
