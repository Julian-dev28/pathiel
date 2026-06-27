"""B6 adx_gated_momentum — only take trend/momentum entries when ADX>threshold (genuine
trend present). Measure EV lift + dud-rate cut vs ungated momentum entries."""
import statistics
import alpha_lib as A

d = A.load_dataset()
coins = d["coins"]
P = 14          # ADX/Wilder period
LMOM = 14
HOR, STOP = 5, 0.25

def adx_series(cd):
    """Wilder ADX(P). Returns list aligned to cd index (None until warm). No lookahead:
    adx[i] uses bars up to i."""
    n=len(cd); adx=[None]*n
    if n < 2*P+1: return adx
    trs=[]; pdm=[]; ndm=[]
    for i in range(1,n):
        h,l,pc,ph,pl=cd[i][A.H],cd[i][A.L],cd[i-1][A.C],cd[i-1][A.H],cd[i-1][A.L]
        tr=max(h-l,abs(h-pc),abs(l-pc))
        up=h-ph; dn=pl-l
        trs.append(tr)
        pdm.append(up if (up>dn and up>0) else 0.0)
        ndm.append(dn if (dn>up and dn>0) else 0.0)
    # Wilder smoothing; first value = sum of first P
    atr=sum(trs[:P]); spdm=sum(pdm[:P]); sndm=sum(ndm[:P])
    dxs=[]
    for k in range(P, len(trs)):
        atr = atr - atr/P + trs[k]
        spdm = spdm - spdm/P + pdm[k]
        sndm = sndm - sndm/P + ndm[k]
        pdi = 100*spdm/atr if atr else 0
        ndi = 100*sndm/atr if atr else 0
        dx = 100*abs(pdi-ndi)/((pdi+ndi) or 1)
        dxs.append(dx)
    # ADX = Wilder smooth of DX over P
    if len(dxs) < P: return adx
    a=sum(dxs[:P])/P
    # dxs[k] corresponds to cd index P+1+k ; first ADX after another P
    for j in range(P, len(dxs)):
        a = (a*(P-1)+dxs[j])/P
        cd_idx = 1 + P + j   # alignment: trs index k -> cd index k+1; dxs index j -> trs P+j -> cd P+1+j
        if cd_idx < n: adx[cd_idx]=a
    return adx

def run(thr):
    trades=[]
    for c in coins:
        cd=A.candles(d,c,"1d")
        if len(cd)<2*P+LMOM+HOR+2: continue
        cl=[b[A.C] for b in cd]
        adx=adx_series(cd)
        for i in range(2*P+LMOM, len(cd)-HOR-1):
            if adx[i] is None or adx[i] < thr: continue
            side = "long" if cl[i]/cl[i-LMOM]-1>0 else "short"
            px=cd[i+1][A.O]
            r=A.sweep_stop(px,side,cd[i+1:],[STOP],HOR)[STOP]
            trades.append({"t":cd[i+1][A.T],"ret":r})
    return trades

print(f"{'ADXthr':6s} {'n':>5s} {'EV12%':>7s} {'win':>5s} {'dud%':>5s} {'sharpe':>6s} {'h1':>7s} {'h2':>7s} verdict")
base_dud=None
for thr in [0,20,25,30]:
    tr=run(thr)
    if not tr: continue
    s=A.summarize(tr); sl,oos=s["slip12"],s["oos_12bps"]
    dud=sum(1 for t in tr if t["ret"]<0)/len(tr)
    if thr==0: base_dud=dud
    print(f"{thr:6d} {s['n']:5d} {sl['mean_ret_pct']:7.3f} {sl['win_rate']:5.3f} {dud*100:5.1f} {sl['sharpe_like']:6.3f} "
          f"{str(oos['first_half_mean_pct']):>7s} {str(oos['second_half_mean_pct']):>7s}  {s['verdict']}")
