"""W-N2 — the replay: would news-considered calls have caught the movers?

For every W-N1 event whose PRE-IGNITION surge fired (surge rule pre-registered
in W-N1_precedence.py), simulate the call:

  ENTRY  long at the OPEN of the first hourly bar whose open-time >= the end
         of the first firing 3h-surge bin (strictly after the information
         existed — no lookahead; the surge uses GDELT article counts only
         from BEFORE that bar).
  EXITS  two pre-registered cells (the Lane-X-validated KAITO exit family):
           cell A: 15% hard stop + trail armed at +2% high, floor keeps 90%
                   of the peak gain (retrace 0.10), 24h max horizon
           cell B: 15% hard stop, exit at open 24h after entry
         Intra-bar is pessimistic: the LOW is processed before the HIGH
         (stops/floors fill before any arming/peak update on the same bar).
  COSTS  alpha_lib.summarize slippage tiers 0/6/12/25/50 bps round-trip;
         EV25 is the decision number.
  NULL   matched same-coin random-time entries: per traded event, 300 random
         entry bars of the same coin (seed 7), identical exit machinery.
         p-value = P(mean of a matched draw of n null trades >= observed mean),
         10,000 bootstrap draws (one null trade per event's coin pool each).

Survivorship: universe is today's liquid set -> any +EV is an upper bound.

Run:  .venv/bin/python research/alpha_swarm/hypotheses/W-N2_replay.py
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HYP = REPO / "research" / "alpha_swarm" / "hypotheses"
sys.path.insert(0, str(REPO / "research" / "alpha_swarm" / "lib"))
import alpha_lib as al  # noqa: E402

HOURLY_CACHE = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
    "4b037816-5b27-4d2d-a13e-a6ebd68a2340/scratchpad/hourly_ext.json"
)
RESULTS = HYP / "W-N1_precedence_results.json"
OUT = HYP / "W-N2_replay_results.json"

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR_MS = 3_600_000
STOP = 0.15
ARM = 1.02
KEEP = 0.90          # keep 90% of peak gain = retrace 0.10
HORIZON = 24
N_NULL_PER_COIN = 300
N_BOOT = 10_000


def simulate(bars: list[list[float]], j: int, cell: str) -> dict | None:
    """Enter at bars[j] open, walk bars j..j+HORIZON-1, exit at j+HORIZON open."""
    if j + HORIZON >= len(bars):
        return None
    e = bars[j][O]
    if e <= 0:
        return None
    stop_px = e * (1 - STOP)
    armed, peak, floor = False, e, None
    min_low = e
    for k in range(j, j + HORIZON):
        b = bars[k]
        min_low = min(min_low, b[L])
        # pessimistic: low first
        if b[L] <= stop_px:
            return {"ret": stop_px / e - 1, "exit": "stop", "hold": k - j + 1,
                    "mae": min_low / e - 1}
        if cell == "A" and armed and floor is not None and b[L] <= floor:
            return {"ret": floor / e - 1, "exit": "trail", "hold": k - j + 1,
                    "mae": min_low / e - 1}
        if cell == "A":
            if not armed and b[H] >= e * ARM:
                armed = True
            if armed and b[H] > peak:
                peak = b[H]
                floor = e * (1 + KEEP * (peak / e - 1))
    x = bars[j + HORIZON][O]
    return {"ret": x / e - 1, "exit": "horizon", "hold": HORIZON,
            "mae": min_low / e - 1}


def pctile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def main() -> None:
    candles = json.loads(HOURLY_CACHE.read_text())["candles"]
    rows = json.loads(RESULTS.read_text())
    fired = [r for r in rows
             if r["event"].get("status") == "OK" and r["event"].get("fires")
             and not r["event"].get("thin")]
    print(f"events with pre-ignition surge (non-thin): {len(fired)}")

    rng = random.Random(7)
    out: dict = {}
    for cell in ("A", "B"):
        trades, null_pools = [], []
        for r in fired:
            bars = candles[r["coin"]]
            fire_end = r["event"]["first_fire_end_ms"]
            j = next((i for i, b in enumerate(bars) if b[T] >= fire_end), None)
            if j is None:
                continue
            if bars[j][T] - fire_end > 2 * HOUR_MS:
                print(f"  skip {r['coin']} {r['day']}: no bar within 2h of fire")
                continue
            tr = simulate(bars, j, cell)
            if tr is None:
                continue
            tr.update(t=bars[j][T], coin=r["coin"], day=r["day"])
            trades.append(tr)
            pool = []
            for _ in range(N_NULL_PER_COIN):
                jj = rng.randrange(0, len(bars) - HORIZON - 1)
                n = simulate(bars, jj, cell)
                if n:
                    pool.append(n["ret"])
            null_pools.append(pool)

        s = al.summarize(trades)
        obs = statistics.mean(t["ret"] for t in trades) if trades else 0.0
        boots = []
        for _ in range(N_BOOT):
            boots.append(statistics.mean(rng.choice(p) for p in null_pools))
        p_val = sum(1 for b in boots if b >= obs) / len(boots) if boots else 1.0
        null_mean = statistics.mean(boots) if boots else 0.0

        maes = [t["mae"] for t in trades]
        first, second = al.time_split(trades)
        ev_h = [round(100 * statistics.mean(t["ret"] for t in h), 2) if h else None
                for h in (first, second)]
        print(f"\n=== cell {cell} "
              f"({'trail 2%/0.10 + 15% stop' if cell == 'A' else '15% stop'} / 24h) ===")
        print(f"n={s.get('n', 0)}  EV0={s['slip0']['mean_ret_pct']:+.2f}%  "
              f"EV25={s['slip25']['mean_ret_pct']:+.2f}%  "
              f"win25={s['slip25']['win_rate']:.2f}" if trades else "no trades")
        if trades:
            print(f"null mean={100*null_mean:+.2f}%  p(null>=obs)={p_val:.4f}")
            print(f"MAE p50={100*pctile(maes, .5):.1f}%  p90={100*pctile(maes, .9):.1f}%")
            print(f"OOS halves EV0: first={ev_h[0]}% (n={len(first)})  "
                  f"second={ev_h[1]}% (n={len(second)})")
            for t in sorted(trades, key=lambda x: x["t"]):
                print(f"  {t['day']} {t['coin']:9s} ret={100*t['ret']:+7.2f}%  "
                      f"exit={t['exit']:7s} hold={t['hold']:2d}h mae={100*t['mae']:+6.2f}%")
        out[cell] = {"summary": s, "p_null": p_val, "null_mean": null_mean,
                     "mae_p50": pctile(maes, .5), "mae_p90": pctile(maes, .9),
                     "oos_halves_ev0_pct": ev_h, "trades": trades}

    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
