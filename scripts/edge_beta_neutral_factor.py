#!/usr/bin/env python3
"""Beta-neutralization audit for R4 factors: HIGH idio-vol + POS skew.

Assignment: settle whether R4's "high-idio-vol + positive-skew" factor is genuine alpha
or just a bull-regime beta bet.

Steps:
  1. Reproduce long-HIGH-idio-vol and POS-skew factors (same params as edge_factors.py).
  2. Quantify the net-beta each spread carries (long-leg avg beta - short-leg avg beta).
  3. Beta-neutralize (scale legs to net-zero portfolio beta) and re-test EV.
  4. Down-regime subset: factor mean return on worst-quartile BTC-return days only.

VERDICT: survives beta-neutralization (mean>0 AND both OOS halves>0) AND no collapse on
down-days → genuine alpha. Collapses to ~0/neg → beta exposure, REFUTED.

Run: BT_CACHE_ONLY=1 python3 scripts/edge_beta_neutral_factor.py
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from hermes_trader.client.universe import get_universe
from _bt_candles import get as get_candles

# ─── constants (match edge_factors.py exactly) ────────────────────────────────
VOL_FLOOR = 5e6
TOPN = 50
K = 8           # names per leg
HOLD = 10       # holding period (days)
COST = 10.0 / 1e4   # 10 bps per name round-trip
IDVOL_WIN = 30
SKEW_WIN = 60
BETA_WIN = 30   # trailing window for per-coin BTC beta


# ─── helpers ──────────────────────────────────────────────────────────────────
def _ymd(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y%m%d")


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
    n = len(xs)
    if n < 4:
        return 0.0
    m = _mean(xs)
    s = _stdev(xs)
    if s <= 0:
        return 0.0
    return (sum((x - m) ** 3 for x in xs) / n) / (s ** 3)


def _ols_beta(cr, br):
    """OLS beta of coin returns on benchmark. 1.0 if degenerate."""
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


def _daily_rets_from_closes(closes):
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(closes[i] / closes[i - 1] - 1.0)
    return rets


def _get_closes(coin_data, days):
    return [coin_data[d]["c"] for d in days if d in coin_data and coin_data[d]["c"] > 0]


def _get_rets_on_days(coin_data, days):
    closes = _get_closes(coin_data, days)
    return _daily_rets_from_closes(closes)


# ─── factor scores ─────────────────────────────────────────────────────────────
def _idio_vol_score(coin_data, btc_data, win_days):
    wd = win_days[-IDVOL_WIN:]
    cr = _get_rets_on_days(coin_data, wd)
    br = _get_rets_on_days(btc_data, wd)
    if len(cr) < 10 or len(br) < 10:
        return None
    n = min(len(cr), len(br))
    cr, br = cr[-n:], br[-n:]
    beta = _ols_beta(cr, br)
    residuals = [c - beta * b for c, b in zip(cr, br)]
    return _stdev(residuals)


def _skew_score(coin_data, btc_data, win_days):
    wd = win_days[-SKEW_WIN:]
    cr = _get_rets_on_days(coin_data, wd)
    if len(cr) < 15:
        return None
    return _skewness(cr)


# ─── coin-to-BTC beta at time t ────────────────────────────────────────────────
def _coin_beta_at_t(coin_data, btc_data, win_days):
    """Trailing BETA_WIN-day OLS beta of coin vs BTC (for beta-neutralization)."""
    wd = win_days[-BETA_WIN:]
    cr = _get_rets_on_days(coin_data, wd)
    br = _get_rets_on_days(btc_data, wd)
    if len(cr) < 8 or len(br) < 8:
        return 1.0
    return _ols_beta(cr, br)


# ─── BTC daily return on a day ─────────────────────────────────────────────────
def _btc_day_return(btc_data, d_entry, d_exit):
    """BTC forward return over the same hold window."""
    if d_entry not in btc_data or d_exit not in btc_data:
        return None
    o = btc_data[d_entry]["o"]
    c = btc_data[d_exit]["c"]
    if o <= 0:
        return None
    return (c - o) / o


# ─── data loading ──────────────────────────────────────────────────────────────
def load():
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


# ─── reporting ─────────────────────────────────────────────────────────────────
def rep(name, arr, down_arr=None):
    if not arr:
        print(f"  {name:45} n=0"); return
    n = len(arr)
    w = sum(1 for r in arr if r > 0)
    mid = n // 2
    h1 = _mean(arr[:mid]) * 100 if mid else 0.0
    h2 = _mean(arr[mid:]) * 100 if (n - mid) else 0.0
    m = _mean(arr) * 100
    rob = "ROBUST" if h1 > 0 and h2 > 0 else "fragile" if (h1 > 0) != (h2 > 0) else "neg"
    flag = "  <<< +EV" if m > 0 and rob == "ROBUST" else ""
    down_str = ""
    if down_arr is not None:
        dm = _mean(down_arr) * 100 if down_arr else float("nan")
        down_str = f"  down-regime={dm:>+6.2f}%"
    print(f"  {name:45} n={n:>4} win={w/n*100:>3.0f}%  mean={m:>+6.2f}%  "
          f"OOS {h1:>+5.2f}/{h2:>+5.2f} {rob}{down_str}{flag}")


# ─── core backtest ─────────────────────────────────────────────────────────────
def run_factor_full(data, btc_data, score_fn, higher_is_long=True):
    """
    Returns per-rebalance records with:
      ls_ret (raw L-S), bn_ret (beta-neutralized L-S), btc_fwd (BTC return same window),
      long_betas, short_betas.
    """
    burn_in = 70
    all_days = sorted({d for cd in data.values() for d in cd})
    records = []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70): t + 1]

        # Score and beta each coin
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

        # Raw long-short
        lr = _mean([fwd(c) for c, _, _ in longs])
        sr = _mean([fwd(c) for c, _, _ in shorts])
        ls_ret = (lr - sr) - 2 * COST

        # Per-leg average beta
        long_betas = [b for _, _, b in longs]
        short_betas = [b for _, _, b in shorts]
        avg_long_beta = _mean(long_betas)
        avg_short_beta = _mean(short_betas)
        net_beta = avg_long_beta - avg_short_beta

        # Beta-neutralized: weight each coin to net out beta exposure.
        # Scale long leg by 1, short leg by (avg_long_beta / avg_short_beta) if avg_short_beta > 0.
        # Equivalent to: BN_spread = long_return - (avg_long_beta / avg_short_beta) * short_return
        # Both legs remain K names; we just scale the short-leg contribution.
        if avg_short_beta > 0.01:
            scale = avg_long_beta / avg_short_beta
        else:
            scale = 1.0
        bn_ret = (lr - scale * sr) - (1 + scale) * COST / 2  # proportional cost

        # BTC forward return over same hold
        btc_fwd = _btc_day_return(btc_data, d_entry, d_exit)

        records.append({
            "ls": ls_ret,
            "bn": bn_ret,
            "btc_fwd": btc_fwd,
            "net_beta": net_beta,
            "long_beta": avg_long_beta,
            "short_beta": avg_short_beta,
        })

    return records


def analyse(name, records):
    """Print the full beta audit table for one factor."""
    if not records:
        print(f"  {name}: no data")
        return

    ls_arr = [r["ls"] for r in records]
    bn_arr = [r["bn"] for r in records]
    net_betas = [r["net_beta"] for r in records]
    long_betas = [r["long_beta"] for r in records]
    short_betas = [r["short_beta"] for r in records]

    avg_net_beta = _mean(net_betas)
    avg_long_b = _mean(long_betas)
    avg_short_b = _mean(short_betas)

    print(f"\n{'─'*70}")
    print(f"  FACTOR: {name}")
    print(f"{'─'*70}")
    print(f"  Beta profile (avg per rebalance):")
    print(f"    Long-leg avg beta  : {avg_long_b:+.3f}")
    print(f"    Short-leg avg beta : {avg_short_b:+.3f}")
    print(f"    Net spread beta    : {avg_net_beta:+.3f}   <<< KEY NUMBER")
    print()

    # Down-regime: worst quartile of BTC forward returns
    btc_fwds = [(r["btc_fwd"], r) for r in records if r["btc_fwd"] is not None]
    if btc_fwds:
        btc_fwds.sort(key=lambda x: x[0])
        q25_idx = len(btc_fwds) // 4
        down_records = [r for _, r in btc_fwds[:q25_idx]]
        down_ls = [r["ls"] for r in down_records]
        down_bn = [r["bn"] for r in down_records]
        btc_threshold = btc_fwds[q25_idx][0] if q25_idx < len(btc_fwds) else None
        print(f"  Down-regime (worst Q1 BTC, n={len(down_records)}, BTC<{btc_threshold*100:.2f}%):")
        down_ls_mean = _mean(down_ls) * 100
        down_bn_mean = _mean(down_bn) * 100
        print(f"    Raw L-S mean in down-regime : {down_ls_mean:>+6.2f}%")
        print(f"    BN  L-S mean in down-regime : {down_bn_mean:>+6.2f}%")
    else:
        down_ls = []
        down_bn = []
        print("  Down-regime: insufficient BTC data")

    print()
    print("  Full-sample results:")
    rep(f"  Raw L-S (un-neutralized)", ls_arr, down_ls)
    rep(f"  Beta-neutralized L-S    ", bn_arr, down_bn)

    # OOS breakdown manually for clarity
    n = len(ls_arr)
    mid = n // 2
    print()
    print(f"  OOS breakdown (n={n}, mid={mid}):")
    raw_h1, raw_h2 = _mean(ls_arr[:mid]) * 100, _mean(ls_arr[mid:]) * 100
    bn_h1, bn_h2 = _mean(bn_arr[:mid]) * 100, _mean(bn_arr[mid:]) * 100
    print(f"    Raw H1={raw_h1:>+6.2f}%  H2={raw_h2:>+6.2f}%  →  {'BOTH +' if raw_h1>0 and raw_h2>0 else 'FAILS OOS'}")
    print(f"    BN  H1={bn_h1:>+6.2f}%  H2={bn_h2:>+6.2f}%  →  {'BOTH +' if bn_h1>0 and bn_h2>0 else 'FAILS OOS'}")

    # VERDICT
    bn_mean = _mean(bn_arr) * 100
    bn_robust = bn_h1 > 0 and bn_h2 > 0
    bn_down_ok = not down_bn or (_mean(down_bn) * 100 > -1.5)  # doesn't collapse badly

    print()
    if bn_mean > 0 and bn_robust and bn_down_ok:
        verdict = "GENUINE ALPHA — survives beta-neutralization and down-regime"
    elif bn_mean > 0 and bn_robust and not bn_down_ok:
        verdict = "PARTIAL — OOS positive but bleeds badly on down-days (beta-tainted)"
    else:
        verdict = "BETA BET — collapses post-neutralization → REFUTED as standalone alpha"

    print(f"  *** VERDICT: {verdict} ***")
    print(f"      Net-beta={avg_net_beta:+.3f} | BN mean={bn_mean:>+6.2f}% | "
          f"BN OOS {bn_h1:>+5.2f}/{bn_h2:>+5.2f} | "
          f"Down-regime BN={(_mean(down_bn)*100) if down_bn else float('nan'):>+6.2f}%")
    return {
        "avg_net_beta": avg_net_beta,
        "raw_mean": _mean(ls_arr) * 100,
        "bn_mean": bn_mean,
        "bn_robust": bn_robust,
        "down_bn_mean": _mean(down_bn) * 100 if down_bn else float("nan"),
        "verdict": verdict,
    }


# ─── also: within-beta-tercile factor (alternative neutralization) ──────────────
def run_within_tercile(data, btc_data, score_fn, higher_is_long=True):
    """
    Alternative beta-neutralization: sort universe into beta terciles, apply
    long-top-K / short-bottom-K WITHIN each tercile, average across terciles.
    This neutralizes beta by construction (each tercile has similar beta).
    Returns list of per-rebalance L-S returns.
    """
    burn_in = 70
    all_days = sorted({d for cd in data.values() for d in cd})
    ls_rets = []

    for t in range(burn_in, len(all_days) - HOLD - 1):
        d = all_days[t]
        d_entry = all_days[t + 1]
        d_exit = all_days[min(t + 1 + HOLD, len(all_days) - 1)]
        win_days = all_days[max(0, t - 70): t + 1]

        # Score and beta each coin
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

        # Sort by beta to form terciles
        ranked_by_beta = sorted(ranked, key=lambda x: x[2])
        n_per_tercile = len(ranked_by_beta) // 3
        if n_per_tercile < 3:
            continue

        tercile_spreads = []
        for ti in range(3):
            start = ti * n_per_tercile
            end = start + n_per_tercile if ti < 2 else len(ranked_by_beta)
            tercile_coins = ranked_by_beta[start:end]

            # Sort within tercile by factor score
            tercile_coins.sort(key=lambda x: x[1], reverse=higher_is_long)
            k_t = max(1, min(3, len(tercile_coins) // 3))  # top/bottom 1/3 per tercile
            longs_t = [c for c, _, _ in tercile_coins[:k_t]]
            shorts_t = [c for c, _, _ in tercile_coins[-k_t:]]

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


# ─── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  R4 BETA-NEUTRALIZATION AUDIT")
    print("  HIGH idio-vol + POS skew — alpha or beta bet?")
    print(f"  K={K}/leg | hold={HOLD}d | cost={COST*1e4:.0f}bps/name | BETA_WIN={BETA_WIN}d")
    print("=" * 70)

    data = load()
    print(f"  {len(data)} coins loaded\n")

    btc_data = data.get("BTC")
    if btc_data is None:
        print("ERROR: BTC not in cache.")
        sys.exit(1)

    # ─── factor score fns (match edge_factors.py) ──────────────────────────────
    def score_idvol(cd, btcd, win_days):
        return _idio_vol_score(cd, btcd, win_days)

    def score_skew(cd, btcd, win_days):
        return _skew_score(cd, btcd, win_days)

    # ─── (A) HIGH IDIO-VOL ────────────────────────────────────────────────────
    print("\n>>> Running HIGH idio-vol factor (higher_is_long=True)…")
    records_iv = run_factor_full(data, btc_data, score_idvol, higher_is_long=True)
    result_iv = analyse("HIGH IDIO-VOL (long high vol / short low vol)", records_iv)

    # Within-tercile version
    print("\n  Alternative: within-beta-tercile L-S (idio-vol):")
    wt_iv = run_within_tercile(data, btc_data, score_idvol, higher_is_long=True)
    rep("  Within-tercile L-S (idio-vol)", wt_iv)

    # ─── (B) POS SKEW ─────────────────────────────────────────────────────────
    print("\n>>> Running POS-skew factor (higher_is_long=True)…")
    records_sk = run_factor_full(data, btc_data, score_skew, higher_is_long=True)
    result_sk = analyse("POS SKEW (long pos-skew / short neg-skew)", records_sk)

    # Within-tercile version
    print("\n  Alternative: within-beta-tercile L-S (skew):")
    wt_sk = run_within_tercile(data, btc_data, score_skew, higher_is_long=True)
    rep("  Within-tercile L-S (skew)", wt_sk)

    # ─── SUMMARY ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  FINAL SUMMARY")
    print("=" * 70)
    print()
    for label, r in [("HIGH idio-vol", result_iv), ("POS skew", result_sk)]:
        if r is None:
            continue
        print(f"  {label}:")
        print(f"    Net-beta of spread  : {r['avg_net_beta']:+.3f}  (0 = market-neutral; >>0 = bull bet)")
        print(f"    Raw L-S mean        : {r['raw_mean']:>+6.2f}%")
        print(f"    BN L-S mean         : {r['bn_mean']:>+6.2f}%  (after beta-neutralizing the legs)")
        print(f"    BN OOS both halves+ : {'YES' if r['bn_robust'] else 'NO'}")
        print(f"    Down-regime BN mean : {r['down_bn_mean']:>+6.2f}%")
        print(f"    VERDICT             : {r['verdict']}")
        print()

    print("  METHODOLOGY NOTE:")
    print("  Beta-neutralization method: scale short leg by (avg_long_beta/avg_short_beta)")
    print("  so net portfolio beta = 0. Within-tercile is an independent beta-sort control.")
    print("  Down-regime = worst Q1 BTC-forward-return rebalances (BTC fell most).")
    print("  If BN mean ≈ raw mean → factor IS alpha (beta-neutral to begin with).")
    print("  If BN mean << raw mean or flips negative → factor was just leveraged beta.")


if __name__ == "__main__":
    main()
