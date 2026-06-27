"""D6 funding_price_divergence — when price-trend and funding DISAGREE, does the funding side win?
2x2 cells (funding sign x recent-price sign), event study:
  PN funding>0 & price DOWN  = trapped longs (crowded-long, price falling)   -> SHORT (funding-fade + price)
  NP funding<0 & price UP    = squeezed shorts (crowded-short, price rising)  -> LONG
  PP funding>0 & price UP     = aligned bull (longs paying, winning)          -> test SHORT (D4 says fade)
  NN funding<0 & price DOWN   = aligned bear                                  -> test LONG
Each cell's implied trade scored as EXCESS over a matched SAME-SIDE random-entry null.
Key question: does the DIVERGENCE (PN/NP) sharpen the funding-fade vs the aligned cells?
"""
from __future__ import annotations
import statistics
import alpha_lib as al, funding_lib as fl, mc_null

d = al.load_dataset(); f = fl.load_funding()
DAY = 86_400_000
COINS = [c for c in d["coins"] if fl.rows(f, c)]
cand = {c: {b[al.T]: b for b in al.candles(d, c, "1d")} for c in COINS}
cand_t = {c: sorted(cand[c]) for c in COINS}
fs = min(fl.rows(f, c)[0][0] for c in COINS); fe = max(fl.rows(f, c)[-1][0] for c in COINS)

def trail_ret(c, t, Ld=3):
    cs = [x for x in cand_t[c] if x <= t][-(Ld + 1):]
    if len(cs) < 2: return None
    a, b = cand[c][cs[0]][al.C], cand[c][cs[-1]][al.C]
    return (b - a) / a if a else None

def fwd_bars(c, t):
    return [cand[c][x] for x in cand_t[c] if x > t]

STOPS = [0.15, 0.20, 0.25]
CELLS = {"PN_trapL_short": ("short", lambda fnd, pr: fnd > 0 and pr < 0),
         "NP_sqzS_long":   ("long",  lambda fnd, pr: fnd < 0 and pr > 0),
         "PP_alignBull_short": ("short", lambda fnd, pr: fnd > 0 and pr > 0),
         "NN_alignBear_long":  ("long",  lambda fnd, pr: fnd < 0 and pr < 0)}

def run(Lfund_h=72, Lprice_d=3, h=5, stop=0.20):
    sig = {k: [] for k in CELLS}; pool = {"long": [], "short": []}
    for c in COINS:
        for t in cand_t[c]:
            if not (fs <= t <= fe): continue
            fb = fwd_bars(c, t)
            if len(fb) < 1 or not fb[0][al.O]: continue
            fnd = fl.trailing_funding(f, c, t, Lfund_h); pr = trail_ret(c, t, Lprice_d)
            if fnd is None or pr is None: continue
            for side in ("long", "short"):
                pool[side].append(al.sweep_stop(fb[0][al.O], side, fb[1:], [stop], h)[stop])
            for k, (side, cond) in CELLS.items():
                if cond(fnd, pr):
                    sig[k].append({"t": t, "side": side,
                                   "ret": al.sweep_stop(fb[0][al.O], side, fb[1:], [stop], h)[stop]})
    out = {}
    for k, (side, _) in CELLS.items():
        obs = [x["ret"] for x in sig[k]]
        if len(obs) >= 8:
            res = mc_null.shuffle_label_p(obs, pool[side], n_iter=5000, seed=5)
            f1, f2 = al.time_split(sig[k])
            def ev(x): return round(100 * statistics.mean(t["ret"] - 0.0025 for t in x), 3) if x else None
            out[k] = {"side": side, "n": len(obs), "net25": ev(sig[k]),
                      "win": round(sum(1 for r in obs if r > 0) / len(obs), 3),
                      "excess": res["excess"], "p": res["p_one_sided"],
                      "oos25": (ev(f1), ev(f2))}
        else:
            out[k] = {"side": side, "n": len(obs), "thin": True}
    return out

if __name__ == "__main__":
    print("=== D6 funding_price_divergence (2x2, EXCESS over matched same-side null) ===")
    for Lp in [3, 5]:
        print(f"\n--- Lfund=72h Lprice={Lp}d h=5d stop=20% ---")
        r = run(Lprice_d=Lp)
        for k, v in r.items():
            if v.get("thin"): print(f"  {k:22s}: n={v['n']} THIN"); continue
            print(f"  {k:22s}: n={v['n']:3d} net25={v['net25']:+.2f}% win={v['win']} "
                  f"excess={v['excess']:+.4f} p={v['p']:.4f} OOS25={v['oos25']}")
