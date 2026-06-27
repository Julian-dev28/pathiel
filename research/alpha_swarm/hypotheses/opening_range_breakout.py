"""C7 opening_range_breakout — UTC opening-range break, regime-gated, EOD exit.

1h candles. For each UTC day, the first K hours set OR=[hi,lo]. After hour K, the FIRST
hourly close beyond OR triggers: close>hi -> long, close<lo -> short, filled next-bar open,
held to the day's last bar (EOD) with an optional protective stop. Regime gate: take longs
only in BTC-up, shorts only in BTC-down (and an ungated variant). Decide on bar close, fill
next open (lookahead-safe). Excess vs matched random intraday same-side entry + mc_null.
"""
from __future__ import annotations
import statistics, random
from collections import defaultdict
import alpha_lib as A
import mc_null

DAY = 86400000


def btc_regime(d, sma=20):
    btc = A.candles(d, "BTC", "1d")
    cl = [b[A.C] for b in btc]
    up = {}
    for i in range(len(btc)):
        if i < sma:
            continue
        up[btc[i][A.T] // DAY] = cl[i] > statistics.mean(cl[i - sma:i])
    return up


def day_groups(cd):
    g = defaultdict(list)
    for bar in cd:
        g[bar[A.T] // DAY].append(bar)
    return g


def stop_exit(entry, side, fwd, stop):
    """hold to end of fwd (EOD) unless stop hit. fwd are intraday bars after entry."""
    stop_px = entry * (1 - stop) if side == "long" else entry * (1 + stop)
    sign = 1 if side == "long" else -1
    for bar in fwd:
        if side == "long" and bar[A.L] <= stop_px:
            return A.pct(entry, stop_px)
        if side == "short" and bar[A.H] >= stop_px:
            return sign * A.pct(entry, stop_px)
    last = fwd[-1][A.C] if fwd else entry
    return sign * A.pct(entry, last)


def run():
    d = A.load_dataset()
    coins = d["coins"]
    up = btc_regime(d)
    series = {c: A.candles(d, c, "1h") for c in coins if len(A.candles(d, c, "1h")) >= 200}
    results, best = [], None
    for K in (3, 4, 6):
        for gate in ("regime", "none"):
            for stop in (0.05, 0.10, 0.20):
                trades = []
                for c, cd in series.items():
                    for day, bars in day_groups(cd).items():
                        if len(bars) < K + 4:
                            continue
                        orb = bars[:K]
                        hi = max(b[A.H] for b in orb)
                        lo = min(b[A.L] for b in orb)
                        reg_up = up.get(day, None)
                        # scan post-OR bars for first break (decide on close j, fill j+1 open)
                        for j in range(K, len(bars) - 1):
                            cl = bars[j][A.C]
                            side = None
                            if cl > hi:
                                side = "long"
                            elif cl < lo:
                                side = "short"
                            if side is None:
                                continue
                            if gate == "regime":
                                if reg_up is None:
                                    break
                                if side == "long" and not reg_up:
                                    break
                                if side == "short" and reg_up:
                                    break
                            entry = bars[j + 1][A.O]
                            ret = stop_exit(entry, side, bars[j + 2:], stop)
                            trades.append({"t": bars[j + 1][A.T], "ret": ret, "side": side})
                            break  # one trade per day
                if len(trades) < 30:
                    continue
                s = A.summarize(trades)
                h1 = s["oos_12bps"]["first_half_mean_pct"]
                h2 = s["oos_12bps"]["second_half_mean_pct"]
                rec = {"K": K, "gate": gate, "stop": stop, "n": len(trades), "trades": trades,
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
    print("K  gate    stop  n    ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['K']:<2} {r['gate']:<7} {r['stop']:<5} {r['n']:<4} {r['ev12']:<6} "
              f"{r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} {r['h1']} {r['h2']}")
    if best:
        print("BEST robust:", {k: best[k] for k in ("K", "gate", "stop", "n", "ev12", "ev25", "h1", "h2")})
        # side-matched null
        for side in ("long", "short"):
            grp = [t["ret"] for t in best["trades"] if t["side"] == side]
            if len(grp) < 10:
                continue
            # random intraday same-side pool
            rng = random.Random(0)
            pool = []
            keys = list(series.keys())
            while len(pool) < 2500:
                cd = series[keys[rng.randrange(len(keys))]]
                if len(cd) < 30:
                    continue
                i = rng.randrange(2, len(cd) - 8)
                pool.append(stop_exit(cd[i][A.O], side, cd[i + 1:i + 8], best["stop"]))
            print(f"  [{side}] n={len(grp)}", mc_null.shuffle_label_p(grp, pool, n_iter=4000, seed=1))
    else:
        print("No robust-both-halves cell.")


if __name__ == "__main__":
    run()
