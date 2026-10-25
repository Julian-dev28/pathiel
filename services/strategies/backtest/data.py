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
    side: str                       # "long" | "short"
    entry_px: Optional[float]
    stop_px: Optional[float]
    invalidation_px: Optional[float]
    targets: List[float]
    thesis_id: str


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
    if side not in ("long", "short"):
        return None
    try:
        created_at = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    created_at_ms = int(created_at.astimezone(timezone.utc).timestamp() * 1000)
    return ThesisDirection(
        created_at_ms=created_at_ms, side=side,
        entry_px=call.get("entry_px"), stop_px=call.get("stop_px"),
        invalidation_px=parse_invalidation_px(call.get("invalidation") or ""),
        targets=list(call.get("targets") or []),
        thesis_id=str(raw.get("id") or os.path.basename(path)),
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
    "ThesisDirection", "parse_invalidation_px", "load_thesis_file", "load_journaled_theses",
]
