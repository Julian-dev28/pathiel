"""Conditioning + intraday crossover hunt for xs reversal.
Daily reversal is an exact gross mirror of momentum (sym legs). So reversal is
only +EV in a subset where MOMENTUM is -EV. Test dispersion/regime gates, then
hunt the momentum->reversal crossover on 1h candles (very short horizon)."""
import statistics
import alpha_lib
from alpha_lib import O, C, T

d = alpha_lib.load_dataset()
coins = d["coins"]

def build(iv):
    base = alpha_lib.candles(d, "BTC", iv)
    tl = [b[T] for b in base]
    px = {}
    for c in coins:
        cd = alpha_lib.candles(d, c, iv)
        px[c] = {b[T]: (b[O], b[C]) for b in cd}
    return tl, px

def make_fns(tl, px):
    def closeat(c, i):
        v = px[c].get(tl[i]); return v[1] if v else None
    def openat(c, i):
        v = px[c].get(tl[i]); return v[0] if v else None
    def tret(c, i, k):
        a, b = closeat(c, i-k), closeat(c, i)
        return (b/a-1.0) if (a and b and a) else None
    return closeat, openat, tret

def run(tl, px, k, m, rebal, book, disp_gate=False, regime=None):
    closeat, openat, tret = make_fns(tl, px)
    N = len(tl)
    trades = []
    start = max(k, 8) + 1
    disp_thresh = None
    if disp_gate:
        ds = []
        for i in range(start, N-rebal-1):
            rs = [tret(c,i,k) for c in coins]; rs=[r for r in rs if r is not None]
            if len(rs) >= 3*m: ds.append(statistics.pstdev(rs))
        ds.sort(); disp_thresh = ds[int(len(ds)*2/3)] if ds else None
    def btc_down(i):
        r = tret("BTC", i, 7); return (r is not None and r < 0)
    for i in range(start, N-rebal-1):
        ei, xi = i+1, i+1+rebal
        if xi >= N: break
        ranked = [(tret(c,i,k), c) for c in coins]
        ranked = [(r,c) for r,c in ranked if r is not None and openat(c,ei) is not None and openat(c,xi) is not None]
        if len(ranked) < 3*m: continue
        if regime == "down" and not btc_down(i): continue
        if regime == "up" and btc_down(i): continue
        if disp_gate and disp_thresh is not None:
            if statistics.pstdev([r for r,_ in ranked]) < disp_thresh: continue
        ranked.sort()
        losers, winners = ranked[:m], ranked[-m:]
        longs, shorts = (losers, winners) if book=="reversal" else (winners, losers)
        for side, grp in (("long",longs),("short",shorts)):
            sign = 1.0 if side=="long" else -1.0
            for r,c in grp:
                eo,xo = openat(c,ei), openat(c,xi)
                if not eo: continue
                trades.append({"t": tl[ei], "ret": sign*(xo/eo-1.0)})
    return trades

def line(tag, s):
    if s.get("n",0)==0: print(f"  {tag}: no trades"); return
    o=s["oos_12bps"]
    print(f"  {tag}: n={s['n']:5d} EV0={s['slip0']['mean_ret_pct']:+.4f} "
          f"EV12={s['slip12']['mean_ret_pct']:+.4f} EV25={s['slip25']['mean_ret_pct']:+.4f} "
          f"win12={s['slip12']['win_rate']:.2f} | OOS h1={o['first_half_mean_pct']} h2={o['second_half_mean_pct']}")

# ---- DAILY conditioning on the shortest horizons (where reversal should live) ----
tl, px = build("1d")
print("="*100)
print("DAILY CONDITIONING (reversal book). Reversal +EV only if momentum is -EV in the subset.")
print("="*100)
for (k,m,rebal) in [(1,4,1),(1,6,1),(1,8,1),(2,6,1),(1,6,2)]:
    print(f"\nk={k} m={m} rebal={rebal}")
    line("REV all       ", alpha_lib.summarize(run(tl,px,k,m,rebal,"reversal")))
    line("REV disp-top3 ", alpha_lib.summarize(run(tl,px,k,m,rebal,"reversal",disp_gate=True)))
    line("REV BTC-down  ", alpha_lib.summarize(run(tl,px,k,m,rebal,"reversal",regime="down")))
    line("REV down+disp ", alpha_lib.summarize(run(tl,px,k,m,rebal,"reversal",disp_gate=True,regime="down")))
    line("MOM all (ref) ", alpha_lib.summarize(run(tl,px,k,m,rebal,"momentum")))

# ---- INTRADAY 1h crossover hunt ----
tl, px = build("1h")
print("\n"+"="*100)
print(f"INTRADAY 1h CROSSOVER HUNT ({len(tl)} bars). k/rebal in HOURS. Looking for reversal>0 & momentum<0.")
print("="*100)
for rebal in (1,2,4,8,12,24):
    for k in (1,2,4,8,12,24):
        if k > 48: continue
        tr = run(tl,px,k,6,rebal,"reversal")
        if not tr: continue
        sr = alpha_lib.summarize(tr)
        rev0 = sr["slip0"]["mean_ret_pct"]
        sm = alpha_lib.summarize(run(tl,px,k,6,rebal,"momentum"))
        mom0 = sm["slip0"]["mean_ret_pct"]
        flag = "  <-- REVERSAL" if rev0 > 0 else ""
        print(f"k={k:2d}h rebal={rebal:2d}h  REV EV0={rev0:+.4f} EV12={sr['slip12']['mean_ret_pct']:+.4f} "
              f"OOS(h1={sr['oos_12bps']['first_half_mean_pct']},h2={sr['oos_12bps']['second_half_mean_pct']}) | "
              f"MOM EV0={mom0:+.4f}{flag}")
