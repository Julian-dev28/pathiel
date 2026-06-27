"""btc_leadlag: does a strong BTC bar predict alt returns in the NEXT bar?
Lookahead-safe: BTC signal known at close[i]; alts FILLED at i+1 open, exited i+1 close (or i+2).
All EV reported per slippage tier + OOS halves via alpha_lib.summarize.
"""
import statistics
import alpha_lib as A

d = A.load_dataset()
COINS = d["coins"]
ALTS = [c for c in COINS if c not in ("BTC", "ETH")]
STEP = {"5m": 300000, "1h": 3600000}


def bymap(coin, iv):
    return {r[A.T]: r for r in A.candles(d, coin, iv)}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return cov / (vx ** 0.5 * vy ** 0.5)


def ols_beta(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    if vx <= 0:
        return 0.0, 0.0
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx
    alpha = my - beta * mx
    return beta, alpha


# ---------------------------------------------------------------------------
# 1. Cross-correlation / regression: alt[i+1] open->close  vs  BTC[i] close-to-close
# ---------------------------------------------------------------------------
def leadlag_stats(iv):
    step = STEP[iv]
    btc = bymap("BTC", iv)
    btc_ts = sorted(btc)
    # BTC bar-i return (close-to-close), known at close[i]
    btc_ret = {}
    for i in range(1, len(btc_ts)):
        t, tp = btc_ts[i], btc_ts[i - 1]
        if t - tp == step:
            btc_ret[t] = A.pct(btc[tp][A.C], btc[t][A.C])
    results = {}
    pooled_x, pooled_y = [], []
    # also a SAME-bar (contemporaneous) corr for reference (alt[i] vs btc[i])
    pooled_same_x, pooled_same_y = [], []
    for coin in ALTS:
        am = bymap(coin, iv)
        xs, ys = [], []
        sx, sy = [], []
        for t, br in btc_ret.items():
            tnext = t + step
            if tnext in am:
                nb = am[tnext]
                ay = A.pct(nb[A.O], nb[A.C])  # tradeable next-bar open->close
                xs.append(br); ys.append(ay)
                pooled_x.append(br); pooled_y.append(ay)
            if t in am and (t - step) in am:
                cb = am[t]
                sx.append(br); sy.append(A.pct(am[t - step][A.C], cb[A.C]))
                pooled_same_x.append(br); pooled_same_y.append(A.pct(am[t - step][A.C], cb[A.C]))
        beta, _ = ols_beta(xs, ys)
        results[coin] = {"n": len(xs), "lead_corr": pearson(xs, ys), "lead_beta": beta}
    pooled = {
        "lead_corr": pearson(pooled_x, pooled_y),
        "lead_beta": ols_beta(pooled_x, pooled_y)[0],
        "same_bar_corr": pearson(pooled_same_x, pooled_same_y),
        "n": len(pooled_x),
    }
    return results, pooled, btc_ret


# ---------------------------------------------------------------------------
# 2. Tradeable directional rule: BTC bar-i ret > thr  -> long alts at i+1 open
#    exit at i+1 close (hold=1) or i+2 close (hold=2). Symmetric short on < -thr.
# ---------------------------------------------------------------------------
def trade_rule(iv, thr, hold=1, top_beta=None, betas=None):
    step = STEP[iv]
    btc = bymap("BTC", iv)
    btc_ts = sorted(btc)
    btc_ret = {}
    for i in range(1, len(btc_ts)):
        t, tp = btc_ts[i], btc_ts[i - 1]
        if t - tp == step:
            btc_ret[t] = A.pct(btc[tp][A.C], btc[t][A.C])
    universe = ALTS
    if top_beta and betas:
        universe = sorted(ALTS, key=lambda c: betas.get(c, {}).get("lead_beta", 0), reverse=True)[:top_beta]
    trades = []
    for coin in universe:
        am = bymap(coin, iv)
        for t, br in btc_ret.items():
            side = None
            if br > thr:
                side = "long"
            elif br < -thr:
                side = "short"
            if side is None:
                continue
            t1 = t + step  # entry bar (fill at open)
            if t1 not in am:
                continue
            entry = am[t1][A.O]
            # exit bar close
            texit = t + step * hold
            if texit not in am:
                continue
            exitpx = am[texit][A.C]
            ret = A.pct(entry, exitpx)
            if side == "short":
                ret = -ret
            trades.append({"t": t1, "ret": ret, "coin": coin, "side": side})
    return trades


# ---------------------------------------------------------------------------
# 3. Cross-sectional catch-up: among bars where BTC moved > thr, long the alts
#    that LAGGED most in bar i (smallest same-bar move vs BTC) expecting catch-up i+1.
# ---------------------------------------------------------------------------
def crosssec_rule(iv, thr, k=8, hold=1):
    step = STEP[iv]
    btc = bymap("BTC", iv)
    btc_ts = sorted(btc)
    btc_ret = {}
    for i in range(1, len(btc_ts)):
        t, tp = btc_ts[i], btc_ts[i - 1]
        if t - tp == step:
            btc_ret[t] = A.pct(btc[tp][A.C], btc[t][A.C])
    altmaps = {c: bymap(c, iv) for c in ALTS}
    trades = []
    for t, br in btc_ret.items():
        if abs(br) < thr:
            continue
        side_dir = 1 if br > 0 else -1
        # measure each alt's bar-i move; gap = btc move - alt move (how much it lagged)
        gaps = []
        for c in ALTS:
            am = altmaps[c]
            if t in am and (t - step) in am:
                amove = A.pct(am[t - step][A.C], am[t][A.C])
                gap = (br - amove) * side_dir  # positive = alt lagged in btc's direction
                gaps.append((gap, c))
        if len(gaps) < k:
            continue
        gaps.sort(reverse=True)  # biggest laggards first
        picks = [c for _, c in gaps[:k]]
        for c in picks:
            am = altmaps[c]
            t1 = t + step
            texit = t + step * hold
            if t1 not in am or texit not in am:
                continue
            entry = am[t1][A.O]
            ret = A.pct(entry, am[texit][A.C]) * side_dir
            trades.append({"t": t1, "ret": ret, "coin": c})
    return trades


def fmt(s):
    o = []
    for bps in A.SLIP_TIERS_BPS:
        k = f"slip{bps}"
        if k in s:
            r = s[k]
            o.append(f"  {bps:>2}bps: mean={r['mean_ret_pct']:+.4f}% win={r['win_rate']:.3f} sharpe={r['sharpe_like']:+.3f} tot={r['total_pct']:+.1f}%")
    oos = s.get("oos_12bps", {})
    o.append(f"  OOS@12bps h1={oos.get('first_half_mean_pct')} h2={oos.get('second_half_mean_pct')} (n {oos.get('n_first')}/{oos.get('n_second')})")
    o.append(f"  n={s.get('n')}  VERDICT={s.get('verdict')}")
    return "\n".join(o)


if __name__ == "__main__":
    out = []
    def p(x=""):
        print(x); out.append(str(x))

    for iv in ["5m", "1h"]:
        p("=" * 78)
        p(f"INTERVAL {iv}")
        p("=" * 78)
        res, pooled, _ = leadlag_stats(iv)
        p(f"POOLED lead-lag (alt[i+1] open->close ON btc[i] cc-return):")
        p(f"  n={pooled['n']}  lead_corr={pooled['lead_corr']:+.4f}  lead_beta={pooled['lead_beta']:+.4f}")
        p(f"  (reference contemporaneous same-bar corr={pooled['same_bar_corr']:+.4f})")
        top = sorted(res.items(), key=lambda kv: kv[1]["lead_corr"], reverse=True)
        p("  top-5 alts by lead_corr: " + ", ".join(f"{c}={v['lead_corr']:+.3f}" for c, v in top[:5]))
        p("  bot-5 alts by lead_corr: " + ", ".join(f"{c}={v['lead_corr']:+.3f}" for c, v in top[-5:]))

        thrs = {"5m": [0.003, 0.005, 0.01], "1h": [0.005, 0.01, 0.02]}[iv]
        for thr in thrs:
            for hold in [1, 2]:
                tr = trade_rule(iv, thr, hold=hold)
                s = A.summarize(tr)
                p(f"\nDIRECTIONAL all-alts thr={thr*100:.1f}% hold={hold}bar")
                p(fmt(s))
        # top-beta subset
        tb = trade_rule(iv, thrs[0], hold=1, top_beta=10, betas=res)
        s = A.summarize(tb)
        p(f"\nDIRECTIONAL top-10-beta thr={thrs[0]*100:.1f}% hold=1")
        p(fmt(s))
        # cross-sectional catch-up
        for thr in thrs:
            cs = crosssec_rule(iv, thr, k=8, hold=1)
            s = A.summarize(cs)
            p(f"\nCROSS-SEC catch-up (long top-8 laggards) thr={thr*100:.1f}% hold=1")
            p(fmt(s))

    open("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad/_btc_leadlag_out.txt", "w").write("\n".join(out))
