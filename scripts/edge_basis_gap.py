#!/usr/bin/env python3
"""Tokenized-equity OVERNIGHT-GAP edge. While the stock is closed, the thin perp
drifts on crypto sentiment and can't price the real overnight news. When the stock
REOPENS, the gap (open vs prior close) is revealed. Hypothesis: the perp hasn't fully
priced it -> it catches up toward the gap in the hours after the open. Trade the perp
in the gap's direction at the open.

Lookahead-free: gap is observed AT the open; we measure the perp's move over the next
H hours FROM the open. Reports how much of the gap the perp already captured overnight,
the gap->catch-up correlation, and the net strategy return (OOS, cost).
"""
import statistics
import httpx
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

COST = 45.0 / 1e4
H = 4                       # catch-up window after the open (hours)
MIN_GAP = 0.005            # only trade meaningful gaps (>0.5%)
MAP = {
    "xyz:MU": "MU", "xyz:INTC": "INTC", "xyz:MRVL": "MRVL", "xyz:SNDK": "SNDK",
    "xyz:NVDA": "NVDA", "xyz:MSTR": "MSTR", "xyz:TSLA": "TSLA", "xyz:MSFT": "MSFT",
    "xyz:ARM": "ARM", "xyz:GOOGL": "GOOGL", "xyz:IBM": "IBM", "xyz:AMD": "AMD",
}


def yahoo_hourly(ticker):
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                  params={"interval": "1h", "range": "3mo"},
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    res = r.json()["chart"]["result"][0]
    return {int(t) // 3600: c for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None}


def main():
    print(f"# overnight-gap catch-up | H={H}h after open | trade gap>{MIN_GAP*100:.1f}% | cost {COST*1e4:.0f}bps")
    print(f"# {'perp':12s} {'n':>3s} | {'gap-captured-overnight':>21s} | {'gap->catchup r':>14s} | {'strat/trade':>11s} {'1H':>6s} {'2H':>6s} {'rob':>4s}")
    agg = []
    for perp_sym, ticker in MAP.items():
        try:
            pc = fetch_hl_candles(perp_sym, "1h", 2000)
            und = yahoo_hourly(ticker)
        except Exception:
            continue
        perp = {int(c.t) // 3_600_000: candle_val(c, "c") for c in pc}
        uh = sorted(und)
        events = []
        for i in range(1, len(uh)):
            if uh[i] - uh[i - 1] > 4:                       # overnight/weekend gap
                oh, pch = uh[i], uh[i - 1]
                if und[pch] > 0:
                    events.append((oh, pch, und[oh] / und[pch] - 1))
        rows = []          # (gap, perp_overnight, perp_post)
        for oh, pch, gap in events:
            if oh in perp and (oh + H) in perp and pch in perp and perp[pch] > 0 and perp[oh] > 0:
                rows.append((gap, perp[oh] / perp[pch] - 1, perp[oh + H] / perp[oh] - 1))
        big = [r for r in rows if abs(r[0]) > MIN_GAP]
        if len(big) < 15:
            continue
        captured = statistics.mean(min(max(po / g, -1), 2) for g, po, _ in big if g != 0)  # frac of gap perp got overnight
        try:
            corr = statistics.correlation([g for g, _, _ in big], [pp for _, _, pp in big])
        except Exception:
            corr = 0.0
        strat = [(1 if g > 0 else -1) * pp - COST for g, _, pp in big]
        half = len(strat) // 2
        m, m1, m2 = statistics.mean(strat), statistics.mean(strat[:half]), statistics.mean(strat[half:])
        agg += strat
        rob = "YES" if (m1 > 0 and m2 > 0) else "no"
        print(f"  {perp_sym:12s} {len(big):>3d} | {captured*100:>19.0f}% | {corr:>+13.2f} | "
              f"{m*100:+10.2f}% {m1*100:+5.2f}% {m2*100:+5.2f}% {rob:>4s}")
    if agg:
        h = len(agg) // 2
        print(f"\n# AGGREGATE: {statistics.mean(agg)*100:+.2f}%/trade over {len(agg)} gaps | "
              f"1stH {statistics.mean(agg[:h])*100:+.2f}% | 2ndH {statistics.mean(agg[h:])*100:+.2f}%")


if __name__ == "__main__":
    main()
