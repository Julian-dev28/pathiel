"""regime_basket: BTC-regime-conditioned cross-sectional long/short basket.

Hypothesis: a daily market-neutral L/S basket whose DIRECTION flips with BTC
regime beats a static (unconditional) momentum book.

Lookahead-safe: rank on trailing k-day return decided at CLOSE of day i,
FILL at OPEN of day i+1, hold H days, EXIT at OPEN of day i+1+H.
One trade = one coin-leg, side-signed (long=+ret, short=-ret).

Variants:
  A momentum     : up-regime long winners / short losers; symmetric in down too
  B regime-flip  : up-regime long winners/short losers; DOWN-regime REVERSE
                   (long losers / short winners)
  C dispersion   : only trade days where cross-sec stdev(k-ret) in top tercile
  baseline       : unconditional momentum book (no regime), and pure long-top book
Weighting: equal-weight and vol-scaled (1/realized-vol).
"""
from __future__ import annotations
import statistics, itertools
import alpha_lib as a

d = a.load_dataset()
COINS = d["coins"]

# master timeline from BTC (has all 301 bars)
btc = a.candles(d, "BTC", "1d")
TS = [r[a.T] for r in btc]
TIDX = {t: i for i, t in enumerate(TS)}

# per-coin maps ts -> (open, close)
OPEN = {}
CLOSE = {}
for c in COINS:
    cc = a.candles(d, c, "1d")
    OPEN[c] = {r[a.T]: r[a.O] for r in cc}
    CLOSE[c] = {r[a.T]: r[a.C] for r in cc}

def trail_ret(c, i, k):
    """trailing k-day return using closes, decided at close of day i (lookahead-safe)."""
    t_now, t_then = TS[i], TS[i - k]
    a0 = CLOSE[c].get(t_then); a1 = CLOSE[c].get(t_now)
    if a0 is None or a1 is None or a0 == 0:
        return None
    return (a1 - a0) / a0

def realized_vol(c, i, w=10):
    """stdev of daily close-returns over trailing w days, decided at close i."""
    rs = []
    for j in range(i - w + 1, i + 1):
        t0, t1 = TS[j - 1], TS[j]
        a0 = CLOSE[c].get(t0); a1 = CLOSE[c].get(t1)
        if a0 and a1 and a0 != 0:
            rs.append((a1 - a0) / a0)
    if len(rs) < 3:
        return None
    return statistics.pstdev(rs)

def leg_ret(c, i_fill, H):
    """signed-long return: fill at open day i_fill, exit at open day i_fill+H."""
    t_in = TS[i_fill]; t_out = TS[i_fill + H]
    p_in = OPEN[c].get(t_in); p_out = OPEN[c].get(t_out)
    if p_in is None or p_out is None or p_in == 0:
        return None, None
    return (p_out - p_in) / p_in, t_in

def btc_regime(i, kind="sma20"):
    """up/down at close of day i (lookahead-safe)."""
    if kind == "sma20":
        if i < 20: return None
        sma = statistics.mean(CLOSE["BTC"][TS[j]] for j in range(i - 19, i + 1))
        return "up" if CLOSE["BTC"][TS[i]] > sma else "down"
    if kind == "ret7":
        if i < 7: return None
        r = trail_ret("BTC", i, 7)
        return "up" if (r is not None and r >= 0) else "down"
    raise ValueError(kind)

def run(variant, k, m, H, weighting="eq", regime_kind="sma20",
        disp_gate=False, start=25, stride=1):
    """Return list of trade-legs {t, ret} side-signed, equal contribution per book-day."""
    legs = []
    i = start
    last = len(TS) - H - 2
    while i <= last:
        reg = btc_regime(i, regime_kind)
        if reg is None:
            i += stride; continue
        # cross-sectional trailing returns (exclude BTC itself from the book)
        scores = []
        for c in COINS:
            if c == "BTC":
                continue
            r = trail_ret(c, i, k)
            if r is None:
                continue
            # need a fillable leg
            tr, _ = leg_ret(c, i + 1, H)
            if tr is None:
                continue
            scores.append((c, r, tr))
        if len(scores) < 2 * m + 2:
            i += stride; continue
        rets_only = [s[1] for s in scores]
        disp = statistics.pstdev(rets_only)
        if disp_gate:
            # need tercile context: compute disp distribution lazily below; handled by caller
            pass
        scores.sort(key=lambda x: x[1], reverse=True)  # winners first
        winners = scores[:m]
        losers = scores[-m:]

        # decide directions
        if variant == "A":            # momentum always
            longs, shorts = winners, losers
        elif variant == "baseline":   # unconditional momentum (same as A but label)
            longs, shorts = winners, losers
        elif variant == "B":          # regime-flip
            if reg == "up":
                longs, shorts = winners, losers
            else:                     # down: reversal
                longs, shorts = losers, winners
        elif variant == "longonly":   # pure long-top book (not neutral)
            longs, shorts = winners, []
        else:
            raise ValueError(variant)

        day_legs = []
        # vol weights
        def wlist(group):
            if weighting == "eq" or not group:
                return [(c, tr, 1.0) for (c, _, tr) in group]
            vs = []
            for (c, _, tr) in group:
                v = realized_vol(c, i, 10) or 0.05
                vs.append((c, tr, 1.0 / max(v, 1e-4)))
            s = sum(w for _, _, w in vs)
            return [(c, tr, w / s * len(vs)) for (c, tr, w) in vs]  # mean-1 weights

        for (c, tr, w) in wlist(longs):
            day_legs.append((TS[i + 1], +tr * w))
        for (c, tr, w) in wlist(shorts):
            day_legs.append((TS[i + 1], -tr * w))
        legs.append((disp, day_legs))
        i += stride
    return legs

