"""floor_long — can the LONG liquidity floor (min_market_volume_usd, $700k) drop?

Test the bot's real long entry (TA-confirmed momentum breakout + a trailing-momentum
long) on NATIVE coins, PER VOLUME BAND, net of each band's OWN slippage (which grows as
volume falls). A "lower it" verdict requires the edge to clear the band's slippage AND
OOS both halves AND not be pure survivor noise.

Lookahead-safe: signal decided on bars [..i], filled at bar i+1 open.
Exit: hold {1,3,5} d, OR a tight profit-floor (TP at +profit_floor, hard 15% stop).
BTC regime: gate on BTC up (close > 20d SMA) — trend-aligned is the documented edge.
Sweep slippage mult {0.5, 1.0, 1.5}.
"""
from __future__ import annotations
import statistics
import liquidity_lib as L
import alpha_lib as A

BANDS = ["0.1-0.7M", "0.7-2M", "2-5M", "5-20M", "20-50M", "50M+"]
T, O, H, Lo, C, V = 0, 1, 2, 3, 4, 5


def btc_up_flags(sma=20):
    """BTC up-regime by timestamp, from the MAIN dataset (has clean BTC 1d)."""
    dmain = A.load_dataset()
    btc = A.candles(dmain, "BTC", "1d")
    closes = [b[A.C] for b in btc]
    times = [b[A.T] for b in btc]
    up = {}
    for i in range(len(btc)):
        if i < sma:
            continue
        up[times[i]] = closes[i] > statistics.mean(closes[i - sma:i])
    return up


def breakout_signal(cd, i, N=20, burst=0.01, volmult=1.5):
    """new N-bar-high close + >=burst 1-bar pop + volume > volmult * avg(N)."""
    if i < N:
        return False
    prior_high = max(cd[j][C] for j in range(i - N, i))
    if cd[i][C] <= prior_high:
        return False
    if A.pct(cd[i - 1][C], cd[i][C]) < burst:
        return False
    avgv = statistics.mean(cd[j][V] for j in range(i - N, i))
    if avgv <= 0 or cd[i][V] < volmult * avgv:
        return False
    return True


def trailmom_signal(cd, i, fast=10, slow=20):
    """trailing-momentum: close > slow SMA and fast-bar return positive (continuation)."""
    if i < slow:
        return False
    sma_slow = statistics.mean(cd[j][C] for j in range(i - slow, i))
    if cd[i][C] <= sma_slow:
        return False
    if A.pct(cd[i - fast][C], cd[i][C]) <= 0:
        return False
    # require it to be rising (this bar up) to mimic a momentum trigger, not a passive hold
    return cd[i][C] > cd[i - 1][C]


def realize(entry, fwd, horizon, profit_floor=None, hard_stop=0.15):
    """Lookahead-safe forward fill. If profit_floor set: TP at +pf, hard stop at -hard_stop,
    else hold `horizon` bars to close. Returns gross fractional long return."""
    if profit_floor is not None:
        tp_px = entry * (1 + profit_floor)
        stop_px = entry * (1 - hard_stop)
        for bar in fwd[:horizon]:
            if bar[Lo] <= stop_px:
                return A.pct(entry, stop_px)
            if bar[H] >= tp_px:
                return A.pct(entry, tp_px)
        last = fwd[min(horizon, len(fwd)) - 1][C] if fwd else entry
        return A.pct(entry, last)
    else:
        last = fwd[min(horizon, len(fwd)) - 1][C] if fwd else entry
        return A.pct(entry, last)


def collect(d, coins, up_flags, sigfn, horizon, profit_floor, gate_btc):
    """All trades for a set of coins under one config. Non-overlapping per coin."""
    trades = []
    for c in coins:
        cd = L.candles(d, c, "1d")
        if len(cd) < 40:
            continue
        last = -999
        for i in range(20, len(cd) - horizon - 2):
            if gate_btc and not up_flags.get(cd[i][T], False):
                continue
            if not sigfn(cd, i):
                continue
            if i - last < horizon:  # non-overlapping
                continue
            if not sigfn.__name__.startswith("breakout"):
                pass
            last = i
            entry = cd[i + 1][O]
            if entry <= 0:
                continue
            ret = realize(entry, cd[i + 2:], horizon, profit_floor)
            trades.append({"t": cd[i + 1][T], "ret": ret})
    return trades


