"""W-H0 — Lane H shared substrate: extended 1h candle cache + common helpers.

Lane H (intra-crypto lead-lag + cascade microstructure) is authorized to fetch
hourly candles via the project's fetch_hl_candles (modest batching + local cache)
because the shared dataset.json holds only ~83 days of 1h bars (2026-04-04 ..
2026-06-27) — too thin for rare-event cells (e.g. n=13 for -8% 1h flushes).

This module:
  1. build_cache()  — one candleSnapshot per coin (40 requests, weight 800 < the
     1200/min budget, sequential + 0.35s sleep), 1h x 5000 bars (~208 days),
     written once to the session scratchpad as hourly_ext.json. Never re-fetches
     if the cache exists.
  2. Validates the fetched bars against dataset.json on overlapping timestamps
     (BTC closes must match to <1e-9 rel) so the two sources are provably the
     same series.
  3. load_ext() — returns {coin: [[t,o,h,l,c,v], ...]} (alpha_lib bar format).
  4. Shared lookahead-safe helpers used by W-H1..W-H4: hourly returns, rolling
     sigma, rolling OLS beta vs BTC, next-open fills, episode dedup.

Run:  .venv/bin/python research/alpha_swarm/hypotheses/W-H0_fetch.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SWARM = REPO / "research" / "alpha_swarm"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
    "4b037816-5b27-4d2d-a13e-a6ebd68a2340/scratchpad"
)
CACHE = SCRATCH / "hourly_ext.json"

sys.path.insert(0, str(SWARM / "lib"))
sys.path.insert(0, str(REPO))

import alpha_lib as al  # noqa: E402

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR_MS = 3_600_000


# ── 1. fetch + cache ─────────────────────────────────────────────────────────

def build_cache(force: bool = False) -> dict:
    """Fetch 1h x 5000 per coin. Drops each coin's final (in-progress) bar.
    Coins blanked by a 429 storm are retried in later passes (up to 3)."""
    if CACHE.exists() and not force:
        payload = json.loads(CACHE.read_text())
        missing = [c for c in payload["coins"] if not payload["candles"].get(c)]
        if not missing:
            return payload
    else:
        d = al.load_dataset()
        payload = {"meta": {"interval": "1h", "fetched_ms": int(time.time() * 1000)},
                   "coins": d["coins"], "candles": {}}
        missing = list(d["coins"])
    from pathia.client.hl_client import fetch_hl_candles  # network import
    for attempt in range(3):
        still: list[str] = []
        for i, coin in enumerate(missing):
            cs = fetch_hl_candles(coin, "1h", 5000)
            if cs:
                # drop the last bar: the current hour is still forming
                payload["candles"][coin] = [
                    [c.t, c.o, c.h, c.l, c.c, c.v] for c in cs[:-1]]
                print(f"  [{i+1}/{len(missing)}] {coin}: {len(cs)-1} bars", flush=True)
            else:
                still.append(coin)
                print(f"  [{i+1}/{len(missing)}] {coin}: EMPTY (429) — will retry", flush=True)
            time.sleep(0.35)
        missing = still
        if not missing:
            break
        print(f"  retry pass {attempt+2} for {missing} after 45s cool-down", flush=True)
        time.sleep(45)
    if missing:
        raise RuntimeError(f"could not fetch: {missing}")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload))
    return payload


def validate_against_dataset(ext: dict) -> None:
    """BTC closes on overlapping timestamps must agree between sources."""
    d = al.load_dataset()
    dbars = al.candles(d, "BTC", "1h")
    # the dataset's final bar was in-progress at snapshot time -> exclude it
    ds = {b[T]: b[C] for b in dbars[:-1]}
    n_ck = 0
    for b in ext["candles"]["BTC"]:
        if b[T] in ds:
            assert abs(b[C] - ds[b[T]]) <= 1e-9 * max(1.0, abs(ds[b[T]])), \
                f"BTC close mismatch at {b[T]}: {b[C]} vs {ds[b[T]]}"
            n_ck += 1
    assert n_ck > 500, f"too little overlap to validate ({n_ck} bars)"
    print(f"  overlap validation OK on {n_ck} BTC bars")


def load_ext() -> dict[str, list[list[float]]]:
    if not CACHE.exists():
        ext = build_cache()
        validate_against_dataset(ext)
        return ext["candles"]
    return json.loads(CACHE.read_text())["candles"]


# ── 2. shared lookahead-safe helpers ────────────────────────────────────────

def bar_index(bars: list[list[float]]) -> dict[int, int]:
    return {int(b[T]): i for i, b in enumerate(bars)}


def hourly_rets(bars: list[list[float]]) -> list[tuple[int, float]]:
    """[(t_ms_of_bar_i, close[i]/close[i-1]-1), ...] — known at close of bar i."""
    out = []
    for i in range(1, len(bars)):
        p0, p1 = bars[i - 1][C], bars[i][C]
        if p0 > 0:
            out.append((int(bars[i][T]), p1 / p0 - 1.0))
    return out


def rolling_sigma(rets: list[tuple[int, float]], window: int) -> dict[int, float]:
    """sigma at t = pstdev of the `window` returns strictly BEFORE t (no lookahead:
    the bar at t is excluded from its own sigma)."""
    out: dict[int, float] = {}
    vals = [r for _, r in rets]
    for i in range(window, len(rets)):
        out[rets[i][0]] = statistics.pstdev(vals[i - window:i])
    return out


def rolling_beta(alt_rets: dict[int, float], btc_rets: dict[int, float],
                 ts_sorted: list[int], window: int) -> dict[int, float]:
    """OLS beta of alt on BTC over the `window` hourly returns strictly before t.
    ts_sorted = BTC return timestamps ascending. beta known entering bar t."""
    out: dict[int, float] = {}
    xs: list[float] = []
    ys: list[float] = []
    tq: list[int] = []
    for t in ts_sorted:
        if len(xs) >= window:
            mx = sum(xs[-window:]) / window
            my = sum(ys[-window:]) / window
            cov = sum((x - mx) * (y - my) for x, y in zip(xs[-window:], ys[-window:]))
            var = sum((x - mx) ** 2 for x in xs[-window:])
            if var > 0:
                out[t] = cov / var
        if t in alt_rets and t in btc_rets:
            xs.append(btc_rets[t]); ys.append(alt_rets[t]); tq.append(t)
    return out


def fwd_open_ret(bars: list[list[float]], idx: dict[int, int], t_signal: int,
                 hold_h: int) -> float | None:
    """Fill at open of the bar AFTER the signal bar (t_signal is the signal bar's
    open timestamp), exit at open hold_h bars later. None if bars missing."""
    i = idx.get(t_signal)
    if i is None or i + 1 + hold_h >= len(bars):
        return None
    e = bars[i + 1][O]
    x = bars[i + 1 + hold_h][O]
    if e <= 0:
        return None
    # gap check: fills must be contiguous hours (no data hole mid-trade)
    if bars[i + 1][T] - bars[i][T] != HOUR_MS:
        return None
    return x / e - 1.0


def dedup_episodes(events: list[dict], spacing_ms: int,
                   key: str = "t") -> list[dict]:
    """Keep only events >= spacing apart (global). Events must be time-sorted."""
    out: list[dict] = []
    last = -1
    for e in sorted(events, key=lambda x: x[key]):
        if e[key] - last >= spacing_ms:
            out.append(e); last = e[key]
    return out


if __name__ == "__main__":
    ext = build_cache()
    validate_against_dataset(ext)
    ns = sorted(((c, len(b)) for c, b in ext["candles"].items()), key=lambda x: x[1])
    import datetime
    btc = ext["candles"]["BTC"]
    print(f"cached {len(ext['candles'])} coins -> {CACHE}")
    print("BTC span:",
          datetime.datetime.fromtimestamp(btc[0][T] / 1000, datetime.UTC),
          "->", datetime.datetime.fromtimestamp(btc[-1][T] / 1000, datetime.UTC),
          f"({len(btc)} bars)")
    print("shortest histories:", ns[:6])
