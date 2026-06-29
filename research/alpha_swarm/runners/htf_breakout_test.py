#!/usr/bin/env python3
"""Validate the '1.5x-previous-candle GREEN volume breakout' edge on native 1h/4h/1d data.

ENTRY (operator rule): green candle (close>open) whose volume >= 1.5x the PREVIOUS candle's volume.
  Decide on complete bar i -> enter bar i+1 open (lookahead-safe).
EXIT (tight profit-floor): arm at +1%, exit on 10% give-back from peak, hard stop 15%, horizon 48 bars.

Rigor: regime split (BTC up/down/flat at entry), TF-scaled $-vol floor sweep, matched random-green null,
cost sensitivity (12/25/40bps), and exit comparison (tight-floor vs hold-to-horizon vs wider trail).
Survivor universe (today's liquid set) => positive results are an UPPER BOUND.
"""
import json, statistics, random
from pathlib import Path
SCR = Path("/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad")
T,O,H,L,Cl,Vv = 0,1,2,3,4,5
random.seed(7)
def mean(xs): return sum(xs)/len(xs) if xs else 0.0
def med(xs): return statistics.median(xs) if xs else 0.0

HORIZON = 48   # forward bars for exit/MFE

# ---- exits (operate on fwd bars AFTER entry; entry is fill price) ----
def exit_tight_floor(entry, fwd, gb=0.10, stop=0.15, arm=0.01):
    peak=entry; armed=False
    for b in fwd:
        peak=max(peak,b[H]); g=peak/entry-1
        if b[L]<=entry*(1-stop): return -stop
        if g>=arm: armed=True
        if armed and b[L]<=entry*(1+g*(1-gb)): return g*(1-gb)
    return fwd[-1][Cl]/entry-1
def exit_hold(entry, fwd, stop=0.15):
    for b in fwd:
        if b[L]<=entry*(1-stop): return -stop
    return fwd[-1][Cl]/entry-1
def exit_wide_trail(entry, fwd, gb=0.25, stop=0.20, arm=0.02):
    peak=entry; armed=False
    for b in fwd:
        peak=max(peak,b[H]); g=peak/entry-1
        if b[L]<=entry*(1-stop): return -stop
        if g>=arm: armed=True
        if armed and b[L]<=entry*(1+g*(1-gb)): return g*(1-gb)
    return fwd[-1][Cl]/entry-1

# ---- BTC regime classifier (percentile terciles of trailing return at signal time) ----
def btc_regime_fn(btc_bars, win):
    """Returns f(ts)->trailing BTC return over `win` bars ending at-or-before ts, and the
    tercile cut points so we label down/flat/up balanced."""
    idx = {b[T]: k for k,b in enumerate(btc_bars)}
    ts_sorted = [b[T] for b in btc_bars]
    def trailing_ret(ts):
        # find last btc bar with t <= ts
        k = None
        # binary-ish: ts are aligned for same interval; fall back to <=
        lo,hi=0,len(ts_sorted)-1; pos=-1
        while lo<=hi:
            mid=(lo+hi)//2
            if ts_sorted[mid]<=ts: pos=mid; lo=mid+1
            else: hi=mid-1
        if pos<win: return None
        return btc_bars[pos][Cl]/btc_bars[pos-win][Cl]-1
    return trailing_ret

