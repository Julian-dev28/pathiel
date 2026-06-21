#!/usr/bin/env python3
"""Test-first: legendary-trader techniques as ISOLATED variants on OUR realized closes
(.agent-memory.json). Each variant is a filter (include/exclude trades) or a sizing mod
(rescale PnL, since PnL is ~linear in notional) applied to the SAME 109-close window, so
comparisons are apples-to-apples. Reports the required metrics + a chronological OOS
split. Look-ahead control: the 200/50d MA at each entry is computed from daily candles
SLICED to t <= entry_time (no future bars).

Cleanly testable here: MA-trend filter (PTJ), regime filter, McKay size-down.
NOT testable on completed trades (need path/setup sim; stated, not faked): probe sizing,
pyramiding, min reward:risk filter.
"""
import json
import statistics
import time
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val, ema


def metrics(pnls):
    if not pnls:
        return None
    w = [x for x in pnls if x > 0]; l = [x for x in pnls if x <= 0]
    gp, gl = sum(w), abs(sum(l))
    eq = peak = mdd = 0.0
    for x in pnls:
        eq += x; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    avgL = abs(statistics.mean(l)) if l else 1.0
    sharpe = (statistics.mean(pnls) / statistics.pstdev(pnls)) if len(pnls) > 1 and statistics.pstdev(pnls) else 0.0
    return {
        "n": len(pnls), "net": sum(pnls), "win": len(w) / len(pnls) * 100,
        "pf": (gp / gl) if gl else float("inf"), "mdd": mdd, "sharpe": sharpe,
        "avgR": statistics.mean([x / avgL for x in pnls]),
    }


def row(name, pnls, half_idx):
    m = metrics(pnls)
    if not m:
        print(f"  {name:26s} | n=0"); return
    m1 = metrics(pnls[:half_idx]) or {"net": 0}
    m2 = metrics(pnls[half_idx:]) or {"net": 0}
    print(f"  {name:26s} | {m['n']:3d} | ${m['net']:7.2f} | {m['win']:3.0f}% | "
          f"{m['pf']:4.2f} | ${m['mdd']:7.2f} | {m['sharpe']:+.3f} | {m['avgR']:+.2f} | "
          f"OOS ${m1['net']:+6.2f}/${m2['net']:+6.2f}")


def daily_ma_aligned(coin, entry_time_ms, period):
    """price>MA (long-favorable) at entry, using only bars up to entry_time. Returns
    +1 (uptrend), -1 (downtrend), or 0 (insufficient history)."""
    try:
        cd = fetch_hl_candles(coin, "1d", 400)
    except Exception:
        return 0
    past = [c for c in cd if c.t <= entry_time_ms]
    if len(past) < max(20, period // 4):
        return 0
    closes = [candle_val(c, "c") for c in past]
    p = min(period, len(closes))
    ma = ema(closes, p)
    if not ma:
        return 0
    return 1 if closes[-1] > ma[-1] else -1


def main():
    cl = json.load(open(".agent-memory.json"))["closes"]
    cl = sorted(cl, key=lambda c: c.get("closed_at", 0))
    pnl = lambda c: c.get("realized_pnl_usd") or 0
    half = len(cl) // 2
    print(f"# legendary-trader techniques on OUR {len(cl)} realized closes | OOS split mid")
    print(f"# {'variant':26s} | {'n':>3s} | {'net':>8s} | {'win':>4s} | {'pf':>4s} | "
          f"{'maxDD':>8s} | {'sharpe':>6s} | {'avgR':>5s} | OOS 1st/2nd")

    # BASELINE
    row("BASELINE (all)", [pnl(c) for c in cl], half)

    # MA trend filters (PTJ): align side with daily MA. Cache MA-alignment per (coin,entry).
    print("# --- precomputing MA alignment (look-ahead-controlled: bars <= entry_time) ---")
    align200, align50 = {}, {}
    for i, c in enumerate(cl):
        et = c.get("entry_time") or c.get("closed_at") or 0
        align200[i] = daily_ma_aligned(c["coin"], et, 200)
        align50[i] = daily_ma_aligned(c["coin"], et, 50)
        time.sleep(0.05)
    cov200 = sum(1 for v in align200.values() if v != 0)
    cov50 = sum(1 for v in align50.values() if v != 0)

    def ma_filter(align):
        out = []
        for i, c in enumerate(cl):
            a = align.get(i, 0)
            if a == 0:                       # no MA history -> can't judge; EXCLUDE (strict)
                continue
            want = 1 if c.get("side") == "long" else -1
            if a == want:                    # trade aligned with the trend
                out.append(pnl(c))
        return out

    row(f"PTJ 200d-MA aligned ({cov200}/{len(cl)} cov)", ma_filter(align200), half // 2 or 1)
    row(f"50d-MA aligned ({cov50}/{len(cl)} cov)", ma_filter(align50), half // 2 or 1)

    # Regime filter (use existing regime_at_entry; our edge is long/up-aligned)
    row("Regime: up-only", [pnl(c) for c in cl if c.get("regime_at_entry") == "up"], 1)
    row("Regime: exclude down", [pnl(c) for c in cl if c.get("regime_at_entry") != "down"], half)

    # McKay: cut size sharply after a losing streak. After 2 consecutive losses, size *0.5
    # for the next 3 trades; reset on a win. PnL scales ~linearly with size.
    def mckay(streak_trigger=2, cut=0.5, cut_len=3):
        out, losses, cooldown = [], 0, 0
        for c in cl:
            size = cut if cooldown > 0 else 1.0
            out.append(pnl(c) * size)
            if cooldown > 0:
                cooldown -= 1
            if pnl(c) <= 0:
                losses += 1
                if losses >= streak_trigger:
                    cooldown = cut_len; losses = 0
            else:
                losses = 0
        return out
    row("McKay size-down (2L->.5x3)", mckay(), half)
    row("McKay aggressive (2L->.3x4)", mckay(2, 0.3, 4), half)

    print("# NOT cleanly testable on completed trades (need path/setup sim, not faked):")
    print("#   - Probe sizing (Jones): needs intra-trade confirmation signal to add")
    print("#   - Pyramid into winners (Dennis): changes which trades/adds exist")
    print("#   - Min reward:risk filter (Druck): our stop/TP are ~fixed by config, not per-setup")


if __name__ == "__main__":
    main()