def band_report(rets_with_t, vol_med, slip_mult):
    """Net of band slippage at given mult, with OOS halves (caller pre-sorted by time)."""
    if not rets_with_t:
        return {"n": 0}
    slip_bps = L.band_slippage_bps(vol_med, slip_mult)
    slip_frac = slip_bps / 10000.0
    srt = sorted(rets_with_t, key=lambda x: x["t"])
    rets = [x["ret"] for x in srt]
    gross = [r for r in rets]
    net = [r - slip_frac for r in rets]
    n = len(net)
    half = n // 2

    def ev(xs):
        return round(100 * statistics.mean(xs), 4) if xs else None

    return {
        "n": n,
        "slip_bps": round(slip_bps, 1),
        "gross_pct": ev(gross),
        "net_pct": ev(net),
        "win": round(sum(1 for x in net if x > 0) / n, 3),
        "oos_h1": ev(net[:half]),
        "oos_h2": ev(net[half:]),
    }


def run():
    d = L.load()
    up_flags = btc_up_flags()

    # band median volume (for band_slippage_bps lookup — each band keys its own slip)
    band_vol = {}
    for b in BANDS:
        nat = L.coins_in_band(d, b, native_only=True)
        vs = [L.vol(d, c) for c in nat]
        band_vol[b] = statistics.median(vs) if vs else 0

    configs = [
        ("breakout", breakout_signal, "hold1", 1, None),
        ("breakout", breakout_signal, "hold3", 3, None),
        ("breakout", breakout_signal, "hold5", 5, None),
        ("breakout", breakout_signal, "pf3%", 5, 0.03),
        ("trailmom", trailmom_signal, "hold3", 3, None),
        ("trailmom", trailmom_signal, "hold5", 5, None),
        ("trailmom", trailmom_signal, "pf3%", 5, 0.03),
    ]

    for gate_btc in (True, False):
        print("\n" + "=" * 110)
        print(f"BTC-UP GATE = {gate_btc}   (gate=True is the FAVORABLE / trend-aligned case)")
        print("=" * 110)
        for (style, sigfn, exitlbl, horizon, pf) in configs:
            print(f"\n--- {style} / exit={exitlbl} (horizon={horizon}, pf={pf}) ---")
            header = (f"{'band':10s} {'n':>4s} {'slip':>6s} {'gross%':>8s} "
                      f"{'net@0.5':>8s} {'net@1.0':>8s} {'net@1.5':>8s} {'win':>5s} "
                      f"{'h1@1.0':>8s} {'h2@1.0':>8s}  verdict")
            print(header)
            for b in BANDS:
                nat = L.coins_in_band(d, b, native_only=True)
                trades = collect(d, nat, up_flags, sigfn, horizon, pf, gate_btc)
                if len(trades) < 12:
                    print(f"{b:10s} {len(trades):>4d}  (thin sample, <12 trades)")
                    continue
                r05 = band_report(trades, band_vol[b], 0.5)
                r10 = band_report(trades, band_vol[b], 1.0)
                r15 = band_report(trades, band_vol[b], 1.5)
                robust = (r10["oos_h1"] is not None and r10["oos_h2"] is not None
                          and r10["oos_h1"] > 0 and r10["oos_h2"] > 0 and r10["net_pct"] > 0)
                verdict = "ROBUST+EV" if robust else (
                    "+net,not-OOS" if (r10["net_pct"] or 0) > 0 else "-EV")
                print(f"{b:10s} {r10['n']:>4d} {r10['slip_bps']:>6.0f} "
                      f"{r10['gross_pct']:>8.3f} {r05['net_pct']:>8.3f} {r10['net_pct']:>8.3f} "
                      f"{r15['net_pct']:>8.3f} {r10['win']:>5.2f} "
                      f"{str(r10['oos_h1']):>8s} {str(r10['oos_h2']):>8s}  {verdict}")


if __name__ == "__main__":
    run()
