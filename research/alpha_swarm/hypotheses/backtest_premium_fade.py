"""Backtest the LIVE premium_fade_short spec through the SAME grader shadow_status
will use forward — so the PENDING book gets a verdict NOW, not in 1-2 weeks.

Replays the live book's recording logic over the 90d funding history (every z>=2
event becomes a record exactly as the live book would write it: side short,
signal_bar_t = that day's bar, entry_ref_px = that day's close, horizon 5, stop 20),
then runs shadow_ledger.grade_records + classify (the identical forward-grade path).
"""
from __future__ import annotations
import statistics, sys
import alpha_lib as A
import funding_lib as F
sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
from hermes_trader.agents import shadow_ledger as SL

DAY = 86_400_000
Z_THRESHOLD = 2.0      # live config
LOOKBACK = 30          # live premium_lookback_days
HORIZON = 5            # live hold_days
STOP = 20.0            # live stop_pct

d = A.load_dataset()
fd = F.load_funding()
coins = [c for c in d["coins"] if c != "BTC"]

# build the historical records exactly as the live book would have recorded them
records = []
candle_index = {}   # coin -> {day: [t,o,h,l,c,v]}
for coin in coins:
    bars = A.candles(coin if False else coin, "1d") if False else A.candles(d, coin, "1d")
    if len(bars) < 35:
        continue
    by_day = {b[A.T] // DAY: b for b in bars}
    candle_index[coin] = by_day
    # daily premium series
    prem_buckets = {}
    for t, rate, prem in F.rows(fd, coin):
        prem_buckets.setdefault(t // DAY, []).append(prem)
    daily_prem = {day: statistics.mean(v) for day, v in prem_buckets.items() if v}
    pdays = sorted(daily_prem)
    for i in range(LOOKBACK, len(pdays)):
        day = pdays[i]
        hist = [daily_prem[pdays[j]] for j in range(i - LOOKBACK, i)]
        mu, sd = statistics.mean(hist), statistics.pstdev(hist)
        if sd <= 0:
            continue
        z = (daily_prem[day] - mu) / sd
        if z < Z_THRESHOLD:
            continue
        if day not in by_day:
            continue
        bar = by_day[day]
        records.append({
            "coin": coin, "side": "short",
            "signal_bar_t": bar[A.T],
            "entry_ref_px": bar[A.C],     # live book records the completed close
            "horizon_days": HORIZON, "stop_pct": STOP,
        })

# fetch_fwd over the dataset candles (bars strictly AFTER signal_bar_t), as dicts simulate_exit reads
def fetch_fwd(coin, sig_t, n):
    by_day = candle_index.get(coin, {})
    out = [{"t": b[A.T], "h": b[A.H], "l": b[A.L], "c": b[A.C]}
           for b in sorted(by_day.values(), key=lambda x: x[A.T]) if b[A.T] > sig_t]
    return out[:n]

# sort by signal time so grade_records' [:half]/[half:] split is a real TIME OOS
# (live forward use appends in time order; the backtest must replicate that ordering)
records.sort(key=lambda r: r["signal_bar_t"])

# grade through the IDENTICAL live path; now_ms far in the future so every event resolves
FUTURE = max(r["signal_bar_t"] for r in records) + 30 * DAY
grade = SL.grade_records(records, fetch_fwd, now_ms=FUTURE)

print(f"# premium_fade_short — BACKTEST through the live grader (spec: z>={Z_THRESHOLD}, {HORIZON}d, {STOP}% stop)")
print(f"# events graded: n={grade['n']}  (this is the backtest preview of the PENDING forward verdict)")
print(f"{'slip_bps':>8} {'mean%':>8} {'total%':>9} {'win':>6}")
for bps in SL.SLIP_TIERS_BPS:
    s = grade.get(f"slip{bps}")
    if s:
        print(f"{bps:>8} {s['mean_pct']:>8.3f} {s['total_pct']:>9.2f} {s['win']:>6.2f}")
oos = grade.get("oos_12bps", {})
print(f"# OOS @12bps  first={oos.get('first')}  second={oos.get('second')}  (n {oos.get('n_first')}/{oos.get('n_second')})")
v = grade["verdict"]
print(f"# BACKTEST VERDICT: {v['label']} — {v['why']}")
print("# NOTE: survivor universe = UPPER BOUND; forward shadow is the PIT confirmation. Same classify() gates as shadow_status.")
