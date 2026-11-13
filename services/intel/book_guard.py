"""Liquidity guardrail for a high-confidence headline: what would it cost to
trade the desk's size RIGHT NOW? Read only, never an order.

When Stage 2 (event_score) says confidence_score >= GUARD_CONFIDENCE the
watcher snapshots the venue's L2 book for the oil coins (xyz:BRENTOIL,
xyz:CL): top-of-book spread and the slippage of a market order of
`notional_usd` each way, so the event log and the tick agent can see whether
the book thinned out on the news (a 40 bps spread on Brent is "do not chase",
whatever the headline says).

The book comes through pathiel.client.hl_client._http_post, the one path
that spends the venue rate budget (rate_limit.HL_LIMITER, l2Book weight 2);
a refused budget is a snapshot with an "error", never a retry storm. The
slippage math (walk the book, VWAP against mid) is the same formula as
services/strategies/evals/market_scout.walk_book, kept here so this service
does not import another service's internals.

Order size: FINLIGHT_BOOK_NOTIONAL_USD when set, else DEFAULT_NOTIONAL_USD.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

GUARD_CONFIDENCE = 0.75
GUARD_COINS = ("xyz:BRENTOIL", "xyz:CL")
NOTIONAL_ENV = "FINLIGHT_BOOK_NOTIONAL_USD"
DEFAULT_NOTIONAL_USD = 2500.0        # market_scout's small size; set the env to the desk's own

Level = Tuple[float, float]           # (px, sz)
Book = Dict[str, List[Level]]         # {"bids": [...], "asks": [...]}, best price first


def notional_usd() -> float:
    try:
        v = float(os.environ.get(NOTIONAL_ENV) or DEFAULT_NOTIONAL_USD)
    except (TypeError, ValueError):
        v = DEFAULT_NOTIONAL_USD
    return v if v > 0 else DEFAULT_NOTIONAL_USD


def parse_l2(raw: Any) -> Book:
    """Hyperliquid's l2Book answer ({"levels": [[bids], [asks]]}, each row
    {px, sz, n}) -> Book. A malformed answer is an empty book. Pure."""
    levels = (raw or {}).get("levels") if isinstance(raw, dict) else None
    if not isinstance(levels, list):
        return {"bids": [], "asks": []}

    def side(i: int) -> List[Level]:
        out: List[Level] = []
        for r in (levels[i] if len(levels) > i and isinstance(levels[i], list) else []):
            try:
                out.append((float(r["px"]), float(r["sz"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out
    return {"bids": side(0), "asks": side(1)}


def walk_book(levels: Sequence[Level], notional: float) -> Tuple[Optional[float], bool]:
    """(vwap_px, filled) of a market order of `notional` USD against `levels`
    (best price first). filled False: the book ran out, vwap is over what
    was there. Pure."""
    cum_notional = 0.0
    cum_size = 0.0
    for px, sz in levels:
        if px <= 0 or sz <= 0:
            continue
        level_notional = px * sz
        if cum_notional + level_notional >= notional:
            need = notional - cum_notional
            cum_size += need / px
            return (notional / cum_size if cum_size > 0 else None), True
        cum_notional += level_notional
        cum_size += sz
    return (cum_notional / cum_size if cum_size > 0 else None), False


def slippage(levels: Sequence[Level], notional: float, mid: float, side: str) -> Dict[str, Any]:
    """{"bps", "vwap_px", "filled"} of a `side` ("buy" walks asks, "sell"
    walks bids) market order against `mid`. Pure."""
    vwap, filled = walk_book(levels, notional)
    if vwap is None or mid <= 0:
        return {"bps": None, "vwap_px": None, "filled": False}
    bps = (vwap / mid - 1.0) * 1e4 if side == "buy" else (1.0 - vwap / mid) * 1e4
    return {"bps": round(bps, 2), "vwap_px": round(vwap, 6), "filled": filled}


def snapshot_from_book(coin: str, book: Book, notional: float, at_ms: Optional[int] = None) -> Dict[str, Any]:
    """The guardrail row for one coin from a parsed book. Pure."""
    bids, asks = book.get("bids") or [], book.get("asks") or []
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
    spread_bps = round((best_ask - best_bid) / mid * 1e4, 2) if mid else None
    return {
        "coin": coin, "at_ms": at_ms if at_ms is not None else int(time.time() * 1000),
        "best_bid": best_bid, "best_ask": best_ask, "mid": round(mid, 6) if mid else None,
        "spread_bps": spread_bps, "notional_usd": notional,
        "buy": slippage(asks, notional, mid, "buy") if mid else {"bps": None, "vwap_px": None, "filled": False},
        "sell": slippage(bids, notional, mid, "sell") if mid else {"bps": None, "vwap_px": None, "filled": False},
        "depth_levels": {"bids": len(bids), "asks": len(asks)},
    }


def fetch_l2_book(coin: str) -> Optional[Book]:
    """The live book through the rate-limited venue client. None when the
    budget refused the read or the venue failed (never raises)."""
    try:
        from pathiel.client.hl_client import _http_post
        raw = _http_post("/info", {"type": "l2Book", "coin": coin})
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    return parse_l2(raw)


def book_snapshot(coins: Sequence[str] = GUARD_COINS, *, notional: Optional[float] = None,
                  fetch: Callable[[str], Optional[Book]] = fetch_l2_book) -> Dict[str, Dict[str, Any]]:
    """One snapshot row per coin (an {"error": ...} row when the read
    failed). Read only."""
    size = notional if notional is not None else notional_usd()
    out: Dict[str, Dict[str, Any]] = {}
    for coin in coins:
        book = fetch(coin)
        if not book or not (book.get("bids") or book.get("asks")):
            out[coin] = {"coin": coin, "error": "no book (budget refused or venue failed)", "notional_usd": size}
            continue
        out[coin] = snapshot_from_book(coin, book, size)
    return out


def summary_line(snap: Dict[str, Dict[str, Any]]) -> str:
    """One console fragment: `xyz:BRENTOIL spread 1.3bps buy 1.9bps sell 2.1bps`."""
    parts = []
    for coin, row in snap.items():
        if row.get("error"):
            parts.append(f"{coin} book: {row['error']}")
            continue
        parts.append(f"{coin} spread {row['spread_bps']}bps buy {row['buy']['bps']}bps sell {row['sell']['bps']}bps "
                     f"@ ${row['notional_usd']:g}")
    return "; ".join(parts)


__all__ = ["DEFAULT_NOTIONAL_USD", "GUARD_COINS", "GUARD_CONFIDENCE", "NOTIONAL_ENV", "book_snapshot",
           "fetch_l2_book", "notional_usd", "parse_l2", "slippage", "snapshot_from_book", "summary_line",
           "walk_book"]
