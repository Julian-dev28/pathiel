"""Engulf signal: SHORT (live book) vs LONG (the opposite call), same exit, OOS.
A real directional edge => its opposite is -EV. Live book: bearish full-body engulf -> short."""
import alpha_lib as A
d = A.load_dataset()
O, H, L, C = A.O, A.H, A.L, A.C

def bearish_engulf(prev, cur, ratio=1.0):
    po, pc, co, cc = prev[O], prev[C], cur[O], cur[C]
    if not (pc > po and cc < co):     return False   # yest green, today red
    if not (co >= pc and cc <= po):   return False   # today engulfs yest body
    pb = abs(pc - po)
    return pb > 0 and abs(cc - co) / pb >= ratio

def exit_ret(side, entry, fwd, stop_pct, horizon):
    if entry <= 0 or not fwd: return None
    if side == "long":
        stop = entry * (1 - stop_pct/100)
        for b in fwd[:horizon]:
            if b[L] <= stop: return -stop_pct/100
        return fwd[min(horizon, len(fwd))-1][C] / entry - 1.0
    else:
        stop = entry * (1 + stop_pct/100)
        for b in fwd[:horizon]:
            if b[H] >= stop: return -stop_pct/100
        return entry / fwd[min(horizon, len(fwd))-1][C] - 1.0

for hold in (1, 3):
    short_tr, long_tr = [], []
    for coin in d["coins"]:
        if coin == "BTC": continue
        bars = A.candles(d, coin, "1d")
        if len(bars) < 30: continue
        for i in range(2, len(bars) - hold - 1):
            if not bearish_engulf(bars[i-1], bars[i]): continue
            entry = bars[i+1][O]; fwd = bars[i+1: i+1+hold+1]
            sr = exit_ret("short", entry, fwd, 20.0, hold)
            lr = exit_ret("long",  entry, fwd, 20.0, hold)
            t = bars[i][A.T]
            if sr is not None: short_tr.append({"t": t, "ret": sr})
            if lr is not None: long_tr.append({"t": t, "ret": lr})
    print(f"\n=== bearish-engulf, hold {hold}d, 20% stop, n={len(short_tr)} ===")
    for name, tr in (("SHORT (live book)", short_tr), ("LONG (opposite call)", long_tr)):
        s = A.summarize(tr)
        s12 = s.get("slip12", {}); s25 = s.get("slip25", {}); oos = s.get("oos_12bps", {})
        print(f"  {name:<22} EV12 {s12.get('mean_ret_pct'):+.3f}% EV25 {s25.get('mean_ret_pct'):+.3f}% | "
              f"win {s12.get('win_rate')} | OOS h1/h2 {oos.get('first_half_mean_pct')}/{oos.get('second_half_mean_pct')} | {s.get('verdict')}")
