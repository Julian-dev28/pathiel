"""C6 round_number_magnet — rejection at psychological round levels.

Grid per coin scaled to price: g = 10^floor(log10(close))/10 (round thousands for BTC,
round tens for SOL, round dollars sub-$10). Levels = integer multiples of g.
Rejection-short: bar pierces a level from below (high>=L) but open<L and close<L -> short.
Rejection-long:  bar pierces from above (low<=L) but open>L and close>L -> long.
Decide i close, fill i+1 open. Stop sweep (fade). Excess vs matched random same-side + null.
"""
from __future__ import annotations
import math, random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def grid(px):
    if px <= 0:
        return None
    return 10 ** math.floor(math.log10(px)) / 10.0


def pierced_level(o, h, l, c, side):
    g = grid(c)
    if not g:
        return False
    if side == "short":  # high pierces a round level above, closes back below
        L = math.ceil(max(o, c) / g) * g  # nearest level above the body
        return h >= L and o < L and c < L and (h - max(o, c)) > 0
    else:               # low pierces a round level below, closes back above
        L = math.floor(min(o, c) / g) * g
        return l <= L and o > L and c > L and (min(o, c) - l) > 0


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
        i = rng.randrange(3, len(cd) - horizon - 2)
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon))
    return pool


def run_tf(d, iv, horizons):
    coins = d["coins"]
    series = {c: A.candles(d, c, iv) for c in coins if len(A.candles(d, c, iv)) >= 60}
    results, best = [], None
    for side in ("short", "long"):
        for horizon in horizons:
            for stop in STOPS:
                trades = []
                for c, cd in series.items():
                    last = -999
                    for i in range(3, len(cd) - horizon - 2):
                        o, h, l, cl = cd[i][A.O], cd[i][A.H], cd[i][A.L], cd[i][A.C]
                        if not pierced_level(o, h, l, cl, side):
                            continue
                        if i - last < horizon:
                            continue
                        last = i
                        trades.append({"t": cd[i + 1][A.T],
                                       "ret": realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon)})
                if len(trades) < 30:
                    continue
                s = A.summarize(trades)
                h1 = s["oos_12bps"]["first_half_mean_pct"]
                h2 = s["oos_12bps"]["second_half_mean_pct"]
                rec = {"iv": iv, "side": side, "horizon": horizon, "stop": stop,
                       "n": len(trades), "trades": trades, "ev12": s["slip12"]["mean_ret_pct"],
                       "ev25": s["slip25"]["mean_ret_pct"], "ev50": s["slip50"]["mean_ret_pct"],
                       "win": s["slip12"]["win_rate"], "h1": h1, "h2": h2}
                results.append(rec)
                robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                score = min(h1, h2) if (h1 and h2) else -99
                if robust and (best is None or score > best.get("score", -99)):
                    rec["score"] = score
                    best = rec
    return series, results, best


def run():
    d = A.load_dataset()
    for iv, hz in (("1d", (2, 3, 5)), ("1h", (6, 12, 24))):
        series, results, best = run_tf(d, iv, hz)
        results.sort(key=lambda r: r["ev25"], reverse=True)
        print(f"\n===== {iv}: top 6 by EV@25bps =====")
        print("side  hz stop  n    ev12   ev25   ev50  win   h1     h2")
        for r in results[:6]:
            print(f"{r['side']:<5} {r['horizon']:<2} {r['stop']:<5} {r['n']:<4} {r['ev12']:<6} "
                  f"{r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")
        if best:
            print("BEST robust:", {k: best[k] for k in ("side", "horizon", "stop", "n", "ev12", "ev25", "h1", "h2")})
            grp = [t["ret"] for t in best["trades"]]
            pool = build_pool(series, best["side"], best["stop"], best["horizon"])
            print("mc_null:", mc_null.shuffle_label_p(grp, pool, n_iter=5000, seed=1))
        else:
            print(f"No robust-both-halves cell on {iv}.")


if __name__ == "__main__":
    run()
