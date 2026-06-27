"""floor_short: can min_short_volume_usd drop below $50M?

Tests the 3 LIVE short-book triggers on lower-volume NATIVE coins, net of each band's
slippage, OOS both halves, scored as EXCESS over a regime+band matched random-SHORT null.

Bands tested: 5-20M (25bps), 20-50M (12bps), 50M+ (6bps reference).
Entry is lookahead-safe: signal on close of bar i, FILL at bar i+1 OPEN, walk i+1..i+hold.
Short ret = (entry - exit)/entry. Stop at entry*(1+stop) -> ret = -stop. Else exit last close.
"""
from __future__ import annotations
import statistics, math
import liquidity_lib as L
import alpha_lib as A

DAY = 86_400_000
H, Lo, C, O, Tt = L.H, L.L, L.C, L.O, L.T

dm = L.load()                       # marginal universe (trade coins + bands)
dmain = A.load_dataset()            # main dataset (BTC regime, per instruction)

# --- BTC 20d-trend regime, keyed by day-bucket ---
_btc = A.candles(dmain, "BTC", "1d")
_bc = {b[Tt] // DAY: b[C] for b in _btc}
_bdays = sorted(_bc)
_bidx = {d: i for i, d in enumerate(_bdays)}
def btc_dir(day):
    """True=up(20d), False=down, None=insufficient. day = ms//DAY of the signal bar."""
    prior = [x for x in _bdays if x <= day]
    if not prior:
        return None
    d = prior[-1]; i = _bidx[d]
    if i < 20:
        return None
    return _bc[d] > _bc[_bdays[i - 20]]

# --- per-coin sorted 1d bars for the three reference bands ---
BANDS = ["5-20M", "20-50M", "50M+"]
SLIP_BPS = {"5-20M": 25.0, "20-50M": 12.0, "50M+": 6.0}
band_bars = {}
for bnd in BANDS:
    for c in L.coins_in_band(dm, bnd, native_only=True):
        bars = sorted(L.candles(dm, c, "1d"), key=lambda x: x[Tt])
        if len(bars) >= 25:
            band_bars.setdefault(bnd, {})[c] = bars

def short_ret(bars, i, hold, stop_pct):
    """Fill at bar i+1 open, walk i+1..i+hold, return (entry_t, gross short ret) or None."""
    if i + 1 >= len(bars):
        return None
    entry = bars[i + 1][O]
    if entry <= 0:
        return None
    fwd = bars[i + 1: i + 1 + hold]
    stop_px = entry * (1 + stop_pct)
    for bar in fwd:
        if bar[H] >= stop_px:
            return bars[i + 1][Tt], -stop_pct
    last = fwd[-1][C]
    return bars[i + 1][Tt], (entry - last) / entry

def bearish_engulf(prev, cur):
    po, pc, co, cc = prev[O], prev[C], cur[O], cur[C]
    if not (pc > po and cc < co):
        return False
    if not (co >= pc and cc <= po):
        return False
    pb = abs(pc - po)
    return pb > 0 and abs(cc - co) / pb >= 1.0

# --- event collectors per band ---
def collect(bnd, trigger, hold, stop_pct):
    """Return list of (entry_t, gross_ret) for the trigger's filled shorts in this band."""
    out = []
    for c, bars in band_bars.get(bnd, {}).items():
        for i in range(2, len(bars) - 1):
            day = bars[i][Tt] // DAY
            d = btc_dir(day)
            hit = False
            if trigger == "rally_exh":
                c0 = bars[i - 2][C]
                if c0 > 0 and bars[i][C] / c0 - 1.0 >= 0.12 and d is False:
                    hit = True
            elif trigger == "crash_cont":
                c0 = bars[i - 2][C]
                if c0 > 0 and bars[i][C] / c0 - 1.0 <= -0.08 and d is True:
                    hit = True
            elif trigger == "engulf":
                if bearish_engulf(bars[i - 1], bars[i]):
                    hit = True
            if hit:
                r = short_ret(bars, i, hold, stop_pct)
                if r:
                    out.append(r)
    return out

def null_pool(bnd, hold, stop_pct, regime):
    """Matched random-SHORT null: EVERY short entry in the band, optionally regime-gated.
    regime: 'down','up','all'. Carries the band's down-beta. Returns list of gross rets."""
    out = []
    for c, bars in band_bars.get(bnd, {}).items():
        for i in range(2, len(bars) - 1):
            day = bars[i][Tt] // DAY
            d = btc_dir(day)
            if regime == "down" and d is not False:
                continue
            if regime == "up" and d is not True:
                continue
            r = short_ret(bars, i, hold, stop_pct)
            if r:
                out.append(r[1])
    return out

def stats(rets, slip_frac):
    if not rets:
        return None
    net = [r - slip_frac for r in rets]
    n = len(net); half = n // 2
    m = statistics.mean(net)
    sd = statistics.pstdev(net) if n > 1 else 0.0
    return {
        "n": n,
        "gross": round(100 * statistics.mean(rets), 3),
        "net": round(100 * m, 3),
        "win": round(sum(1 for x in net if x > 0) / n, 3),
        "h1": round(100 * statistics.mean(net[:half]), 3) if half else None,
        "h2": round(100 * statistics.mean(net[half:]), 3) if n - half else None,
        "tstat": round(m / (sd / math.sqrt(n) + 1e-12), 2),
    }

def run_trigger(name, trigger, hold, stop_pct, null_regime):
    print(f"\n{'='*100}\n{name}  (hold={hold}d stop={int(stop_pct*100)}% null_regime={null_regime})\n{'='*100}")
    hdr = f"{'band':>7} {'slipMult':>8} {'n':>4} {'gross%':>7} {'net%':>7} {'win':>5} {'oosH1%':>7} {'oosH2%':>7} {'null%':>7} {'excess%':>8} {'t':>6}  flag"
    for bnd in BANDS:
        evs = collect(bnd, trigger, hold, stop_pct)
        ev_rets = [r for _, r in evs]
        evs_sorted = sorted(evs, key=lambda x: x[0])
        ev_rets_t = [r for _, r in evs_sorted]
        nullr = null_pool(bnd, hold, stop_pct, null_regime)
        print(f"\n  [{bnd}] events n={len(ev_rets)}  null-pool n={len(nullr)}  band-slip={SLIP_BPS[bnd]}bps")
        print("  " + hdr)
        for mult in (0.5, 1.0, 1.5):
            slip = SLIP_BPS[bnd] / 10000.0 * mult
            s = stats(ev_rets_t, slip)
            ns = stats(nullr, slip)
            if not s:
                print(f"  {bnd:>7} {mult:>8.1f}  NO EVENTS")
                continue
            null_net = ns["net"] if ns else None
            excess = round(s["net"] - null_net, 3) if null_net is not None else None
            # flag: clears slip (net>0) AND both OOS halves>0 AND excess>0 over null AND n>=8
            ok = (s["net"] > 0 and s["h1"] is not None and s["h2"] is not None
                  and s["h1"] > 0 and s["h2"] > 0 and excess is not None and excess > 0 and s["n"] >= 8)
            flag = "CLEARS" if ok else ("thin" if s["n"] < 8 else "FAILS")
            print(f"  {bnd:>7} {mult:>8.1f} {s['n']:>4} {s['gross']:>7} {s['net']:>7} {s['win']:>5} "
                  f"{str(s['h1']):>7} {str(s['h2']):>7} {str(null_net):>7} {str(excess):>8} {s['tstat']:>6}  {flag}")

# 1) rally_exhaustion: +12%/2d & BTC-DOWN, wide stop sweep, hold sweep
for stop in (0.15, 0.20, 0.25):
    for hold in (5, 10):
        run_trigger(f"rally_exhaustion stop{int(stop*100)} hold{hold}", "rally_exh", hold, stop, "down")

# 2) crash_continue: -8%/2d & BTC-UP, hold10, stop {8,20}
for stop in (0.08, 0.20):
    run_trigger(f"crash_continue stop{int(stop*100)}", "crash_cont", 10, stop, "up")

# 3) engulf: bearish full engulf -> short next day, hold1, stop20
run_trigger("engulf hold1 stop20", "engulf", 1, 0.20, "all")
