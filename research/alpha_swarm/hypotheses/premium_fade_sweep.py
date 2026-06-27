"""Config sweep for premium_fade_short: does a tighter z / regime gate / different
hold-stop turn the MARGINAL live edge ROBUST, or is the calm-regime weakness structural?

Builds every z>=2.0 event once (with its z and BTC regime), then for each
(z_threshold, hold, stop, regime_gate) filters + grades through the IDENTICAL live
grader (shadow_ledger.grade_records + classify). Time-OOS via sorted records.
The decisive rows are regime=up: if up is REFUTED for all z, the edge is a pure
down-regime trade (structural weakness, overlaps the existing short thesis).
"""
from __future__ import annotations
import statistics, sys
import alpha_lib as A
import funding_lib as F
sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
from hermes_trader.agents import shadow_ledger as SL

DAY = 86_400_000
LOOKBACK = 30
d = A.load_dataset(); fd = F.load_funding()
coins = [c for c in d["coins"] if c != "BTC"]

# BTC 20d regime per day
btc = A.candles(d, "BTC", "1d")
btc_close = {b[A.T] // DAY: b[A.C] for b in btc}
btc_days = sorted(btc_close)
def btc_up_on(day):
    prior = [x for x in btc_days if x <= day]
    if not prior: return None
    day = prior[-1]; idx = btc_days.index(day)
    if idx < 20: return None
    return btc_close[day] > btc_close[btc_days[idx - 20]]

candle_index = {}
events = []   # (signal_bar_t, coin, entry_px, z, btc_up)
for coin in coins:
    bars = A.candles(d, coin, "1d")
    if len(bars) < 35: continue
    by_day = {b[A.T] // DAY: b for b in bars}
    candle_index[coin] = by_day
    pb = {}
    for t, rate, prem in F.rows(fd, coin):
        pb.setdefault(t // DAY, []).append(prem)
    daily = {day: statistics.mean(v) for day, v in pb.items() if v}
    pdays = sorted(daily)
    for i in range(LOOKBACK, len(pdays)):
        day = pdays[i]
        hist = [daily[pdays[j]] for j in range(i - LOOKBACK, i)]
        mu, sd = statistics.mean(hist), statistics.pstdev(hist)
        if sd <= 0 or day not in by_day: continue
        z = (daily[day] - mu) / sd
        if z < 2.0: continue
        up = btc_up_on(day)
        events.append((by_day[day][A.T], coin, by_day[day][A.C], z, up))

def fetch_fwd(coin, sig_t, n):
    by_day = candle_index.get(coin, {})
    out = [{"t": b[A.T], "h": b[A.H], "l": b[A.L], "c": b[A.C]}
           for b in sorted(by_day.values(), key=lambda x: x[A.T]) if b[A.T] > sig_t]
    return out[:n]

def grade(zt, hold, stop, regime):
    sel = [e for e in events if e[3] >= zt and (regime == "all"
           or (regime == "up" and e[4] is True) or (regime == "down" and e[4] is False))]
    sel.sort(key=lambda e: e[0])
    recs = [{"coin": c, "side": "short", "signal_bar_t": t, "entry_ref_px": px,
             "horizon_days": hold, "stop_pct": stop} for t, c, px, z, up in sel]
    if not recs: return None
    FUT = max(r["signal_bar_t"] for r in recs) + 30 * DAY
    return SL.grade_records(recs, fetch_fwd, now_ms=FUT)

print(f"{'z':>4} {'hold':>4} {'stop':>4} {'regime':>6} {'n':>4} {'m25%':>7} {'win':>5} {'OOS h1/h2 @12':>16}  verdict")
print("-" * 86)
for regime in ("all", "down", "up"):
    for zt in (2.0, 2.5, 3.0):
        for hold in (5, 7):
            for stop in (20.0,):
                g = grade(zt, hold, stop, regime)
                if not g or g["n"] < 5:
                    continue
                m25 = g.get("slip25", {}).get("mean_pct")
                win = g.get("slip25", {}).get("win")
                oos = g.get("oos_12bps", {})
                v = g["verdict"]["label"]
                print(f"{zt:>4} {hold:>4} {stop:>4.0f} {regime:>6} {g['n']:>4} {m25:>7.2f} {win:>5.2f} "
                      f"{str(oos.get('first')):>7}/{str(oos.get('second')):>7}  {v}")
