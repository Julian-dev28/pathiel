"""W-A4 idio_momentum_residual — does BTC-beta-RESIDUAL momentum beat RAW-return momentum
on Sharpe, and does it cut the down-beta confound (W-A3)?

RAW:   score = trailing L-day return. long top-k / short bottom-k.
RESID: score = rc - beta_i*rb  (rb = BTC trailing L return, beta_i from trailing 30 daily rets).
Both: rank on bars<=i, fill open[i+1]->open[i+1+H], per-leg signed return.
Report per-leg EV (slippage sweep), OOS both halves, Sharpe (per-rebal book-return series,
non-overlap), and the book's NET beta tilt (mean long beta - mean short beta) — residual should
be more beta-neutral.
"""
import statistics, math
import alpha_lib as al
from alpha_lib import O, H as HI, L, C

d = al.load_dataset()
SER = {c: al.candles(d, c, "1d") for c in d["coins"] if len(al.candles(d, c, "1d")) >= 60}
N = min(len(b) for b in SER.values()); ARR={c:SER[c][-N:] for c in SER}; BTC=ARR["BTC"]

def tret(bars,i,lb):
    if i-lb<0 or bars[i-lb][C]<=0: return None
    return bars[i][C]/bars[i-lb][C]-1.0
def beta(c,i,w=30):
    bc=ARR[c]
    cr=[bc[j][C]/bc[j-1][C]-1 for j in range(i-w+1,i+1) if bc[j-1][C]>0]
    br=[BTC[j][C]/BTC[j-1][C]-1 for j in range(i-w+1,i+1) if BTC[j-1][C]>0]
    n=min(len(cr),len(br))
    if n<8: return 1.0
    cr,br=cr[-n:],br[-n:]; mb=sum(br)/n; vb=sum((x-mb)**2 for x in br)
    if vb<=0: return 1.0
    mc=sum(cr)/n
    return sum((a-mc)*(b-mb) for a,b in zip(cr,br))/vb
def fwd(c,i,hold):
    bc=ARR[c]; e=i+1; x=i+1+hold
    if x>=len(bc) or bc[e][O]<=0: return None
    return bc[x][O]/bc[e][O]-1.0
def sharpe(xs):
    if len(xs)<3: return float('nan')
    sd=statistics.pstdev(xs); return statistics.mean(xs)/sd if sd>0 else float('nan')

def run(lb, hold, k=8, step=None):
    step = step or hold
    rows={"raw":[], "resid":[]}   # per-rebal book per-leg return, time-ordered
    trades={"raw":[], "resid":[]}
    betatilt={"raw":[], "resid":[]}
    start=max(lb+2,32)
    i=start
    while i<N-hold-2:
        rb=tret(BTC,i,lb)
        sc_raw=[]; sc_res=[]
        for c in ARR:
            r=tret(ARR[c],i,lb)
            if r is None: continue
            sc_raw.append((c,r))
            if rb is not None:
                sc_res.append((c, r-beta(c,i)*rb))
        for tag, sc in [("raw",sc_raw),("resid",sc_res)]:
            if len(sc)<2*k: continue
            sc.sort(key=lambda x:x[1],reverse=True)
            lon=[c for c,_ in sc[:k]]; sho=[c for c,_ in sc[-k:]]
            t=ARR[lon[0]][i][0]
            lr=[fwd(c,i,hold) for c in lon]; sr=[fwd(c,i,hold) for c in sho]
            lr=[x for x in lr if x is not None]; sr=[x for x in sr if x is not None]
            if not lr or not sr: continue
            book=0.5*statistics.mean(lr)-0.5*statistics.mean(sr)
            rows[tag].append(book)
            betatilt[tag].append(statistics.mean([beta(c,i) for c in lon])-statistics.mean([beta(c,i) for c in sho]))
            for c in lon:
                rr=fwd(c,i,hold)
                if rr is not None: trades[tag].append({"t":t,"ret":rr})
            for c in sho:
                rr=fwd(c,i,hold)
                if rr is not None: trades[tag].append({"t":t,"ret":-rr})
        i+=step
    print(f"\n==== lb={lb} hold={hold} k={k} step={step} ====")
    for tag in ("raw","resid"):
        s=al.summarize(trades[tag]); o=s["oos_12bps"]; r=rows[tag]
        mid=len(r)//2
        print(f"  {tag:5} n_reb={len(r):3} EV12={s['slip12']['mean_ret_pct']:+.3f} EV25={s['slip25']['mean_ret_pct']:+.3f} EV50={s['slip50']['mean_ret_pct']:+.3f} | OOS h1={o['first_half_mean_pct']:+.3f}/h2={o['second_half_mean_pct']:+.3f} | Sh full={sharpe(r):+.3f} h1={sharpe(r[:mid]):+.3f} h2={sharpe(r[mid:]):+.3f} | beta_tilt={statistics.mean(betatilt[tag]):+.2f}")

for lb in (14,30):
    for hold in (7,10):
        run(lb,hold,k=8)
