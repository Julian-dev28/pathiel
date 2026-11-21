"""Hand-built candles with one known zigzag swing, and the fakes a tick runs on.

The up scenario, 1h bars from Monday 2026-11-09 00:00Z:

  hours 0-19   flat: open = close = 1000, high 1000.5, low 999.5 (ATR settles at 1)
  hours 20-29  ten up bars, +3 each: the leg runs 999.5 -> 1030.5
  hour 30      a drop to 1023.5 that confirms the high at 1030.5

So with 31 closed hours the swing is up, origin 999.5, extreme 1030.5, size 31 (ATR 3.34, a third of a percent, so the stop passes the 20x rule).
`mirror=True` reflects every price around 1000: a down-swing, origin 1000.5,
extreme 969.5.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pathiel.models.types import Candle

HOUR, Q = 3_600_000, 900_000
T0 = 1_794_182_400_000            # Monday 2026-11-09 00:00Z
NOW = T0 + 31 * HOUR + 60_000     # one minute into hour 31: 31 hours have closed
ORIGIN, EXTREME, SIZE = 999.5, 1030.5, 31.0
SUB = "0x3d3b471f6f18f1e3018064112abb9bf6d46cc272"
MASTER = "0x2c2e4eff574b3fd9879adef9c5cac6fd7c916985"
COIN = "xyz:BRENTOIL"


def _flip(b: Candle) -> Candle:
    return Candle(t=b.t, o=2000 - b.o, h=2000 - b.l, l=2000 - b.h, c=2000 - b.c, v=b.v)


def hours(mirror: bool = False) -> List[Candle]:
    out = [Candle(t=T0 + i * HOUR, o=1000, h=1000.5, l=999.5, c=1000, v=1) for i in range(20)]
    px = 1000.0
    for i in range(20, 30):
        out.append(Candle(t=T0 + i * HOUR, o=px, h=px + 3.5, l=px - 0.2, c=px + 3, v=1))
        px += 3
    out.append(Candle(t=T0 + 30 * HOUR, o=1030, h=1030.2, l=1023.5, c=1024, v=1))
    return [_flip(b) for b in out] if mirror else out


def quarters(hs: List[Candle]) -> List[Candle]:
    """Four 15m bars per hour: the low prints in the first when the hour
    rose (the high in the third), the high in the first when it fell."""
    out: List[Candle] = []
    for b in hs:
        up = b.c >= b.o
        step = (b.c - b.o) / 4
        for k in range(4):
            o, c = b.o + k * step, b.o + (k + 1) * step
            h, l = max(o, c), min(o, c)
            if (up and k == 2) or (not up and k == 0):
                h = b.h
            if (up and k == 0) or (not up and k == 2):
                l = b.l
            out.append(Candle(t=b.t + k * Q, o=o, h=h, l=l, c=c, v=1))
    return out


def atr14(hs: List[Candle]) -> float:
    trs = []
    for i, b in enumerate(hs):
        pc = hs[i - 1].c if i else b.o
        trs.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
    return sum(trs[-14:]) / 14


def forming(price: float, t: int = T0 + 31 * HOUR) -> Candle:
    return Candle(t=t, o=price, h=price, l=price, c=price, v=1)


class FakeEx:
    """The slice of pathiel.client.exchange the bracket path and the tick use."""

    def __init__(self, position: float = 0.0, orders: Optional[List[Dict[str, Any]]] = None,
                 price: float = 1024.0) -> None:
        self.position: Optional[float] = position
        self.orders = list(orders or [])
        self.price = price
        self.calls: List[tuple] = []
        self.reads: List[tuple] = []

    # reads
    def resolve_user_address(self) -> str:
        return SUB

    def _position_size_on_coin(self, user: str, coin: str) -> Optional[float]:
        self.reads.append(("position", user, coin))
        return self.position

    def _resting_orders_for_coin(self, user: str, coin: str) -> List[Dict[str, Any]]:
        self.reads.append(("orders", user, coin))
        return list(self.orders)

    def get_max_leverage(self, coin: str) -> int:
        return 20

    def get_coin_index(self, coin: str) -> tuple:
        return (49, 2, 4)

    def get_hl_price(self, coin: str) -> float:
        return self.price

    def attached_child_oids(self, orders: List[Dict[str, Any]]) -> set:
        out = set()
        for o in orders:
            if not o.get("reduceOnly"):
                out |= {int(c["oid"]) for c in o.get("children") or []}
        return out

    # signed actions
    def cancel_stranded_reduce_only_orders(self, coin: str) -> int:
        self.calls.append(("cancel_stranded", coin))
        return 0

    def set_leverage(self, coin: str, leverage: int, is_cross: Any = None, strict: bool = False) -> Dict[str, Any]:
        self.calls.append(("set_leverage", coin, leverage, is_cross, strict))
        return {"ok": True}

    def place_hl_bracket_order(self, coin: str, is_buy: bool, size: float, entry_px: float, stop_px: float,
                               tp_px: float) -> Dict[str, Any]:
        self.calls.append(("bracket", coin, is_buy, size, entry_px, stop_px, tp_px))
        return {"ok": True, "entry": {"ok": True, "order_id": 1}, "sl": {"ok": True}, "tp": {"ok": True}}

    def place_hl_order(self, is_buy: bool, size: float, mid_price: float, coin: str = "BTC",
                       reduce_only: bool = False) -> Dict[str, Any]:
        self.calls.append(("ioc", coin, is_buy, size, reduce_only))
        return {"ok": True, "total_sz": size, "avg_px": mid_price}

    def place_hl_trigger_order(self, is_long_position: bool, size: float, trigger_px: float, kind: str,
                               coin: str = "BTC") -> Dict[str, Any]:
        self.calls.append(("trigger", coin, is_long_position, size, trigger_px, kind))
        return {"ok": True, "order_id": 77}

    def cancel_orders(self, oid: int, coin: Optional[str] = None, asset_idx: Optional[int] = None) -> Dict[str, Any]:
        self.calls.append(("cancel", oid, coin))
        return {"ok": True}

    def cancel_open_orders_for_coin(self, coin: str) -> int:
        self.calls.append(("cancel_all", coin))
        return len(self.orders)


def equity(value: float = 20.06, positions: Optional[List[Dict[str, Any]]] = None, ok: bool = True) -> Any:
    def read(user: str, coin: str) -> Dict[str, Any]:
        if not ok:
            return {"ok": False, "error": "HLRequestError: 429"}
        return {"ok": True, "user": user, "coin": coin, "mode": "unifiedAccount", "equity": value,
                "source": "spot USDC + unrealized P&L (unified account)", "positions": positions or []}
    return read


def stop_row(px: float, sz: float, oid: int = 11) -> Dict[str, Any]:
    return {"oid": oid, "coin": COIN, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": str(px),
            "sz": str(sz)}


def tp_row(px: float, sz: float, oid: int = 12) -> Dict[str, Any]:
    return {"oid": oid, "coin": COIN, "reduceOnly": True, "orderType": "Take Profit Limit", "triggerPx": str(px),
            "sz": str(sz)}


def entry_row(px: float, sz: float, side: str = "B", oid: int = 10) -> Dict[str, Any]:
    return {"oid": oid, "coin": COIN, "reduceOnly": False, "orderType": "Limit", "limitPx": str(px),
            "sz": str(sz), "side": side, "children": [{"oid": 11}, {"oid": 12}]}


def fill(t: int, side: str, sz: float, px: float, start: float, pnl: float = 0.0, fee: float = 0.01,
         tid: int = 0) -> Dict[str, Any]:
    return {"coin": COIN, "time": t, "side": side, "sz": str(sz), "px": str(px), "startPosition": str(start),
            "closedPnl": str(pnl), "fee": str(fee), "tid": tid or t}


def losing_trips(n: int, loss: float = 0.5, start: int = T0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(n):
        t = start + i * HOUR
        out.append(fill(t, "B", 1.0, 100.0, 0.0))
        out.append(fill(t + 60_000, "A", 1.0, 99.5, 1.0, pnl=-loss))
    return out
