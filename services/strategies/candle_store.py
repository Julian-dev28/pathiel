"""Persistent daily-candle store shared by every process that builds desk analytics.

A desk brief needs ~400 daily candles per instrument. Fetching them live on
every page view, through the dashboard server's Hyperliquid budget (60 burst,
2 weight/s, see scripts/restart.sh), made briefs slow: measured 2026-10-18,
oil 42s, btc 22s, metals 32s, indices 52s, ai 122s (11 instruments).

Closed daily candles never change, so this module keeps them on disk at
`$PATHIEL_STATE_DIR/strategies/candles/<quoted coin>.json` and asks the venue
only for what it doesn't have yet:

    written < max_age_s ago                  -> served from disk, zero requests
    stale, history on disk                   -> fetch the last few days, merge by open time
    nothing on disk, or full copy > 24h old  -> one full fetch
    any fetch failure (429, timeout, [])     -> serve what is on disk, never a blank desk

The dashboard server, the MCP server and the scheduler all resolve
PATHIEL_STATE_DIR from .env.local, so one fetch serves all three.

Where it can break: a coin whose venue history is revised after the fact
would keep the old bar until the next full fetch (at most FULL_REFRESH_S
later). Hyperliquid does not revise closed candles, so this is a theoretical
gap, bounded by the daily full refresh.
"""
from __future__ import annotations

import logging
import math
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from pathiel.agents.atomic_io import read_json, write_json_atomic
from pathiel.models.types import Candle

logger = logging.getLogger(__name__)

DAY_MS = 86_400_000
FULL_REFRESH_S = 86_400.0          # re-pull the whole history once a day
DEFAULT_MAX_AGE_S = 900.0          # a brief may serve candles up to 15 min old
MIN_TAIL_DAYS = 3                  # an incremental fetch always re-pulls at least this many bars
SCHEMA = 1

RawFetcher = Callable[[str, str, int], Sequence[Candle]]

_FALLBACK_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".state")


def default_max_age_s() -> float:
    try:
        return float(os.environ.get("PATHIEL_DESK_CANDLE_MAX_AGE_S", DEFAULT_MAX_AGE_S))
    except ValueError:
        return DEFAULT_MAX_AGE_S


def _store_dir() -> str:
    state = os.environ.get("PATHIEL_STATE_DIR") or _FALLBACK_STATE_DIR
    return os.path.join(state, "strategies", "candles")


def path_for(coin: str) -> str:
    """Collision-free, reversible file name: `xyz:CL` -> `xyz%3ACL.json`."""
    return os.path.join(_store_dir(), urllib.parse.quote(coin, safe="") + ".json")


@dataclass
class Stored:
    fetched_at: float                  # epoch seconds of the last successful fetch (any size)
    full_at: float                     # epoch seconds of the last FULL history fetch
    requested: int                     # the largest count a full fetch asked for
    candles: List[Candle] = field(default_factory=list)


def load(coin: str) -> Optional[Stored]:
    raw = read_json(path_for(coin))
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    try:
        return Stored(
            fetched_at=float(raw["fetched_at"]), full_at=float(raw["full_at"]),
            requested=int(raw["requested"]),
            candles=[Candle(**c) for c in raw.get("candles") or []],
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(f"[strategies.candles] unreadable store for {coin}, ignoring it: {exc}")
        return None


def save(coin: str, stored: Stored) -> None:
    write_json_atomic(path_for(coin), {
        "schema": SCHEMA, "coin": coin, "fetched_at": stored.fetched_at,
        "full_at": stored.full_at, "requested": stored.requested,
        "candles": [c.model_dump() for c in stored.candles],
    })


def merge(old: Sequence[Candle], new: Sequence[Candle]) -> List[Candle]:
    """Union by candle open time, the NEWER fetch winning on a clash (it holds
    the latest print of the still-open bar). Sorted oldest first."""
    by_t: Dict[int, Candle] = {c.t: c for c in old}
    for c in new:
        by_t[c.t] = c
    return [by_t[t] for t in sorted(by_t)]


def get_daily(coin: str, count: int, fetch: RawFetcher, *,
              max_age_s: Optional[float] = None, now: Optional[float] = None) -> List[Candle]:
    """The last `count` daily candles for `coin`, from disk when fresh enough,
    else from `fetch` (only the missing tail when history is on disk)."""
    now = time.time() if now is None else now
    max_age = default_max_age_s() if max_age_s is None else max_age_s
    stored = load(coin)
    have = bool(stored and stored.candles)

    if have and stored.requested >= count and (now - stored.fetched_at) < max_age:
        return list(stored.candles[-count:])

    full = (not have or stored.requested < count or (now - stored.full_at) >= FULL_REFRESH_S)
    if full:
        n = count
    else:
        behind_days = math.ceil(max(0.0, now * 1000 - stored.candles[-1].t) / DAY_MS)
        n = min(count, max(MIN_TAIL_DAYS, behind_days + 2))

    try:
        fresh = list(fetch(coin, "1d", n) or [])
    except Exception as exc:  # noqa: BLE001 - a failed fetch falls back to disk below
        logger.warning(f"[strategies.candles] {coin} fetch raised: {exc}")
        fresh = []

    if not fresh:
        if have:
            logger.info(f"[strategies.candles] {coin}: fetch empty, serving {len(stored.candles)} "
                        f"bars from disk ({(now - stored.fetched_at) / 60:.0f} min old)")
            return list(stored.candles[-count:])
        return []

    merged = fresh if full else merge(stored.candles, fresh)
    keep = max(count, stored.requested if have else 0)
    updated = Stored(
        fetched_at=now, full_at=now if full else stored.full_at,
        requested=max(count, stored.requested if have else 0), candles=merged[-keep:],
    )
    try:
        save(coin, updated)
    except OSError as exc:
        logger.warning(f"[strategies.candles] could not persist {coin}: {exc}")
    return list(merged[-count:])
