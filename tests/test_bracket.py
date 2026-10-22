"""pathiel.agents.bracket and exchange.place_hl_bracket_order.

Regression for 2026-10-22/23: Claude-placed oil trades went through ad-hoc
scripts because `execute` swaps in its own ATR stop/target and config
leverage; a pullback entry became a chase (2.5R -> 0.8R) while waiting on
permission; and after a stop or target fired, its sibling kept resting.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pathiel.agents import bracket

COIN = "xyz:BRENTOIL"


class FakeEx:
    def __init__(self, pos=0.0, orders=None, mid=107.6, fill=1.27, sl_ok=True):
        self.pos, self.orders, self.mid, self.fill, self.sl_ok = pos, orders or [], mid, fill, sl_ok
        self.calls = []

    def get_hl_price(self, coin):
        return self.mid

    def resolve_user_address(self):
        return "0xabc"

    def _position_size_on_coin(self, user, coin):
        return self.pos

    def _resting_orders_for_coin(self, user, coin):
        return self.orders

    def cancel_stranded_reduce_only_orders(self, coin):
        self.calls.append(("sweep", coin))
        return 1

    def set_leverage(self, coin, lev, is_cross=None, strict=False):
        self.calls.append(("lev", coin, lev, is_cross))
        return {"ok": True}

    def place_hl_bracket_order(self, coin, is_buy, size, entry, stop, tp):
        self.calls.append(("bracket", coin, is_buy, size, entry, stop, tp))
        return {"ok": True, "entry": {"ok": True, "order_id": "1"}, "sl": {"ok": True}, "tp": {"ok": True}}

    def place_hl_order(self, is_buy, size, mid, coin):
        self.calls.append(("ioc", is_buy, size, mid, coin))
        return {"ok": True, "avg_px": mid, "total_sz": self.fill}

    def place_hl_trigger_order(self, is_long, size, px, kind, coin):
        self.calls.append((kind, is_long, size, px))
        return {"ok": self.sl_ok if kind == "sl" else True}


# ------------------------------------------------------------------ R gate
@pytest.mark.parametrize("entry,stop,tp,side,r", [
    (107.20, 106.70, 108.45, "long", 2.5),
    (100.0, 102.0, 95.0, "short", 2.5),
])
def test_check_levels_side_and_r(entry, stop, tp, side, r):
    g = bracket.check_levels(entry, stop, tp)
    assert g["ok"] and g["side"] == side and g["r_multiple"] == r


def test_the_chase_that_happened_is_refused():
    # 2026-10-23: market at 107.61 with stop 106.70 / tp 108.45 paid 0.92R.
    g = bracket.check_levels(107.61, 106.70, 108.45)
    assert not g["ok"] and "under the 1.5R minimum" in g["reason"]


def test_the_desk_call_that_paid_047r_is_refused():
    g = bracket.check_levels(107.0, 95.87, 112.19)
    assert not g["ok"] and g["r_multiple"] == 0.47


@pytest.mark.parametrize("entry,stop,tp", [(107, 108, 109), (107, 106, 105), (107, 107, 109)])
def test_check_levels_rejects_entry_outside_stop_and_target(entry, stop, tp):
    assert not bracket.check_levels(entry, stop, tp)["ok"]


# ------------------------------------------------------------- placement
def test_limit_bracket_places_one_attached_order_at_set_leverage():
    ex = FakeEx()
    out = bracket.place_bracket(COIN, entry_px=107.20, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, ex=ex)
    assert out["ok"] and out["placed"] and out["mode"] == "limit"
    assert ("lev", COIN, 10, False) in ex.calls
    assert ("bracket", COIN, True, 1.27, 107.20, 106.70, 108.45) in ex.calls
    assert ("sweep", COIN) in ex.calls          # stale triggers cleared first
    assert not any(c[0] == "ioc" for c in ex.calls)


def test_market_bracket_checks_r_against_the_live_mid():
    ex = FakeEx(mid=107.61)
    out = bracket.place_bracket(COIN, entry_px=None, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, ex=ex)
    assert not out["placed"] and out["r_multiple"] == 0.92
    assert ex.calls == []                       # nothing touched the exchange


def test_market_bracket_protects_the_filled_size():
    ex = FakeEx(mid=107.20, fill=0.9)           # partial fill
    out = bracket.place_bracket(COIN, entry_px=None, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, ex=ex)
    assert out["ok"] and out["filled_sz"] == 0.9
    assert ("sl", True, 0.9, 106.70) in ex.calls and ("tp", True, 0.9, 108.45) in ex.calls


def test_market_fill_without_a_stop_is_loud():
    ex = FakeEx(mid=107.20, sl_ok=False)
    out = bracket.place_bracket(COIN, entry_px=None, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, ex=ex)
    assert out["placed"] and not out["ok"] and "WITHOUT A STOP" in out["reason"]


@pytest.mark.parametrize("ex,why", [
    (FakeEx(pos=1.27), "already in a position"),
    (FakeEx(pos=None), "position read failed"),
    (FakeEx(orders=[{"oid": 5, "reduceOnly": False}]), "already resting"),
])
def test_refuses_to_stack_a_second_trade(ex, why):
    out = bracket.place_bracket(COIN, entry_px=107.20, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, ex=ex)
    assert not out["placed"] and why in out["reason"]
    assert not any(c[0] in ("bracket", "ioc", "lev") for c in ex.calls)


def test_dry_run_places_nothing():
    ex = FakeEx()
    out = bracket.place_bracket(COIN, entry_px=107.20, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, dry_run=True, ex=ex)
    assert out["ok"] and out["dry_run"] and not out["placed"] and ex.calls == []


# ------------------------------------------------------------------ sweep
def test_sweep_on_a_flat_coin_cancels_leftovers_and_lists_entries():
    ex = FakeEx(pos=0.0, orders=[{"oid": 9, "reduceOnly": False, "limitPx": "107.2", "sz": "1.27"},
                                 {"oid": 8, "reduceOnly": True, "orderType": "Stop Market"}])
    out = bracket.sweep(COIN, ex=ex)
    assert out["cancelled_stranded"] == 1 and out["resting_entries"][0]["oid"] == 9


def test_sweep_flags_an_unprotected_position():
    ex = FakeEx(pos=1.27, orders=[{"reduceOnly": True, "orderType": "Take Profit Market",
                                   "sz": "1.27", "triggerPx": "108.45"}])
    out = bracket.sweep(COIN, ex=ex)
    assert not out["ok"] and "NOT FULLY COVERED" in out["reason"]
    assert ("sweep", COIN) not in ex.calls      # never cancels a live position's orders


def test_sweep_passes_a_covered_position():
    ex = FakeEx(pos=1.27, orders=[{"reduceOnly": True, "orderType": "Stop Market",
                                   "sz": "1.27", "triggerPx": "106.7"}])
    assert bracket.sweep(COIN, ex=ex)["ok"]


# ------------------------------------------------- the wire sent to Hyperliquid
def test_exchange_bracket_is_one_normaltpsl_action(monkeypatch):
    from pathiel.client import exchange

    sent = {}

    class FakeSdk:
        def bulk_orders(self, reqs, grouping="na"):
            sent["reqs"], sent["grouping"] = reqs, grouping
            return {"status": "ok", "response": {"data": {"statuses": [
                {"resting": {"oid": 1}}, "waitingForFill", "waitingForFill"]}}}

    monkeypatch.setattr(exchange, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(exchange, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(exchange, "_make_exchange", lambda: FakeSdk())
    out = exchange.place_hl_bracket_order(COIN, True, 1.27, 107.2, 106.7, 108.45)

    assert out["ok"] and out["entry"]["order_id"] == "1"
    assert sent["grouping"] == "normalTpsl"
    entry, sl, tp = sent["reqs"]
    assert entry["is_buy"] is True and entry["reduce_only"] is False and entry["sz"] == 1.27
    assert entry["order_type"] == {"limit": {"tif": "Gtc"}}
    for leg, kind, px in ((sl, "sl", 106.7), (tp, "tp", 108.45)):
        assert leg["is_buy"] is False and leg["reduce_only"] is True and leg["sz"] == 1.27
        assert leg["order_type"]["trigger"] == {"triggerPx": px, "isMarket": True, "tpsl": kind}


def test_exchange_bracket_reports_a_rejected_leg(monkeypatch):
    from pathiel.client import exchange

    class FakeSdk:
        def bulk_orders(self, reqs, grouping="na"):
            return {"status": "ok", "response": {"data": {"statuses": [
                {"error": "Insufficient margin"}, "waitingForFill", "waitingForFill"]}}}

    monkeypatch.setattr(exchange, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(exchange, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(exchange, "_make_exchange", lambda: FakeSdk())
    out = exchange.place_hl_bracket_order(COIN, True, 1.27, 107.2, 106.7, 108.45)
    assert not out["ok"] and "Insufficient margin" in out["error"]


# ------------------------------------------------------------- MCP surface
def _mcp():
    script = Path(__file__).resolve().parents[1] / "scripts" / "pathiel-mcp-server.py"
    spec = importlib.util.spec_from_file_location("pathiel_mcp_bracket_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tools_are_registered_as_live_money():
    mcp = _mcp()
    tools = {t["name"]: t for t in mcp.TOOLS}
    for name in ("place_bracket", "bracket_sweep"):
        assert tools[name]["annotations"] == {"destructiveHint": True}
        assert tools[name]["description"].startswith("LIVE MONEY")


def test_handler_sizes_then_places(monkeypatch):
    mcp = _mcp()
    from pathiel.agents import bracket as br

    seen = {}
    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (SimpleNamespace(max_leverage=10.0), [], None))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
    monkeypatch.setattr(mcp, "_live_equity", lambda: 15.21)
    import services.risk.size as size_mod
    monkeypatch.setattr(size_mod, "size_trade", lambda *a, **k: SimpleNamespace(
        can_trade=True, size=1.27, leverage=10.0, reason="", notional_usd=136.1,
        margin_usd=13.6, risk_usd=0.64, risk_pct=4.2))
    monkeypatch.setattr(br, "place_bracket", lambda coin, **k: seen.update(coin=coin, **k) or {"ok": True})
    out = json.loads(mcp.handle_place_bracket(
        {"coin": "xyz:BRENTOIL", "entry_px": 107.2, "stop_px": 106.7, "tp_px": 108.45}))
    assert out["ok"] and out["sizing"]["risk_usd"] == 0.64
    assert seen == {"coin": COIN, "entry_px": 107.2, "stop_px": 106.7, "tp_px": 108.45,
                    "size": 1.27, "leverage": 10, "min_r": 1.5, "dry_run": False}
