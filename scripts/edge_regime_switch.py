#!/usr/bin/env python3
"""Alpha hunt — REGIME-SWITCHED cross-sectional strategy.

Hypothesis: cross-sectional MOMENTUM (long top-8 / short bottom-8) is +EV in trending regimes;
cross-sectional REVERSAL (long bottom-8 / short top-8) is the play in mean-reverting regimes.
Two regime classifiers are tested, each lookahead-safe (uses only data ≤ day t).

Classifiers:
  (1) Hurst exponent of BTC returns over a trailing window (60–90d):
        H > 0.5 ⇒ TRENDING (momentum persists)
        H < 0.5 ⇒ MEAN-REVERTING (reversals dominate)
  (2) Lag-1 autocorrelation of BTC daily returns over a trailing window (60d):
        autocorr > 0 ⇒ TRENDING; < 0 ⇒ MEAN-REVERTING

Strategy:
  - In TRENDING days  → long-short momentum (longs=top-K, shorts=bottom-K)
  - In MR days        → long-short reversal  (longs=bottom-K, shorts=top-K)
  - Hold ∈ {5, 10}d; cost 10bps per name round-trip

Benchmark comparison:
  - always-momentum  (K=8, LB=7d, hold=5/10d) — the validated +2.37% edge
  - always-reversal  (-EV benchmark)
  - regime-switched combo per classifier

Lookahead safety:
  - regime label for day t uses ONLY BTC closes up to and including t
  - entry happens on t+1 open, exit on t+1+hold close

Run with BT_CACHE_ONLY=1 (no network).
"""
import os, sys, math, statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────
TOPN = 50
VOL_FLOOR = 5e6
K = 8                       # names per leg
COST_BPS = 10.0             # per name, round-trip
LB_CONFIGS = [7, 14]       # momentum look-back windows to test
HOLD_CONFIGS = [5, 10]      # hold periods to test
HURST_WINDOW = 60           # rolling window for Hurst calc
AUTOCORR_WINDOW = 60        # rolling window for lag-1 autocorr
HURST_LAG_MAX = 20          # max lag for Hurst R/S


# ──────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


