"""A10 rsi_extreme_xs — cross-sectional: long most-oversold RSI(14) / short
most-overbought, daily rebal, regime-gated.

Rule (lookahead-safe): day i, RSI(14) per coin from closes <= i (Wilder). Rank.
Long bottom-m (low RSI/oversold), short top-m (high RSI/overbought). Fill open[i+1],
hold H, exit open[i+1+H]. Daily overlapping rebal. m=6. Regime gate via BTC 7d sign.
vs random 50/50 baseline. hold{1,3,5} x regime{all,up,down}.
Also report the INVERTED book (momentum: long high-RSI) since XS reversal was refuted.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def rsi(c, i, period=14):
    # need closes from i-period .. i
    closes = [px.close(c, j) for j in range(i - period, i + 1)]
    if any(v is None for v in closes):
        return None
    gains = []; losses = []
    for k in range(1, len(closes)):
        ch = closes[k] - closes[k - 1]
        gains.append(max(ch, 0.0)); losses.append(max(-ch, 0.0))
    ag = statistics.mean(gains); al = statistics.mean(losses)
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)

def run(hold, regime, book, m=6):
    trades = []
    for i in range(20, N - hold - 2):
        if regime != "all" and px.btc_regime(i) != regime:
            continue
        rs = []
        for c in coins:
            v = rsi(c, i)
            if v is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            rs.append((v, c))
        if len(rs) < 3 * m:
            continue
        rs.sort()
        low = rs[:m]; high = rs[-m:]
        if book == "reversal":
            longs, shorts = low, high     # long oversold
        else:
            longs, shorts = high, low     # momentum: long overbought
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for v, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
    return trades

print("=" * 100)
print("A10 RSI-EXTREME XS  (reversal=long oversold/short overbought ; momentum=inverse)")
print("=" * 100)
for book in ("reversal", "momentum"):
    print(f"\n### book = {book}")
    for regime in ("all", "up", "down"):
        for hold in (1, 3, 5):
            tr = run(hold, regime, book)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  regime={regime:4s} H={hold}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
