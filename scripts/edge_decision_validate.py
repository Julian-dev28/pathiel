#!/usr/bin/env python3
"""Decision-quality validation on OUR analysis history. Answers, with realistic PnL
(live DSL exit) and lookahead-safe forward paths:
  1) If we LOWERED the confidence gate to 0.68, would the newly-admitted LONGs be +PnL?
  2) PASS-then-ran: of the coins the AI PASSed, how many ran? (was passing right?)
  3) TA-CONFIRMED-then-ran: of everything that reached research, did it run?
  4) Calibration: does higher AI confidence => higher forward PnL? (is the AI's number real?)

Method: each analysis -> entry at the close of its bar -> replay forward 1h bars through
the LIVE DSL exit (tight stop 0.4% spot / 3% ROE, trail protect 1.25% / retrace 0.20) at a
representative 10x. ROE per trade, cost-aware. Forward path strictly after entry (no peek).
"""
import json
import os
import statistics
import time
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val

CACHE = ".decision-validate-cache.json"
LEV = 10
COST_ROE = (4.5 + 5.0) / 1e4 * 2 * LEV * 100   # taker+slip both sides, % ROE
STOP = min(0.004, 0.03 / LEV)   # live fixed stop
PROTECT, RETRACE = 0.0125, 0.20


def _bars(coin):
    # INTEGRITY-FIRST (owner directive: risk performance for product integrity).
    # Persistent exponential backoff on 429/timeout; NEVER cache an empty/partial result
    # (so a transient 429 can't poison the cache as a permanent silent data-gap that
    # would bias the backtest). A coin only enters the cache with full OHLC bars.
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    if cache.get(coin):
        return cache[coin]
    b = []
    delay = 2.0
    for attempt in range(7):
        try:
            cd = fetch_hl_candles(coin, "1h", 100)
            if cd:
                b = [{"t": x.t, "h": candle_val(x, "h"), "l": candle_val(x, "l"), "c": candle_val(x, "c")}
                     for x in cd]
                # only accept fully-formed bars
                if b and all(all(k in r for k in ("t", "h", "l", "c")) for r in b):
                    break
                b = []
        except Exception:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 30)   # 2,4,8,16,30,30,30s — slow but complete
    if not b:
        print(f"  [integrity] WARN: no complete data for {coin} after 7 tries — EXCLUDED (not cached as gap)")
        return []          # excluded from stats, never cached as a false gap
    cache[coin] = b
    json.dump(cache, open(CACHE, "w"))
    return b


def _entry_atr_pct(at):
    """ATR(14) as a fraction of entry, from pre-entry bars only (lookahead-safe)."""
    if len(at) < 16:
        return None
    trs = []
    for i in range(1, len(at)):
        h, l, pc = at[i]["h"], at[i]["l"], at[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-14:]) / min(14, len(trs))
    px = at[-1]["c"]
    return (atr / px) if px > 0 else None


def long_roe(coin, t_ms, stop_mode="tight"):
    """Replay a LONG from the bar at t_ms through the live DSL trail. stop_mode:
    'tight' = live fixed 0.4%/3%ROE; 'vol' = clamp(2.0*entryATR, 1%,5%) (the shadow's
    wider stop). Forward path strictly after entry. Returns % ROE or None."""
    bs = _bars(coin)
    at = [b for b in bs if b["t"] <= t_ms]
    fwd = [b for b in bs if b["t"] > t_ms]
    if not at or len(fwd) < 3:
        return None
    entry = at[-1]["c"]
    if entry <= 0:
        return None
    if stop_mode == "vol":
        a = _entry_atr_pct(at)
        stop = min(max((a or 0.0) * 2.0, 0.01), 0.05) if a else 0.025
    else:
        stop = STOP
    peak = entry
    armed = False
    for b in fwd:
        if b["l"] <= entry * (1 - stop):
            return -stop * LEV * 100 - COST_ROE
        peak = max(peak, b["h"])
        if (peak - entry) / entry >= PROTECT:
            armed = True
        if armed:
            floor = peak - (peak - entry) * RETRACE
            if b["l"] <= floor:
                return (floor / entry - 1) * LEV * 100 - COST_ROE
    return (fwd[-1]["c"] / entry - 1) * LEV * 100 - COST_ROE


def fwd_spot(coin, t_ms, H=6):
    bs = _bars(coin)
    aft = [b for b in bs if b["t"] > t_ms]
    at = [b for b in bs if b["t"] <= t_ms]
    if not at or len(aft) < H:
        return None
    return (aft[H - 1]["c"] / at[-1]["c"] - 1) * 100 if at[-1]["c"] > 0 else None


