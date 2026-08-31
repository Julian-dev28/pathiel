"""W-L2 — empirical 5m anchor for the sub-hour edge decay (extreme_fade).

The W-L1 grid bottoms out at 1h resolution. This script measures the ACTUAL
sub-hour price drift around extreme_fade signals using HL 5m candles, so the
30m-vs-5m scan question gets one empirical anchor instead of pure
interpolation.

CONSTRAINT (measured 2026-07-13): HL retains only ~5000 5m bars (~17 days;
earliest 2026-06-25). The "10 largest signals of the 208d window" are older
than retention, so the anchor set = ALL extreme_fade signals (daily ret
<= -12%, live rule) whose signal day has 5m coverage: in-cache signals with
close >= 2026-06-26 plus fresh signals 2026-07-09..today found by re-scanning
the 40 cached coins' recent daily bars (40 paced calls). Up to 10 largest.

Per signal, two measurements:
  1. intra-hour drift from crash-threshold-cross to that hour's close: within
     the signal day, the first 5m bar whose low crosses prev_close*0.88 ->
     drift to the containing hour's close. (How much a 5m scanner could act
     on before a 1h scanner even sees the bar — context only; the live book
     trades COMPLETED daily bars, so this is the intrabar-cousin frontier,
     not the live book's latency.)
  2. post-close drift at +5/+10/+15/+30/+45/+60 min after the signal-day
     close: the direct 30m-vs-5m entry-latency anchor. For the LONG fade,
     positive drift = a later scan buys higher = EV given up.

Network: ~40 daily-candle calls + <=10 5m calls, 2s pace. Read-only.

Usage: .venv/bin/python research/alpha_swarm/hypotheses/W-L2_anchor_5m.py
Writes: W-L2_anchor_results.json (same dir).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from pathia.client.hl_client import _http_post  # noqa: E402

HOURLY_CACHE = os.path.join(HERE, "W-R_cache_hourly.json")
RESULTS = os.path.join(HERE, "W-L2_anchor_results.json")
PACE_S = 2.0
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
FIVE_MS = 300_000
CRASH = -0.12
RETENTION_START = int(dt.datetime(2026, 6, 26, tzinfo=dt.UTC).timestamp() * 1000)


def snap(coin: str, interval: str, start: int, end: int) -> list[dict]:
    time.sleep(PACE_S)
    r = _http_post("/info", {"type": "candleSnapshot",
                             "req": {"coin": coin, "interval": interval,
                                     "startTime": start, "endTime": end}})
    return r if isinstance(r, list) else []


def cached_signals() -> list[tuple[str, int, float, float]]:
    """(coin, sig_close_ms, ret, prev_close) from the hourly cache, complete
    UTC days only (same reconstruction as W-L1)."""
    d = json.load(open(HOURLY_CACHE))["candles"]
    out = []
    for coin, bars in d.items():
        days: dict[int, list] = {}
        for b in bars:
            days.setdefault(int(b[0]) // DAY_MS, []).append(b)
        closes = {k: sorted(v, key=lambda x: x[0])[-1][4]
                  for k, v in days.items() if len(v) == 24}
        for k, c in closes.items():
            p = closes.get(k - 1)
            if p and p > 0 and c / p - 1.0 <= CRASH:
                out.append((coin, (k + 1) * DAY_MS, c / p - 1.0, p))
    return out


def fresh_signals(coins: list[str], after_ms: int) -> list[tuple[str, int, float, float]]:
    """Crash days between cache end and now, from live daily candles."""
    now_ms = int(time.time() * 1000)
    today = now_ms // DAY_MS
    out = []
    for i, coin in enumerate(coins):
        bars = snap(coin, "1d", after_ms - 2 * DAY_MS, now_ms)
        closes = {int(b["t"]) // DAY_MS: float(b["c"]) for b in bars}
        for k in sorted(closes):
            if k >= today:                       # forming bar — not a signal
                continue
            p = closes.get(k - 1)
            c = closes[k]
            sig_close = (k + 1) * DAY_MS
            if p and p > 0 and c / p - 1.0 <= CRASH and sig_close > after_ms:
                out.append((coin, sig_close, c / p - 1.0, p))
        print(f"  fresh-scan [{i+1}/{len(coins)}] {coin}", flush=True)
    return out


def measure(coin: str, sig_close: int, prev_close: float) -> dict | None:
    day_start = sig_close - DAY_MS
    bars = snap(coin, "5m", day_start, sig_close + HOUR_MS + FIVE_MS)
    if not bars:
        return None
    by_t = {int(b["t"]): b for b in bars}
    day_bars = [by_t[t] for t in sorted(by_t) if day_start <= t < sig_close]
    if len(day_bars) < 200:                       # need real 5m coverage
        return None
    thr = prev_close * (1 + CRASH)
    cross = next((b for b in day_bars if float(b["l"]) <= thr), None)
    out: dict = {"coin": coin,
                 "sig_close_utc": dt.datetime.fromtimestamp(
                     sig_close / 1000, dt.UTC).isoformat()}
    if cross is not None:
        ct = int(cross["t"])
        cross_px = min(float(cross["o"]), thr)
        h0 = (ct // HOUR_MS) * HOUR_MS
        in_hour = [b for b in day_bars if h0 <= int(b["t"]) < h0 + HOUR_MS]
        hr_close = float(in_hour[-1]["c"])
        out["cross_to_hour_close_pct"] = round((hr_close / cross_px - 1) * 100, 3)
        out["cross_minute_of_hour"] = (ct - h0) // 60_000
    close_px = float(day_bars[-1]["c"])
    drifts = {}
    for mins in (5, 10, 15, 30, 45, 60):
        b = by_t.get(sig_close + mins * 60_000 - FIVE_MS)   # bar ENDING at +mins
        if b is not None:
            drifts[str(mins)] = round((float(b["c"]) / close_px - 1) * 100, 3)
    out["post_close_drift_pct"] = drifts
    return out


def main() -> None:
    coins = json.load(open(HOURLY_CACHE))["coins"]
    cache_end = max(b[-1][0] for b in
                    json.load(open(HOURLY_CACHE))["candles"].values())
    sigs = [s for s in cached_signals() if s[1] >= RETENTION_START]
    print(f"in-cache signals with 5m coverage: {len(sigs)}")
    print("scanning fresh daily bars for post-cache signals...")
    sigs += fresh_signals(coins, cache_end)
    sigs.sort(key=lambda s: s[2])                 # largest crash first
    sigs = sigs[:10]
    print(f"anchor set (n={len(sigs)}):",
          [(c, f"{r*100:.1f}%") for c, _, r, _ in sigs])

    rows = []
    for coin, sig_close, ret, prev_close in sigs:
        m = measure(coin, sig_close, prev_close)
        if m:
            m["daily_ret_pct"] = round(ret * 100, 2)
            rows.append(m)
            print(m, flush=True)

    summary: dict = {"n": len(rows)}
    for mins in ("5", "10", "15", "30", "45", "60"):
        xs = [r["post_close_drift_pct"][mins] for r in rows
              if mins in r["post_close_drift_pct"]]
        if xs:
            summary[f"drift_{mins}m"] = {
                "mean_pct": round(statistics.mean(xs), 3),
                "median_pct": round(statistics.median(xs), 3),
                "n": len(xs)}
    xs = [r["cross_to_hour_close_pct"] for r in rows
          if "cross_to_hour_close_pct" in r]
    if xs:
        summary["cross_to_hour_close"] = {
            "mean_pct": round(statistics.mean(xs), 3),
            "median_pct": round(statistics.median(xs), 3), "n": len(xs)}
    json.dump({"retention_note": ("HL serves ~5000 5m bars (~17d); anchor set = "
                                  "largest crashes inside retention, NOT the 208d "
                                  "top-10 (those predate retention)"),
               "signals": rows, "summary": summary}, open(RESULTS, "w"), indent=1)
    print("\nsummary:", json.dumps(summary, indent=1))
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
