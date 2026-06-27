"""C8 vwap_reversion — fade extreme intraday VWAP deviation on 5m. Cost-brutal.

Session VWAP resets each UTC day, cumulative typical-price*vol. At bar i, dev=(close-vwap)/
vwap using bars up to i (lookahead-safe). |dev|>thr -> fade toward VWAP (above->short,
below->long). Fill i+1 open. Exit: VWAP touch or horizon, with stop sweep. Excess vs random
same-side + null. Report slippage decay 0->50bps (the whole point on 5m).
"""
from __future__ import annotations
import random
from collections import defaultdict
import alpha_lib as A
import mc_null

DAY = 86400000
STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def day_vwap_dev(cd):
    """returns dev[i] = (close_i - vwap_upto_i)/vwap, per UTC day reset."""
    dev = [None] * len(cd)
    cum_pv = cum_v = 0.0
    cur_day = None
    for i, bar in enumerate(cd):
        day = bar[A.T] // DAY
        if day != cur_day:
            cum_pv = cum_v = 0.0
            cur_day = day
        tp = (bar[A.H] + bar[A.L] + bar[A.C]) / 3.0
        cum_pv += tp * bar[A.V]
        cum_v += bar[A.V]
        if cum_v > 0:
            vwap = cum_pv / cum_v
            dev[i] = (bar[A.C] - vwap) / vwap if vwap else None
    return dev


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def build_pool(series, side, stop, horizon, n=2500, seed=0):
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


def run():
    d = A.load_dataset()
    coins = d["coins"]
    series, devs = {}, {}
    for c in coins:
        cd = A.candles(d, c, "5m")
        if len(cd) >= 200:
            series[c] = cd
            devs[c] = day_vwap_dev(cd)
    results, best = [], None
    for thr in (0.005, 0.01, 0.02):
        for horizon in (6, 12, 24):
            for stop in STOPS:
                trades = []
                for c, cd in series.items():
                    dv = devs[c]
                    last = -999
                    for i in range(3, len(cd) - horizon - 2):
                        x = dv[i]
                        if x is None or abs(x) < thr:
                            continue
                        if i - last < horizon:
                            continue
                        side = "short" if x > 0 else "long"
                        last = i
                        trades.append({"t": cd[i + 1][A.T], "side": side,
                                       "ret": realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon)})
                if len(trades) < 40:
                    continue
                s = A.summarize(trades)
                h1 = s["oos_12bps"]["first_half_mean_pct"]
                h2 = s["oos_12bps"]["second_half_mean_pct"]
                rec = {"thr": thr, "horizon": horizon, "stop": stop, "n": len(trades),
                       "trades": trades, "ev0": s["slip0"]["mean_ret_pct"],
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
    print("=== top 8 by EV@25bps (note ev0 -> ev50 decay) ===")
    print("thr    hz stop  n     ev0    ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['thr']:<6} {r['horizon']:<2} {r['stop']:<5} {r['n']:<5} {r['ev0']:<6} "
              f"{r['ev12']:<6} {r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")
    if best:
        print("BEST robust:", {k: best[k] for k in ("thr", "horizon", "stop", "n", "ev0", "ev12", "ev25", "h1", "h2")})
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
