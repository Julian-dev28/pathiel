"""C2 liquidation_cascade_fade — fade the 5m forced-liquidation overshoot.

Signal (5m, lookahead-safe, decided on bar i close): a violent bar where
  range_i/close > N * ATR20%   AND   vol_i > M * avg_vol20
is a liquidation-cascade signature. Direction of fade = against the bar:
  red cascade (c<o) -> liquidation DOWN -> fade LONG; green -> fade SHORT.
Entry: fill at open of bar i+1+delay (delay sweeps the falling-knife). Exit: stop-width
sweep {8,15,20,25,40}% + horizon in 5m bars. Score as EXCESS over a side-matched random
entry baseline + mc_null shuffle-label p. Fees dominate on 5m -> slippage sweep is decisive.
"""
from __future__ import annotations
import statistics, random
import alpha_lib as A
import mc_null

STOPS = [0.08, 0.15, 0.20, 0.25, 0.40]


def atr_pct_series(cd, look=20):
    """ATR% at bar i using bars [i-look, i-1] (no lookahead)."""
    n = len(cd)
    out = [None] * n
    trs = [0.0] * n
    for i in range(1, n):
        h, l, pc = cd[i][A.H], cd[i][A.L], cd[i - 1][A.C]
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    for i in range(n):
        if i - look < 1:
            continue
        atr = statistics.mean(trs[i - look:i])
        c = cd[i][A.C]
        out[i] = atr / c if c else None
    return out


def avg_vol_series(cd, look=20):
    n = len(cd)
    out = [None] * n
    for i in range(n):
        if i - look < 0:
            continue
        vs = [cd[j][A.V] for j in range(i - look, i)]
        out[i] = statistics.mean(vs) if vs else None
    return out


def realize(entry_px, side, fwd, stop, horizon):
    r = A.sweep_stop(entry_px, side, fwd, [stop], horizon)
    return r[stop]


def build_pool(d, side, stop, horizon, delay, n=2500, seed=0):
    """Side-matched random-entry baseline returns from random 5m bars."""
    rng = random.Random(seed)
    coins = d["coins"]
    pool = []
    tries = 0
    while len(pool) < n and tries < n * 6:
        tries += 1
        coin = coins[rng.randrange(len(coins))]
        cd = A.candles(d, coin, "5m")
        if len(cd) < 60:
            continue
        i = rng.randrange(40, len(cd) - horizon - delay - 2)
        ei = i + 1 + delay
        if ei >= len(cd):
            continue
        entry = cd[ei][A.O]
        pool.append(realize(entry, side, cd[ei + 1:], stop, horizon))
    return pool


def run():
    d = A.load_dataset()
    coins = d["coins"]
    # precompute series once
    series = {}
    for coin in coins:
        cd = A.candles(d, coin, "5m")
        if len(cd) < 100:
            continue
        series[coin] = (cd, atr_pct_series(cd), avg_vol_series(cd))

    best = None
    results = []
    for N in (2.5, 3.5):
        for M in (3.0, 5.0):
            # find raw signal bars once per (N,M) with a generous max-horizon de-cluster
            sig_bars = {}
            for coin, (cd, atrp, avol) in series.items():
                bars = []
                last_sig = -999
                for i in range(40, len(cd) - 2):
                    ap, av = atrp[i], avol[i]
                    if ap is None or av is None or av <= 0:
                        continue
                    c, o = cd[i][A.C], cd[i][A.O]
                    rng_i = (cd[i][A.H] - cd[i][A.L]) / c if c else 0
                    if rng_i < N * ap or cd[i][A.V] < M * av:
                        continue
                    if i - last_sig < 12:  # min spacing; per-horizon de-cluster below
                        continue
                    last_sig = i
                    bars.append((i, "long" if c < o else "short"))
                sig_bars[coin] = bars
            for delay in (0, 1, 2):
                for horizon in (12, 24, 48):
                    for stop in STOPS:
                        trades = []
                        for coin, (cd, atrp, avol) in series.items():
                            last_used = -999
                            for (i, side) in sig_bars[coin]:
                                if i - last_used < horizon:
                                    continue
                                ei = i + 1 + delay
                                if ei + 1 >= len(cd) or ei + horizon >= len(cd):
                                    continue
                                last_used = i
                                entry = cd[ei][A.O]
                                ret = realize(entry, side, cd[ei + 1:], stop, horizon)
                                trades.append({"t": cd[ei][A.T], "ret": ret, "side": side})
                        if len(trades) < 30:
                            continue
                        s = A.summarize(trades)
                        h1 = s["oos_12bps"]["first_half_mean_pct"]
                        h2 = s["oos_12bps"]["second_half_mean_pct"]
                        rec = {"N": N, "M": M, "delay": delay, "horizon": horizon,
                               "stop": stop, "n": len(trades), "trades": trades,
                               "ev12": s["slip12"]["mean_ret_pct"],
                               "ev25": s["slip25"]["mean_ret_pct"],
                               "ev50": s["slip50"]["mean_ret_pct"],
                               "win": s["slip12"]["win_rate"], "h1": h1, "h2": h2}
                        results.append(rec)
                        robust = (h1 and h2 and h1 > 0 and h2 > 0 and rec["ev25"] > 0)
                        score = min(h1, h2) if (h1 and h2) else -99
                        if robust and (best is None or score > best["score"]):
                            rec["score"] = score
                            best = rec
    # report top cells by EV25
    results.sort(key=lambda r: r["ev25"], reverse=True)
    print("=== top 8 cells by EV@25bps ===")
    print("N    M   dly hz  stop  n    ev12   ev25   ev50  win   h1     h2")
    for r in results[:8]:
        print(f"{r['N']:<4} {r['M']:<3} {r['delay']:<3} {r['horizon']:<3} {r['stop']:<5} "
              f"{r['n']:<4} {r['ev12']:<6} {r['ev25']:<6} {r['ev50']:<6} {r['win']:<5} "
              f"{r['h1']} {r['h2']}")

    if best:
        print("\n=== BEST robust-both-halves cell ===")
        for k in ("N", "M", "delay", "horizon", "stop", "n", "ev12", "ev25", "ev50", "win", "h1", "h2"):
            print(f"  {k}: {best[k]}")
        # excess over side-matched baseline + mc_null p, per side group
        for side in ("long", "short"):
            grp = [t["ret"] for t in best["trades"] if t["side"] == side]
            if len(grp) < 10:
                print(f"  [{side}] n={len(grp)} too thin for null")
                continue
            pool = build_pool(d, side, best["stop"], best["horizon"], best["delay"])
            r = mc_null.shuffle_label_p(grp, pool, n_iter=4000, seed=1)
            print(f"  [{side}] n={len(grp)} obs_mean={r['obs_mean']} null_mean={r['null_mean']} "
                  f"excess={r['excess']} z={r['z']} p={r['p_one_sided']}")
    else:
        print("\nNo robust-both-halves cell found.")
    return results, best


if __name__ == "__main__":
    run()
