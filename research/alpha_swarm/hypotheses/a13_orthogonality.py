"""W-A1 a13_orthogonality — THE decider.

Is A13 (relative_strength_drawdown: long nearest-50d-high / short deepest-drawdown,
market-neutral) NEW capacity, or a re-expression of the LIVE xs-momentum book?

LIVE book (from hermes_trader/agents/config_store.py xs_momentum + xs_momentum_live.py):
  ranking = "pct_k"  (pctk_score: percent-location in trailing 14d high/low channel, centered 0)
  k_per_leg = 8, hold_days = 10, zext_window = 14
  vol_gate = True  -> go FLAT in high-BTC-vol regime (short=14 vs trailing 90d median)
  (residual flag is IGNORED for pct_k ranking — pctk doesn't use bench)

A13 book (relative_strength_drawdown.md):
  signal = close[i]/max(high[i-49..i]) - 1   (proximity to 50d high, <=0)
  long top-6 NEAREST high (dd closest to 0) / short bottom-6 DEEPEST drawdown
  hold = 7, no regime gate, market-neutral.

Both: decide on bars up to & incl day i, fill open[i+1], exit open[i+1+H]. Per-leg signed
return = 0.5*mean(long fwd) + 0.5*(-mean(short fwd)) (matches A13's "%/leg" framing).

Outputs the three deciding numbers:
  (1) return correlation (live vs A13)
  (2) residual-alpha t-stat: OLS a13 = alpha + beta*live; alpha & t(alpha), FULL + both halves
  (3) combined-book OOS Sharpe vs each single, both halves.
VERDICT new-capacity ONLY if combined Sharpe > best single AND residual alpha > 0 both halves.
"""
import statistics, math
import alpha_lib as al
from alpha_lib import O, H, L, C

d = al.load_dataset()
coins = d["coins"]

# ---- per-coin daily series ----
SER = {}
for c in coins:
    bars = al.candles(d, c, "1d")
    if len(bars) >= 60:
        SER[c] = bars
btc = al.candles(d, "BTC", "1d")
N_DAYS = min(len(b) for b in SER.values())
# align on a common length from the end so indices line up across coins
# build per-coin arrays indexed by a shared day index using last N_COMMON bars
N_COMMON = min(len(b) for b in SER.values())
ARR = {c: SER[c][-N_COMMON:] for c in SER}
BTC = btc[-N_COMMON:]

def pctk(bars, i, n=14):
    seg = bars[i-n+1:i+1]
    if len(seg) < n: return None
    hi = max(b[H] for b in seg); lo = min(b[L] for b in seg); cur = bars[i][C]
    if hi <= lo: return None
    return (cur - lo)/(hi - lo) - 0.5

def rsdd(bars, i, n=50):
    seg = bars[i-n+1:i+1]
    if len(seg) < n: return None
    mx = max(b[H] for b in seg)
    if mx <= 0: return None
    return bars[i][C]/mx - 1.0  # <=0

def btc_high_vol(i, short=14, long=90):
    # decide using bars up to & incl i
    if i < short + long + 5: return False  # fail-open low
    closes = [b[C] for b in BTC[:i+1]]
    rets = [closes[j]/closes[j-1]-1 for j in range(1,len(closes)) if closes[j-1]>0]
    vols = [statistics.pstdev(rets[j-short:j]) for j in range(short, len(rets)+1)]
    if len(vols) < 10: return False
    med = statistics.median(vols[-long:] if len(vols) >= long else vols)
    return vols[-1] > med

def fwd_leg_ret(c, i, hold):
    """open[i+1] -> open[i+1+hold] return for coin c. None if unavailable."""
    bars = ARR[c]
    e = i+1; x = i+1+hold
    if x >= len(bars): return None
    o_e = bars[e][O]; o_x = bars[x][O]
    if o_e <= 0: return None
    return o_x/o_e - 1.0

def book_ret(longs, shorts, i, hold):
    lr = [fwd_leg_ret(c, i, hold) for c in longs]
    sr = [fwd_leg_ret(c, i, hold) for c in shorts]
    lr = [x for x in lr if x is not None]; sr = [x for x in sr if x is not None]
    if not lr or not sr: return None
    return 0.5*(statistics.mean(lr)) + 0.5*(-statistics.mean(sr))

