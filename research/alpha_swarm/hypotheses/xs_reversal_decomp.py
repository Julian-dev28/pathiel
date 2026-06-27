"""Decompose the BTC-down + high-dispersion daily reversal node.
Is the edge in the LONG leg (losers bounce) or SHORT leg (winners revert)?
Sweep m and the dispersion tercile cutoff to check it's not a single outlier."""
import statistics
import alpha_lib
from alpha_lib import O, C, T

d = alpha_lib.load_dataset()
coins = d["coins"]
base = alpha_lib.candles(d, "BTC", "1d")
tl = [b[T] for b in base]
px = {c: {b[T]:(b[O],b[C]) for b in alpha_lib.candles(d,c,"1d")} for c in coins}

def closeat(c,i):
    v=px[c].get(tl[i]); return v[1] if v else None
def openat(c,i):
    v=px[c].get(tl[i]); return v[0] if v else None
def tret(c,i,k):
    a,b=closeat(c,i-k),closeat(c,i); return (b/a-1.0) if (a and b and a) else None
def btc_down(i):
    r=tret("BTC",i,7); return (r is not None and r<0)

N=len(tl)

def run(k,m,rebal,leg="both",disp_q=2/3,down_only=True):
    trades=[]
    start=max(k,8)+1
    ds=[]
    for i in range(start,N-rebal-1):
        rs=[tret(c,i,k) for c in coins]; rs=[r for r in rs if r is not None]
        if len(rs)>=3*m: ds.append(statistics.pstdev(rs))
    ds.sort(); thr=ds[int(len(ds)*disp_q)] if ds else None
    for i in range(start,N-rebal-1):
        ei,xi=i+1,i+1+rebal
        if xi>=N: break
        ranked=[(tret(c,i,k),c) for c in coins]
        ranked=[(r,c) for r,c in ranked if r is not None and openat(c,ei) is not None and openat(c,xi) is not None]
        if len(ranked)<3*m: continue
        if down_only and not btc_down(i): continue
        if thr is not None and statistics.pstdev([r for r,_ in ranked])<thr: continue
        ranked.sort()
        losers,winners=ranked[:m],ranked[-m:]
        # reversal: long losers, short winners
        if leg in ("both","long"):
            for r,c in losers:
                eo,xo=openat(c,ei),openat(c,xi)
                if eo: trades.append({"t":tl[ei],"ret":(xo/eo-1.0)})
        if leg in ("both","short"):
            for r,c in winners:
                eo,xo=openat(c,ei),openat(c,xi)
                if eo: trades.append({"t":tl[ei],"ret":-(xo/eo-1.0)})
    return trades

def line(tag,s):
    if s.get("n",0)==0: print(f"  {tag}: no trades"); return
    o=s["oos_12bps"]
    print(f"  {tag}: n={s['n']:4d} EV0={s['slip0']['mean_ret_pct']:+.4f} EV12={s['slip12']['mean_ret_pct']:+.4f} "
          f"EV25={s['slip25']['mean_ret_pct']:+.4f} win12={s['slip12']['win_rate']:.2f} | "
          f"OOS h1={o['first_half_mean_pct']} h2={o['second_half_mean_pct']} [{s['verdict']}]")

print("LEG DECOMPOSITION (k=1, rebal=1, BTC-down + top-tercile dispersion)")
for m in (4,6,8):
    print(f"\nm={m}")
    line("both ", alpha_lib.summarize(run(1,m,1,"both")))
    line("long-losers-bounce ", alpha_lib.summarize(run(1,m,1,"long")))
    line("short-winners-revert", alpha_lib.summarize(run(1,m,1,"short")))

print("\nDISPERSION-CUTOFF SWEEP (k=1,m=4,rebal=1,BTC-down, both legs)")
for q in (0.0,0.33,0.5,0.66,0.8):
    line(f"disp>={q:.2f}", alpha_lib.summarize(run(1,4,1,"both",disp_q=q)))

print("\nGATE ABLATION (k=1,m=4,rebal=1, both legs)")
line("down+disp(2/3)", alpha_lib.summarize(run(1,4,1,"both",disp_q=2/3,down_only=True)))
line("disp-only(2/3)", alpha_lib.summarize(run(1,4,1,"both",disp_q=2/3,down_only=False)))
line("down-only     ", alpha_lib.summarize(run(1,4,1,"both",disp_q=0.0,down_only=True)))
line("neither(all)  ", alpha_lib.summarize(run(1,4,1,"both",disp_q=0.0,down_only=False)))

print("\nrebal=2 robustness (k=1,m=4, down+disp)")
line("rebal2", alpha_lib.summarize(run(1,4,2,"both")))
