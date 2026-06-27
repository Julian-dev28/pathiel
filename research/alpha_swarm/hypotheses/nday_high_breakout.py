"""C5 nday_high_breakout — positional: long a new N-day-high breakout, WIDE stop, BTC-up gate.

Daily. Entry: close_i > max(close[i-N..i-1]) (fresh N-day-high), BTC up-regime (BTC close >
20d SMA). Fill i+1 open. Horizon {5,10,20}d, stop sweep {8,15,20,25,40}%. Score EXCESS over
matched random LONG entries in the SAME BTC-up regime + mc_null. The -44% tape means a
random long is negative; the gate must beat random-long-in-up-regime, not zero.
"""
from __future__ import annotations
import statistics, random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def btc_up_flags(d, sma=20):
    btc = A.candles(d, "BTC", "1d")
    closes = [b[A.C] for b in btc]
    times = [b[A.T] for b in btc]
    up = {}
    for i in range(len(btc)):
        if i < sma:
            continue
        m = statistics.mean(closes[i - sma:i])
        up[times[i]] = closes[i] > m
    return up, times


def realize(entry, fwd, stop, horizon):
    return A.sweep_stop(entry, "long", fwd, [stop], horizon)[stop]


def build_pool(d, up_flags, stop, horizon, n=3000, seed=0):
    rng = random.Random(seed)
    coins = d["coins"]
    pool, tries = [], 0
    while len(pool) < n and tries < n * 8:
        tries += 1
        c = coins[rng.randrange(len(coins))]
        cd = A.candles(d, c, "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(25, len(cd) - horizon - 2)
        if not up_flags.get(cd[i][A.T], False):
            continue
        entry = cd[i + 1][A.O]
        pool.append(realize(entry, cd[i + 2:], stop, horizon))
    return pool


def run():
    d = A.load_dataset()
    coins = d["coins"]
    up_flags, _ = btc_up_flags(d)
    series = {c: A.candles(d, c, "1d") for c in coins if len(A.candles(d, c, "1d")) >= 60}
    results, best = [], None
    for N in (20, 50, 100):
        for horizon in (5, 10, 20):
            for stop in STOPS:
                trades = []
                for c, cd in series.items():
                    last = -999
                    for i in range(N, len(cd) - horizon - 2):
                        if not up_flags.get(cd[i][A.T], False):
                            continue
                        prior_high = max(cd[j][A.C] for j in range(i - N, i))
                        if cd[i][A.C] <= prior_high:
                            continue
                        if i - last < horizon:
                            continue
                        last = i
                        entry = cd[i + 1][A.O]
                        ret = realize(entry, cd[i + 2:], stop, horizon)
                        trades.append({"t": cd[i + 1][A.T], "ret": ret})
                if len(trades) < 25:
                    continue
                s = A.summarize(trades)
                h1 = s["oos_12bps"]["first_half_mean_pct"]
                h2 = s["oos_12bps"]["second_half_mean_pct"]
                rec = {"N": N, "horizon": horizon, "stop": stop, "n": len(trades),
                       "trades": trades, "ev12": s["slip12"]["mean_ret_pct"],
                       "ev25": s["slip25"]["mean_ret_pct"], "ev50": s["slip50"]["mean_ret_pct"],
                       "win": s["slip12"]["win_rate"], "h1": h1, "h2": h2}
                results.append(rec)
                robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                score = min(h1, h2) if (h1 and h2) else -99
                if robust and (best is None or score > best.get("score", -99)):
                    rec["score"] = score
                    best = rec
    results.sort(key=lambda r: r["ev25"], reverse=True)
    print("=== top 8 by EV@25bps (vs ZERO; excess computed for best) ===")
    print("N    hz  stop  n    ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['N']:<4} {r['horizon']:<3} {r['stop']:<5} {r['n']:<4} {r['ev12']:<6} "
              f"{r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")
    if best:
        print("\nBEST robust:", {k: best[k] for k in ("N", "horizon", "stop", "n", "ev12", "ev25", "h1", "h2")})
        grp = [t["ret"] for t in best["trades"]]
        pool = build_pool(d, up_flags, best["stop"], best["horizon"])
        import statistics as _st
        sl = mc_null.shuffle_label_p(grp, pool, n_iter=5000, seed=1)
        print("mc_null shuffle vs random-long-in-up-regime:", sl)
        bb = mc_null.block_bootstrap_p(pool, k=len(grp), observed_mean=_st.mean(grp),
                                       block_len=5, n_iter=5000, seed=2)
        print("mc_null block-bootstrap:", bb)
        # nearby-cell consistency: same N, all horizons/stops, count positive-EV@25
        fam = [r for r in results if r["N"] == best["N"]]
        pos = sum(1 for r in fam if r["ev25"] > 0)
        print(f"family N={best['N']}: {pos}/{len(fam)} cells EV@25bps>0")
    else:
        print("\nNo robust-both-halves cell.")
        # still measure excess for the top EV cell
        if results:
            top = results[0]
            grp = [t["ret"] for t in top["trades"]]
            pool = build_pool(d, up_flags, top["stop"], top["horizon"])
            print("top-EV cell mc_null vs random-long-in-up:", mc_null.shuffle_label_p(grp, pool, n_iter=5000, seed=1))


if __name__ == "__main__":
    run()
