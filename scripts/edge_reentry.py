#!/usr/bin/env python3
"""Re-entry churn fix backtest. The live "win-and-reenter" pattern (e.g. JUP 2026-06-21:
+13/+16/+13/-15/-23% ROE over 5 legs, net negative) repeatedly re-enters a momentum coin
at high leverage; the tight 0.10 trail clips wins small while max-loss stops lose big, and
EVERY cycle pays a fresh ~24bps round-trip fee. Question: is the churn systematically -EV,
and which fix helps?

Simulates the per-coin re-entry state machine (flat -> trigger+cooldown -> entry -> live
exit -> cooldown) over real 5m candles, with the live exit (atr/roe stop incl. between-tick
OVERSHOOT from the bar low + 0.10 trail) and realistic round-trip fees. Compares policies:
  BASELINE      unlimited re-entries, cooldown 30min(win)/180min(loss), lev 10
  CAP-2 / CAP-3 max N entries per coin per rolling 24h
  LONGCD-120    cooldown 120min after ANY exit
  HALFSIZE-RE   re-entries at half notional
  CAP3+HALF     combo
Lookahead-safe; OOS = first/second half; $ PnL on a fixed notional so policies compare.
"""
import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

TF, BARS, LB = "5m", 5000, 20
LEV = 10
ROE_CAP, ATR_CEIL_SPOT, PROTECT, RETRACE = 15.0, 2.5, 1.25, 0.10   # live exit
STOP_SPOT = min(ATR_CEIL_SPOT, ROE_CAP / LEV) / 100.0              # 1.5% spot at 10x
NOTIONAL = 200.0
COST = 0.0012                                                       # per side (24bps round-trip)
CD_WIN_BARS, CD_LOSS_BARS = 6, 36                                   # 30min / 180min on 5m
FWD_CAP = 48
TOPN, VOL_FLOOR, FETCH_SLEEP = 35, 5e6, 0.25
WINDOW_24H = 288


def load():
    uni = [m for m in get_universe(include_hip3=False) if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    out, failed = {}, []
    for m in uni:
        c = m.get("name") or m.get("coin")
        try:
            b = fetch_hl_candles(c, TF, BARS)
            if len(b) >= 1000:
                out[c] = ([candle_val(x, "c") for x in b], [candle_val(x, "h") for x in b],
                          [candle_val(x, "l") for x in b])
            else:
                failed.append(c)
        except Exception:
            failed.append(c)
        time.sleep(FETCH_SLEEP)
    for c in failed:
        time.sleep(FETCH_SLEEP * 3)
        try:
            b = fetch_hl_candles(c, TF, BARS)
            if len(b) >= 1000:
                out[c] = ([candle_val(x, "c") for x in b], [candle_val(x, "h") for x in b],
                          [candle_val(x, "l") for x in b])
        except Exception:
            pass
    return out


def run(series, *, cap=None, cd_any=None, half_reentry=False):
    """One policy over all coins. Returns dict of aggregate stats."""
    pnl_usd = 0.0; fees_usd = 0.0; n = wins = 0; rets = []
    for c, (cl, hi, lo) in series.items():
        N = len(cl)
        in_pos = False; entry_px = peak = 0.0; armed = False; entry_bar = 0
        cd_until = -1; entries = []  # bar indices of entries (for the rolling cap)
        i = LB + 1
        while i < N:
            if in_pos:
                # exit check on bar i (overshoot: realized loss can exceed the cap if it gaps)
                ex = None
                if lo[i] <= entry_px * (1 - STOP_SPOT):
                    ex = min(lo[i], entry_px * (1 - STOP_SPOT)) / entry_px - 1   # gap-through = worse
                else:
                    peak = max(peak, hi[i])
                    if (peak - entry_px) / entry_px * 100 >= PROTECT:
                        armed = True
                    if armed:
                        floor = peak - (peak - entry_px) * RETRACE
                        if lo[i] <= floor:
                            ex = floor / entry_px - 1
                    if ex is None and i - entry_bar >= FWD_CAP:
                        ex = cl[i] / entry_px - 1
                if ex is not None:
                    notl = NOTIONAL * (0.5 if (half_reentry and len(entries) > 1) else 1.0)
                    fee = notl * COST * 2
                    pnl = notl * ex - fee
                    pnl_usd += pnl; fees_usd += fee; n += 1; wins += (ex > 0)
                    rets.append((entry_bar / N, pnl))
                    cd = (cd_any if cd_any is not None else (CD_LOSS_BARS if ex <= 0 else CD_WIN_BARS))
                    cd_until = i + cd
                    in_pos = False
                i += 1
                continue
            # flat: entry?
            if i <= cd_until:
                i += 1; continue
            ma = sum(cl[i - LB:i]) / LB
            trend = cl[i] > ma and cl[i] > cl[i - 6]
            recent = [e for e in entries if i - e <= WINDOW_24H]
            if trend and (cap is None or len(recent) < cap):
                in_pos = True; entry_px = cl[i]; peak = cl[i]; armed = False; entry_bar = i
                entries.append(i)
            i += 1
    h = 0.5
    a1 = sum(p for f, p in rets if f < h); a2 = sum(p for f, p in rets if f >= h)
    return {"net": pnl_usd, "fees": fees_usd, "n": n, "win": (wins / n * 100 if n else 0),
            "oos1": a1, "oos2": a2, "robust": a1 > 0 and a2 > 0}


def main():
    print(f"# loading top {TOPN} liquid movers {TF} ~{BARS}b...")
    S = load()
    Nd = min(len(v[0]) for v in S.values()) / 288
    print(f"# {len(S)} coins | ~{Nd:.0f}d | lev{LEV} stop {STOP_SPOT*100:.1f}%spot(+overshoot) "
          f"trail {RETRACE} | notional ${NOTIONAL:.0f} | {COST*2e4:.0f}bps round-trip\n")
    print(f"  {'policy':16s} | {'entries':>7s} | {'win':>4s} | {'fees$':>7s} | {'net$':>8s} | "
          f"{'net/trade':>9s} | OOS h1/h2 rob")
    policies = [
        ("BASELINE", {}),
        ("CAP-2/coin/24h", {"cap": 2}),
        ("CAP-3/coin/24h", {"cap": 3}),
        ("LONGCD-120min", {"cd_any": 24}),
        ("HALFSIZE-RE", {"half_reentry": True}),
        ("CAP3+HALFSIZE", {"cap": 3, "half_reentry": True}),
    ]
    base = None
    for name, kw in policies:
        r = run(S, **kw)
        if name == "BASELINE":
            base = r["net"]
        d = "" if name == "BASELINE" else f"  (Δ {r['net']-base:+.1f})"
        print(f"  {name:16s} | {r['n']:>7d} | {r['win']:>3.0f}% | {r['fees']:>7.1f} | "
              f"{r['net']:>+8.1f} | {r['net']/r['n'] if r['n'] else 0:>+9.3f} | "
              f"OOS {r['oos1']:>+6.1f}/{r['oos2']:>+6.1f} {'Y' if r['robust'] else '-'}{d}")
    print("\n# net/trade in $ on ${:.0f} notional. Δ = net vs BASELINE. If BASELINE net<0 or "
          "net/trade<~fees, the churn is -EV and a cap/cooldown that raises net wins.".format(NOTIONAL))


if __name__ == "__main__":
    main()
