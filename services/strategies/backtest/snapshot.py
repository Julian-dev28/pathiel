"""One pinned candle snapshot for the final oil-strategy adoption test
(FINAL_STRATEGY_TEST_2026-11-04.md).

Problem this fixes: every earlier eval (wide_stop_swing_test.py,
oil_event_timing_test.py, extended_history_test.py, ...) fetched its own
15m/1h/1d candles on whatever day it ran, through `data.fetch_candles_cached`
/`fetch_candles_paged_cached` -- and the venue's own `candleSnapshot` only
serves the latest ~5000 bars per request (15m: back to about 2026-09-12 as
of 2026-11-04). Two harnesses run on different days therefore see
DIFFERENT windows of "the same" September replay, and can disagree on its
live-rule P&L (one run: -$34.45; another: +$29.55 -- same code, same
theses, different candles). This module is the fix: fetch the candles
ONCE, save them to disk, and have every dataset in the final test read
that one file.

    load_snapshot(snapshot_dir) -> Snapshot      # pure, no I/O side effects
    fetch_and_save(snapshot_dir, ...)            # network once, merge-append

Layout (one `coin` per directory, durable, OUTSIDE /tmp and outside this
repo -- `/tmp` gets swept, as the October replay already found out):

    <snapshot_dir>/
        <quoted coin>__candles_15m.json
        <quoted coin>__candles_1h.json
        <quoted coin>__candles_1d.json
        <quoted coin>__funding.json
        meta.json

`fetch_and_save` merge-appends: a second run (any time later, even after
the venue's 5000-bar window has rolled forward) reads what is already on
disk, fetches only what `candleSnapshot`/`fundingHistory` serves for the
window asked, and writes back the union, de-duplicated by timestamp and
re-sorted -- so the archive only ever grows, and a stale local row can
never shadow a fresher one at the same timestamp (the fetch always wins
on overlap, since the venue's own candle for a timestamp does not change
once that bar has closed).
"""
from __future__ import annotations

import argparse
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pathiel.agents.atomic_io import read_json, write_json_atomic
from pathiel.models.types import Candle

from services.strategies.backtest.data import CandlePager, FundingFetcher, fetch_candles_paged

DEFAULT_COIN = "xyz:BRENTOIL"
INTERVALS: tuple = ("15m", "1h", "1d")
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# Default pinned snapshot directory this test's docs point at, outside /tmp
# and outside the repo so a tmp sweep (the October replay's fate) can never
# wipe it. Callers (final_strategy_test.py) take a --snapshot-dir flag
# instead of hardcoding this, but it is the agreed default.
DEFAULT_SNAPSHOT_ROOT = os.path.expanduser("~/Documents/code/pathiel-replays/candles")


def _quoted(coin: str) -> str:
    return urllib.parse.quote(coin, safe="")


def candles_path(snapshot_dir: str, coin: str, interval: str) -> str:
    return os.path.join(snapshot_dir, f"{_quoted(coin)}__candles_{interval}.json")


def funding_path(snapshot_dir: str, coin: str) -> str:
    return os.path.join(snapshot_dir, f"{_quoted(coin)}__funding.json")


def meta_path(snapshot_dir: str) -> str:
    return os.path.join(snapshot_dir, "meta.json")


# `fundingHistory` caps at 500 rows per request too (confirmed 2026-11-03
# against the live venue: a 70-day Brent request came back as exactly 500
# hourly rows, 2026-08-25 23:00Z to 2026-09-15 18:00Z -- the SAME kind of
# single-request cap `data.fetch_candles_paged` already works around for
# candles, just with a smaller page and rows returned oldest-first rather
# than newest-first). This pages FORWARD (unlike the candle pager, which
# pages backward from `end_ms`), since each page's `startTime` is simply
# the previous page's last row's time + 1ms -- the natural direction for
# an oldest-first, cap-truncated series.
_FUNDING_PAGE_ROWS = 500


