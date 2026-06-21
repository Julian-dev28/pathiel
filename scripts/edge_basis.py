#!/usr/bin/env python3
"""Tokenized-equity basis / lead-lag edge. The xyz: HIP-3 perps track a real-world
asset that prices on traditional venues. Hypothesis: the thin perp LAGS its real
underlying — when the underlying moves, the perp follows next hour. Tradeable if the
directional follow-through beats the (high) thin-perp cost.

Lookahead-free: underlying return over [h-1, h] (already-closed bars) -> perp return
over [h, h+1]. Strategy return = sign(underlying move) * perp forward. OOS split,
net of round-trip cost. Underlying prices from Yahoo (free); only hours the
underlying actually traded are used.
"""
import statistics
import httpx
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

COST = 45.0 / 1e4         # thin xyz-perp round-trip (they slip ~12-50bps)
MIN_MOVE = 0.008          # only trade when the underlying moved > 0.3% (signal, not noise)
MAP = {
    "xyz:CL": "CL=F", "xyz:BRENTOIL": "BZ=F", "xyz:SILVER": "SI=F", "xyz:GOLD": "GC=F",
    "xyz:SP500": "^GSPC", "xyz:EWY": "EWY", "xyz:MU": "MU", "xyz:INTC": "INTC",
    "xyz:MRVL": "MRVL", "xyz:SNDK": "SNDK", "xyz:NVDA": "NVDA", "xyz:MSTR": "MSTR",
    "xyz:TSLA": "TSLA", "xyz:MSFT": "MSFT", "xyz:ARM": "ARM", "xyz:GOOGL": "GOOGL",
    "xyz:IBM": "IBM", "xyz:AMD": "AMD",
}


def yahoo_hourly(ticker):
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                  params={"interval": "1h", "range": "3mo"},
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    cl = res["indicators"]["quote"][0]["close"]
    return {int(t) // 3600: c for t, c in zip(ts, cl) if c is not None}  # UTC hour -> close


def main():
    all_strat = []   # (half, strat_ret) aggregated
    print(f"# tokenized-basis lead-lag | cost {COST*1e4:.0f}bps | trade when |underlying move|>{MIN_MOVE*100:.1f}%")
    print(f"# {'perp':14s} {'undr':8s} {'n':>4s} | {'corr':>5s} | {'strat/trade':>11s} {'1stH':>7s} {'2ndH':>7s} {'robust':>6s}")
    for perp_sym, ticker in MAP.items():
        try:
            pc = fetch_hl_candles(perp_sym, "1h", 2000)
            und = yahoo_hourly(ticker)
        except Exception:
            continue
        if len(pc) < 100 or len(und) < 50:
            continue
        perp = {int(c.t) // 3_600_000: candle_val(c, "c") for c in pc}
        hours = sorted(h for h in und if (h - 1) in und and h in perp and (h + 1) in perp)
        rows = []
        for h in hours:
            ur = und[h] / und[h - 1] - 1
            pf = perp[h + 1] / perp[h] - 1
            if perp[h] > 0 and und[h - 1] > 0:
                rows.append((ur, pf))
        if len(rows) < 40:
            continue
        # contemporaneous-lag correlation (does underlying move predict perp next?)
        urs = [r[0] for r in rows]; pfs = [r[1] for r in rows]
        try:
            corr = statistics.correlation(urs, pfs)
        except Exception:
            corr = 0.0
        # directional strategy: trade perp in underlying's direction when move is big
        half = len(rows) // 2
        strat = [(0 if i < half else 1, (1 if ur > 0 else -1) * pf - COST)
                 for i, (ur, pf) in enumerate(rows) if abs(ur) > MIN_MOVE]
        if not strat:
            continue
        all_strat += strat
        s1 = [s for hh, s in strat if hh == 0]; s2 = [s for hh, s in strat if hh == 1]
        m1 = statistics.mean(s1) if s1 else 0; m2 = statistics.mean(s2) if s2 else 0
        overall = statistics.mean(s for _, s in strat)
        robust = "YES" if (m1 > 0 and m2 > 0) else "no"
        print(f"  {perp_sym:14s} {ticker:8s} {len(strat):>4d} | {corr:+.2f} | "
              f"{overall*100:+10.2f}% {m1*100:+6.2f}% {m2*100:+6.2f}% {robust:>6s}")
    if all_strat:
        h = len(all_strat) // 2
        a1 = statistics.mean(s for i, (_, s) in enumerate(all_strat) if i < h)
        a2 = statistics.mean(s for i, (_, s) in enumerate(all_strat) if i >= h)
        print(f"\n# AGGREGATE strat/trade: {statistics.mean(s for _,s in all_strat)*100:+.2f}% over {len(all_strat)} trades "
              f"| 1stH {a1*100:+.2f}% | 2ndH {a2*100:+.2f}%")


if __name__ == "__main__":
    main()
