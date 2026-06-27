"""Seasonality alpha hunt: hour-of-day, day-of-week, session-block drift.

Lookahead-safe: calendar buckets are known in advance. Each trade enters at the
bucket-start bar OPEN and exits at the bucket-end bar CLOSE (no peeking at any
close to decide). Returns are signed by side. We screen ALL buckets in-sample,
then demand OOS both-halves robustness (alpha_lib.summarize) — multiple-comparison
honesty is the whole point.
"""
from __future__ import annotations
import datetime as dt
import statistics
import alpha_lib as al

d = al.load_dataset()
coins = d["coins"]
T, O, H, L, C, V = al.T, al.O, al.H, al.L, al.C, al.V

def utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)

# ---------------------------------------------------------------------------
# 1) HOUR-OF-DAY  (1h candles)
# trade hour h = enter at OPEN of the bar covering [h:00,h+1:00), exit at CLOSE.
# bucket return = (C-O)/O of that bar. Calendar => no lookahead.
# ---------------------------------------------------------------------------
hour_trades = {h: [] for h in range(24)}  # h -> list of {t,ret_long}
for coin in coins:
    for bar in al.candles(d, coin, "1h"):
        o, c = bar[O], bar[C]
        if not o:
            continue
        h = utc(bar[T]).hour
        hour_trades[h].append({"t": bar[T], "ret": al.pct(o, c)})

print("=== HOUR-OF-DAY in-sample mean (long) bps ===")
hour_mean = {}
for h in range(24):
    rs = [t["ret"] for t in hour_trades[h]]
    hour_mean[h] = statistics.mean(rs)
    print(f"  h{h:02d}  mean={1e4*hour_mean[h]:+7.2f}bps  n={len(rs)}")

best_h = max(hour_mean, key=hour_mean.get)
worst_h = min(hour_mean, key=hour_mean.get)
print(f"BEST hour (long candidate): h{best_h:02d}  WORST hour (short candidate): h{worst_h:02d}")

# ---------------------------------------------------------------------------
# 2) DAY-OF-WEEK  (1d candles) weekday Mon=0..Sun=6
# trade enter at day OPEN exit at day CLOSE.
# ---------------------------------------------------------------------------
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
wd_trades = {w: [] for w in range(7)}
for coin in coins:
    for bar in al.candles(d, coin, "1d"):
        o, c = bar[O], bar[C]
        if not o:
            continue
        w = utc(bar[T]).weekday()
        wd_trades[w].append({"t": bar[T], "ret": al.pct(o, c)})

print("\n=== DAY-OF-WEEK in-sample mean (long) bps ===")
wd_mean = {}
for w in range(7):
    rs = [t["ret"] for t in wd_trades[w]]
    wd_mean[w] = statistics.mean(rs)
    print(f"  {WD[w]}  mean={1e4*wd_mean[w]:+8.2f}bps  n={len(rs)}")
best_w = max(wd_mean, key=wd_mean.get)
worst_w = min(wd_mean, key=wd_mean.get)
print(f"BEST weekday (long): {WD[best_w]}  WORST weekday (short): {WD[worst_w]}")

# ---------------------------------------------------------------------------
# 3) SESSION BLOCKS  (1h candles) Asia 00-08, Europe 08-16, US 16-24 UTC.
# one position: enter at OPEN of first hour of block, exit at CLOSE of last hour.
# group per coin per calendar day.
# ---------------------------------------------------------------------------
SESSIONS = {"Asia(00-08)": (0, 8), "Europe(08-16)": (8, 16), "US(16-24)": (16, 24)}
sess_trades = {s: [] for s in SESSIONS}
for coin in coins:
    # index bars by (date, hour)
    by_day = {}
    for bar in al.candles(d, coin, "1h"):
        u = utc(bar[T])
        by_day.setdefault(u.date(), {})[u.hour] = bar
    for day, hrs in by_day.items():
        for s, (a, b) in SESSIONS.items():
            if a in hrs and (b - 1) in hrs:
                o = hrs[a][O]
                c = hrs[b - 1][C]
                if o:
                    sess_trades[s].append({"t": hrs[a][T], "ret": al.pct(o, c)})

print("\n=== SESSION BLOCK in-sample mean (long) bps ===")
for s in SESSIONS:
    rs = [t["ret"] for t in sess_trades[s]]
    print(f"  {s}  mean={1e4*statistics.mean(rs):+8.2f}bps  n={len(rs)}")

# ---------------------------------------------------------------------------
# Validate the candidate winners with OOS both-halves + slippage sweep.
# For a SHORT candidate, flip the sign of the gross return so summarize's
# cost model and OOS logic apply to the short P&L directly.
# ---------------------------------------------------------------------------
def short(trades):
    return [{"t": t["t"], "ret": -t["ret"]} for t in trades]

def show(name, trades):
    print(f"\n----- {name}  (n={len(trades)}) -----")
    s = al.summarize(trades)
    for bps in al.SLIP_TIERS_BPS:
        r = s[f"slip{bps}"]
        print(f"  slip{bps:2d}bps  mean={100*0+r['mean_ret_pct']:+.4f}%  "
              f"win={r['win_rate']:.3f}  sharpe={r['sharpe_like']:+.3f}  tot={r['total_pct']:+.2f}%")
    o = s["oos_12bps"]
    print(f"  OOS@12bps  H1={o['first_half_mean_pct']}%  H2={o['second_half_mean_pct']}%  "
          f"(n {o['n_first']}/{o['n_second']})")
    print(f"  VERDICT: {s['verdict']}")
    return s

print("\n\n################ CANDIDATE VALIDATION ################")
show(f"HOUR long h{best_h:02d}", hour_trades[best_h])
show(f"HOUR short h{worst_h:02d}", short(hour_trades[worst_h]))
show(f"WEEKDAY long {WD[best_w]}", wd_trades[best_w])
show(f"WEEKDAY short {WD[worst_w]}", short(wd_trades[worst_w]))

# best/worst session
sess_mean = {s: statistics.mean([t["ret"] for t in sess_trades[s]]) for s in SESSIONS}
bs = max(sess_mean, key=sess_mean.get); ws = min(sess_mean, key=sess_mean.get)
show(f"SESSION long {bs}", sess_trades[bs])
show(f"SESSION short {ws}", short(sess_trades[ws]))

# ---------------------------------------------------------------------------
# Robustness scan: how many of the 24 hours survive OOS both-halves same-sign?
# This quantifies the multiple-comparison risk directly.
# ---------------------------------------------------------------------------
print("\n\n################ MULTIPLE-COMPARISON AUDIT ################")
def oos_signs(trades):
    f, s = al.time_split(trades)
    if not f or not s:
        return None, None
    h1 = statistics.mean([t["ret"] for t in f])
    h2 = statistics.mean([t["ret"] for t in s])
    return h1, h2

surv_long = surv_short = 0
for h in range(24):
    h1, h2 = oos_signs(hour_trades[h])
    if h1 is None:
        continue
    if h1 > 0 and h2 > 0:
        surv_long += 1
    if h1 < 0 and h2 < 0:
        surv_short += 1
print(f"hours with BOTH-half positive (long-robust, pre-cost): {surv_long}/24")
print(f"hours with BOTH-half negative (short-robust, pre-cost): {surv_short}/24")
print("(pre-cost; a real edge must also clear ~12-25bps round-trip)")