def fetch_funding_paged(coin: str, start_ms: int, end_ms: int,
                        fetch_funding: Optional[FundingFetcher] = None, max_pages: int = 60) -> List[Dict[str, Any]]:
    """Every funding row for `coin` in [start_ms, end_ms], paging forward in
    `_FUNDING_PAGE_ROWS`-row windows until `end_ms` is reached, a page comes
    back empty or short of the cap (end of history), or `max_pages` requests
    have been made. `fetch_funding` injectable (gate tests: no network).
    De-duped by `time`; pure given `fetch_funding`."""
    if fetch_funding is None:
        from pathiel.client.hl_client import fetch_funding_history as fetch_funding
    out: Dict[int, Dict[str, Any]] = {}
    cur_start = start_ms
    for _ in range(max_pages):
        if cur_start > end_ms:
            break
        page = list(fetch_funding(coin, cur_start, end_ms))
        if not page:
            break
        for row in page:
            if "time" in row:
                out[int(row["time"])] = row
        last_t = max(int(row["time"]) for row in page if "time" in row)
        if last_t <= cur_start or len(page) < _FUNDING_PAGE_ROWS:
            break   # no progress, or short of the cap: end of history for this window
        cur_start = last_t + 1
    return [out[t] for t in sorted(out)]


# ── pure candle/funding merge + (de)serialize ──────────────────────────────

def merge_candles(existing: Sequence[Candle], fresh: Sequence[Candle]) -> List[Candle]:
    """Union of `existing` and `fresh`, de-duped by `.t` (a timestamp in
    `fresh` always wins -- the venue's own view of that bar, assumed never
    staler than a previous fetch's), sorted oldest first. Pure."""
    by_t: Dict[int, Candle] = {c.t: c for c in existing}
    by_t.update({c.t: c for c in fresh})
    return [by_t[t] for t in sorted(by_t)]


