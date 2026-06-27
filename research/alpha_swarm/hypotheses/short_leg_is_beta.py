"""W-A3 short_leg_is_beta — is shorting the deepest-50d-drawdown basket ALPHA or just
down-beta in the -44% tape?

Two controls:
 (1) matched random-SHORT excess (controls AVERAGE beta — the pool carries the same tape).
 (2) BTC-BETA RESIDUALIZATION: resid_fwd = coin_fwd - beta_i*btc_fwd, beta_i from trailing
     30 daily rets (bars<=i). Short the deep basket on RESIDUALS. If short-resid is still +EV
     both halves & significant -> ALPHA. If it collapses to ~0 -> the short leg is a regime
     (down-beta) bet, not alpha. Also report deep-basket avg beta vs universe avg beta.
Lookahead-safe: rank/beta on bars<=i, fill open[i+1]->open[i+1+H].
"""
import statistics
import alpha_lib as al
from alpha_lib import O, H as HI, L, C
import mc_null

d = al.load_dataset()
SER = {c: al.candles(d, c, "1d") for c in d["coins"] if len(al.candles(d, c, "1d")) >= 60}
N = min(len(b) for b in SER.values())
ARR = {c: SER[c][-N:] for c in SER}
BTC = ARR["BTC"]

def rsdd(bars, i, n=50):
    seg = bars[i-n+1:i+1]
    if len(seg) < n: return None
    mx = max(b[HI] for b in seg)
    return bars[i][C]/mx - 1.0 if mx > 0 else None

def beta(c, i, w=30):
    bc = ARR[c]
    cr = [bc[j][C]/bc[j-1][C]-1 for j in range(i-w+1, i+1) if bc[j-1][C]>0]
    br = [BTC[j][C]/BTC[j-1][C]-1 for j in range(i-w+1, i+1) if BTC[j-1][C]>0]
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

def run(hold=7, k=6, N50=50, bw=30):
    ds_raw, ds_resid = [], []          # deep-short raw / residual
    pool_short_raw, pool_short_resid = [], []
    deep_betas, univ_betas = [], []
    start=max(N50+2, bw+2)
    for i in range(start, N-hold-2):
        bf = fwd("BTC", i, hold)
        if bf is None: continue
        rs=[(c,rsdd(ARR[c],i,N50)) for c in ARR]; rs=[(c,v) for c,v in rs if v is not None]
        if len(rs)<2*k: continue
        rs.sort(key=lambda x:x[1], reverse=True)
        deep=[c for c,_ in rs[-k:]]
        t=ARR[deep[0]][i][0]
        for c,_ in rs:
            r=fwd(c,i,hold)
            if r is None: continue
            b=beta(c,i,bw); resid=r - b*bf
            pool_short_raw.append(-r); pool_short_resid.append(-resid)
            univ_betas.append(b)
            if c in deep:
                ds_raw.append({"t":t,"ret":-r}); ds_resid.append({"t":t,"ret":-resid})
                deep_betas.append(b)
    print(f"\n==== hold={hold} k={k} N50={N50} bw={bw} ====")
    print(f"  deep-basket avg beta = {statistics.mean(deep_betas):+.2f}  vs universe avg beta = {statistics.mean(univ_betas):+.2f}  (n={len(ds_raw)})")
    for name, tr, pool in [("DEEP-short RAW", ds_raw, pool_short_raw),
                           ("DEEP-short BETA-RESIDUAL", ds_resid, pool_short_resid)]:
        s=al.summarize(tr); o=s["oos_12bps"]
        mc=mc_null.shuffle_label_p([x["ret"] for x in tr], pool, n_iter=4000, seed=1)
        print(f"  {name:26} n={s['n']:4} EV0={s['slip0']['mean_ret_pct']:+.3f} EV12={s['slip12']['mean_ret_pct']:+.3f} EV25={s['slip25']['mean_ret_pct']:+.3f} EV50={s['slip50']['mean_ret_pct']:+.3f} | OOS h1={o['first_half_mean_pct']:+.3f}/h2={o['second_half_mean_pct']:+.3f} | excess={mc['excess']*100:+.3f}% z={mc['z']:+.2f} p={mc['p_one_sided']}")

run(hold=7,k=6); run(hold=5,k=6); run(hold=7,k=8)
