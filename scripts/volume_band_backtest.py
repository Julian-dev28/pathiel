#!/usr/bin/env python3
"""Does lowering the volume floor admit +EV movers? Segments mover-longs by 24h
volume band and simulates the LIVE DSL exit — raw, then with volume-scaled
slippage + a thin-book liquidation-gap check (the real cost the floor guards).

Entry model: for every current 24h mover (>= +MOVE%), simulate a long entered at
each of the last N hourly bars (entry-timing spread) and average the live-exit
ROE per coin; aggregate win-rate / net ROE by volume band.
"""
import sys
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val
from hermes_trader.agents.config_store import read_agent_config

cfg = read_agent_config()
LEV = float(cfg.get("leverage", 12))
dsl = cfg.get("dsl_exit", {})
HARD = min(float(dsl.get("max_loss_pct", 0.4)) / 100,
           float(dsl.get("max_loss_roe_pct", 3.0)) / 100 / LEV)
PROTECT = float(dsl.get("protect_pct", 1.25)) / 100
RETRACE = float(dsl.get("retrace_threshold", 0.2))
LIQ = 0.9 / LEV  # approx spot distance to liquidation at LEV

MOVE = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0   # mover threshold %
ENTRIES = 18                                              # entry points per coin (last N 1h bars)
BANDS = [(0, 1e6), (1e6, 3e6), (3e6, 5e6), (5e6, 10e6), (10e6, 1e18)]
LABELS = ["<$1M", "$1-3M", "$3-5M", "$5-10M", ">$10M"]
# per-side slippage by band (bps): derived from realized HIP-3 ~12.7bps / crypto ~1.9bps,
# extrapolated so thinner books cost much more (conservative for thin).
SLIP_BPS = {0: 60.0, 1: 30.0, 2: 15.0, 3: 8.0, 4: 4.0}


def sim_long(c, i, slip_bps):
    s = slip_bps / 1e4
    e = candle_val(c[i], "c") * (1 + s)            # entry fill (buy higher)
    pk = e
    armed = False
    for b in c[i + 1:]:
        h, lo = candle_val(b, "h"), candle_val(b, "l")
        if (lo - e) / e <= -LIQ:                   # thin-book gap through to liquidation
            return -1.0 * LEV * 100, "liq"
        if (lo - e) / e <= -HARD:                  # hard stop
            fill = e * (1 - HARD) * (1 - s)
            return (fill - e) / e * LEV * 100, "stop"
        pk = max(pk, h)
        if (pk - e) / e >= PROTECT:
            armed = True
        if armed:
            floor = pk - (pk - e) * RETRACE
            if lo <= floor:
                fill = floor * (1 - s)
                return (fill - e) / e * LEV * 100, "trail"
    fill = candle_val(c[-1], "c") * (1 - s)
    return (fill - e) / e * LEV * 100, "open"


def main():
    uni = get_universe(include_hip3=True)
    movers = []
    for m in uni:
        prev = float(m.get("prevDayPx") or 0)
        vol = float(m.get("dayNtlVlm") or 0)
        px = float(m.get("midPx") or m.get("markPx") or 0)
        if prev > 0 and px > 0 and (px - prev) / prev * 100 >= MOVE:
            movers.append((m.get("name") or m.get("coin"), vol))
    print(f"# movers >= +{MOVE:.0f}% 24h: {len(movers)} | LEV {LEV:g}x | hard-stop {HARD*100:.2f}% spot | "
          f"liq ~{LIQ*100:.1f}% | entries/coin {ENTRIES}")
    print(f"# {'band':8s} {'tickers':>7s} | {'RAW (no slip)':>22s} | {'+ vol-scaled slip + liq':>26s}")
    for bi, (lo, hi) in enumerate(BANDS):
        coins = [c for c, v in movers if lo <= v < hi]
        raw, net, liqs, n = [], [], 0, 0
        for coin in coins[:30]:
            try:
                cd = fetch_hl_candles(coin, "1h", 48)
            except Exception:
                continue
            if len(cd) < ENTRIES + 4:
                continue
            for i in range(len(cd) - ENTRIES - 1, len(cd) - 1):
                r0, _ = sim_long(cd, i, 0.0)
                r1, how = sim_long(cd, i, SLIP_BPS[bi])
                raw.append(r0)
                net.append(r1)
                liqs += (how == "liq")
                n += 1
        if not net:
            print(f"  {LABELS[bi]:8s} {len(coins):>7d} | (no data)")
            continue
        rw = sum(1 for x in raw if x > 0) / len(raw) * 100
        nw = sum(1 for x in net if x > 0) / len(net) * 100
        print(f"  {LABELS[bi]:8s} {len(coins):>7d} | win {rw:4.0f}%  avgROE {sum(raw)/len(raw):+5.1f}% | "
              f"win {nw:4.0f}%  avgROE {sum(net)/len(net):+5.1f}%  liq {liqs}/{n}")


if __name__ == "__main__":
    main()
