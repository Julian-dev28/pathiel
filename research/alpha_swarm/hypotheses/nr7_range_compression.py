"""C10 nr7_range_compression — NR4/NR7 compression then breakout follow-through.

Daily. NRk = bar i whose (high-low) is the smallest of the last k bars (confirmed at i
close). Then a stop-entry break of the NRk bar's high (->long) or low (->short), whichever
triggers first within W bars, at the break LEVEL (lookahead-safe entry price). Hold horizon,
stop sweep. Regime variants: both / long-up-only / short-down-only. Excess vs random + null.
"""
from __future__ import annotations
import statistics, random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]
DAY = 86400000


def btc_up(d, sma=20):
    btc = A.candles(d, "BTC", "1d")
    cl = [b[A.C] for b in btc]
    up = {}
    for i in range(sma, len(btc)):
        up[btc[i][A.T] // DAY] = cl[i] > statistics.mean(cl[i - sma:i])
    return up


def realize(entry, side, fwd, stop, horizon):
    return A.sweep_stop(entry, side, fwd, [stop], horizon)[stop]


def build_pool(d, side, stop, horizon, up=None, regime_match=False, n=3000, seed=0):
    """regime_match: long pool only up-regime, short pool only down-regime (matches gate)."""
    rng = random.Random(seed)
    coins = d["coins"]
    pool, tries = [], 0
    while len(pool) < n and tries < n * 12:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], "1d")
        if len(cd) < 60:
            continue
        i = rng.randrange(10, len(cd) - horizon - 2)
        if regime_match and up is not None:
            ru = up.get(cd[i][A.T] // DAY)
            if ru is None or (side == "long" and not ru) or (side == "short" and ru):
                continue
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:], stop, horizon))
    return pool


def run():
    d = A.load_dataset()
    up = btc_up(d)
    series = {c: A.candles(d, c, "1d") for c in d["coins"] if len(A.candles(d, c, "1d")) >= 60}
    W = 3
    results, best = [], None
    for k in (4, 7):
        for gate in ("both", "regime"):
            for horizon in (3, 5, 10):
                for stop in STOPS:
                    trades = []
                    for c, cd in series.items():
                        last = -999
                        for i in range(k, len(cd) - horizon - W - 2):
                            rng_i = cd[i][A.H] - cd[i][A.L]
                            if rng_i != min(cd[j][A.H] - cd[j][A.L] for j in range(i - k + 1, i + 1)):
                                continue
                            if i - last < horizon:
                                continue
                            nh, nl = cd[i][A.H], cd[i][A.L]
                            side = entry = ej = None
                            for j in range(i + 1, i + 1 + W):
                                if cd[j][A.H] >= nh:
                                    side, entry, ej = "long", nh, j
                                    break
                                if cd[j][A.L] <= nl:
                                    side, entry, ej = "short", nl, j
                                    break
                            if side is None:
                                continue
                            day = cd[ej][A.T] // DAY
                            if gate == "regime":
                                ru = up.get(day)
                                if ru is None or (side == "long" and not ru) or (side == "short" and ru):
                                    continue
                            last = i
                            ret = realize(entry, side, cd[ej + 1:], stop, horizon)
                            trades.append({"t": cd[ej][A.T], "side": side, "ret": ret})
                    if len(trades) < 30:
                        continue
                    s = A.summarize(trades)
                    h1 = s["oos_12bps"]["first_half_mean_pct"]
                    h2 = s["oos_12bps"]["second_half_mean_pct"]
                    rec = {"k": k, "gate": gate, "horizon": horizon, "stop": stop,
                           "n": len(trades), "trades": trades, "ev12": s["slip12"]["mean_ret_pct"],
                           "ev25": s["slip25"]["mean_ret_pct"], "ev50": s["slip50"]["mean_ret_pct"],
                           "win": s["slip12"]["win_rate"], "h1": h1, "h2": h2}
                    results.append(rec)
                    robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                    score = min(h1, h2) if (h1 and h2) else -99
                    if robust and (best is None or score > best.get("score", -99)):
                        rec["score"] = score
                        best = rec
    results.sort(key=lambda r: r["ev25"], reverse=True)
    print("=== top 8 by EV@25bps ===")
    print("k gate    hz stop  n    ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['k']:<2}{r['gate']:<8}{r['horizon']:<3}{r['stop']:<6}{r['n']:<5}{r['ev12']:<7}"
              f"{r['ev25']:<7}{r['ev50']:<7}{r['win']:<6}{r['h1']} {r['h2']}")
    if best:
        print("BEST robust:", {kk: best[kk] for kk in ("k", "gate", "horizon", "stop", "n", "ev12", "ev25", "h1", "h2")})
        for side in ("long", "short"):
            grp = [t["ret"] for t in best["trades"] if t["side"] == side]
            if len(grp) < 10:
                continue
            pool = build_pool(d, side, best["stop"], best["horizon"], up=up,
                              regime_match=(best["gate"] == "regime"))
            r = mc_null.shuffle_label_p(grp, pool, n_iter=4000, seed=1)
            # short-side OOS both-halves on its own
            sh = A.summarize([t for t in best["trades"] if t["side"] == side])
            print(f"  [{side}] n={len(grp)} regime-matched-null {r}  oos={sh['oos_12bps']}")
    else:
        print("No robust-both-halves cell.")


if __name__ == "__main__":
    run()