def agg(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    w = [x for x in xs if x > 0]
    return {"n": len(xs), "mean": statistics.mean(xs), "win": len(w) / len(xs) * 100, "sum": sum(xs)}


def main():
    an = [a for a in json.load(open(".agent-memory.json")).get("analyses", []) if a.get("created_at")]
    for a in an:
        _bars(a["coin"])  # warm cache

    def conf(a):
        try:
            return float(a.get("confidence", 0) or 0)
        except Exception:
            return 0.0

    longs = [a for a in an if a.get("verdict") == "LONG"]
    passes = [a for a in an if a.get("verdict") == "PASS"]

    print("=== 1) CONFIDENCE-GATE counterfactual — LONG PnL (live DSL exit, 10x) by conf band ===")
    print(f"# {'conf band':14s} | {'n':>3s} | {'avg ROE':>8s} | {'win':>4s} | {'sum ROE':>8s}")
    bands = [(0.68, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 1.01)]
    for lo, hi in bands:
        sub = [long_roe(a["coin"], a["created_at"]) for a in longs if lo <= conf(a) < hi]
        m = agg(sub)
        if m:
            print(f"  [{lo:.2f},{hi:.2f})    | {m['n']:3d} | {m['mean']:+7.2f}% | {m['win']:3.0f}% | {m['sum']:+7.1f}%")
        else:
            print(f"  [{lo:.2f},{hi:.2f})    | n=0")
    # The specific question: the 0.68-0.70 band that a gate=0.68 would ADD
    add = agg([long_roe(a["coin"], a["created_at"]) for a in longs if 0.68 <= conf(a) < 0.70])
    if add:
        print(f"  => lowering gate 0.70->0.68 ADMITS {add['n']} longs @ avg {add['mean']:+.2f}% ROE "
              f"({'+EV ✓' if add['mean'] > 0 else '-EV ✗'})")

    print("\n=== 2) PASS-then-ran — did the coins we PASSed run? (counterfactual long PnL) ===")
    p_roe = agg([long_roe(a["coin"], a["created_at"]) for a in passes])
    p_spot = [fwd_spot(a["coin"], a["created_at"], 6) for a in passes]
    p_spot = [x for x in p_spot if x is not None]
    ran = sum(1 for x in p_spot if x > 3)
    if p_roe:
        print(f"  PASS as-if-long: n={p_roe['n']} | avg {p_roe['mean']:+.2f}% ROE | win {p_roe['win']:.0f}% | "
              f"{ran}/{len(p_spot)} ran >+3% spot in 6h")
        print(f"  => avg {'POSITIVE = we passed runners (misses)' if p_roe['mean'] > 0 else 'NEGATIVE = passing AVOIDED losers (right call)'}")

    print("\n=== 3) DISCRIMINATION — LONG vs PASS (live-exit PnL). LONG must beat PASS ===")
    l_roe = agg([long_roe(a["coin"], a["created_at"]) for a in longs])
    if l_roe and p_roe:
        print(f"  LONG: n={l_roe['n']} avg {l_roe['mean']:+.2f}% ROE win {l_roe['win']:.0f}%")
        print(f"  PASS: n={p_roe['n']} avg {p_roe['mean']:+.2f}% ROE win {p_roe['win']:.0f}%")
        print(f"  => discrimination edge {l_roe['mean'] - p_roe['mean']:+.2f}% ROE "
              f"({'GOOD (we trade the better ones)' if l_roe['mean'] > p_roe['mean'] else 'BAD (passes do as well/better)'})")

    print("\n=== 3b) THE KEY TEST — TIGHT vs WIDER VOL STOP (does the wider stop unlock runs?) ===")
    print(f"# {'set':22s} | {'TIGHT 0.4% stop':>16s} | {'VOL 2x ATR [1-5%]':>17s}")
    for label, group in (("LONG verdicts", longs), ("PASS verdicts (as-if-long)", passes),
                         ("worst-pass runners", [a for a in passes if (fwd_spot(a["coin"], a["created_at"], 6) or 0) > 5])):
        t = agg([long_roe(a["coin"], a["created_at"], "tight") for a in group])
        v = agg([long_roe(a["coin"], a["created_at"], "vol") for a in group])
        if t and v:
            print(f"  {label:22s} | {t['mean']:+6.2f}% ROE ({t['win']:2.0f}%w) | "
                  f"{v['mean']:+6.2f}% ROE ({v['win']:2.0f}%w)  n={t['n']}")

    print("\n=== 4) the WORST passes — coins we PASSed that ran hardest (>+5% in 6h) ===")
    worst = []
    for a in passes:
        s = fwd_spot(a["coin"], a["created_at"], 6)
        if s is not None and s > 5:
            worst.append((s, a["coin"], conf(a), (a.get("reasoning") or "")[:70]))
    for s, c, cf, r in sorted(worst, reverse=True)[:8]:
        print(f"  +{s:5.1f}% 6h | {c:10s} conf={cf:.2f} | AI said: {r}")
    if not worst:
        print("  (none ran >+5% — passes were correct)")


if __name__ == "__main__":
    main()
