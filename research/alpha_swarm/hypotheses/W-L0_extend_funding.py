"""W-L0 — extend the hourly funding dataset to the full W-R hourly-cache span.

funding.json covers 2026-03-29..2026-06-27 (90d). The hourly candle cache
(W-R_cache_hourly.json) spans 2025-12-13..2026-07-09 (208d). funding_spike_short
signal reconstruction needs funding across the whole candle span (plus the 30d
z lookback), so this script fetches the two gap windows per coin via
hermes_trader.client.hl_client.fetch_funding_history, 2s pace, and writes the
merged per-coin hourly rows to W-L_cache_funding.json in funding_lib shape:
{meta, funding: {coin: [[time_ms, fundingRate, premium], ...]}}.

Bounded: ~7 paginated calls/coin x 40 coins ~= 280 calls ~= 10 min at 2s pace.
Progress lines go to stdout (run in background, tail the log).
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", ".."))
from hermes_trader.client.hl_client import fetch_funding_history  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOURLY_CACHE = os.path.join(HERE, "W-R_cache_hourly.json")
BASE_FUNDING = os.path.join(HERE, "..", "funding.json")
OUT = os.path.join(HERE, "W-L_cache_funding.json")
PACE_S = 2.0
HOUR_MS = 3_600_000


def paged_fetch(coin: str, start_ms: int, end_ms: int) -> list[list[float]]:
    """fundingHistory caps ~500 rows/response; page forward until covered."""
    out: list[list[float]] = []
    cur = start_ms
    while cur < end_ms:
        time.sleep(PACE_S)
        try:
            rows = fetch_funding_history(coin, cur, end_ms)
        except Exception as e:
            print(f"  {coin}: fetch error at {cur}: {e}", flush=True)
            break
        if not rows:
            break
        for r in rows:
            try:
                out.append([int(r["time"]), float(r["fundingRate"]),
                            float(r.get("premium") or 0.0)])
            except Exception:
                continue
        last_t = int(rows[-1].get("time") or 0)
        if last_t <= cur:
            break
        cur = last_t + 1
        if len(rows) < 400:            # short page => window exhausted
            break
    return out


def main() -> None:
    hc = json.load(open(HOURLY_CACHE))
    coins = hc["coins"]
    cand = hc["candles"]
    span_start = min(b[0][0] for b in cand.values())
    span_end = max(b[-1][0] for b in cand.values()) + HOUR_MS
    # z lookback needs 30d+ before the first usable signal; fetch from span start
    # (signals earlier than span_start+31d are dropped by the backtest anyway).
    base = json.load(open(BASE_FUNDING))["funding"]

    merged: dict[str, list[list[float]]] = {}
    t0 = time.time()
    for i, coin in enumerate(coins):
        rows = {int(r[0]): [int(r[0]), float(r[1]), float(r[2])]
                for r in base.get(coin, [])}
        have_ts = sorted(rows)
        gaps = []
        if have_ts:
            if span_start < have_ts[0]:
                gaps.append((span_start, have_ts[0]))
            if have_ts[-1] + HOUR_MS < span_end:
                gaps.append((have_ts[-1] + HOUR_MS, span_end))
        else:
            gaps.append((span_start, span_end))
        for g0, g1 in gaps:
            for r in paged_fetch(coin, g0, g1):
                rows[r[0]] = r
        merged[coin] = [rows[t] for t in sorted(rows)]
        print(f"[{i+1}/{len(coins)}] {coin}: {len(merged[coin])} rows "
              f"({(time.time()-t0)/60:.1f} min elapsed)", flush=True)

    json.dump({"meta": {"built": int(time.time() * 1000),
                        "span_ms": [span_start, span_end],
                        "source": "funding.json + fetch_funding_history gap fill",
                        "note": "hourly rows [t, fundingRate, premium]"},
               "funding": merged}, open(OUT, "w"))
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
