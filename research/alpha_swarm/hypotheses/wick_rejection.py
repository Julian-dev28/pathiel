"""C4 wick_rejection — long after a large lower-wick rejection, short after upper-wick.

1h and aggregated-4h. Decide on bar i close, fill i+1 open. Sweep wick/body ratio + stop.
Score EXCESS over matched random-entry (same side/stop/horizon) + mc_null shuffle-label p.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def agg(cd, k):
    """aggregate k consecutive bars into one OHLCV (4h from 1h => k=4)."""
    out = []
    for i in range(0, len(cd) - k + 1, k):
        block = cd[i:i + k]
        out.append([block[0][A.T], block[0][A.O], max(b[A.H] for b in block),
                    min(b[A.L] for b in block), block[-1][A.C], sum(b[A.V] for b in block)])
    return out


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def wick(cd, i):
    o, h, l, c = cd[i][A.O], cd[i][A.H], cd[i][A.L], cd[i][A.C]
    body = abs(c - o) + 1e-12
    lower = min(o, c) - l
    upper = h - max(o, c)
    return lower, upper, body


def build_pool(series, side, stop, horizon, n=3000, seed=0):
    rng = random.Random(seed)
    keys = list(series.keys())
    pool, tries = [], 0
    while len(pool) < n and tries < n * 6:
        tries += 1
        cd = series[keys[rng.randrange(len(keys))]]
        if len(cd) < horizon + 6:
            continue
        i = rng.randrange(3, len(cd) - horizon - 2)
        entry = cd[i + 1][A.O]
        pool.append(realize(entry, side, cd[i + 2:], stop, horizon))
    return pool


def run_tf(d, tf, k, horizons):
    coins = d["coins"]
    series = {}
    for c in coins:
        cd = A.candles(d, c, "1h")
        if k > 1:
            cd = agg(cd, k)
        if len(cd) >= 60:
            series[c] = cd
    results, best = [], None
    for mode in ("lower", "upper"):
        side = "long" if mode == "lower" else "short"
        for R in (1.5, 2.0, 3.0):
            for horizon in horizons:
                for stop in STOPS:
                    trades = []
                    for c, cd in series.items():
                        last = -999
                        for i in range(3, len(cd) - horizon - 2):
                            lo, up, body = wick(cd, i)
                            if mode == "lower":
                                hit = lo > R * body and lo > up
                            else:
                                hit = up > R * body and up > lo
                            if not hit or i - last < horizon:
                                continue
                            last = i
                            entry = cd[i + 1][A.O]
                            ret = realize(entry, side, cd[i + 2:], stop, horizon)
                            trades.append({"t": cd[i + 1][A.T], "ret": ret})
                    if len(trades) < 30:
                        continue
                    s = A.summarize(trades)
                    h1 = s["oos_12bps"]["first_half_mean_pct"]
                    h2 = s["oos_12bps"]["second_half_mean_pct"]
                    rec = {"tf": tf, "mode": mode, "side": side, "R": R, "horizon": horizon,
                           "stop": stop, "n": len(trades), "trades": trades,
                           "ev12": s["slip12"]["mean_ret_pct"], "ev25": s["slip25"]["mean_ret_pct"],
                           "ev50": s["slip50"]["mean_ret_pct"], "win": s["slip12"]["win_rate"],
                           "h1": h1, "h2": h2}
                    results.append(rec)
                    robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                    score = min(h1, h2) if (h1 and h2) else -99
                    if robust and (best is None or score > best.get("score", -99)):
                        rec["score"] = score
                        best = rec
    return series, results, best


def run():
    d = A.load_dataset()
    for tf, k, hz in (("1h", 1, (6, 12, 24)), ("4h", 4, (3, 6, 12))):
        series, results, best = run_tf(d, tf, k, hz)
        results.sort(key=lambda r: r["ev25"], reverse=True)
        print(f"\n===== {tf} : top 6 by EV@25bps =====")
        print("mode  side  R   hz stop  n    ev12   ev25   ev50  win   h1     h2")
        for r in results[:6]:
            print(f"{r['mode']:<5} {r['side']:<5} {r['R']:<3} {r['horizon']:<2} {r['stop']:<5} "
                  f"{r['n']:<4} {r['ev12']:<6} {r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} "
                  f"{r['h1']} {r['h2']}")
        if best:
            print(f"  BEST robust {tf}:", {kk: best[kk] for kk in
                  ("mode", "side", "R", "horizon", "stop", "n", "ev12", "ev25", "h1", "h2")})
            grp = [t["ret"] for t in best["trades"]]
            pool = build_pool(series, best["side"], best["stop"], best["horizon"])
            print("  mc_null:", mc_null.shuffle_label_p(grp, pool, n_iter=5000, seed=1))
        else:
            print(f"  No robust-both-halves cell on {tf}.")


if __name__ == "__main__":
    run()