def collect(coins, candles, btc_bars, btc_win, min_bars=200):
    """Collect all 1.5x-green-vol breakout signals across coins. Returns list of trade dicts
    with raw fwd attached for flexible exit/cost re-scoring."""
    trail = btc_regime_fn(btc_bars, btc_win)
    sigs=[]
    for coin in coins:
        bars=candles.get(coin,[])
        if len(bars)<min_bars: continue
        i=1
        n=len(bars)
        while i < n-HORIZON-2:
            b=bars[i]; pv=bars[i-1][Vv]
            green = b[Cl]>b[O]
            if green and pv>0 and b[Vv]>=1.5*pv:
                entry=bars[i+1][O]
                if entry>0:
                    fwd=bars[i+1:i+1+HORIZON]
                    if len(fwd)>=HORIZON:
                        ext = b[Cl]/b[O]-1                      # signal-candle body return
                        dvol = b[Cl]*b[Vv]                      # dollar volume of signal candle
                        sigs.append({"coin":coin,"t":b[T],"entry":entry,"fwd":fwd,
                                     "ext":ext,"dvol":dvol,
                                     "mfe":max(x[H] for x in fwd)/entry-1,
                                     "btc":trail(b[T])})
                        i += 12   # cooldown
                        continue
            i+=1
    return sigs

def score(sigs, exitfn, cost):
    out=[]
    for s in sigs:
        r=exitfn(s["entry"], s["fwd"]) - cost
        out.append({"t":s["t"],"ret":r,"mfe":s["mfe"],"ext":s["ext"]})
    return out

def report(trades, label):
    if len(trades)<20:
        print(f"  {label:<40} n={len(trades)} (too few)"); return None
    trades=sorted(trades,key=lambda x:x["t"]); h=len(trades)//2
    rets=[t["ret"] for t in trades]
    ev=mean(rets); win=sum(1 for r in rets if r>0)/len(rets)
    r10=sum(1 for t in trades if t["mfe"]>=0.10)/len(trades)
    r20=sum(1 for t in trades if t["mfe"]>=0.20)/len(trades)
    r50=sum(1 for t in trades if t["mfe"]>=0.50)/len(trades)
    h1=mean([t["ret"] for t in trades[:h]]); h2=mean([t["ret"] for t in trades[h:]])
    mext=med([t["ext"] for t in trades])
    fl="OK+EV" if h1>0 and h2>0 else ("NEG" if h1<0 and h2<0 else "MIX")
    print(f"  {label:<40} n={len(trades):<5} EV {100*ev:+.2f}% win {100*win:3.0f}% med {100*med(rets):+.2f}% "
          f"| r10 {100*r10:3.0f}% r20 {100*r20:3.0f}% r50 {100*r50:3.0f}% | ext {100*mext:+.1f}% | OOS {100*h1:+.2f}/{100*h2:+.2f} {fl}")
    return {"ev":ev,"win":win,"h1":h1,"h2":h2,"n":len(trades)}

