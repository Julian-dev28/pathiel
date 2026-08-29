"""W-N0 — Lane N (news-catalyst replay): event + control selection. PRE-REGISTERED.

Selects the ignition events and matched controls that W-N1/W-N2 replay against
GDELT historical coverage. Everything here is deterministic and cache-only
(hourly_ext.json built by W-H0_fetch.py — 40 coins, 1h bars,
2025-12-13 .. 2026-07-09, ~208 days). No network in this file.

PRE-REGISTERED SELECTION RULE (fixed before any GDELT query was run):

  Coin-day: a UTC calendar day D for coin X with >= 20 hourly bars in D and a
  prior-day close. day_ret(D) = last_close(D) / last_close(D-1) - 1.
  dollar_vol(D) = sum(v * c) over D's hourly bars.

  EVENT candidates: day_ret >= +0.12 AND dollar_vol >= $5,000,000.
  Per-coin dedup: keep candidates in day_ret-descending order, dropping any
  candidate within 3 calendar days of an already-kept stronger event of the
  same coin (one event per coin-day by construction).

  Spread across the period: split the full span into 7 equal blocks; within
  each block keep the top 6 candidates by day_ret with at most 2 per coin per
  block; pool the survivors and take the overall top 40 by day_ret.

  CONTROLS: one per event, same coin. Uniform random draw (random.Random(42),
  events processed in (coin, day) order) from that coin's days with
  |day_ret| < 0.04, dollar_vol >= $1,000,000, and not within +/- 2 calendar
  days of ANY event candidate day of that coin. If a coin has no qualifying
  day the event is kept but flagged control=None (reported, not silently
  dropped).

  IGNITION BAR of event day D: the first hourly bar of D whose close >=
  1.06 * open of D's first bar; if none, the bar of D with the max close.
  (Used only for the lead-time stat; entries in W-N2 fill strictly at hourly
  opens after the surge bar.)

  GDELT symbol: coin name with a leading "k" multiplier stripped
  (kPEPE -> PEPE). Query template (pre-registered, from the operator mandate):
      "<SYM>" crypto sourcelang:eng

Output: research/alpha_swarm/hypotheses/W-N_events.json
Run:  .venv/bin/python research/alpha_swarm/hypotheses/W-N0_events.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HYP = REPO / "research" / "alpha_swarm" / "hypotheses"
HOURLY_CACHE = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/"
    "4b037816-5b27-4d2d-a13e-a6ebd68a2340/scratchpad/hourly_ext.json"
)
OUT = HYP / "W-N_events.json"

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
DAY_MS = 86_400_000

EVENT_RET = 0.12
EVENT_DVOL = 5_000_000.0
CONTROL_ABS_RET = 0.04
CONTROL_DVOL = 1_000_000.0
DEDUP_DAYS = 3
N_BLOCKS = 7
BLOCK_TOP = 6
BLOCK_PER_COIN = 2
N_EVENTS = 40
IGNITION_X = 1.06
SEED = 42


def sym_of(coin: str) -> str:
    return coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin


def day_key(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def day_open_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def build_coin_days(bars: list[list[float]]) -> dict[str, dict]:
    by_day: dict[str, list[list[float]]] = {}
    for b in bars:
        by_day.setdefault(day_key(int(b[T])), []).append(b)
    days = sorted(by_day)
    out: dict[str, dict] = {}
    for i, d in enumerate(days):
        rows = by_day[d]
        if len(rows) < 20 or i == 0:
            continue
        prev = by_day[days[i - 1]]
        if len(prev) < 20:
            continue
        c0, c1 = prev[-1][C], rows[-1][C]
        if c0 <= 0:
            continue
        out[d] = {
            "ret": c1 / c0 - 1.0,
            "dvol": sum(r[V] * r[C] for r in rows),
            "open_ms": int(rows[0][T]),
            "open_px": rows[0][O],
        }
    return out


def ignition_bar(bars: list[list[float]], day: str) -> int:
    """Open-ms of the ignition bar (see rule in docstring)."""
    rows = [b for b in bars if day_key(int(b[T])) == day]
    o0 = rows[0][O]
    for b in rows:
        if b[C] >= IGNITION_X * o0:
            return int(b[T])
    return int(max(rows, key=lambda b: b[C])[T])


def main() -> None:
    candles = json.loads(HOURLY_CACHE.read_text())["candles"]
    coin_days = {c: build_coin_days(b) for c, b in candles.items()}

    # 1. event candidates
    cands = []
    for coin, days in coin_days.items():
        for d, row in days.items():
            if row["ret"] >= EVENT_RET and row["dvol"] >= EVENT_DVOL:
                cands.append({"coin": coin, "day": d, **row})
    # per-coin 3-day dedup, strongest first
    kept: list[dict] = []
    for e in sorted(cands, key=lambda x: -x["ret"]):
        clash = any(k["coin"] == e["coin"]
                    and abs(k["open_ms"] - e["open_ms"]) < DEDUP_DAYS * DAY_MS
                    for k in kept)
        if not clash:
            kept.append(e)

    # 2. spread blocks
    t0 = min(min(d["open_ms"] for d in days.values())
             for days in coin_days.values() if days)
    t1 = max(max(d["open_ms"] for d in days.values())
             for days in coin_days.values() if days) + DAY_MS
    span = (t1 - t0) / N_BLOCKS
    pooled: list[dict] = []
    for blk in range(N_BLOCKS):
        lo, hi = t0 + blk * span, t0 + (blk + 1) * span
        in_blk = sorted((e for e in kept if lo <= e["open_ms"] < hi),
                        key=lambda x: -x["ret"])
        per_coin: dict[str, int] = {}
        got = 0
        for e in in_blk:
            if per_coin.get(e["coin"], 0) >= BLOCK_PER_COIN or got >= BLOCK_TOP:
                continue
            per_coin[e["coin"]] = per_coin.get(e["coin"], 0) + 1
            e["block"] = blk
            pooled.append(e)
            got += 1
    events = sorted(pooled, key=lambda x: -x["ret"])[:N_EVENTS]
    events.sort(key=lambda x: (x["coin"], x["day"]))

    # candidate-day map for control exclusion (ALL candidates, not just kept)
    cand_days: dict[str, set[int]] = {}
    for e in cands:
        cand_days.setdefault(e["coin"], set()).add(e["open_ms"])

    # 3. controls
    rng = random.Random(SEED)
    for e in events:
        coin = e["coin"]
        pool = [
            (d, row) for d, row in sorted(coin_days[coin].items())
            if abs(row["ret"]) < CONTROL_ABS_RET and row["dvol"] >= CONTROL_DVOL
            and all(abs(row["open_ms"] - em) > 2 * DAY_MS
                    for em in cand_days.get(coin, ()))
        ]
        if pool:
            d, row = rng.choice(pool)
            e["control"] = {"day": d, "ret": row["ret"], "dvol": row["dvol"],
                            "open_ms": row["open_ms"]}
        else:
            e["control"] = None
        e["ignition_ms"] = ignition_bar(candles[coin], e["day"])
        e["sym"] = sym_of(coin)

    OUT.write_text(json.dumps({"rule": "see W-N0_events.py docstring",
                               "n_candidates": len(cands),
                               "n_after_dedup": len(kept),
                               "events": events}, indent=1))
    print(f"candidates={len(cands)} deduped={len(kept)} selected={len(events)}")
    per_coin: dict[str, int] = {}
    for e in events:
        per_coin[e["coin"]] = per_coin.get(e["coin"], 0) + 1
    print("per-coin:", dict(sorted(per_coin.items(), key=lambda x: -x[1])))
    print("blocks:", sorted(e.get("block") for e in events))
    print("no-control events:", [e["coin"] + " " + e["day"]
                                 for e in events if not e["control"]])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
