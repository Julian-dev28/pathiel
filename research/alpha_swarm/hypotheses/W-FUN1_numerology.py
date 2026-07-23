#!/usr/bin/env python3
"""W-FUN1 — THE COSMIC WEALTH ORACLE: numerology-timed 25x ETH gambling backtest.

For fun (operator, 2026-07-23). We consult the ancient money vibrations — life-path
numbers, master numbers 11/22/33, the angel number 8 (infinity/abundance/Saturn-capital),
Feng-Shui prosperity 5/8, and the sacred resonance of the word ETHEREUM — to decide, each
day at a wealth-charged hour, whether to slam a 25x ETH LONG or SHORT.

Then, because this is still that repo, the matched-null coin flip executes the oracle in
broad daylight. Rigor spoils the party on purpose: same entry days/hours, directions
replaced by 2000 random ±1 draws. If the money numbers can't beat a coin, they're a coin.

Data: dataset.json ETH candles. PRIMARY interval 1h (honours "a certain time"; ~83 days).
25x model: pnl = 25*ret_24h, with an honest LIQUIDATION rule — if the 24h path breaches
~1/25 = 4% against the position (low for a long, high for a short) the bet is a total loss
(-1.0). One position per day. Fees 6bps taker round-trip at 25x notional = 0.06*25*... no:
25bps on 25x notional = 0.0025*25 = 6.25% of margin/trade round trip (leverage eats fees).
"""
from __future__ import annotations

import random
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import alpha_lib as A  # noqa: E402

SEED = 20260723
LEV = 25
LIQ = 1.0 / LEV            # 4% adverse => liquidated
FEE_ON_MARGIN = 0.0025 * LEV * 2   # 25bps/side on 25x notional, round trip, per margin
T, O, H, L, C = 0, 1, 2, 3, 4


# ─────────────────────────────── numerology core ───────────────────────────────
def reduce_num(n: int, keep_master: bool = True) -> int:
    while n > 9:
        if keep_master and n in (11, 22, 33):
            return n
        n = sum(int(d) for d in str(n))
    return n


def lifepath(dt: datetime) -> int:
    return reduce_num(sum(int(d) for d in dt.strftime("%Y%m%d")))


def word_number(word: str) -> int:
    # Pythagorean: A=1..I=9, J=1.. (1 + (ord-1) mod 9 handled as mod-9 wheel)
    tot = sum(((ord(ch) - 65) % 9) + 1 for ch in word.upper() if ch.isalpha())
    return reduce_num(tot)


ETH_RESONANCE = word_number("ETHEREUM")   # E5 T2 H8 E5 R9 E5 U3 M4 = 41 -> 5 (prosperity!)
MONEY_NUMBERS = {1, 5, 6, 8, 9, 11, 22, 33}   # expansion / abundance / master vibration
CONTRACTION = {2, 4, 7}                        # Saturn restriction -> short the poverty


def schemes(dt: datetime) -> dict:
    """Each returns +1 LONG (money flows in) / -1 SHORT (poverty vibration)."""
    lp = lifepath(dt)
    ds = dt.strftime("%Y%m%d")
    return {
        "lifepath_money": 1 if lp in MONEY_NUMBERS else -1,
        "master_ascension": 1 if lp in (11, 22, 33) else -1,
        "angel_eight": 1 if ds.count("8") >= 2 else -1,   # 88 = abundance gate
        "eth_resonance": 1 if lp == ETH_RESONANCE else -1,  # day vibes with ETHEREUM(=5)
        "triple_one": 1 if ("111" in ds or "1111" in ds) else -1,   # manifestation portal
        "saturn_fade": -1 if lp in CONTRACTION else 1,
        "day_root_odd": 1 if reduce_num(dt.day) % 2 == 1 else -1,   # pure digit superstition
    }


# ─────────────────────────────── backtest ───────────────────────────────
def eth_bars(iv="1h"):
    return A.candles(A.load_dataset(), "ETH", iv)


def daily_entries(bars, hour):
    """One entry per UTC day at `hour`; hold 24 bars. Returns list of
    (date, entry_open, ret24, liq_long, liq_short)."""
    out = []
    seen = set()
    for i, b in enumerate(bars):
        dt = datetime.fromtimestamp(b[T] / 1000, timezone.utc)
        key = dt.date()
        if dt.hour != hour or key in seen:
            continue
        if i + 24 >= len(bars):
            break
        seen.add(key)
        entry = b[O]
        if entry <= 0:
            continue
        window = bars[i:i + 24]
        lo = min(x[L] for x in window)
        hi = max(x[H] for x in window)
        ret = bars[i + 24][O] / entry - 1.0
        out.append((dt, entry, ret, (lo / entry - 1.0) <= -LIQ, (hi / entry - 1.0) >= LIQ))
    return out