def run_tf(iv, btc_win):
    p=SCR/f"htf_{iv}.json"
    if not p.exists(): print(f"\n##### {iv}: no data file"); return
    d=json.loads(p.read_text())
    coins=d["meta"]["coins"]; candles=d["candles"]
    btc_bars=candles.get("BTC",[])
    if len(btc_bars)<btc_win+5:
        print(f"\n##### {iv}: no BTC bars for regime"); return
    # span info
    allts=[b[T] for c in candles.values() for b in c if c]
    span_days=(max(allts)-min(allts))/86400000 if allts else 0
    sigs=collect(coins, candles, btc_bars, btc_win)
    print(f"\n{'='*92}\n##### TIMEFRAME {iv}  (BTC regime win={btc_win} bars; ~{span_days:.0f}d span; {len(coins)} coins; {len(sigs)} signals)")
    print("-- (1) BASE: 1.5x-green-vol breakout, tight-floor exit, net cost tiers --")
    base12=report(score(sigs, exit_tight_floor, 0.0012), "tight-floor @12bps")
    # (d) cost sensitivity
    print("-- (d) COST SENSITIVITY --")
    report(score(sigs, exit_tight_floor, 0.0025), "tight-floor @25bps")
    report(score(sigs, exit_tight_floor, 0.0040), "tight-floor @40bps")
    # (e) exit comparison @12bps
    print("-- (e) EXIT COMPARISON @12bps --")
    report(score(sigs, exit_hold, 0.0012),       "hold-to-horizon (15% stop)")
    report(score(sigs, exit_wide_trail, 0.0012), "wide-trail (25% gb / 20% stop)")
    # (c) matched null: random GREEN candles, same coins/times region, same exit
    print("-- (c) MATCHED NULL (random green candle, tight-floor @12bps) --")
    null=collect_null(coins, candles, btc_bars, btc_win, n_target=len(sigs))
    nullres=report(score(null, exit_tight_floor, 0.0012), "random-green null")
    if base12 and nullres:
        print(f"     EXCESS over null: EV {100*(base12['ev']-nullres['ev']):+.2f}%  win {100*(base12['win']-nullres['win']):+.0f}pp")
    # (a) REGIME SPLIT (terciles of BTC trailing ret at entry)
    print("-- (a) BTC REGIME SPLIT (terciles of BTC trailing return at entry; tight-floor @12bps) --")
    have=[s for s in sigs if s["btc"] is not None]
    if len(have)>=30:
        btcs=sorted(s["btc"] for s in have); n=len(btcs)
        lo=btcs[n//3]; hi=btcs[2*n//3]
        down=[s for s in have if s["btc"]<=lo]
        flat=[s for s in have if lo<s["btc"]<hi]
        up  =[s for s in have if s["btc"]>=hi]
        print(f"     tercile cuts: down<= {100*lo:+.1f}%  | flat | up>= {100*hi:+.1f}% (BTC {btc_win}-bar trailing ret)")
        report(score(down, exit_tight_floor, 0.0012), "DOWN-tape (lowest BTC tercile)")
        report(score(flat, exit_tight_floor, 0.0012), "FLAT-tape (mid BTC tercile)")
        report(score(up,   exit_tight_floor, 0.0012), "UP-tape (highest BTC tercile)")
    else:
        print(f"     too few regime-tagged signals ({len(have)})")
    # (b) TF-scaled $-vol floor sweep
    print("-- (b) ABSOLUTE $-VOLUME FLOOR SWEEP (signal-candle close*vol; tight-floor @12bps) --")
    dvols=sorted(s["dvol"] for s in sigs)
    qs=[0,0.25,0.5,0.75,0.9]
    for q in qs:
        floor=dvols[int(q*(len(dvols)-1))]
        sub=[s for s in sigs if s["dvol"]>=floor]
        report(score(sub, exit_tight_floor, 0.0012), f"$vol>= {floor/1e6:.2f}M (p{int(q*100)})")

def collect_null(coins, candles, btc_bars, btc_win, n_target):
    """Random GREEN candles (no volume condition) matched roughly to signal count, same exit horizon."""
    trail=btc_regime_fn(btc_bars,btc_win)
    pool=[]
    for coin in coins:
        bars=candles.get(coin,[])
        if len(bars)<200: continue
        for i in range(1,len(bars)-HORIZON-2):
            if bars[i][Cl]>bars[i][O]:
                pool.append((coin,i))
    random.shuffle(pool)
    out=[]
    for coin,i in pool[:max(n_target*3,n_target)]:
        bars=candles[coin]; entry=bars[i+1][O]
        if entry<=0: continue
        fwd=bars[i+1:i+1+HORIZON]
        if len(fwd)<HORIZON: continue
        out.append({"coin":coin,"t":bars[i][T],"entry":entry,"fwd":fwd,
                    "ext":bars[i][Cl]/bars[i][O]-1,"dvol":bars[i][Cl]*bars[i][Vv],
                    "mfe":max(x[H] for x in fwd)/entry-1,"btc":trail(bars[i][T])})
        if len(out)>=n_target: break
    return out

if __name__=="__main__":
    # BTC regime window ~ recent trend: 1h->24 bars(1d), 4h->42 bars(7d), 1d->7 bars(1w)
    run_tf("1h", 24)
    run_tf("4h", 42)
    run_tf("1d", 7)
