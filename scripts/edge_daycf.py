#!/usr/bin/env python3
"""Day counterfactual: what would the last ~24h have yielded with PROPER (funding-account)
sizing vs the old aggregate sizing? Replays the day's actual top movers' 5m candles through
the live gates+exit. Two arms on the SAME mover stream + SAME $60 main account:

  OLD (agg-sized):  notional sized vs aggregate $170 -> ~$226/pos -> ~2 fit on $60 main
  NEW (main-sized): notional sized vs main $60        -> ~$80/pos  -> ~6 fit on $60 main

Both arms: extension cap (skip 24h ext>30% = -EV chase), late_chase_relax (admit liquid
20-30% pocket without a fresh breakout), CAP-3 per coin, live exit (2.5%/atr stop + 0.10
trail, with between-tick overshoot), 24bps round-trip fees. Lookahead-safe. NOT a perfect
loop replica (no AI verdict layer) — it isolates the SIZING/capacity effect, which is the fix.
"""
import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

# the day's actual top movers (peak % from our scan logs, last 24h)
MOVERS = ["TNSR","RESOLV","ACE","AXS","SAGA","2Z","MET","SAND","W","CHIP","POPCAT","AERO","JUP","DYM"]
TF, DAY_BARS, LB24, MA = "5m", 320, 288, 20
MAIN_EQ, AGG_EQ = 60.0, 170.0
RISK_PCT, LEV = 0.02, 10
STOP_SPOT = min(2.5, 15.0/LEV)/100.0          # 1.5% spot at 10x (roe cap binds)
PROTECT, RETRACE = 1.25, 0.10
COST = 0.0012
MARGIN_FLOOR = 0.10
CAP_PER_COIN, CD_WIN, CD_LOSS = 3, 6, 36
EXT_MAX, POCKET_LO, POCKET_HI = 30.0, 20.0, 30.0
FWD_CAP = 48


def load():
    S = {}
    for c in MOVERS:
        try:
            b = fetch_hl_candles(c, TF, DAY_BARS)
            if len(b) >= LB24 + 24:
                S[c] = ([candle_val(x,"c") for x in b], [candle_val(x,"h") for x in b], [candle_val(x,"l") for x in b])
        except Exception:
            pass
        time.sleep(0.25)
    return S


def run(S, size_equity, label):
    N = min(len(v[0]) for v in S.values())
    coins = list(S)
    notional = (RISK_PCT * size_equity) / STOP_SPOT          # the FIX: notional from funding equity
    margin = notional / LEV
    max_conc = max(1, int((MAIN_EQ * (1-MARGIN_FLOOR)) / margin))   # how many fit on the $60 main
    book = {}; cooldown = {}; entries = {}; closes = []
    for t in range(LB24+1, N):
        # exits
        for c in list(book):
            cl,hi,lo = S[c]; p = book[c]
            ex = None
            if lo[t] <= p["e"]*(1-STOP_SPOT): ex = min(lo[t], p["e"]*(1-STOP_SPOT))/p["e"]-1
            else:
                p["pk"]=max(p["pk"],hi[t])
                if (p["pk"]-p["e"])/p["e"]*100>=PROTECT: p["arm"]=True
                if p["arm"]:
                    fl=p["pk"]-(p["pk"]-p["e"])*RETRACE
                    if lo[t]<=fl: ex=fl/p["e"]-1
                if ex is None and t-p["t"]>=FWD_CAP: ex=cl[t]/p["e"]-1
            if ex is not None:
                closes.append((t, c, notional*(ex-COST), ex>0)); cooldown[c]=t+(CD_WIN if ex>0 else CD_LOSS); del book[c]
        # entries (strongest-extension first, capturable only)
        cands=[]
        for c in coins:
            cl,hi,lo=S[c]
            if c in book or t<cooldown.get(c,0): continue
            if len([e for e in entries.get(c,[]) if t-e<=LB24])>=CAP_PER_COIN: continue
            base=cl[t-LB24]; ext=(cl[t]/base-1)*100 if base>0 else 0
            ma=sum(cl[t-MA:t])/MA
            uptrend = cl[t]>ma and cl[t]>cl[t-6]
            if not uptrend or ext>EXT_MAX: continue          # extension cap: skip >30% (-EV)
            hh=max(hi[t-48:t]); fresh = cl[t]>hh
            pocket = POCKET_LO<=ext<=POCKET_HI               # late_chase_relax band
            if fresh or pocket:                              # runner-gate proxy OR late-chase pocket
                cands.append((ext,c))
        cands.sort(reverse=True)
        for ext,c in cands:
            if len(book)>=max_conc: break
            cl=S[c][0]
            book[c]={"e":cl[t],"pk":cl[t],"arm":False,"t":t}
            entries.setdefault(c,[]).append(t)
    for c,p in book.items():
        cl=S[c][0]; ex=cl[N-1]/p["e"]-1; closes.append((N-1, c, notional*(ex-COST), ex>0))
    net=sum(x[2] for x in closes); n=len(closes); w=sum(1 for x in closes if x[3])
    bycoin={}
    for _,c,pnl,_ in closes: bycoin[c]=bycoin.get(c,0)+pnl
    top=sorted(bycoin.items(), key=lambda kv: kv[1], reverse=True)
    print(f"{label:18s} | notional ${notional:5.0f} | max_conc {max_conc} | trades {n:3d} | win {w/n*100 if n else 0:3.0f}% | net ${net:+7.2f}")
    return net, top


def main():
    print(f"# day counterfactual | {len(MOVERS)} movers {TF} ~24h | $60 main | extension cap {EXT_MAX:.0f}% | "
          f"late_chase {POCKET_LO:.0f}-{POCKET_HI:.0f}% | CAP-{CAP_PER_COIN} | exit {STOP_SPOT*100:.1f}%/{RETRACE}\n")
    S = load()
    print(f"# loaded {len(S)}/{len(MOVERS)} movers\n")
    old_net,_ = run(S, AGG_EQ, "OLD (agg-sized)")
    new_net,top = run(S, MAIN_EQ, "NEW (main-sized/FIX)")
    print(f"\n# Δ (fix vs old): ${new_net-old_net:+.2f} on the day | actual live day (JUP churn only) = -$2.27")
    print("# NEW top contributors:", ", ".join(f"{c} ${v:+.1f}" for c,v in top[:6]))
    print("# Caveat: isolates SIZING/capacity (no AI verdict layer); +EV here is the thin 20-30% pocket, "
          "not the >30% headline movers (correctly skipped as -EV).")


if __name__ == "__main__":
    main()
