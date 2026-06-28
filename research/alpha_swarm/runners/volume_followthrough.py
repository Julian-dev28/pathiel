"""User's pattern: 5m breakout candle with ~3x volume, CONFIRMED by the next candle holding
>=1.5x volume (follow-through), THEN the run. Test: does the 2nd-candle volume confirmation
separate runners from one-bar pump-and-dumps? Decision made at close of the CONFIRM candle,
enter next bar open (lookahead-safe)."""
import statistics
import alpha_lib as A
O,H,L,C,V = A.O,A.H,A.L,A.C,A.V
d = A.load_dataset()   # 40 coins, 5m ~5000 bars
coins = [c for c in d["coins"] if c != "BTC"]
W = 48          # trailing window for vol-avg + breakout high (48 5m bars = 4h)
def mean(xs): return sum(xs)/len(xs) if xs else 0.0

def fwd_stats(group, label):
    if len(group) < 10:
        print(f"  {label:<34} n={len(group)} (too few)"); return
    # forward over 1h (12 bars) and 4h (48 bars) from entry
    r1=[g["r1"] for g in group]; r4=[g["r4"] for g in group]
    run20=sum(1 for g in group if g["mfe"]>=0.20)/len(group)
    run50=sum(1 for g in group if g["mfe"]>=0.50)/len(group)
    print(f"  {label:<34} n={len(group):<4} fwd1h {100*mean(r1):+.2f}% fwd4h {100*mean(r4):+.2f}% "
          f"| ran>=20% {100*run20:.0f}% ran>=50% {100*run50:.0f}% | win4h {sum(1 for x in r4 if x>0)/len(group):.2f}")

confirmed, unconfirmed = [], []
for coin in coins:
    bars = A.candles(d, coin, "5m")
    if len(bars) < W + 60: continue
    i = W
    while i < len(bars) - 60:
        vmean = mean([b[V] for b in bars[i-W:i]]) or 1e-9
        hi = max(b[H] for b in bars[i-W:i])
        # breakout candle i: new 4h high + >=3x volume + green
        if bars[i][C] > hi and bars[i][V] >= 3*vmean and bars[i][C] > bars[i][O]:
            confirm = bars[i+1][V] >= 1.5*vmean           # next candle holds >=1.5x = follow-through
            entry = bars[i+2][O] if i+2 < len(bars) else 0  # enter AFTER the confirm candle (lookahead-safe)
            if entry > 0:
                fwd = bars[i+2:i+2+48]
                rec = {"r1": fwd[min(12,len(fwd))-1][C]/entry-1, "r4": fwd[-1][C]/entry-1,
                       "mfe": max(b[H] for b in fwd)/entry-1}
                (confirmed if confirm else unconfirmed).append(rec)
            i += 24   # cooldown
        else:
            i += 1

print(f"# 5m '3x breakout + 1.5x follow-through' on {len(coins)} coins (net of ~10bps both legs):")
fwd_stats(confirmed,   "CONFIRMED (next candle >=1.5x vol)")
fwd_stats(unconfirmed, "UNCONFIRMED (vol died next candle)")
# net of cost view
for g in confirmed: g["r1"]-=0.0012; g["r4"]-=0.0012
for g in unconfirmed: g["r1"]-=0.0012; g["r4"]-=0.0012
print("# (above is gross; -12bps applied below)")
fwd_stats(confirmed,   "CONFIRMED net12")
fwd_stats(unconfirmed, "UNCONFIRMED net12")
