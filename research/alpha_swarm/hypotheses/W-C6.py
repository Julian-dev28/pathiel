"""W-C6 engulf_1h — does the engulf edge exist on 1h candles (>> samples) or only daily?
1h re-trades more so fees bite: report net-of-25bps + OOS halves. Focus on the SHORT leg
(W-C2 real leg) + symmetric, sweep horizon {1,4,12,24} bars, stop {0.08,0.40}. MC excess vs
matched same-side 1h null.
"""
from __future__ import annotations
import random
import alpha_lib as A
import mc_null

IV = "1h"


def engulf(cd, i):
    po, pc = cd[i - 1][A.O], cd[i - 1][A.C]
    o, c = cd[i][A.O], cd[i][A.C]
    if c > o and pc < po and o <= pc and c >= po:
        return 1
    if c < o and pc > po and o >= pc and c <= po:
        return -1
    return 0


def realize(entry, side, fwd, stop, hz):
    return A.sweep_stop(entry, side, fwd, [stop], hz)[stop]


def trades_for(series, hz, stop, want=None):
    out = []
    for c, cd in series.items():
        last = -999
        for i in range(2, len(cd) - hz - 2):
            sig = engulf(cd, i)
            if sig == 0 or i - last < hz:
                continue
            side = "long" if sig == 1 else "short"
            if want and side != want:
                continue
            last = i
            out.append({"t": cd[i + 1][A.T], "side": side,
                        "ret": realize(cd[i + 1][A.O], side, cd[i + 2:], stop, hz)})
    return out


def side_pool(d, side, hz, stop, n=6000, seed=0):
    rng = random.Random(seed); coins = d["coins"]; pool, tries = [], 0
    while len(pool) < n and tries < n * 25:
        tries += 1
        cd = A.candles(d, coins[rng.randrange(len(coins))], IV)
        if len(cd) < 200:
            continue
        i = rng.randrange(3, len(cd) - hz - 2)
        pool.append(realize(cd[i + 1][A.O], side, cd[i + 2:], stop, hz))
    return pool


def main():
    d = A.load_dataset()
    series = {c: A.candles(d, c, IV) for c in d["coins"] if len(A.candles(d, c, IV)) >= 200}
    print(f"coins with >=200 1h bars = {len(series)}")
    print("leg   hz  stop  n     ev0    ev12   ev25   ev50   win   h1      h2     excess  z     p")
    for want, tag in (("short", "SHORT"), (None, "SYM")):
        for hz in (1, 4, 12, 24):
            for stop in (0.08, 0.40):
                tr = trades_for(series, hz, stop, want)
                if len(tr) < 50:
                    continue
                s = A.summarize(tr)
                h1 = s["oos_12bps"]["first_half_mean_pct"]; h2 = s["oos_12bps"]["second_half_mean_pct"]
                # MC excess only for the headline short side and on net-positive cells
                ex = z = p = ""
                if want == "short" and s["slip25"]["mean_ret_pct"] > 0:
                    pool = side_pool(d, "short", hz, stop)
                    r = mc_null.shuffle_label_p([t["ret"] for t in tr], pool, n_iter=4000, seed=1)
                    ex, z, p = f"{r['excess']*100:+.3f}%", r["z"], r["p_one_sided"]
                print(f"{tag:<5} {hz:<3} {stop:<5} {len(tr):<5} {s['slip0']['mean_ret_pct']:<6} "
                      f"{s['slip12']['mean_ret_pct']:<6} {s['slip25']['mean_ret_pct']:<6} "
                      f"{s['slip50']['mean_ret_pct']:<6} {s['slip12']['win_rate']:<5} "
                      f"{h1}  {h2}  {ex:<8} {z} {p}")
        print()


if __name__ == "__main__":
    main()
