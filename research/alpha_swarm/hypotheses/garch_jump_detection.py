"""B10 garch_jump_detection — classify a daily bar as JUMP if |ret| > z*trailing-vol (vol-scaled,
not fixed %). Trade post-jump drift vs reversion, up/down jumps separately. Stop-width sweep +
excess over matched random-entry baseline (the -44% tape demands it)."""
import statistics, random
import alpha_lib as A

d = A.load_dataset()
coins=d["coins"]
random.seed(9)
VW=20
STOPS=[0.08,0.15,0.20,0.25,0.40]
HOR=5

def gather():
    """Return jump events: dict (dir) -> list of (t, entry_px, fwd)."""
    ev={"up":[],"down":[]}
    for c in coins:
        cd=A.candles(d,c,"1d")
        if len(cd)<VW+HOR+3: continue
        cl=[b[A.C] for b in cd]
        rets=[cl[k]/cl[k-1]-1 if cl[k-1] else 0 for k in range(1,len(cl))]  # rets[k]-> bar k+1
        for i in range(VW+1,len(cd)-HOR-1):
            sd=statistics.pstdev(rets[i-1-VW:i-1])  # vol up to bar i-1 (excludes bar i)
            if sd<=0: continue
            r_i=cl[i]/cl[i-1]-1
            z=r_i/sd
            # fresh: prior bar not itself a jump
            r_prev=cl[i-1]/cl[i-2]-1 if i>=2 else 0
            sd_prev=statistics.pstdev(rets[i-2-VW:i-2]) if i-2-VW>=0 else sd
            prev_jump = sd_prev>0 and abs(r_prev/sd_prev)>3
            if prev_jump: continue
            if abs(z)<3: continue
            dirn="up" if z>0 else "down"
            ev[dirn].append((cd[i+1][A.T], cd[i+1][A.O], cd[i+1:], abs(z)))
    return ev

ev=gather()
print(f"jump events: up={len(ev['up'])} down={len(ev['down'])} (z>=3, vol-scaled, fresh)")

def best_cell(events, side):
    """Pick stop maximizing OOS-robust EV; return summary at that stop + random baseline."""
    best=None
    for sp in STOPS:
        trades=[{"t":t,"ret":A.sweep_stop(px,side,fwd,[sp],HOR)[sp]} for t,px,fwd,_ in events]
        s=A.summarize(trades)
        oos=s["oos_12bps"]; h1,h2=oos["first_half_mean_pct"],oos["second_half_mean_pct"]
        robust = h1 is not None and h2 is not None and h1>0 and h2>0
        key=(robust, min(h1,h2) if robust else s["slip12"]["mean_ret_pct"])
        if best is None or key>best[0]:
            best=(key, sp, s)
    # random baseline at chosen stop (random side, same events/stop/horizon)
    _,sp,s=best
    rnd=[{"t":t,"ret":A.sweep_stop(px,random.choice(['long','short']),fwd,[sp],HOR)[sp]} for t,px,fwd,_ in events]
    rb=A.summarize(rnd)["slip12"]["mean_ret_pct"]
    return sp,s,rb

print(f"\n{'quadrant':18s} {'stop':>4s} {'n':>4s} {'EV12%':>7s} {'win':>5s} {'h1':>7s} {'h2':>7s} {'excess':>7s} verdict")
for dirn in ["up","down"]:
    for side_name,side in [("continue", "long" if dirn=="up" else "short"),
                           ("fade",     "short" if dirn=="up" else "long")]:
        sp,s,rb=best_cell(ev[dirn],side)
        sl,oos=s["slip12"],s["oos_12bps"]
        exc=sl["mean_ret_pct"]-rb
        print(f"{dirn+'_'+side_name:18s} {sp*100:4.0f} {s['n']:4d} {sl['mean_ret_pct']:7.3f} {sl['win_rate']:5.3f} "
              f"{str(oos['first_half_mean_pct']):>7s} {str(oos['second_half_mean_pct']):>7s} {exc:7.3f}  {s['verdict']}")