def build_books(i, hold, gate=True):
    # live: pct_k k=8 with vol gate
    pk = [(c, pctk(ARR[c], i, 14)) for c in ARR]
    pk = [(c,v) for c,v in pk if v is not None]
    live = None
    if len(pk) >= 16:
        pk.sort(key=lambda x: x[1], reverse=True)
        l_long = [c for c,_ in pk[:8]]; l_short = [c for c,_ in pk[-8:]]
        if gate and btc_high_vol(i):
            live = 0.0  # flat this period
        else:
            live = book_ret(l_long, l_short, i, hold)
    # A13: rsdd k=6
    rs = [(c, rsdd(ARR[c], i, 50)) for c in ARR]
    rs = [(c,v) for c,v in rs if v is not None]
    a13 = None
    if len(rs) >= 12:
        rs.sort(key=lambda x: x[1], reverse=True)  # nearest-high (dd~0) first
        a_long = [c for c,_ in rs[:6]]; a_short = [c for c,_ in rs[-6:]]
        a13 = book_ret(a_long, a_short, i, hold)
    return live, a13

def ols(y, x):
    n = len(y)
    mx = statistics.mean(x); my = statistics.mean(y)
    sxx = sum((xi-mx)**2 for xi in x)
    if sxx <= 0: return None
    beta = sum((xi-mx)*(yi-my) for xi,yi in zip(x,y))/sxx
    alpha = my - beta*mx
    resid = [yi - (alpha+beta*xi) for xi,yi in zip(x,y)]
    if n <= 2: return None
    s2 = sum(r*r for r in resid)/(n-2)
    se_alpha = math.sqrt(s2*(1.0/n + mx*mx/sxx))
    t_alpha = alpha/se_alpha if se_alpha>0 else float('nan')
    return {"alpha":alpha, "beta":beta, "t_alpha":t_alpha, "n":n}

def corr(x, y):
    n=len(x); mx=statistics.mean(x); my=statistics.mean(y)
    sx=statistics.pstdev(x); sy=statistics.pstdev(y)
    if sx<=0 or sy<=0: return float('nan')
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/(n*sx*sy)

def sharpe(xs):
    if len(xs)<2: return float('nan')
    sd=statistics.pstdev(xs)
    return statistics.mean(xs)/sd if sd>0 else float('nan')

def run(hold, step, gate=True, label=""):
    # earliest i needs: pctk(14)+1, rsdd(50), vol hist; and i+1+hold < N_COMMON
    start = 55
    rows = []  # (i, live, a13)
    i = start
    while i + 1 + hold < N_COMMON:
        live, a13 = build_books(i, hold, gate=gate)
        if live is not None and a13 is not None:
            rows.append((i, live, a13))
        i += step
    if len(rows) < 6:
        print(f"  [{label}] thin n={len(rows)}"); return
    idx=[r[0] for r in rows]; L_=[r[1] for r in rows]; A_=[r[2] for r in rows]
    comb=[0.5*a+0.5*b for a,b in zip(L_,A_)]
    # halves by time (idx ordered ascending)
    mid=len(rows)//2
    def stats(sl):
        Ls=[r[1] for r in sl]; As=[r[2] for r in sl]; Cs=[0.5*a+0.5*b for a,b in zip(Ls,As)]
        o=ols(As, Ls)
        return {
            "n":len(sl), "corr":corr(Ls,As),
            "live_mean":statistics.mean(Ls)*100, "a13_mean":statistics.mean(As)*100,
            "live_sh":sharpe(Ls), "a13_sh":sharpe(As), "comb_sh":sharpe(Cs),
            "alpha":(o["alpha"]*100 if o else None), "t_alpha":(o["t_alpha"] if o else None),
            "beta":(o["beta"] if o else None),
        }
    full=stats(rows); h1=stats(rows[:mid]); h2=stats(rows[mid:])
    print(f"\n=== {label}  hold={hold} step={step} gate={gate}  n={full['n']} ===")
    for nm,s in [("FULL",full),("H1",h1),("H2",h2)]:
        print(f" {nm:4} n={s['n']:2} corr={s['corr']:+.3f}  live_sh={s['live_sh']:+.3f} a13_sh={s['a13_sh']:+.3f} comb_sh={s['comb_sh']:+.3f}  alpha={s['alpha']:+.4f}%/per t={s['t_alpha']:+.2f} beta={s['beta']:+.2f}  live_mu={s['live_mean']:+.3f}% a13_mu={s['a13_mean']:+.3f}%")
    return full,h1,h2

print(f"N_COMMON daily bars per coin = {N_COMMON}; coins with >=60d = {len(SER)}")
# Non-overlapping (honest t-stat) at both holds
run(hold=10, step=10, gate=True,  label="NONOVERLAP gated(live)")
run(hold=7,  step=7,  gate=True,  label="NONOVERLAP gated(live)")
run(hold=10, step=10, gate=False, label="NONOVERLAP NOgate")
run(hold=7,  step=7,  gate=False, label="NONOVERLAP NOgate")
# Daily-overlapping (large n for correlation; autocorr inflates t — caveat)
run(hold=10, step=1, gate=True,  label="OVERLAP daily gated(live)")
run(hold=7,  step=1, gate=True,  label="OVERLAP daily gated(live)")
run(hold=7,  step=1, gate=False, label="OVERLAP daily NOgate")
