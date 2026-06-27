"""A11 connors_rsi_fade — cross-sectional fade of Connors-RSI extremes.

CRSI = mean( RSI(close,3), RSI(streak,2), PercentRank(ROC1, 100) ).
  streak = signed consecutive up/down day count.
  PercentRank = % of last 100 one-day returns below today's.
Designed for short-term reversion. Fade book: long low-CRSI / short high-CRSI (oversold up).
Lookahead-safe: all from closes <= i. Fill open[i+1], hold H (1-3), daily overlap rebal. m=6.
vs random 50/50 baseline. Also inverse (momentum) for completeness. regime{all,up,down}.
"""
import statistics
import laneA_common as LC

px = LC.Px("1d")
coins = px.coins
N = px.N

def _rsi(vals, period):
    if len(vals) < period + 1: return None
    g = []; l = []
    for k in range(len(vals) - period, len(vals)):
        ch = vals[k] - vals[k - 1]
        g.append(max(ch, 0.0)); l.append(max(-ch, 0.0))
    ag = statistics.mean(g); al = statistics.mean(l)
    if al == 0: return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)

def crsi(c, i):
    closes = [px.close(c, j) for j in range(i - 105, i + 1)]
    if any(v is None for v in closes): return None
    rsi3 = _rsi(closes, 3)
    # streak
    streak = 0
    for k in range(len(closes) - 1, 0, -1):
        if closes[k] > closes[k - 1]:
            if streak >= 0: streak += 1
            else: break
        elif closes[k] < closes[k - 1]:
            if streak <= 0: streak -= 1
            else: break
        else:
            break
    # build streak series (last ~10 needed for rsi period 2)
    streaks = []
    run = 0
    for k in range(1, len(closes)):
        if closes[k] > closes[k - 1]:
            run = run + 1 if run >= 0 else 1
        elif closes[k] < closes[k - 1]:
            run = run - 1 if run <= 0 else -1
        else:
            run = 0
        streaks.append(float(run))
    rsi_streak = _rsi(streaks, 2)
    # percent rank of today's 1d ROC vs last 100
    rocs = [(closes[k] / closes[k - 1] - 1.0) for k in range(len(closes) - 100, len(closes))]
    today = rocs[-1]
    pr = 100.0 * sum(1 for r in rocs[:-1] if r < today) / (len(rocs) - 1)
    if rsi3 is None or rsi_streak is None: return None
    return (rsi3 + rsi_streak + pr) / 3.0

def run(hold, regime, book, m=6):
    trades = []
    for i in range(110, N - hold - 2):
        if regime != "all" and px.btc_regime(i) != regime: continue
        vs = []
        for c in coins:
            v = crsi(c, i)
            if v is None: continue
            if not (px.open(c, i + 1) and px.open(c, i + 1 + hold)): continue
            vs.append((v, c))
        if len(vs) < 3 * m: continue
        vs.sort()
        low = vs[:m]; high = vs[-m:]
        if book == "fade":
            longs, shorts = low, high      # long oversold CRSI
        else:
            longs, shorts = high, low
        for side, grp in (("long", longs), ("short", shorts)):
            sign = 1.0 if side == "long" else -1.0
            for v, c in grp:
                eo, xo = px.open(c, i + 1), px.open(c, i + 1 + hold)
                if eo == 0: continue
                trades.append({"t": px.timeline[i + 1], "ret": sign * (xo / eo - 1.0)})
    return trades

print("=" * 100)
print("A11 CONNORS-RSI FADE  (fade=long low-CRSI/short high-CRSI ; mom=inverse)")
print("=" * 100)
for book in ("fade", "mom"):
    print(f"\n### book={book}")
    for regime in ("all", "up", "down"):
        for hold in (1, 2, 3):
            tr = run(hold, regime, book)
            s = LC.summarize(tr)
            if s.get("n", 0) == 0: continue
            base = LC.baseline_random(px, 0.5, hold, n_samp=4000)
            ex = s["slip12"]["mean_ret_pct"] - base["slip12"]["mean_ret_pct"]
            print(f"  regime={regime:4s} H={hold}: {LC.fmt(s)}  EXCESS={ex:+.4f}")