def merge_funding(existing: Sequence[Dict[str, Any]], fresh: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same merge-by-timestamp as `merge_candles`, for raw funding rows
    (HL's own `{"coin", "fundingRate", "premium", "time"}`). Pure."""
    by_t: Dict[int, Dict[str, Any]] = {int(r["time"]): r for r in existing if "time" in r}
    by_t.update({int(r["time"]): r for r in fresh if "time" in r})
    return [by_t[t] for t in sorted(by_t)]


def _load_candles_file(path: str) -> List[Candle]:
    raw = read_json(path, default=[])
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        try:
            out.append(Candle(**row))
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda c: c.t)


def _save_candles_file(path: str, candles: Sequence[Candle]) -> None:
    write_json_atomic(path, [c.model_dump() for c in sorted(candles, key=lambda c: c.t)])


def _load_funding_file(path: str) -> List[Dict[str, Any]]:
    raw = read_json(path, default=[])
    return sorted((r for r in raw if isinstance(r, dict) and "time" in r), key=lambda r: r["time"]) \
        if isinstance(raw, list) else []


def _save_funding_file(path: str, rows: Sequence[Dict[str, Any]]) -> None:
    write_json_atomic(path, sorted(rows, key=lambda r: r["time"]))


# ── the snapshot itself ─────────────────────────────────────────────────────

@dataclass
class Snapshot:
    coin: str
    c15: List[Candle] = field(default_factory=list)
    c1h: List[Candle] = field(default_factory=list)
    c1d: List[Candle] = field(default_factory=list)
    funding: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def window(self, interval: str = "15m") -> Optional[tuple]:
        """(first_bar_open_ms, last_bar_close_ms) for `interval`, or None
        if that series is empty. The "real tradable window" every dataset
        in the final report is measured against."""
        bars = {"15m": self.c15, "1h": self.c1h, "1d": self.c1d}.get(interval, [])
        if not bars:
            return None
        step = {"15m": 900_000, "1h": HOUR_MS, "1d": DAY_MS}[interval]
        return bars[0].t, bars[-1].t + step

    def window_iso(self, interval: str = "15m") -> Optional[tuple]:
        w = self.window(interval)
        if w is None:
            return None
        return (datetime.fromtimestamp(w[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                datetime.fromtimestamp(w[1] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))


def load_snapshot(snapshot_dir: str, coin: str = DEFAULT_COIN) -> Snapshot:
    """Pure load. Every missing file comes back as an empty list rather
    than an error, so a partial snapshot (e.g. funding not yet fetched)
    still loads -- callers that need a series to be present check for it
    themselves (`Snapshot.window` returns None on an empty series)."""
    return Snapshot(
        coin=coin,
        c15=_load_candles_file(candles_path(snapshot_dir, coin, "15m")),
        c1h=_load_candles_file(candles_path(snapshot_dir, coin, "1h")),
        c1d=_load_candles_file(candles_path(snapshot_dir, coin, "1d")),
        funding=_load_funding_file(funding_path(snapshot_dir, coin)),
        meta=read_json(meta_path(snapshot_dir), default={}) or {},
    )


def fetch_and_save(snapshot_dir: str, coin: str = DEFAULT_COIN, start_ms: Optional[int] = None,
                   end_ms: Optional[int] = None, fetch_page: Optional[CandlePager] = None,
                   fetch_funding: Optional[FundingFetcher] = None, max_pages: int = 60) -> Snapshot:
    """Fetch `coin`'s 15m/1h/1d candles and funding over [start_ms, end_ms)
    (default: the last 75 days to now -- wide enough to cover the Sept
    replay's 08-31 thesis start, even though the venue will only actually
    HAVE 15m bars from about 09-12 on; `Snapshot.window` reports the real
    overlap) and merge-append them onto whatever is already saved at
    `snapshot_dir`. `fetch_page`/`fetch_funding` are injectable (the gate
    test never touches the network); the real defaults are
    `data.fetch_candles_paged`'s `_raw_candle_snapshot` and
    `pathiel.client.hl_client.fetch_funding_history`. Returns the
    merged, just-saved `Snapshot`."""
    os.makedirs(snapshot_dir, exist_ok=True)
    now_ms = int(time.time() * 1000)
    end_ms = now_ms if end_ms is None else end_ms
    start_ms = end_ms - 75 * DAY_MS if start_ms is None else start_ms
    existing = load_snapshot(snapshot_dir, coin)

    merged: Dict[str, List[Candle]] = {}
    for interval in INTERVALS:
        fresh = fetch_candles_paged(coin, interval, start_ms, end_ms, fetch_page, max_pages=max_pages)
        old = {"15m": existing.c15, "1h": existing.c1h, "1d": existing.c1d}[interval]
        combined = merge_candles(old, fresh)
        _save_candles_file(candles_path(snapshot_dir, coin, interval), combined)
        merged[interval] = combined

    fresh_funding = fetch_funding_paged(coin, start_ms, end_ms, fetch_funding, max_pages=max_pages)
    merged_funding = merge_funding(existing.funding, fresh_funding)
    _save_funding_file(funding_path(snapshot_dir, coin), merged_funding)

    meta = dict(existing.meta)
    meta.setdefault("coin", coin)
    meta.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    meta["last_fetch_at"] = datetime.now(timezone.utc).isoformat()
    meta["last_fetch_window"] = [start_ms, end_ms]
    fetches = list(meta.get("fetches") or [])
    fetches.append({"at": meta["last_fetch_at"], "start_ms": start_ms, "end_ms": end_ms,
                    "counts_after_merge": {iv: len(merged[iv]) for iv in INTERVALS},
                    "funding_rows_after_merge": len(merged_funding)})
    meta["fetches"] = fetches
    write_json_atomic(meta_path(snapshot_dir), meta)

    return Snapshot(coin=coin, c15=merged["15m"], c1h=merged["1h"], c1d=merged["1d"],
                    funding=merged_funding, meta=meta)


# ── CLI ──────────────────────────────────────────────────────────────────

def _default_snapshot_dir() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(DEFAULT_SNAPSHOT_ROOT, stamp)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot-dir", default=None,
                    help=f"default: a new UTC-stamped dir under {DEFAULT_SNAPSHOT_ROOT}")
    ap.add_argument("--coin", default=DEFAULT_COIN)
    ap.add_argument("--days-back", type=float, default=75.0)
    ap.add_argument("--start-ms", type=int, default=None, help="overrides --days-back")
    ap.add_argument("--end-ms", type=int, default=None, help="default: now")
    a = ap.parse_args(argv)
    snap_dir = a.snapshot_dir or _default_snapshot_dir()
    end_ms = a.end_ms if a.end_ms is not None else int(time.time() * 1000)
    start_ms = a.start_ms if a.start_ms is not None else end_ms - int(a.days_back * DAY_MS)
    snap = fetch_and_save(snap_dir, a.coin, start_ms, end_ms)
    print(f"snapshot: {snap_dir}")
    for interval in INTERVALS:
        w = snap.window_iso(interval)
        bars = {"15m": snap.c15, "1h": snap.c1h, "1d": snap.c1d}[interval]
        print(f"  {interval}: {len(bars)} bars, window {w}")
    print(f"  funding: {len(snap.funding)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