def materialize(legs, disp_gate=False):
    if disp_gate and legs:
        disps = sorted(x[0] for x in legs)
        thr = disps[int(len(disps) * 2 / 3)]  # top tercile threshold
        sel = [dl for (dp, dl) in legs if dp >= thr]
    else:
        sel = [dl for (_, dl) in legs]
    flat = []
    for dl in sel:
        for (t, r) in dl:
            flat.append({"t": t, "ret": r})
    return flat

def show(name, flat):
    s = a.summarize(flat)
    if s.get("n", 0) == 0:
        print(f"{name:42s} NO TRADES"); return None
    o = s["oos_12bps"]
    print(f"{name:42s} n={s['n']:4d} "
          f"EV0={s['slip0']['mean_ret_pct']:+.3f} "
          f"EV12={s['slip12']['mean_ret_pct']:+.3f} "
          f"EV25={s['slip25']['mean_ret_pct']:+.3f} "
          f"win={s['slip12']['win_rate']:.2f} "
          f"H1={o['first_half_mean_pct']} H2={o['second_half_mean_pct']} "
          f"=> {s['verdict']}")
    return s

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "sweep"

    if mode == "sweep":
        print("=== PARAMETER SWEEP (EV in % per book-leg, 12bps realistic) ===\n")
        best = []
        for variant in ["baseline", "B"]:
            for reg in ["sma20", "ret7"]:
                for k in [3, 5, 10]:
                    for m in [4, 6, 8]:
                        for H in [1, 3, 5]:
                            for wt in ["eq", "vol"]:
                                legs = run(variant, k, m, H, wt, reg)
                                flat = materialize(legs)
                                s = a.summarize(flat)
                                if s.get("n", 0) < 40:
                                    continue
                                o = s["oos_12bps"]
                                h1, h2 = o["first_half_mean_pct"], o["second_half_mean_pct"]
                                ev12 = s["slip12"]["mean_ret_pct"]
                                robust = (h1 and h2 and h1 > 0 and h2 > 0)
                                best.append((ev12, robust, variant, reg, k, m, H, wt,
                                             s["n"], h1, h2, s["slip25"]["mean_ret_pct"]))
        best.sort(reverse=True)
        print(f"{'EV12':>7} {'rob':>4} {'var':>8} {'reg':>6} {'k':>2} {'m':>2} {'H':>2} {'wt':>3} {'n':>5} {'H1':>8} {'H2':>8} {'EV25':>7}")
        for row in best[:25]:
            ev12, robust, variant, reg, k, m, H, wt, n, h1, h2, ev25 = row
            print(f"{ev12:>7.3f} {'Y' if robust else '.':>4} {variant:>8} {reg:>6} {k:>2} {m:>2} {H:>2} {wt:>3} {n:>5} {str(h1):>8} {str(h2):>8} {ev25:>7.3f}")
        print("\n--- best robust (both halves +EV @12bps) ---")
        rob = [r for r in best if r[1]]
        for row in rob[:15]:
            ev12, robust, variant, reg, k, m, H, wt, n, h1, h2, ev25 = row
            print(f"{ev12:>7.3f}  {variant} reg={reg} k={k} m={m} H={H} wt={wt} n={n} H1={h1} H2={h2} EV25={ev25}")
        print(f"\n total configs tested: {len(best)}, robust: {len(rob)}")

    elif mode == "detail":
        # dispersion-gate (variant C) + headline comparisons on a fixed mid config
        for (v, lbl) in [("baseline", "A/baseline momentum"), ("B", "B regime-flip"),
                         ("longonly", "long-top only")]:
            for k in [5]:
                for m in [6]:
                    for H in [3]:
                        legs = run(v, k, m, H, "eq", "sma20")
                        show(f"{lbl} k{k}m{m}H{H} eq", materialize(legs))
                        show(f"{lbl} k{k}m{m}H{H} eq +DISP", materialize(legs, disp_gate=True))