def load():
    """Load candles from disk cache (BT_CACHE_ONLY=1). Mirror of edge_xsectional.load()."""
    uni = [m for m in get_universe(include_hip3=False)
           if ":" not in (m.get("coin") or "") and not (m.get("coin") or "").startswith("@")
           and m.get("type") != "spot" and float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    data = {}
    for m in uni:
        c = m["coin"]
        bars = get_candles(c, "1d", 260)
        if len(bars) >= 80:
            data[c] = {_ymd(b["t"]): (b["o"], b["c"]) for b in bars}
    return data


# ──────────────────────────────────────────────────────────────
# Regime classifiers
# ──────────────────────────────────────────────────────────────
def _hurst_rs(returns: list) -> float:
    """Hurst exponent via R/S analysis over the given return series.
    H > 0.5 → trending / persistence; H < 0.5 → mean-reverting / anti-persistence.
    Returns NaN if series is too short or degenerate."""
    n = len(returns)
    if n < 20:
        return float("nan")

    def rs_for_chunk(chunk):
        m = statistics.mean(chunk)
        devs = [x - m for x in chunk]
        cumdevs = []
        s = 0.0
        for d in devs:
            s += d
            cumdevs.append(s)
        r_range = max(cumdevs) - min(cumdevs)
        std = statistics.pstdev(chunk)
        if std <= 0 or r_range <= 0:
            return None
        return r_range / std

    lags = [sz for sz in range(10, min(HURST_LAG_MAX, n // 2) + 1, 2)]
    if len(lags) < 4:
        return float("nan")

    log_rs_vals = []
    log_n_vals = []
    for lag in lags:
        # Average R/S over non-overlapping chunks of size `lag`
        chunk_rs = []
        for start in range(0, n - lag + 1, lag):
            chunk = returns[start: start + lag]
            v = rs_for_chunk(chunk)
            if v is not None:
                chunk_rs.append(math.log(v))
        if chunk_rs:
            log_rs_vals.append(statistics.mean(chunk_rs))
            log_n_vals.append(math.log(lag))

    if len(log_rs_vals) < 3:
        return float("nan")

    # OLS slope = Hurst exponent
    n_pts = len(log_rs_vals)
    mx = statistics.mean(log_n_vals)
    my = statistics.mean(log_rs_vals)
    num = sum((log_n_vals[i] - mx) * (log_rs_vals[i] - my) for i in range(n_pts))
    den = sum((log_n_vals[i] - mx) ** 2 for i in range(n_pts))
    if den <= 0:
        return float("nan")
    return num / den


def _lag1_autocorr(returns: list) -> float:
    """Lag-1 autocorrelation of returns. Positive → trending; negative → mean-reverting."""
    n = len(returns)
    if n < 10:
        return float("nan")
    m = statistics.mean(returns)
    var = statistics.pvariance(returns)
    if var <= 0:
        return float("nan")
    cov = sum((returns[i] - m) * (returns[i - 1] - m) for i in range(1, n)) / n
    return cov / var


def build_btc_regime_series(btc_oc: dict, all_days: list, window: int = HURST_WINDOW):
    """For each day t in all_days, compute:
      - hurst: H over trailing `window` BTC daily returns up to t (lookahead-safe)
      - autocorr: lag-1 autocorr over trailing `window` BTC daily returns up to t

    Returns dict: day -> {"hurst": float, "autocorr": float, "trending_hurst": bool|None,
                           "trending_autocorr": bool|None}
    """
    # Build sorted BTC close price series aligned to all_days
    btc_closes = []   # (day, close)
    for d in all_days:
        if d in btc_oc:
            btc_closes.append((d, btc_oc[d][1]))   # (ymd, close)

    btc_close_map = {d: c for d, c in btc_closes}
    # Daily returns indexed by day (return[t] = close[t]/close[t-1] - 1)
    btc_ret_by_day = {}
    day_order = [d for d in all_days if d in btc_close_map]
    for i in range(1, len(day_order)):
        d = day_order[i]
        prev = day_order[i - 1]
        c_now = btc_close_map[d]
        c_prev = btc_close_map[prev]
        if c_prev > 0:
            btc_ret_by_day[d] = c_now / c_prev - 1.0

    # For each signal day t, compute classifier using only returns ≤ t
    regime = {}
    for i, d in enumerate(all_days):
        # Gather trailing `window` returns ending AT day t (not t+1 — lookahead-safe)
        past_days = [dd for dd in all_days[:i + 1] if dd in btc_ret_by_day]
        rets = [btc_ret_by_day[dd] for dd in past_days[-window:]]

        hurst = _hurst_rs(rets) if len(rets) >= window // 2 else float("nan")
        ac = _lag1_autocorr(rets) if len(rets) >= 10 else float("nan")

        trending_hurst = None if math.isnan(hurst) else hurst > 0.5
        trending_autocorr = None if math.isnan(ac) else ac > 0.0

        regime[d] = {
            "hurst": hurst,
            "autocorr": ac,
            "trending_hurst": trending_hurst,
            "trending_autocorr": trending_autocorr,
        }
    return regime


# ──────────────────────────────────────────────────────────────
# Backtest engine
# ──────────────────────────────────────────────────────────────
def fwd_return(data, coin, d_entry, d_exit):
    """Enter at d_entry open, exit at d_exit close. Return 0 if missing."""
    oc_entry = data[coin].get(d_entry)
    oc_exit = data[coin].get(d_exit)
    if oc_entry is None or oc_exit is None:
        return 0.0
    o = oc_entry[0]
    c = oc_exit[1]
    return (c - o) / o if o > 0 else 0.0


def run_regime_switched(data, regime, lb, hold, cost, classifier_key):
    """Main backtest loop.
    Returns:
      ls_switched: long-short spread per rebalance period for the regime-switched strategy
      ls_momentum: always-momentum
      ls_reversal: always-reversal
      regime_log: list of (day, trending: bool|None, ls_switched, ls_mom, ls_rev)
    """
    all_days = sorted({d for oc in data.values() for d in oc})
    cost_both = 2 * cost   # both legs

    ls_switched = []
    ls_momentum = []
    ls_reversal = []
    regime_log = []   # (day, trending, spread_switched, spread_mom, spread_rev)

    # Warmup: need `lb` days for ranking + regime window
    warmup = max(lb, HURST_WINDOW) + 5

    for t in range(warmup, len(all_days) - hold - 1):
        d = all_days[t]
        d_lb = all_days[t - lb]
        d_entry = all_days[t + 1]
        d_exit = all_days[t + 1 + hold] if t + 1 + hold < len(all_days) else all_days[-1]

        # Regime label at day t (lookahead-safe: uses only data ≤ t)
        reg = regime.get(d, {})
        trending = reg.get(classifier_key)   # True / False / None

        # Rank by trailing return (close[t] / close[t-lb] - 1)
        ranked = []
        for coin, oc in data.items():
            if d in oc and d_lb in oc and d_entry in oc and d_exit in oc:
                c_now = oc[d][1]
                c_past = oc[d_lb][1]
                if c_past > 0:
                    ranked.append((coin, c_now / c_past - 1))

        if len(ranked) < 2 * K + 4:
            continue

        ranked.sort(key=lambda x: x[1], reverse=True)
        top_k = [c for c, _ in ranked[:K]]     # momentum longs / reversal shorts
        bot_k = [c for c, _ in ranked[-K:]]    # momentum shorts / reversal longs

        def leg_mean(coins):
            rets = [fwd_return(data, c, d_entry, d_exit) for c in coins]
            return statistics.mean(rets) if rets else 0.0

        top_ret = leg_mean(top_k)
        bot_ret = leg_mean(bot_k)

        mom_spread = (top_ret - bot_ret) - cost_both   # momentum: long top, short bot
        rev_spread = (bot_ret - top_ret) - cost_both   # reversal: long bot, short top

        if trending is None:
            sw_spread = mom_spread   # default to momentum when regime unclear
        elif trending:
            sw_spread = mom_spread
        else:
            sw_spread = rev_spread

        ls_switched.append(sw_spread)
        ls_momentum.append(mom_spread)
        ls_reversal.append(rev_spread)
        regime_log.append((d, trending, sw_spread, mom_spread, rev_spread))

    return ls_switched, ls_momentum, ls_reversal, regime_log


# ──────────────────────────────────────────────────────────────
# Reporting helpers
# ──────────────────────────────────────────────────────────────
def rep(name: str, arr: list):
    if not arr:
        print(f"    {name:34} n=0")
        return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = statistics.mean(arr[:mid]) * 100 if mid else 0.0
    h2 = statistics.mean(arr[mid:]) * 100 if (n - mid) else 0.0
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    mean_pct = statistics.mean(arr) * 100
    flag = "  <<< +EV" if mean_pct > 0 and rob == "ROBUST" else ""
    print(f"    {name:34} n={n:>4} win {w/n*100:>3.0f}%  mean {mean_pct:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{flag}")


def analyse_per_regime(regime_log, spread_idx, label):
    """Split the spread stream by regime (trending=True/False/None) and report each bucket."""
    trending_rets = [row[spread_idx] for row in regime_log if row[1] is True]
    reverting_rets = [row[spread_idx] for row in regime_log if row[1] is False]
    unclear_rets = [row[spread_idx] for row in regime_log if row[1] is None]
    n_total = len(regime_log)
    print(f"    Per-regime breakdown for {label}:")
    print(f"      TRENDING  days: {len(trending_rets):>4} ({len(trending_rets)/n_total*100:>4.1f}%)")
    print(f"      REVERTING days: {len(reverting_rets):>4} ({len(reverting_rets)/n_total*100:>4.1f}%)")
    print(f"      UNCLEAR   days: {len(unclear_rets):>4} ({len(unclear_rets)/n_total*100:>4.1f}%)")
    for nm, rets in [("  trending-regime momentum", trending_rets),
                     ("  reverting-regime reversal", reverting_rets),
                     ("  unclear (defaulted mom)", unclear_rets)]:
        if rets:
            mean_pct = statistics.mean(rets) * 100
            w = sum(1 for r in rets if r > 0)
            print(f"      {nm:34} n={len(rets):>4} win {w/len(rets)*100:>3.0f}%  "
                  f"mean {mean_pct:>+6.2f}%")
    return trending_rets, reverting_rets


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
def main():
    print("=" * 78)
    print("# REGIME-SWITCHED CROSS-SECTIONAL STRATEGY")
    print(f"# K={K}/leg | cost {COST_BPS:.0f}bps/name | lookahead-safe | OOS split")
    print("=" * 78)

    data = load()
    n_coins = len(data)
    all_days = sorted({d for oc in data.values() for d in oc})
    print(f"\n# {n_coins} coins | {len(all_days)} trading days "
          f"({all_days[0]} – {all_days[-1]})\n")

    if "BTC" not in data:
        print("ERROR: BTC not in cache — cannot build regime signal.")
        sys.exit(1)

    # Build BTC regime time-series (both classifiers, once)
    print("# Building BTC regime classifiers...")
    regime = build_btc_regime_series(data["BTC"], all_days)

    # Quick sanity on regime distribution (over the whole period)
    h_vals = [v["hurst"] for v in regime.values() if not math.isnan(v.get("hurst", float("nan")))]
    ac_vals = [v["autocorr"] for v in regime.values() if not math.isnan(v.get("autocorr", float("nan")))]
    if h_vals:
        n_h_trend = sum(1 for v in h_vals if v > 0.5)
        print(f"  Hurst classifier: {len(h_vals)} valid days  "
              f"trending={n_h_trend} ({n_h_trend/len(h_vals)*100:.1f}%)  "
              f"mean_H={statistics.mean(h_vals):.3f}")
    if ac_vals:
        n_ac_trend = sum(1 for v in ac_vals if v > 0.0)
        print(f"  Autocorr classifier: {len(ac_vals)} valid days  "
              f"trending={n_ac_trend} ({n_ac_trend/len(ac_vals)*100:.1f}%)  "
              f"mean_ac={statistics.mean(ac_vals):.3f}")

    cost = COST_BPS / 1e4

    for lb in LB_CONFIGS:
        for hold in HOLD_CONFIGS:
            print(f"\n{'─'*78}")
            print(f"# LB={lb}d  HOLD={hold}d")
            print(f"{'─'*78}")

            for clf_key, clf_name in [
                ("trending_hurst", f"Classifier-1: Hurst R/S (window={HURST_WINDOW}d)"),
                ("trending_autocorr", f"Classifier-2: Lag-1 Autocorr (window={AUTOCORR_WINDOW}d)"),
            ]:
                sw, mom, rev, log = run_regime_switched(data, regime, lb, hold, cost, clf_key)
                print(f"\n  [{clf_name}]")

                # Regime split
                n_trend = sum(1 for row in log if row[1] is True)
                n_rev = sum(1 for row in log if row[1] is False)
                n_unclear = sum(1 for row in log if row[1] is None)
                n_total = len(log)
                print(f"    Regime split ({n_total} rebal periods): "
                      f"TRENDING={n_trend} ({n_trend/n_total*100:.1f}%)  "
                      f"REVERTING={n_rev} ({n_rev/n_total*100:.1f}%)  "
                      f"UNCLEAR={n_unclear}")

                # Main result table
                print(f"  Results:")
                rep("regime-SWITCHED (combo)", sw)
                rep("always-momentum  (bench)", mom)
                rep("always-reversal  (bench)", rev)

                # Per-regime breakdown of SWITCHED strategy
                print()
                analyse_per_regime(log, 2, f"{clf_name} switched")

                # Regime-conditional mean for momentum: does classifier help?
                trend_mom = [row[3] for row in log if row[1] is True]
                rev_mom = [row[3] for row in log if row[1] is False]
                trend_rev_strat = [row[4] for row in log if row[1] is False]  # reversal in MR regime
                print(f"    Cross-check (classifier signal quality):")
                if trend_mom:
                    print(f"      momentum EV when TRENDING  : {statistics.mean(trend_mom)*100:>+6.2f}%  n={len(trend_mom)}")
                if rev_mom:
                    print(f"      momentum EV when REVERTING : {statistics.mean(rev_mom)*100:>+6.2f}%  n={len(rev_mom)}")
                if trend_rev_strat:
                    print(f"      reversal EV when REVERTING : {statistics.mean(trend_rev_strat)*100:>+6.2f}%  n={len(trend_rev_strat)}")

                # Verdict
                print()
                if sw and mom:
                    sw_mean = statistics.mean(sw) * 100
                    mom_mean = statistics.mean(mom) * 100
                    mid = len(sw) // 2
                    sw_h1 = statistics.mean(sw[:mid]) * 100 if mid else 0
                    sw_h2 = statistics.mean(sw[mid:]) * 100 if (len(sw) - mid) else 0
                    sw_robust = sw_h1 > 0 and sw_h2 > 0
                    beats = sw_mean > mom_mean
                    print(f"  VERDICT [{clf_name}]:")
                    print(f"    Switched mean {sw_mean:>+.2f}% vs always-momentum {mom_mean:>+.2f}%")
                    if beats and sw_robust:
                        print(f"    BEATS BENCHMARK robustly — switching adds {sw_mean - mom_mean:>+.2f}%/rebal")
                    elif beats and not sw_robust:
                        print(f"    Beats benchmark mean but NOT robust (fragile OOS) — NOT validated")
                    else:
                        print(f"    Does NOT beat always-momentum ({sw_mean - mom_mean:>+.2f}% delta) — "
                              f"classifier does not add value here")

    print(f"\n{'='*78}")
    print("# SUMMARY")
    print(f"{'='*78}")
    print("""
Regime-switching thesis: if the classifier correctly identifies trending vs mean-reverting
regimes, the combo should outperform always-momentum. The key question is whether the
Hurst or autocorr signal is predictive enough (and stable) to overcome the extra
complexity + the risk of misclassification.

Methodology bar reminder:
  - lookahead-safe  : regime for day t uses only BTC closes ≤ t; entry is t+1 open
  - cost-aware      : 10bps/leg both legs = 20bps/rebal subtracted
  - OOS-robust      : h1 AND h2 both mean-positive
  - survivorship-free: whole cached liquid universe (no cherry-pick)
""")


if __name__ == "__main__":
    main()
