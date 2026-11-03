"""Fetch + disk cache for the desk-tick backtest (plan section 3/4).

Candles and funding history are fetched from Hyperliquid's public,
read-only `/info` endpoint exactly once per (coin, interval, count) or
(coin, window), then cached to disk under `<cache_dir>/<quoted coin>__
<kind>.json`. Every later run of the eval reads the cache, so the network
fetch is the only non-reproducible step and it happens at most once per
cache directory. Pass `--cache-dir "$(mktemp -d)"` (or any throwaway dir)
to force a fresh fetch; the gate tests never touch the network because
they pass an injected `fetch` callable instead of the live default.

Also reads the journaled oil theses (READ ONLY — `pathiel`'s live desk
state, never written here) and parses each thesis into the simulator's
`Direction` series input: side, the structural stop the live loop chose,
and a numeric invalidation price parsed out of the thesis's free-text
`invalidation` field (e.g. "1h close below 110.80 (under the 8h low
110.97)" -> 110.80). The text sometimes carries a second, softer
daily-close/headline criterion (plan section 8: not replayable from
candles); only the first "below N"/"above N" number is used, matching the
live loop's own 1h-close rule.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from pathiel.agents.atomic_io import read_json, write_json_atomic
from pathiel.models.types import Candle

CandleFetcher = Callable[[str, str, int], Sequence[Candle]]
FundingFetcher = Callable[[str, int, int], Sequence[Dict[str, Any]]]

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def _cache_path(cache_dir: str, coin: str, kind: str) -> str:
    name = urllib.parse.quote(coin, safe="") + f"__{kind}.json"
    return os.path.join(cache_dir, name)


def fetch_candles_cached(coin: str, interval: str, count: int, cache_dir: str,
                         fetch: Optional[CandleFetcher] = None) -> List[Candle]:
    """`count` candles of `interval` for `coin`, from `<cache_dir>` when
    present, else fetched once (default fetcher: the live, public
    `candleSnapshot` read via `pathiel.client.hl_client.fetch_hl_candles`)
    and written back. Round-trips through `Candle.model_dump()` /
    `Candle(**row)`, so the cache is exactly what a fresh fetch would have
    returned."""
    path = _cache_path(cache_dir, coin, f"candles_{interval}_{count}")
    cached = read_json(path)
    if isinstance(cached, list) and cached:
        try:
            return [Candle(**row) for row in cached]
        except (TypeError, ValueError):
            pass   # corrupt/old cache: fall through and refetch
    if fetch is None:
        from pathiel.client.hl_client import fetch_hl_candles as fetch
    candles = list(fetch(coin, interval, count))
    if candles:
        write_json_atomic(path, [c.model_dump() for c in candles])
    return candles


# ── paged candles (2026-11-03, SCALP_TEST_2026-11-03.md) ───────────────────
# `fetch_candles_cached` above asks for `count` bars ending NOW, and the
# venue's own `candleSnapshot` caps a single request at 5000 bars (about
# 17.4 days of 5m, 52 days of 15m) -- too short a window for a scalp sim's
# walk-forward split on 5m bars. These page backward in <=5000-bar windows
# from `end_ms` to `start_ms`, so the window can run as long as the venue
# actually has history for, at the cost of more than one request.

_PAGE_INTERVAL_MS: Dict[str, int] = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": HOUR_MS, "4h": 4 * HOUR_MS,
                                     "1d": DAY_MS}
CandlePager = Callable[[str, str, int, int], Sequence[Candle]]


def _raw_candle_snapshot(coin: str, interval: str, start_ms: int, end_ms: int) -> List[Candle]:
    """One `candleSnapshot` request against the venue's public, read-only
    `/info` endpoint for an explicit [start_ms, end_ms) window (<=5000 bars
    returned; the venue silently caps a wider request). No key, no order."""
    import requests

    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": interval, "startTime": int(start_ms), "endTime": int(end_ms)}}
    resp = requests.post("https://api.hyperliquid.xyz/info", json=payload, timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    if not isinstance(raw, list):
        return []
    return [Candle(t=c["t"], o=float(c["o"]), h=float(c["h"]), l=float(c["l"]), c=float(c["c"]),
                   v=float(c.get("v", "0"))) for c in raw]


def fetch_candles_paged(coin: str, interval: str, start_ms: int, end_ms: int,
                        fetch_page: Optional[CandlePager] = None, max_pages: int = 60) -> List[Candle]:
    """Every candle of `interval` for `coin` in [start_ms, end_ms), oldest
    first, paging backward from `end_ms` in <=5000-bar windows until
    `start_ms` is reached, the venue runs out of history (an empty or
    non-progressing page), or `max_pages` requests have been made.
    `fetch_page` is injectable (gate tests never touch the network).
    De-duped by timestamp; pure given `fetch_page`."""
    fetch_page = fetch_page or _raw_candle_snapshot
    ms_per = _PAGE_INTERVAL_MS.get(interval)
    if ms_per is None:
        raise ValueError(f"unknown interval {interval!r}")
    page_span = ms_per * 5000
    out: Dict[int, Candle] = {}
    cur_end = end_ms
    for _ in range(max_pages):
        if cur_end <= start_ms:
            break
        cur_start = max(start_ms, cur_end - page_span)
        got = fetch_page(coin, interval, cur_start, cur_end)
        if not got:
            break
        for c in got:
            if start_ms <= c.t < end_ms:
                out[c.t] = c
        earliest = min(c.t for c in got)
        if earliest >= cur_end:   # no progress: stop rather than loop forever
            break
        cur_end = earliest
    return [out[t] for t in sorted(out)]


def fetch_candles_paged_cached(coin: str, interval: str, start_ms: int, end_ms: int, cache_dir: str,
                               fetch_page: Optional[CandlePager] = None) -> List[Candle]:
    """`fetch_candles_paged`, cached to disk per (coin, interval, window) the
    same way `fetch_candles_cached` is. Pass a fresh `cache_dir` to force a
    refetch."""
    path = _cache_path(cache_dir, coin, f"paged_{interval}_{start_ms}_{end_ms}")
    cached = read_json(path)
    if isinstance(cached, list) and cached:
        try:
            return [Candle(**row) for row in cached]
        except (TypeError, ValueError):
            pass
    candles = fetch_candles_paged(coin, interval, start_ms, end_ms, fetch_page)
    if candles:
        write_json_atomic(path, [c.model_dump() for c in candles])
    return candles


def fetch_funding_cached(coin: str, start_ms: int, end_ms: int, cache_dir: str,
                         fetch: Optional[FundingFetcher] = None) -> List[Dict[str, Any]]:
    """Hourly funding history for `coin` over [start_ms, end_ms], cached the
    same way as candles. Raw HL rows: {"coin", "fundingRate", "premium",
    "time"}."""
    path = _cache_path(cache_dir, coin, f"funding_{start_ms}_{end_ms}")
    cached = read_json(path)
    if isinstance(cached, list) and cached:
        return cached
    if fetch is None:
        from pathiel.client.hl_client import fetch_funding_history as fetch
    rows = list(fetch(coin, start_ms, end_ms))
    if rows:
        write_json_atomic(path, rows)
    return rows


# ── journaled theses (read-only input) ───────────────────────────────────────

@dataclass
class ThesisDirection:
    """One journaled thesis, reduced to what the simulator's direction
    series needs."""
    created_at_ms: int
    side: str                       # "long" | "short" | "flat"
    entry_px: Optional[float]
    stop_px: Optional[float]
    invalidation_px: Optional[float]
    targets: List[float]
    thesis_id: str
    # The thesis call's own confidence (0-1), for Params.conviction_leverage
    # (2026-10-30 AI-informed variants). None for a thesis written before
    # this field was read, or any malformed value.
    confidence: Optional[float] = None


# "below 110.80", "above $4,028.50" — the first number after the directional
# word, which is the 1h-close invalidation gate the live loop actually acts
# on. A later sentence (daily close, Polymarket odds, a headline) is a
# softer/secondary criterion the plan documents as not replayable from
# candles (section 8) and is intentionally ignored here.
_INVALIDATION_RE = re.compile(r"(below|above)\s+\$?([0-9][0-9,]*\.?[0-9]*)", re.IGNORECASE)


def parse_invalidation_px(text: str) -> Optional[float]:
    m = _INVALIDATION_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(2).replace(",", ""))
    except ValueError:
        return None


def load_thesis_file(path: str) -> Optional[ThesisDirection]:
    raw = read_json(path)
    if not isinstance(raw, dict):
        return None
    call = raw.get("call") or {}
    side = call.get("side")
    # "flat" is kept (2026-10-31): dropping it left the previous long/short in
    # force through every flat day, so the sim traded calls the desk had
    # already withdrawn. journaled_direction turns a flat call into "no call".
    if side not in ("long", "short", "flat"):
        return None
    try:
        created_at = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    created_at_ms = int(created_at.astimezone(timezone.utc).timestamp() * 1000)
    confidence = call.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    return ThesisDirection(
        created_at_ms=created_at_ms, side=side,
        entry_px=call.get("entry_px"), stop_px=call.get("stop_px"),
        invalidation_px=parse_invalidation_px(call.get("invalidation") or ""),
        targets=list(call.get("targets") or []),
        thesis_id=str(raw.get("id") or os.path.basename(path)),
        confidence=confidence,
    )


def load_journaled_theses(theses_dir: str) -> List[ThesisDirection]:
    """Every parseable thesis file under `theses_dir`, oldest first.
    READ ONLY: never writes to this directory (it is the live desk's own
    state). Missing directory -> empty list, not an error, so the eval can
    still run its other direction series."""
    if not os.path.isdir(theses_dir):
        return []
    out: List[ThesisDirection] = []
    for name in sorted(os.listdir(theses_dir)):
        if not name.endswith(".json"):
            continue
        parsed = load_thesis_file(os.path.join(theses_dir, name))
        if parsed is not None:
            out.append(parsed)
    out.sort(key=lambda t: t.created_at_ms)
    return out


__all__ = [
    "HOUR_MS", "DAY_MS", "fetch_candles_cached", "fetch_funding_cached",
    "fetch_candles_paged", "fetch_candles_paged_cached", "CandlePager",
    "ThesisDirection", "parse_invalidation_px", "load_thesis_file", "load_journaled_theses",
]
