"""Config sweeps for engulf_short and crash_continue_div_short, same method as
premium_fade: replay the live recording logic over the dataset, grade each config
through the IDENTICAL shadow_ledger.classify, time-OOS via sorted records.
"""
from __future__ import annotations
import sys
import alpha_lib as A
sys.path.insert(0, "/Users/julian_dev/Documents/code/hermes-trader")
from hermes_trader.agents import shadow_ledger as SL

DAY = 86_400_000
d = A.load_dataset()
coins = [c for c in d["coins"] if c != "BTC"]

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
for coin in coins:
    bars = A.candles(d, coin, "1d")
    if len(bars) >= 25:
        candle_index[coin] = {b[A.T] // DAY: b for b in bars}

def fetch_fwd(coin, sig_t, n):
    by_day = candle_index.get(coin, {})
    return [{"t": b[A.T], "h": b[A.H], "l": b[A.L], "c": b[A.C]}
            for b in sorted(by_day.values(), key=lambda x: x[A.T]) if b[A.T] > sig_t][:n]

def bearish_full_engulf(prev, cur, ratio):
    po, pc, co, cc = prev[A.O], prev[A.C], cur[A.O], cur[A.C]
    if not (pc > po and cc < co): return None
    if not (co >= pc and cc <= po): return None
    pb = abs(pc - po)
    if pb <= 0: return None
    r = abs(cc - co) / pb
    return r if r >= 1.0 else None

# --- detect events once (lowest threshold), tag regime + the swept attribute ---
engulf_ev, crash_ev = [], []
for coin, by_day in candle_index.items():
    bars = sorted(by_day.values(), key=lambda x: x[A.T])
    for i in range(2, len(bars) - 1):           # leave >=1 fwd bar
        day = bars[i][A.T] // DAY
        r = bearish_full_engulf(bars[i - 1], bars[i], 1.0)
        if r is not None:
            engulf_ev.append((bars[i][A.T], coin, bars[i][A.C], btc_up_on(day), r))
        c0 = bars[i - 2][A.C]
        if c0 > 0:
            ret2 = bars[i][A.C] / c0 - 1.0
            if ret2 <= -0.06:
                crash_ev.append((bars[i][A.T], coin, bars[i][A.C], btc_up_on(day), ret2))

def regime_ok(up, regime):
    return regime == "all" or (regime == "up" and up is True) or (regime == "down" and up is False)

def grade(events, attr_min, hold, stop, regime):
    sel = [e for e in events if e[4] >= attr_min and regime_ok(e[3], regime)] if attr_min >= 0 else \
          [e for e in events if e[4] <= attr_min and regime_ok(e[3], regime)]
    sel.sort(key=lambda e: e[0])
    recs = [{"coin": c, "side": "short", "signal_bar_t": t, "entry_ref_px": px,
             "horizon_days": hold, "stop_pct": stop} for t, c, px, up, a in sel]
    if not recs: return None
    FUT = max(r["signal_bar_t"] for r in recs) + 30 * DAY
    return SL.grade_records(recs, fetch_fwd, now_ms=FUT)

def show(title, events, grid):
    print(f"\n=== {title} ===")
    print(f"{'param':>8} {'hold':>4} {'stop':>4} {'regime':>6} {'n':>4} {'m25%':>7} {'win':>5} {'OOS h1/h2':>16}  verdict")
    print("-" * 84)
    for label, attr, hold, stop, regime in grid:
        g = grade(events, attr, hold, stop, regime)
        if not g or g["n"] < 5:
            continue
        m25 = g.get("slip25", {}).get("mean_pct"); win = g.get("slip25", {}).get("win")
        oos = g.get("oos_12bps", {})
        print(f"{label:>8} {hold:>4} {stop:>4.0f} {regime:>6} {g['n']:>4} {m25:>7.2f} {win:>5.2f} "
              f"{str(oos.get('first')):>7}/{str(oos.get('second')):>7}  {g['verdict']['label']}")

# engulf_short: live = body>=1.0, hold 1, stop 20, all regimes. Sweep body, hold, regime.
engulf_grid = []
for regime in ("all", "down", "up"):
    for body in (1.0, 1.5):
        for hold in (1, 2, 3):
            engulf_grid.append((f"b{body}", body, hold, 20.0, regime))
show("engulf_short (live: body1.0/hold1/stop20/all)", engulf_ev, engulf_grid)

# crash_continue: live = ret2<=-8%, BTC-UP gate, hold 10, stop 8. Sweep threshold, hold, stop, regime.
crash_grid = []
for regime in ("up", "all", "down"):
    for thr in (-0.08, -0.10, -0.12):
        for hold in (10, 5):
            for stop in (8.0, 20.0):
                crash_grid.append((f"{int(thr*100)}%", thr, hold, stop, regime))
show("crash_continue_div_short (live: -8%/BTC-up/hold10/stop8)", crash_ev, crash_grid)
