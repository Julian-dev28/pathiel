"""The 'MANTA setup' = volume-surge breakout (new 48h high + big volume). Test on the universe:
do other coins in the same condition run, or fizzle? Sweep thresholds + measure the FP rate."""
import statistics
import alpha_lib as A
O,H,L,C,V = A.O,A.H,A.L,A.C,A.V
d = A.load_dataset()
coins = [c for c in d["coins"] if c != "BTC"]
W = 48        # breakout window (1h bars)
HZ = 48       # forward horizon (1h bars = 2 days)

def trailing_vmean(bars, i, n=W):
    return statistics.mean([bars[j][V] for j in range(i-n, i)]) or 1e-9

print("# MANTA-setup = new 48h-high breakout + volume surge >= Vx. fwd 24h/48h, net 12bps.")
print(f"{'vol_surge':>9}{'minBO%':>8}{'n':>6}{'fwd24h':>8}{'fwd48h':>8}{'win48':>7}{'ran>=20%':>9}{'ran>=50%':>9}  read")
print("-"*78)
for vx in (5, 10, 30, 65):
    for min_bo in (0.0, 0.05):           # breakout must be >= min_bo above the 48h high
        ev24, ev48, runs20, runs50 = [], [], 0, 0
        for coin in coins:
            bars = A.candles(d, coin, "1h")
            if len(bars) < W + HZ + 5: continue
            i = W
            while i < len(bars) - HZ - 1:
                hi = max(bars[j][H] for j in range(i-W, i))
                vmean = trailing_vmean(bars, i)
                bo = bars[i][C]/hi - 1.0 if hi>0 else -1
                if bo >= min_bo and bars[i][V] >= vx*vmean and bars[i][C] > bars[i][O]:
                    entry = bars[i+1][O]
                    if entry > 0:
                        fwd = bars[i+1:i+1+HZ]
                        r24 = fwd[min(24,len(fwd))-1][C]/entry - 1
                        r48 = fwd[-1][C]/entry - 1
                        mfe = max(b[H] for b in fwd)/entry - 1
                        ev24.append(r24); ev48.append(r48)
                        if mfe >= 0.20: runs20 += 1
                        if mfe >= 0.50: runs50 += 1
                    i += HZ   # one event per window (no overlap)
                else:
                    i += 1
        n = len(ev48)
        if n < 5:
            print(f"{vx:>8}x{100*min_bo:>7.0f}%{n:>6}  (too few)"); continue
        m24 = statistics.mean(ev24)-0.0012; m48 = statistics.mean(ev48)-0.0012
        win48 = sum(1 for x in ev48 if x>0.0012)/n
        read = "RAN" if m48>0.01 else ("ok" if m48>0 else "fizzles -EV")
        print(f"{vx:>8}x{100*min_bo:>7.0f}%{n:>6}{100*m24:>+7.2f}%{100*m48:>+7.2f}%{win48:>7.2f}{runs20/n:>8.0%}{runs50/n:>8.0%}  {read}")
