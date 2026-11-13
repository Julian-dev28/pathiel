"""Synthetic venue data for services/pool tests: fills, orders and candles
shaped like the real info endpoint replies (contracts.pool's fills_request /
orders_request docstrings, and services/pool/tests/fixtures for the shapes
the real venue returns). Not a fixture file itself: a builder module each
test imports."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pathiel.models.types import Candle

_counter = [10_000]


def next_id() -> int:
    _counter[0] += 1
    return _counter[0]


def fill(coin: str, side: str, sz: float, px: float, time_ms: int, start_position: float, *,
        oid: Optional[int] = None, tid: Optional[int] = None, closed_pnl: float = 0.0,
        fee: float = 0.0, fee_token: str = "USDC") -> Dict[str, Any]:
    """One userFillsByTime row (contracts.pool.fills_request's docstring)."""
    return {
        "coin": coin, "px": f"{px:.6f}", "sz": f"{sz:.6f}", "side": side, "time": int(time_ms),
        "startPosition": f"{start_position:.6f}",
        "dir": ("Open Long" if side == "B" else "Close Long") if start_position >= 0 else
               ("Open Short" if side == "A" else "Close Short"),
        "closedPnl": f"{closed_pnl:.6f}", "fee": f"{fee:.6f}",
        "oid": oid if oid is not None else next_id(), "tid": tid if tid is not None else next_id(),
        "hash": "0x" + "ab" * 32, "crossed": True, "feeToken": fee_token,
    }


def order(coin: str, side: str, limit_px: float, sz: float, timestamp_ms: int, *,
         oid: Optional[int] = None, trigger_px: Optional[float] = None, order_type: str = "Limit",
         reduce_only: bool = False, status: str = "filled", status_t: Optional[int] = None) -> Dict[str, Any]:
    """One historicalOrders row (contracts.pool.orders_request's docstring)."""
    return {
        "order": {"coin": coin, "side": side, "limitPx": f"{limit_px:.6f}", "sz": f"{sz:.6f}",
                  "oid": oid if oid is not None else next_id(), "timestamp": int(timestamp_ms),
                  "triggerPx": f"{trigger_px:.6f}" if trigger_px is not None else "0.0",
                  "orderType": order_type, "reduceOnly": reduce_only},
        "status": status, "statusTimestamp": int(status_t if status_t is not None else timestamp_ms),
    }


def bracket_trade(coin: str, side: str, entry_px: float, stop_px: float, size: float, entry_time_ms: int, *,
                  target_px: Optional[float] = None, exit_kind: str = "stop", hold_ms: int = 3_600_000,
                  entry_start_position: float = 0.0) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """One opening fill, a reduce-side Stop Market order (and, with
    `target_px`, a Take Profit Market order) placed a second after it, and a
    closing fill at the stop or target `hold_ms` later. `side` is 'long' or
    'short'. Returns (fills, orders)."""
    open_side = "B" if side == "long" else "A"
    close_side = "A" if side == "long" else "B"
    open_fill = fill(coin, open_side, size, entry_px, entry_time_ms, entry_start_position)
    stop_order = order(coin, close_side, stop_px, size, entry_time_ms + 1_000, trigger_px=stop_px,
                       order_type="Stop Market", reduce_only=True)
    orders = [stop_order]
    target_order = None
    if target_px is not None:
        target_order = order(coin, close_side, target_px, size, entry_time_ms + 1_000, trigger_px=target_px,
                             order_type="Take Profit Market", reduce_only=True)
        orders.append(target_order)
    exit_order = target_order if (exit_kind == "target" and target_order is not None) else stop_order
    exit_px = exit_order["order"]["limitPx"]
    exit_px = float(exit_px)
    pnl = (exit_px - entry_px) * size if side == "long" else (entry_px - exit_px) * size
    start_at_close = size if side == "long" else -size
    close_fill = fill(coin, close_side, size, exit_px, entry_time_ms + hold_ms, start_at_close,
                      oid=exit_order["order"]["oid"], closed_pnl=pnl)
    return [open_fill, close_fill], orders


def candles(coin: str, start_t: int, interval_ms: int, count: int, *, start_px: float = 100.0,
           step: float = 0.0, width: float = 0.05) -> List[Candle]:
    """`count` candles of `interval_ms` from `start_t`, the close stepping
    by `step` each bar, with `width` added above the high and below the low
    (coin unused; kept for readability at call sites)."""
    out: List[Candle] = []
    px = start_px
    for i in range(count):
        o = px
        px = px + step
        c = px
        out.append(Candle(t=start_t + i * interval_ms, o=o, h=max(o, c) + width, l=min(o, c) - width, c=c, v=0.0))
    return out