def pnl_of(entries, dirs):
    """dirs: list of +1/-1 aligned with entries. Returns per-trade margin PnL list
    with 25x + liquidation + fees."""
    out = []
    for (_, _, ret, liq_l, liq_s), d in zip(entries, dirs):
        if (d > 0 and liq_l) or (d < 0 and liq_s):
            out.append(-1.0)                      # liquidated: whole bet gone
        else:
            out.append(d * ret * LEV - FEE_ON_MARGIN)
    return out


def evaluate(entries, dirs, label):
    pnl = pnl_of(entries, dirs)
    n = len(pnl)
    liqs = sum(1 for p in pnl if p <= -0.999)
    mean = st.fmean(pnl)
    # compounding a fixed-fraction (full-margin) bettor to see the crater
    eq = 1.0
    for p in pnl:
        eq *= max(0.0, 1.0 + p)
    return {"label": label, "n": n, "mean_margin_pnl_pct": round(mean * 100, 2),
            "win_pct": round(100 * sum(1 for p in pnl if p > 0) / n, 1) if n else 0,
            "liq_rate_pct": round(100 * liqs / n, 1) if n else 0,
            "final_equity_x": round(eq, 4), "_pnl": pnl}


def null_p(entries, obs_mean, seed=SEED):
    rnd = random.Random(seed)
    ge = 0
    for _ in range(2000):
        dirs = [rnd.choice((1, -1)) for _ in entries]
        m = st.fmean(pnl_of(entries, dirs))
        if m >= obs_mean:
            ge += 1
    return round((1 + ge) / 2001, 4)


def main():
    bars = eth_bars("1h")
    span0 = datetime.fromtimestamp(bars[0][T] / 1000, timezone.utc)
    span1 = datetime.fromtimestamp(bars[-1][T] / 1000, timezone.utc)
    print(f"ETH 1h: {len(bars)} bars  {span0:%Y-%m-%d}..{span1:%Y-%m-%d}  "
          f"| ETHEREUM resonance number = {ETH_RESONANCE} | 25x, liq at {LIQ*100:.0f}% adverse\n")

    results = []
    for hour in range(24):
        entries = daily_entries(bars, hour)
        if len(entries) < 20:
            continue
        for name in schemes(span0):  # scheme names
            dirs = [schemes(dt)[name] for (dt, *_ ) in entries]
            r = evaluate(entries, dirs, f"{name} @ {hour:02d}:00 UTC")
            r["hour"], r["scheme"] = hour, name
            results.append(r)

    results.sort(key=lambda r: -r["mean_margin_pnl_pct"])
    print("=== THE MONEY FORMULA — top 8 by mean 25x margin PnL/trade ===")
    for r in results[:8]:
        print(f"  {r['label']:<34} n={r['n']:<3} mean {r['mean_margin_pnl_pct']:+6.2f}%/trade  "
              f"win {r['win_pct']:>4}%  liq {r['liq_rate_pct']:>4}%  final_equity {r['final_equity_x']:.3f}x")

    winner = results[0]
    p = null_p(daily_entries(bars, winner["hour"]), winner["mean_margin_pnl_pct"] / 100)
    print(f"\n=== THE ORACLE MEETS THE COIN ===")
    print(f"  Best formula: {winner['label']}  ({winner['mean_margin_pnl_pct']:+.2f}%/trade)")
    print(f"  vs 2000 random coin-flip direction sets on the SAME days/hour: p = {p}")
    med_final = st.median([r["final_equity_x"] for r in results])
    print(f"  median formula final equity across all {len(results)} formulas: {med_final:.3f}x of bankroll")
    print(f"  median liquidation rate: {st.median([r['liq_rate_pct'] for r in results]):.0f}% of days")

    verdict = ("NOISE — the money numbers do not beat a coin flip" if p > 0.05
               else "spurious 'win' — top of many formulas, expected by multiple comparisons")
    print(f"\n  VERDICT: {verdict}. And 25x daily turns the coin flip into a crater "
          f"(median bankroll -> {med_final:.2f}x). The universe's abundance frequency is, "
          f"as always, a random number generator wearing a fee.")


if __name__ == "__main__":
    main()
