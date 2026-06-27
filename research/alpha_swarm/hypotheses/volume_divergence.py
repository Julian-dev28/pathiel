"""C3 volume_divergence — price-trend vs volume-trend divergence (daily).

Classic read: price up but volume fading = unsupported move -> fade (short);
price down but volume fading = selling exhaustion -> reversal (long). Contrarian.
Lookahead-safe: decide on bar i close, fill i+1 open. Excess over matched random-entry
baseline + mc_null shuffle-label p. Stop-width sweep (it's a fade).
"""
from __future__ import annotations
import statistics, random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def vol_trend(cd, i, L):
    """ratio of recent-half avg vol to prior-half avg vol over [i-L, i]. <0 => fading."""
    half = L // 2
    recent = [cd[j][A.V] for j in range(i - half + 1, i + 1)]
    prior = [cd[j][A.V] for j in range(i - L + 1, i - half + 1)]
    if not recent or not prior or statistics.mean(prior) == 0:
        return None
    return statistics.mean(recent) / statistics.mean(prior) - 1.0


def build_pool(d, side, stop, horizon, n=3000, seed=0):
    rng = random.Random(seed)
    coins = d["coins"]
    pool = []
    tries = 0
    while len(pool) < n and tries < n * 6:
        tries += 1
        coin = coins[rng.randrange(len(coins))]
        cd = A.candles(d, coin, "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(30, len(cd) - horizon - 2)
        entry = cd[i + 1][A.O]
        pool.append(realize(entry, side, cd[i + 2:], stop, horizon))
    return pool


def run():
    d = A.load_dataset()
    coins = d["coins"]
    series = {c: A.candles(d, c, "1d") for c in coins if len(A.candles(d, c, "1d")) >= 60}
    results = []
    best = None
    # mode 'bear' = price up + vol fade -> short ; 'bull' = price down + vol fade -> long
    for mode in ("bear", "bull"):
        side = "short" if mode == "bear" else "long"
        for L in (10, 20):
            for pthr in (0.05, 0.10, 0.15):
                for horizon in (3, 5, 10):
                    for stop in STOPS:
                        trades = []
                        for coin, cd in series.items():
                            last = -999
                            for i in range(L + 1, len(cd) - horizon - 2):
                                pret = A.pct(cd[i - L][A.C], cd[i][A.C])
                                vt = vol_trend(cd, i, L)
                                if vt is None:
                                    continue
                                hit = ((mode == "bear" and pret > pthr and vt < 0) or
                                       (mode == "bull" and pret < -pthr and vt < 0))
                                if not hit:
                                    continue
                                if i - last < horizon:
                                    continue
                                last = i
                                entry = cd[i + 1][A.O]
                                ret = realize(entry, side, cd[i + 2:], stop, horizon)
                                trades.append({"t": cd[i + 1][A.T], "ret": ret})
                        if len(trades) < 25:
                            continue
                        s = A.summarize(trades)
                        h1 = s["oos_12bps"]["first_half_mean_pct"]
                        h2 = s["oos_12bps"]["second_half_mean_pct"]
                        rec = {"mode": mode, "side": side, "L": L, "pthr": pthr,
                               "horizon": horizon, "stop": stop, "n": len(trades),
                               "trades": trades, "ev12": s["slip12"]["mean_ret_pct"],
                               "ev25": s["slip25"]["mean_ret_pct"],
                               "ev50": s["slip50"]["mean_ret_pct"],
                               "win": s["slip12"]["win_rate"], "h1": h1, "h2": h2}
                        results.append(rec)
                        robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                        score = min(h1, h2) if (h1 and h2) else -99
                        if robust and (best is None or score > best.get("score", -99)):
                            rec["score"] = score
                            best = rec
    results.sort(key=lambda r: r["ev25"], reverse=True)
    print("=== top 8 by EV@25bps ===")
    print("mode  side  L  pthr  hz stop  n    ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['mode']:<5} {r['side']:<5} {r['L']:<2} {r['pthr']:<5} {r['horizon']:<2} "
              f"{r['stop']:<5} {r['n']:<4} {r['ev12']:<6} {r['ev25']:<6} {r['ev50']:<6} "
              f"{r['win']:<5} {r['h1']} {r['h2']}")
    if best:
        print("\n=== BEST robust cell ===", {k: best[k] for k in
              ("mode", "side", "L", "pthr", "horizon", "stop", "n", "ev12", "ev25", "h1", "h2")})
        grp = [t["ret"] for t in best["trades"]]
        pool = build_pool(d, best["side"], best["stop"], best["horizon"])
        r = mc_null.shuffle_label_p(grp, pool, n_iter=5000, seed=1)
        print("  mc_null:", r)
    else:
        print("\nNo robust-both-halves cell.")


if __name__ == "__main__":
    run()
