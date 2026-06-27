"""overnight_intraday: crypto intraday-segment drift anomaly.

Hypothesis: daily return decomposes into a UTC 'overnight' block vs an 'active'
block and one segment systematically carries the drift. Open-to-open fills
(lookahead-safe, realistically fillable). summarize() handles OOS halves + slip.
"""
import statistics, datetime
import alpha_lib as A

d = A.load_dataset()
coins = d["coins"]
HOUR_MS = 3600_000
DAY_MS = 86400_000

def hour_index(d):
    """coin -> {ts -> bar} for 1h candles, ts at top of hour UTC."""
    idx = {}
    for c in coins:
        m = {}
        for bar in A.candles(d, c, "1h"):
            m[int(bar[0])] = bar
        idx[c] = m
    return idx

IDX = hour_index(d)

# enumerate UTC day-starts covered
all_ts = [int(b[0]) for b in A.candles(d, "BTC", "1h")]
t0, t1 = min(all_ts), max(all_ts)
day0 = (t0 // DAY_MS) * DAY_MS
days = list(range(day0, t1 + DAY_MS, DAY_MS))

# ---- BTC regime: 20d SMA on BTC 1d closes ----
btc_1d = A.candles(d, "BTC", "1d")
btc_close = [(int(b[0]), b[A.C]) for b in btc_1d]
def btc_regime(ts_ms):
    """up if BTC close >= 20d SMA at the most recent daily close <= ts."""
    # find daily closes strictly before ts (lookahead-safe)
    closes = [c for (t, c) in btc_close if t <= ts_ms]
    if len(closes) < 21:
        return None
    sma = statistics.mean(closes[-20:])
    return "up" if closes[-1] >= sma else "down"

def seg_open_to_open(coin, start_ts, end_ts):
    """return open@start -> open@end, both must exist as 1h bars."""
    m = IDX[coin]
    a, b = m.get(start_ts), m.get(end_ts)
    if not a or not b:
        return None
    o_a, o_b = a[A.O], b[A.O]
    if not o_a:
        return None
    return o_b / o_a - 1.0

# ---- segment schemes ----
# scheme: name -> list of (seg_label, start_hour, end_hour_relative_day_offset)
# overnight = 00->12 same day ; active = 12->00 next day
# us split: 13->21 (US session) vs 21->13 (rest, spans midnight)
def build_segments(split):
    """yield per-day per-coin segment trades. Returns dict label->list[trade]."""
    res = {}
    for coin in coins:
        for day in days:
            if split == "12h":
                segs = [("overnight", day + 0*HOUR_MS, day + 12*HOUR_MS),
                        ("active",    day + 12*HOUR_MS, day + 24*HOUR_MS)]
            elif split == "us":
                segs = [("us_active", day + 13*HOUR_MS, day + 21*HOUR_MS),
                        ("us_rest",   day + 21*HOUR_MS, day + (21+16)*HOUR_MS)]
            elif split == "6h":
                segs = [("q0_00_06", day+0*HOUR_MS,  day+6*HOUR_MS),
                        ("q1_06_12", day+6*HOUR_MS,  day+12*HOUR_MS),
                        ("q2_12_18", day+12*HOUR_MS, day+18*HOUR_MS),
                        ("q3_18_24", day+18*HOUR_MS, day+24*HOUR_MS)]
            for label, s, e in segs:
                r = seg_open_to_open(coin, s, e)
                if r is None:
                    continue
                res.setdefault(label, []).append({"t": s, "ret": r, "coin": coin,
                                                  "day": day, "regime": btc_regime(s)})
    return res

def show(title, trades, side="long"):
    if not trades:
        print(f"\n== {title} == NO TRADES"); return None
    tr = [{"t": x["t"], "ret": (x["ret"] if side == "long" else -x["ret"])} for x in trades]
    s = A.summarize(tr)
    print(f"\n== {title} ==  side={side} n={s['n']}")
    for bps in [0, 6, 12, 25]:
        b = s[f"slip{bps}"]
        print(f"  slip{bps:>2}: mean={b['mean_ret_pct']:+.4f}% tot={b['total_pct']:+.1f}% win={b['win_rate']:.3f} sh={b['sharpe_like']:+.3f}")
    o = s["oos_12bps"]
    print(f"  OOS@12bps: h1={o['first_half_mean_pct']} h2={o['second_half_mean_pct']} (n {o['n_first']}/{o['n_second']}) -> {s['verdict']}")
    return s

print("="*70, "\n12h split: overnight 00-12 UTC  vs  active 12-24 UTC")
S12 = build_segments("12h")
show("OVERNIGHT 00-12 (long)", S12["overnight"])
show("ACTIVE 12-24 (long)", S12["active"])
# buy-hold over same bars = full open-to-open day (both segments concatenated, no per-seg cost)
bh = []
for coin in coins:
    for day in days:
        r = seg_open_to_open(coin, day, day + 24*HOUR_MS)
        if r is not None:
            bh.append({"t": day, "ret": r})
sbh = A.summarize(bh)
print(f"\n== BUY-HOLD full day (long, 1 trade/day) == n={sbh['n']}")
for bps in [0,6,12,25]:
    b=sbh[f"slip{bps}"]; print(f"  slip{bps:>2}: mean={b['mean_ret_pct']:+.4f}% tot={b['total_pct']:+.1f}% win={b['win_rate']:.3f}")
print(f"  OOS@12: h1={sbh['oos_12bps']['first_half_mean_pct']} h2={sbh['oos_12bps']['second_half_mean_pct']}")

# short the weak segment
show("OVERNIGHT 00-12 (SHORT)", S12["overnight"], side="short")
show("ACTIVE 12-24 (SHORT)", S12["active"], side="short")

print("\n", "="*70, "\nUS-session split: us_active 13-21 vs us_rest 21-13")
SUS = build_segments("us")
show("US_ACTIVE 13-21 (long)", SUS["us_active"])
show("US_REST 21-13 (long)", SUS["us_rest"])

print("\n", "="*70, "\n6h quarters (long each)")
S6 = build_segments("6h")
for lab in ["q0_00_06","q1_06_12","q2_12_18","q3_18_24"]:
    show(lab, S6[lab])

# ---- regime conditioning (12h split) ----
print("\n", "="*70, "\nREGIME-CONDITIONED (BTC 20d SMA), 12h split")
for label in ["overnight", "active"]:
    for reg in ["up", "down"]:
        tr = [x for x in S12[label] if x["regime"] == reg]
        show(f"{label} | regime={reg} (long)", tr)

# ---- continuation / reversal: active(t) -> overnight(t+1) ----
print("\n", "="*70, "\nCONTINUATION/REVERSAL: strong active(day) -> next overnight(day+1)")
# build per-coin map day->active ret and day->overnight ret
act = {}; ovn = {}
for x in S12["active"]:
    act[(x["coin"], x["day"])] = x["ret"]
for x in S12["overnight"]:
    ovn[(x["coin"], x["day"])] = x["ret"]
cont_trades = []   # go long next overnight when active was strongly UP (continuation)
rev_trades = []    # go SHORT next overnight when active was strongly UP (reversal)
# also unconditional correlation
pairs = []
for (coin, day), a in act.items():
    nxt = ovn.get((coin, day + DAY_MS))
    if nxt is None:
        continue
    pairs.append((a, nxt))
    # next overnight is fillable at (day+1) 00:00 open, decided on active close = (day+1)00:00...
    # active ends at day+24h = next day 00:00 open = same bar we'd enter overnight. lookahead-safe (open known).
    ovn_start = day + DAY_MS  # next day 00:00
    if a > 0.005:   # active up >0.5%
        cont_trades.append({"t": ovn_start, "ret": nxt})
        rev_trades.append({"t": ovn_start, "ret": -nxt})
if len(pairs) > 30:
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x-mx)*(y-my) for x,y in pairs)/len(pairs)
    cr = cov/((statistics.pstdev(xs)+1e-12)*(statistics.pstdev(ys)+1e-12))
    print(f"  corr(active_ret, next_overnight_ret) = {cr:+.4f}  n={len(pairs)}")
show("CONTINUATION long-overnight after strong active", cont_trades)
show("REVERSAL short-overnight after strong active", rev_trades)

print("\nDONE")
