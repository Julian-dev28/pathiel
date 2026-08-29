#!/usr/bin/env python
"""W-V1: grade the W-V0 quadrants — signed forward 24h/72h returns net 25bps,
with matched same-coin random-time nulls (same side, same horizon).

Data: HL 1h candles per coin (count=680 ≈ 28d, covers the whole session-log
span), fetched once at 2s pacing, cached to W-V_cache_1h.json (resumable —
rerun is free). Entry is the OPEN of the first hourly bar that STARTS after
the event ts (candleSnapshot returns the bar containing t — taking t > ts
avoids the known lookahead trap). Forward return = open(entry_t + H) vs
open(entry_t), signed by side, minus 25bps.

Null: per event, K=40 random same-coin entry bars (with full 72h coverage),
same side; bootstrap 2000 tag-mean draws -> p = P(null_mean >= obs_mean).

Cells with n < 30 are printed UNDERSAMPLED — no verdict is claimed for them.
Never imports scripts.trading_loop.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(HERE, "W-V0_events.json")
CACHE = os.path.join(HERE, "W-V_cache_1h.json")
OUT = os.path.join(HERE, "W-V1_results.json")

HOUR = 3_600_000
COST = 0.0025          # 25 bps round trip
K_NULL = 40
BOOT = 2000
random.seed(42)


def load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    return {}


def fetch_all(coins):
    cache = load_cache()
    todo = [c for c in coins if c not in cache]
    for i, coin in enumerate(todo):
        try:
            rows = [[c.t, c.o, c.h, c.l, c.c, c.v]
                    for c in fetch_hl_candles(coin, "1h", 680)]
        except Exception as exc:
            print(f"  fetch fail {coin}: {exc}")
            rows = []
        cache[coin] = rows
        if (i + 1) % 10 == 0 or i == len(todo) - 1:
            with open(CACHE, "w") as fh:
                json.dump(cache, fh)
            print(f"  fetched {i + 1}/{len(todo)}")
        time.sleep(2.0)
    return cache


def fwd_ret(bars_by_t, ts, side, horizon_h):
    """Signed net forward return from the first bar starting after ts."""
    entry_t = ((ts // HOUR) + 1) * HOUR
    e = bars_by_t.get(entry_t)
    x = bars_by_t.get(entry_t + horizon_h * HOUR)
    if not e or not x or e[1] <= 0:
        return None
    raw = x[1] / e[1] - 1.0
    return (raw if side == "long" else -raw) - COST


def main() -> None:
    events = json.load(open(EVENTS))
    dir_ev = [e for e in events if e["side"]]
    coins = sorted({e["coin"] for e in dir_ev})
    print(f"directional events: {len(dir_ev)}, coins: {len(coins)}")
    cache = fetch_all(coins)
    by_coin = {c: {r[0]: r for r in rows} for c, rows in cache.items()}
    tmax = {c: (max(d) if d else 0) for c, d in by_coin.items()}

    results = {}
    for horizon_h, label in ((24, "24h"), (72, "72h")):
        graded = {}
        for e in dir_ev:
            d = by_coin.get(e["coin"]) or {}
            if not d:
                continue
            r = fwd_ret(d, e["ts"], e["side"], horizon_h)
            if r is None:
                continue
            graded.setdefault(e["tag"], []).append((e, r))
        for tag, rows in sorted(graded.items()):
            rets = [r for _, r in rows]
            obs = sum(rets) / len(rets)
            # matched same-coin same-side nulls
            nulls_per_event = []
            for e, _ in rows:
                d = by_coin[e["coin"]]
                ts_ok = [t for t in d
                         if t + (horizon_h + 1) * HOUR <= tmax[e["coin"]]]
                if len(ts_ok) < 10:
                    nulls_per_event.append([])
                    continue
                draws = []
                for _ in range(K_NULL):
                    t0 = random.choice(ts_ok)
                    r = fwd_ret(d, t0 - 1, e["side"], horizon_h)
                    if r is not None:
                        draws.append(r)
                nulls_per_event.append(draws)
            boot = []
            for _ in range(BOOT):
                acc = [random.choice(dr) for dr in nulls_per_event if dr]
                if acc:
                    boot.append(sum(acc) / len(acc))
            null_mean = sum(boot) / len(boot) if boot else None
            p = (sum(1 for b in boot if b >= obs) / len(boot)) if boot else None
            win = sum(1 for r in rets if r > 0) / len(rets)
            results[f"{tag}:{label}"] = {
                "n": len(rets), "mean_net25": round(obs, 5),
                "win": round(win, 3),
                "null_mean": round(null_mean, 5) if null_mean is not None else None,
                "p_null_ge_obs": round(p, 4) if p is not None else None,
                "undersampled": len(rets) < 30,
            }

    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"\n{'cell':24} {'n':>5} {'mean net25':>10} {'win':>5} "
          f"{'null':>8} {'p':>7}  flag")
    for k, v in sorted(results.items()):
        print(f"{k:24} {v['n']:>5} {v['mean_net25']*100:>9.2f}% "
              f"{v['win']:>5.2f} "
              f"{(v['null_mean'] or 0)*100:>7.2f}% {v['p_null_ge_obs']!s:>7}  "
              f"{'UNDERSAMPLED' if v['undersampled'] else ''}")

    # per-event detail for the tiny polar cells (they seed the findings doc)
    print("\n--- every ALIGNED / CONFLICT / NEUTRAL event, graded ---")
    for e in dir_ev:
        if e["tag"] == "NO_NEWS_DATA":
            continue
        d = by_coin.get(e["coin"]) or {}
        r24 = fwd_ret(d, e["ts"], e["side"], 24) if d else None
        r72 = fwd_ret(d, e["ts"], e["side"], 72) if d else None
        print(e["ts"], e["coin"], e["verdict"], e["confidence"], e["tag"],
              f"24h={r24*100:+.2f}%" if r24 is not None else "24h=pending",
              f"72h={r72*100:+.2f}%" if r72 is not None else "72h=pending",
              "|", (e["news_context"] or "")[:90])


if __name__ == "__main__":
    main()
