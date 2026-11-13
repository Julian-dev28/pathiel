"""The pool's reads: a member's public fills and orders, and the pool coins'
candles. Public /info only (`services.pool.venue.post_info`), no key, no
`/exchange`. Also the offline bundle a build can replay byte for byte.

Bundle shape (the dict this module hands `grade.member_records`):

    {"as_of_ms": int,
     "inputs": {"<address>": {"fills": [...], "orders": [...]}},
     "bars5": {"<coin>": [Candle, ...]}, "bars1h": {"<coin>": [Candle, ...]}}

On disk (`save_bundle` / `load_bundle`):

    meta.json                              {as_of_ms, coins, members}
    members/<address>/fills.json           the raw userFillsByTime rows
    members/<address>/orders.json          the raw historicalOrders rows
    candles/<url-quoted coin>_5m.json      [[t, o, h, l, c], ...]
    candles/<url-quoted coin>_1h.json
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import contracts.pool as pool
from pathiel.models.types import Candle
from services.pool import venue
from services.strategies.backtest import data as D

BARS_5M = 5000      # services.review.inputs.BARS_5M: the venue's per-request cap, about 17 days of 5m
BARS_1H = 1800       # services.review.inputs.BARS_1H


def _fill_key(row: Dict[str, Any]) -> Any:
    tid = row.get("tid")
    if tid is not None:
        return ("tid", tid)
    return ("row", row.get("time"), row.get("oid"), row.get("px"), row.get("sz"), row.get("side"))


def fetch_fills(address: str, start_ms: int, end_ms: int, *, post: Optional[Callable[[Dict[str, Any]], Any]] = None,
                sleep: Callable[[float], None] = time.sleep) -> List[Dict[str, Any]]:
    """Every fill of `address` on a pool coin, `start_ms` to `end_ms`
    inclusive, oldest first. Pages of `pool.FILLS_PAGE`: when a reply is a
    full page, ask again from its last row's time (inclusive), dropping rows
    already seen (by `tid`, else the row's own fields). `venue.VenueError`
    when a full page adds nothing new: the venue is not advancing, which
    must never be read as "no more fills"."""
    post = post or venue.post_info
    address = pool.normalize_address(address)
    seen: set = set()
    rows: List[Dict[str, Any]] = []
    start = int(start_ms)
    while True:
        reply = post(pool.fills_request(address, start, end_ms))
        page = reply if isinstance(reply, list) else []
        added = 0
        for row in page:
            if not isinstance(row, dict):
                continue
            key = _fill_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        full = len(page) >= pool.FILLS_PAGE
        if not full:
            break
        if added == 0:
            raise venue.VenueError("fills page did not advance")
        start = max(int(r["time"]) for r in page)
        sleep(0.25)
    kept = [r for r in rows if r.get("coin") in pool.POOL_COINS]
    kept.sort(key=lambda r: int(r["time"]))
    return kept


def fetch_orders(address: str, *, post: Optional[Callable[[Dict[str, Any]], Any]] = None) -> List[Dict[str, Any]]:
    """The historical orders of `address` whose `order.coin` is a pool coin."""
    post = post or venue.post_info
    reply = post(pool.orders_request(pool.normalize_address(address)))
    page = reply if isinstance(reply, list) else []
    return [row for row in page if isinstance(row, dict) and (row.get("order") or {}).get("coin") in pool.POOL_COINS]


def member_inputs(member: "pool.ActiveMember", as_of_ms: int, *,
                  post: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """One member's fills (since they joined) and orders."""
    return {
        "fills": fetch_fills(member["address"], member["count_from_ms"], as_of_ms, post=post),
        "orders": fetch_orders(member["address"], post=post),
    }


def load_candles(cache_dir: str, *, fetch: Optional[D.CandleFetcher] = None
                 ) -> Tuple[Dict[str, List[Candle]], Dict[str, List[Candle]]]:
    """(bars5, bars1h) for every pool coin, through
    `services.strategies.backtest.data.fetch_candles_cached`."""
    bars5 = {c: D.fetch_candles_cached(c, "5m", BARS_5M, cache_dir, fetch) for c in pool.POOL_COINS}
    bars1h = {c: D.fetch_candles_cached(c, "1h", BARS_1H, cache_dir, fetch) for c in pool.POOL_COINS}
    return bars5, bars1h


# ── the offline bundle ───────────────────────────────────────────────────────

def _coin_file(coin: str, interval: str) -> str:
    return urllib.parse.quote(coin, safe="") + f"_{interval}.json"


def _bars_to_rows(bars: Sequence[Candle]) -> List[List[float]]:
    return [[b.t, b.o, b.h, b.l, b.c] for b in bars]


def _rows_to_bars(rows: Sequence[Sequence[float]]) -> List[Candle]:
    return [Candle(t=int(r[0]), o=r[1], h=r[2], l=r[3], c=r[4], v=0.0) for r in rows]


def save_bundle(path: str, bundle: Dict[str, Any]) -> None:
    """Write `bundle` so `load_bundle` can replay it byte for byte."""
    os.makedirs(os.path.join(path, "candles"), exist_ok=True)
    members = sorted(bundle.get("inputs") or {})
    coins = sorted(set(bundle.get("bars5") or {}) | set(bundle.get("bars1h") or {}))
    with open(os.path.join(path, "meta.json"), "w") as fh:
        json.dump({"as_of_ms": int(bundle["as_of_ms"]), "coins": coins, "members": members}, fh)
    for address in members:
        one = bundle["inputs"][address]
        d = os.path.join(path, "members", address)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "fills.json"), "w") as fh:
            json.dump(one.get("fills") or [], fh, separators=(",", ":"))
        with open(os.path.join(d, "orders.json"), "w") as fh:
            json.dump(one.get("orders") or [], fh, separators=(",", ":"))
    for key, interval in (("bars5", "5m"), ("bars1h", "1h")):
        for coin, bars in (bundle.get(key) or {}).items():
            with open(os.path.join(path, "candles", _coin_file(coin, interval)), "w") as fh:
                json.dump(_bars_to_rows(bars), fh, separators=(",", ":"))


def load_bundle(path: str) -> Dict[str, Any]:
    """The inverse of `save_bundle`. A bundle with no members or coins (the
    empty offline bundle `docs/scoreboard/scoreboard.json` was generated
    from) loads with `inputs = {}`, `bars5 = {}`, `bars1h = {}`."""
    with open(os.path.join(path, "meta.json")) as fh:
        meta = json.load(fh)
    bundle: Dict[str, Any] = {"as_of_ms": int(meta["as_of_ms"]), "inputs": {}}
    for address in meta.get("members") or []:
        d = os.path.join(path, "members", address)
        fills, orders = [], []
        fp, op = os.path.join(d, "fills.json"), os.path.join(d, "orders.json")
        if os.path.exists(fp):
            with open(fp) as fh:
                fills = json.load(fh)
        if os.path.exists(op):
            with open(op) as fh:
                orders = json.load(fh)
        bundle["inputs"][address] = {"fills": fills, "orders": orders}
    coins = meta.get("coins") or []
    for key, interval in (("bars5", "5m"), ("bars1h", "1h")):
        bundle[key] = {}
        for coin in coins:
            p = os.path.join(path, "candles", _coin_file(coin, interval))
            rows = []
            if os.path.exists(p):
                with open(p) as fh:
                    rows = json.load(fh)
            bundle[key][coin] = _rows_to_bars(rows)
    return bundle
