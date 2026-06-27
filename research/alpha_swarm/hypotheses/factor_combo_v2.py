"""W-A6 factor_combo_v2 — combine the genuine Lane-A survivors into one market-neutral book;
measure diversification lift over the best single.

Survivors (Lane A, post-Wave-2):
  M = live momentum book. Tested as pct_k (live ranker) AND residual-momentum (W-A4 better
      construction). These are the SAME factor -> pick the better single, do NOT stack both.
  A = A13 relative_strength_drawdown L/S (k6, N50). W-A1: corr~0.35 (partially orthogonal,
      MARGINAL). W-A3: its SHORT leg is down-beta -> combo will import a beta tilt; we MEASURE it.
  (B13 skew-arm is a Lane-B overlay; no return series available here -> excluded, noted.)

Combo weightings between the two BOOKS: 50/50 and inverse-stream-vol (rolling 6-period).
Report Sharpe full/h1/h2 for each single + each combo, diversification lift over best single,
and the combo's net beta tilt (down-beta import check). Lookahead-safe open[i+1]->[i+1+H].
"""
import statistics
import alpha_lib as al
from alpha_lib import O, H as HI, L, C

d=al.load_dataset()
SER={c:al.candles(d,c,"1d") for c in d["coins"] if len(al.candles(d,c,"1d"))>=60}
N=min(len(b) for b in SER.values()); ARR={c:SER[c][-N:] for c in SER}; BTC=ARR["BTC"]

def pctk(b,i,n=14):
    s=b[i-n+1:i+1]
    if len(s)<n: return None
    hi=max(x[HI] for x in s); lo=min(x[L] for x in s); cur=b[i][C]
    return (cur-lo)/(hi-lo)-0.5 if hi>lo else None
def rsdd(b,i,n=50):
    s=b[i-n+1:i+1]
    if len(s)<n: return None
    mx=max(x[HI] for x in s); return b[i][C]/mx-1.0 if mx>0 else None
def tret(b,i,lb):
    return b[i][C]/b[i-lb][C]-1.0 if (i-lb>=0 and b[i-lb][C]>0) else None
def beta(c,i,w=30):
    bc=ARR[c]
    cr=[bc[j][C]/bc[j-1][C]-1 for j in range(i-w+1,i+1) if bc[j-1][C]>0]
    br=[BTC[j][C]/BTC[j-1][C]-1 for j in range(i-w+1,i+1) if BTC[j-1][C]>0]
    n=min(len(cr),len(br))
    if n<8: return 1.0
    cr,br=cr[-n:],br[-n:]; mb=sum(br)/n; vb=sum((x-mb)**2 for x in br)
    if vb<=0: return 1.0
    mc=sum(cr)/n
    return sum((a-mc)*(x-mb) for a,x in zip(cr,br))/vb
def fwd(c,i,h):
    bc=ARR[c]; e=i+1; x=i+1+h
    if x>=len(bc) or bc[e][O]<=0: return None
    return bc[x][O]/bc[e][O]-1.0
def book(longs,shorts,i,h):
    lr=[fwd(c,i,h) for c in longs]; sr=[fwd(c,i,h) for c in shorts]
    lr=[x for x in lr if x is not None]; sr=[x for x in sr if x is not None]
    if not lr or not sr: return None,None
    bt=statistics.mean([beta(c,i) for c in longs])-statistics.mean([beta(c,i) for c in shorts])
    return 0.5*statistics.mean(lr)-0.5*statistics.mean(sr), bt
def sharpe(xs):
    xs=[x for x in xs if x is not None]
    if len(xs)<3: return float('nan')
    sd=statistics.pstdev(xs); return statistics.mean(xs)/sd if sd>0 else float('nan')

def run(hold, step, mom="pctk", k=8, kA=6):
    M,A,btM,btA=[],[],[],[]
    start=max(52,32); i=start
    while i<N-hold-2:
        rb=tret(BTC,i,14)
        sc=[]
        for c in ARR:
            if mom=="pctk": v=pctk(ARR[c],i,14)
            else:
                r=tret(ARR[c],i,14); v=(r-beta(c,i)*rb) if (r is not None and rb is not None) else None
            if v is not None: sc.append((c,v))
        rs=[(c,rsdd(ARR[c],i,50)) for c in ARR]; rs=[(c,v) for c,v in rs if v is not None]
        if len(sc)<2*k or len(rs)<2*kA: i+=step; continue
        sc.sort(key=lambda x:x[1],reverse=True); rs.sort(key=lambda x:x[1],reverse=True)
        m,bm=book([c for c,_ in sc[:k]],[c for c,_ in sc[-k:]],i,hold)
        a,ba=book([c for c,_ in rs[:kA]],[c for c,_ in rs[-kA:]],i,hold)
        if m is None or a is None: i+=step; continue
        M.append(m); A.append(a); btM.append(bm); btA.append(ba); i+=step
    # combos
    c5050=[0.5*m+0.5*a for m,a in zip(M,A)]
    civ=[]
    for j in range(len(M)):
        if j<6: civ.append(0.5*M[j]+0.5*A[j]); continue
        vM=statistics.pstdev(M[j-6:j])+1e-9; vA=statistics.pstdev(A[j-6:j])+1e-9
        wM=(1/vM)/((1/vM)+(1/vA)); civ.append(wM*M[j]+(1-wM)*A[j])
    mid=len(M)//2
    def sh3(x): return (sharpe(x),sharpe(x[:mid]),sharpe(x[mid:]))
    print(f"\n==== mom={mom} hold={hold} step={step} n={len(M)} ====")
    for nm,x in [("M(mom)",M),("A(a13)",A),("combo50/50",c5050),("combo invvol",civ)]:
        f,h1,h2=sh3(x)
        print(f"  {nm:13} EV={statistics.mean(x)*100:+.3f}% Sh full={f:+.3f} h1={h1:+.3f} h2={h2:+.3f}")
    best=max(sharpe(M),sharpe(A))
    print(f"  best single Sh={best:+.3f}  lift(50/50)={sharpe(c5050)-best:+.3f}  lift(invvol)={sharpe(civ)-best:+.3f}")
    print(f"  beta tilt: M={statistics.mean(btM):+.2f} A={statistics.mean(btA):+.2f} combo50={statistics.mean([0.5*a+0.5*b for a,b in zip(btM,btA)]):+.2f}")

for mom in ("pctk","resid"):
    run(hold=10,step=10,mom=mom)
    run(hold=7,step=7,mom=mom)
