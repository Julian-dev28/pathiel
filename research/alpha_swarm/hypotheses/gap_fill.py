"""C11 gap_fill — fade hourly gaps (low-liquidity-hour jumps) toward the pre-gap close.

1h candles. Gap at i+1 = |open_{i+1}/close_i - 1| > G. Gap-up -> fade SHORT (target = prior
close = fill); gap-down -> fade LONG. Entry at i+1 open (the gapped, tradeable price). Hold
horizon, stop sweep (it's a fade). Also report fill probability (touches prior close within
horizon). Excess vs matched random same-side + mc_null.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def build_pool(series, side, stop, horizon, n=3000, seed=0):
    rng = random.Random(seed)
    keys = list(series.keys())
    pool, tries = [], 0
    while len(pool) < n and tries < n * 6:
        tries += 1
        cd = series[keys[rng.randrange(len(keys))]]
        if len(cd) < horizon + 6:
            continue
        i = rng.randrange(2, len(cd) - horizon - 2)
        pool.append(realize(cd[i][A.O], side, cd[i + 1:], stop, horizon))
    return pool


def run():
    d = A.load_dataset()
    series = {c: A.candles(d, c, "1h") for c in d["coins"] if len(A.candles(d, c, "1h")) >= 200}
    results, best = [], None
    for G in (0.005, 0.01, 0.02):
        for horizon in (6, 12, 24):
            for stop in STOPS:
                trades = []
                fills = 0
                for c, cd in series.items():
                    last = -999
                    for i in range(2, len(cd) - horizon - 2):
                        pc = cd[i][A.C]
                        op = cd[i + 1][A.O]
                        g = (op - pc) / pc if pc else 0
                        if abs(g) < G or (i + 1) - last < horizon:
                            continue
                        last = i + 1
                        side = "short" if g > 0 else "long"
                        target = pc  # fill level
                        entry = op
                        fwd = cd[i + 2:i + 2 + horizon]
                        # fill probability
                        filled = any((bar[A.L] <= target <= bar[A.H]) for bar in fwd)
                        fills += 1 if filled else 0
                        ret = realize(entry, side, cd[i + 2:], stop, horizon)
                        trades.append({"t": cd[i + 1][A.T], "side": side, "ret": ret})
                if len(trades) < 30:
                    continue
                s = A.summarize(trades)
                h1 = s["oos_12bps"]["first_half_mean_pct"]
                h2 = s["oos_12bps"]["second_half_mean_pct"]
                rec = {"G": G, "horizon": horizon, "stop": stop, "n": len(trades),
                       "fill_rate": round(fills / len(trades), 3), "trades": trades,
                       "ev12": s["slip12"]["mean_ret_pct"], "ev25": s["slip25"]["mean_ret_pct"],
                       "ev50": s["slip50"]["mean_ret_pct"], "win": s["slip12"]["win_rate"],
                       "h1": h1, "h2": h2}
                results.append(rec)
                robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                score = min(h1, h2) if (h1 and h2) else -99
                if robust and (best is None or score > best.get("score", -99)):
                    rec["score"] = score
                    best = rec
    results.sort(key=lambda r: r["ev25"], reverse=True)
    print("=== top 8 by EV@25bps ===")
    print("G      hz stop  n     fill  ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['G']:<6} {r['horizon']:<2} {r['stop']:<5} {r['n']:<5} {r['fill_rate']:<5} "
              f"{r['ev12']:<6} {r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")
    if best:
        print("BEST robust:", {k: best[k] for k in ("G", "horizon", "stop", "n", "fill_rate", "ev12", "ev25", "h1", "h2")})
        for side in ("long", "short"):
            grp = [t["ret"] for t in best["trades"] if t["side"] == side]
            if len(grp) < 10:
                continue
            pool = build_pool(series, side, best["stop"], best["horizon"])
            print(f"  [{side}] n={len(grp)}", mc_null.shuffle_label_p(grp, pool, n_iter=4000, seed=1))
    else:
        print("No robust-both-halves cell.")


if __name__ == "__main__":
    run()
