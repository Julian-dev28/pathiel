"""STEP 2: lookahead-safe hour-of-day long test on xyz HIP-3 stocks.

Entry = open of the bar whose UTC hour == H. Exit = open of bar exactly
HOLD hours later (matched by timestamp; gaps skipped). Net cost applied.

Null = each coin's OWN grand-mean return for that hold (demean per coin),
so 'excess' = hour effect beyond the coin just drifting. A real time-of-day
edge must show positive excess, OOS-robust (both date halves), cost-surviving.
"""
import json, statistics
from datetime import datetime, timezone
from collections import defaultdict

D = json.load(open('scratchpad/hip3_1h.json'))
C = D['candles']
HOUR_MS = 3600_000
COST = 0.0012  # net 12 bps round trip (prompt). 25bps tested separately.

def bars_index(rows):
    return {r[0]: r for r in rows}  # t -> [t,o,h,l,c,v]

def trades_for_coin(rows, hold, weekday_only):
    """Return list of (entry_t, hour, weekday, ret_no_cost)."""
    idx = bars_index(rows)
    out = []
    for t, o, h, l, c, v in rows:
        dt = datetime.fromtimestamp(t/1000, tz=timezone.utc)
        if weekday_only and dt.weekday() >= 5:
            continue
        ex = idx.get(t + hold*HOUR_MS)
        if ex is None:
            continue
        entry = o
        exitp = ex[1]  # open of exit bar
        if entry <= 0:
            continue
        ret = exitp/entry - 1.0
        out.append((t, dt.hour, dt.weekday(), ret))
    return out

def date_half(t, coin_rows):
    mid = coin_rows[0][0] + (coin_rows[-1][0] - coin_rows[0][0]) / 2
    return 0 if t < mid else 1

def analyze(coins, hold, weekday_only=True, cost=COST):
    """Pooled, per-coin-demeaned. Returns dict hour-> stats and OOS halves."""
    # gather per coin
    per_hour = defaultdict(list)            # hour -> [demeaned_ret_net]
    per_hour_half = {0: defaultdict(list), 1: defaultdict(list)}
    raw_per_hour = defaultdict(list)        # hour -> raw net ret (not demeaned)
    n_coins_used = 0
    for coin in coins:
        rows = C.get(coin, [])
        if len(rows) < 24*30:  # need ~30 days min
            continue
        tr = trades_for_coin(rows, hold, weekday_only)
        if len(tr) < 24*20:
            continue
        n_coins_used += 1
        grand = statistics.mean(r[3] for r in tr)
        for t, hr, wd, ret in tr:
            net = ret - cost
            dem = (ret - grand) - 0.0  # demean removes coin drift; cost is constant so cancels in excess
            per_hour[hr].append(dem)
            raw_per_hour[hr].append(net)
            half = date_half(t, rows)
            per_hour_half[half][hr].append(ret - grand)
    res = {}
    for hr in range(24):
        ex = per_hour.get(hr, [])
        raw = raw_per_hour.get(hr, [])
        res[hr] = {
            'n': len(ex),
            'excess_bps': (statistics.mean(ex)*1e4) if ex else 0.0,
            'raw_net_bps': (statistics.mean(raw)*1e4) if raw else 0.0,
            'win': (sum(1 for x in raw if x > 0)/len(raw)) if raw else 0.0,
            'ex_h0_bps': (statistics.mean(per_hour_half[0][hr])*1e4) if per_hour_half[0].get(hr) else 0.0,
            'ex_h1_bps': (statistics.mean(per_hour_half[1][hr])*1e4) if per_hour_half[1].get(hr) else 0.0,
            'n_h0': len(per_hour_half[0].get(hr, [])),
            'n_h1': len(per_hour_half[1].get(hr, [])),
        }
    return res, n_coins_used

ALL = [c for c in C if C[c]]
STOCKS = ALL  # all xyz are stocks/commodities/indices

def print_table(res, ncoins, title):
    print(f"\n### {title}  (coins used={ncoins})")
    print(" H | n     | excess_bps | net_bps | win% | exH0 | exH1 | OOS")
    for hr in range(24):
        r = res[hr]
        oos = 'BOTH+' if (r['ex_h0_bps']>0 and r['ex_h1_bps']>0) else ('both-' if (r['ex_h0_bps']<0 and r['ex_h1_bps']<0) else 'mixed')
        mark = ' <==' if hr in (20,21,22,23) else ''
        print(f"{hr:02d} | {r['n']:5d} | {r['excess_bps']:9.1f} | {r['raw_net_bps']:7.1f} | {r['win']*100:4.1f} | {r['ex_h0_bps']:6.1f} | {r['ex_h1_bps']:6.1f} | {oos}{mark}")

for hold in [24, 4, 1, 16]:
    print("\n" + "="*78)
    print(f"HOLD = {hold}h  | POOLED across all xyz stocks (weekday-only) | cost {COST*1e4:.0f}bps")
    res, nc = analyze(STOCKS, hold, weekday_only=True)
    print_table(res, nc, f"POOLED weekday-only hold={hold}h")

# SPCX specific (single coin, no pooling). Both weekday-only and all-days.
print("\n" + "="*78)
print("SpaceX xyz:SPCX  (single coin)")
for hold in [24, 4, 1, 16]:
    for wk in [True, False]:
        res, nc = analyze(['xyz:SPCX'], hold, weekday_only=wk)
        if nc == 0:
            print(f"  SPCX hold={hold}h weekday_only={wk}: insufficient sample")
            continue
        print_table(res, nc, f"SPCX hold={hold}h weekday_only={wk}")

# US-close window summary: hours 20-23, pooled, weekday-only, cost sensitivity
print("\n" + "="*78)
print("US-CLOSE WINDOW (UTC 20-23) pooled weekday-only — cost sensitivity")
for cost in [0.0012, 0.0025]:
    res, nc = analyze(STOCKS, 24, weekday_only=True, cost=cost)
    for hr in [20,21,22,23]:
        r = res[hr]
        print(f"  cost{cost*1e4:.0f}bps H{hr}: excess={r['excess_bps']:.1f}bps net={r['raw_net_bps']:.1f}bps "
              f"win={r['win']*100:.1f}% OOS h0={r['ex_h0_bps']:.1f} h1={r['ex_h1_bps']:.1f}")
