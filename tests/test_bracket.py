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


@pytest.fixture(autouse=True)
def _no_target_offset(monkeypatch):
    """Most tests here pin stacking, leverage, sizing and wiring on tight
    hand-made setups; the 0.5% target offset has its own tests below."""
    monkeypatch.setattr(bracket, "TARGET_OFFSET_FRAC", 0.0)


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
        from pathiel.client.exchange import attached_child_oids
        attached = attached_child_oids(self.orders)
        return sum(1 for o in self.orders if o.get("reduceOnly") and int(o.get("oid") or 0) not in attached)

    def attached_child_oids(self, orders):
        from pathiel.client.exchange import attached_child_oids
        return attached_child_oids(orders)

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
        # the stop fires at market; the target is a limit at the target itself
        # (backtested 2026-10-28: never missed, no slippage on winners)
        assert leg["order_type"]["trigger"] == {"triggerPx": px, "isMarket": kind == "sl", "tpsl": kind}
        assert leg["limit_px"] == px


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
    import pathiel.client.exchange as ex
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xu")
    monkeypatch.setattr(br, "slot_taken_by", lambda user, coin, n: None)

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
                    "size": 1.27, "leverage": 10, "min_r": 1.5, "coin_max_leverage": 20.0, "dry_run": False,
                    "add": False}


def test_handler_add_sizes_the_top_up_as_full_port_minus_what_is_held(monkeypatch):
    """Operator, 2026-10-24: "add to position when possible". Long 1.62 held,
    full-port size 10.4 -> the top-up is 8.78 (sz_decimals 2), passed with
    add=True; a position already at full size places nothing and says so."""
    mcp = _mcp()
    from pathiel.agents import bracket as br
    import pathiel.client.exchange as ex
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xu")
    monkeypatch.setattr(ex, "_position_size_on_coin", lambda user, coin: 1.62)
    monkeypatch.setattr(br, "slot_taken_by", lambda user, coin, n: None)
    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (SimpleNamespace(max_leverage=20.0), [], None))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
    monkeypatch.setattr(mcp, "_live_equity", lambda: 64.5)
    import services.risk.size as size_mod
    monkeypatch.setattr(size_mod, "size_trade", lambda *a, **k: SimpleNamespace(
        can_trade=True, size=10.4, leverage=20.0, reason="", notional_usd=1158.6,
        margin_usd=57.9, risk_usd=7.3, risk_pct=11.3))
    # free margin on xyz: $40.30 of $64.50 equity. Margin plus the opening fee
    # uses all of it: 40.30 / (1 + 20 x 4.5 bps) x 20 / 111.4 = 7.17 contracts
    # (a 10% reserve until 2026-10-26, 2% until 2026-10-27)
    import pathiel.client.account_snapshot as snap
    monkeypatch.setattr(snap, "account_state", lambda user, include_hip3=True, **kw: {
        "equity": 64.5, "dex_available": {"": 0.0, "xyz": 40.3}})
    seen = {}
    monkeypatch.setattr(br, "place_bracket", lambda coin, **k: seen.update(coin=coin, **k) or {"ok": True, "placed": True})
    out = json.loads(mcp.handle_place_bracket(
        {"coin": COIN, "entry_px": 111.4, "stop_px": 110.9, "tp_px": 114.0, "add": True}))
    assert out["placed"] and out["topup"] == {"held": 1.62, "full_size": 10.4, "free_margin_usd": 40.3,
                                              "topup_cap_size": 7.17, "topup_size": 7.17}
    assert seen["size"] == 7.17 and seen["add"] is True and seen["leverage"] == 20
    # with plenty of free margin the top-up is simply full-port minus held
    monkeypatch.setattr(snap, "account_state", lambda user, include_hip3=True, **kw: {
        "equity": 64.5, "dex_available": {"xyz": 500.0}})
    out = json.loads(mcp.handle_place_bracket(
        {"coin": COIN, "entry_px": 111.4, "stop_px": 110.9, "tp_px": 114.0, "add": True}))
    assert out["topup"]["topup_size"] == 8.78 and seen["size"] == 8.78

    monkeypatch.setattr(ex, "_position_size_on_coin", lambda user, coin: 10.4)
    seen.clear()
    out = json.loads(mcp.handle_place_bracket(
        {"coin": COIN, "entry_px": 111.4, "stop_px": 110.9, "tp_px": 114.0, "add": "true"}))
    assert out["ok"] and not out["placed"] and "already at or above the full-port size" in out["reason"]
    assert not seen, "nothing is placed when the position is already full"


def test_handler_add_says_free_margin_not_full_size_when_margin_is_the_limit(monkeypatch):
    """2026-10-26, live: 8.58 held of a 9.5 full-port size, $5.19 free of $58
    equity under the old 10% floor. The refusal read "already at or above the
    full-port size", which was false. No reserve since 2026-10-27, only the
    opening fee; when free margin is still the limit the reason says so."""
    mcp = _mcp()
    from pathiel.agents import bracket as br
    import pathiel.client.exchange as ex
    import pathiel.client.account_snapshot as snap
    import services.risk.size as size_mod
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xu")
    monkeypatch.setattr(ex, "_position_size_on_coin", lambda user, coin: 8.58)
    monkeypatch.setattr(br, "slot_taken_by", lambda user, coin, n: None)
    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (SimpleNamespace(max_leverage=20.0), [], None))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
    monkeypatch.setattr(mcp, "_live_equity", lambda: 58.01)
    monkeypatch.setattr(size_mod, "size_trade", lambda *a, **k: SimpleNamespace(
        can_trade=True, size=10.35, leverage=20.0, reason="", notional_usd=1137.0,
        margin_usd=56.85, risk_usd=10.9, risk_pct=18.8))
    seen = {}
    monkeypatch.setattr(br, "place_bracket", lambda coin, **k: seen.update(coin=coin, **k) or {"ok": True, "placed": True})

    # the live numbers: $5.19 free -> 5.19 / 1.009 x 20 / 109.85 = 0.94
    # (0.73 under the 2% reserve, 0.25 under the old 10%)
    monkeypatch.setattr(snap, "account_state", lambda user, include_hip3=True, **kw: {
        "equity": 58.01, "dex_available": {"xyz": 5.19}})
    out = json.loads(mcp.handle_place_bracket(
        {"coin": COIN, "entry_px": 109.85, "stop_px": 108.8, "tp_px": 111.49, "add": True}))
    assert out["placed"] and out["topup"]["topup_size"] == 0.94 and seen["size"] == 0.94

    # two cents free buys under one size step: refused, and the reason names margin
    seen.clear()
    monkeypatch.setattr(snap, "account_state", lambda user, include_hip3=True, **kw: {
        "equity": 58.01, "dex_available": {"xyz": 0.02}})
    out = json.loads(mcp.handle_place_bracket(
        {"coin": COIN, "entry_px": 109.85, "stop_px": 108.8, "tp_px": 111.49, "add": True}))
    assert not out["placed"] and "no free margin to add" in out["reason"]
    assert "full-port" not in out["reason"] and not seen


def test_sizing_holds_back_only_the_opening_fee():
    """Operator, 2026-10-27: "the fees are taken out at time of trade against
    the position, ive literally used 100% of equity on hyperliquid". No
    reserve: margin plus the opening taker fee is the whole equity."""
    from services.risk.derive import VenueRules, margin_after_open_fee
    venue = VenueRules()
    assert venue.free_margin_floor_frac == 0.0 and venue.taker_fee_rate == 0.00045
    margin = margin_after_open_fee(72.76, 20, venue)
    fee = margin * 20 * venue.taker_fee_rate
    assert margin + fee == pytest.approx(72.76)
    assert 72.76 - margin < 0.01 * 72.76              # under 1% idle at 20x, was 2%
    assert margin_after_open_fee(-5, 20, venue) == 0.0


# ------------------------------------------------- the 20x rule (leverage gate)
def test_liq_estimate_matches_the_venue_within_fees():
    # Julien's 20x Brent long 2026-10-23: venue showed liquidationPx 104.4289.
    assert bracket.liq_estimate(107.16, 20, 20, True) == pytest.approx(104.41, abs=0.03)
    # The pathiel 10x long: venue showed 99.3422.
    assert bracket.liq_estimate(107.61, 10, 20, True) == pytest.approx(99.34, abs=0.02)


def test_tight_stop_passes_at_20x_wide_stop_does_not():
    ok = bracket.check_leverage(107.16, 106.70, 20, 20)
    assert ok["ok"] and ok["stop_frac_of_liq"] < 0.2 and ok["max_leverage_ok"] == 20
    bad = bracket.check_leverage(107.0, 101.65, 20, 20)        # a 5% stop at 20x: past liquidation
    assert not bad["ok"] and bad["stop_frac_of_liq"] > 1
    assert bad["max_leverage_ok"] == 8 and "at most 8x" in bad["reason"]


def test_max_leverage_for_stop_is_the_boundary():
    # The returned leverage passes; one more fails. Long and short.
    for entry, stop in ((107.0, 105.5), (100.0, 103.0)):
        lev = bracket.max_leverage_for_stop(entry, stop, 20)
        assert bracket.check_leverage(entry, stop, lev, 20)["ok"]
        if lev < 20:
            assert not bracket.check_leverage(entry, stop, lev + 1, 20)["ok"]


def test_place_bracket_refuses_a_stop_too_close_to_liquidation():
    ex = FakeEx()
    out = bracket.place_bracket(COIN, entry_px=107.0, stop_px=101.65, tp_px=116.0,   # 1.68R: passes the R gate
                                size=1.0, leverage=20, coin_max_leverage=20, ex=ex)
    assert not out["placed"] and "at most 8x" in out["reason"]
    assert out["liq"]["max_leverage_ok"] == 8
    assert ex.calls == []                                       # refused before touching the exchange


def test_place_bracket_passes_the_gate_and_reports_liquidation():
    ex = FakeEx()
    out = bracket.place_bracket(COIN, entry_px=107.20, stop_px=106.70, tp_px=108.45,
                                size=1.27, leverage=10, coin_max_leverage=20, ex=ex)
    assert out["placed"] and out["liq"]["liq_px"] == pytest.approx(98.95, abs=0.02)


def test_handler_passes_the_coin_max_leverage_through(monkeypatch):
    mcp = _mcp()
    from pathiel.agents import bracket as br
    import pathiel.client.exchange as ex
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xu")
    monkeypatch.setattr(br, "slot_taken_by", lambda user, coin, n: None)

    seen = {}
    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (SimpleNamespace(max_leverage=10.0), [], None))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
    monkeypatch.setattr(mcp, "_live_equity", lambda: 15.21)
    import services.risk.size as size_mod
    monkeypatch.setattr(size_mod, "size_trade", lambda *a, **k: SimpleNamespace(
        can_trade=True, size=1.27, leverage=10.0, reason="", notional_usd=1, margin_usd=1, risk_usd=1, risk_pct=1))
    monkeypatch.setattr(br, "place_bracket", lambda coin, **k: seen.update(k) or {"ok": True})
    mcp.handle_place_bracket({"coin": COIN, "entry_px": 107.2, "stop_px": 106.7, "tp_px": 108.45})
    assert seen["coin_max_leverage"] == 20.0


# ------------------------------------------- a resting entry's attached children
def test_sweep_leaves_a_resting_entrys_stop_and_target_alone():
    """2026-10-23: a bracket placed with grouping normalTpsl rests its stop and
    target as reduce-only children before the entry fills. On a flat coin the
    sweep used to read them as leftovers and would have cancelled them, leaving
    the entry naked on fill."""
    sl = {"oid": 2, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "107.4", "sz": "1.35"}
    tp = {"oid": 3, "reduceOnly": True, "orderType": "Take Profit Market", "triggerPx": "108.95", "sz": "1.35"}
    entry = {"oid": 1, "reduceOnly": False, "limitPx": "107.95", "sz": "1.35", "orderType": "Limit",
             "children": [sl, tp]}                              # as the venue returns it
    stale = {"oid": 9, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "106.7", "sz": "1.27"}
    ex = FakeEx(pos=0.0, orders=[entry, sl, tp, stale])
    out = bracket.sweep(COIN, ex=ex)
    assert out["ok"] and out["cancelled_stranded"] == 1          # only the stale one
    assert out["resting_entries"] == [{"oid": 1, "px": "107.95", "sz": "1.35"}]
    assert [c["px"] for c in out["attached_to_entry"]] == ["107.4", "108.95"]


def test_sweep_still_cancels_true_leftovers_when_nothing_rests():
    sl = {"oid": 2, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "106.7", "sz": "1.27"}
    ex = FakeEx(pos=0.0, orders=[sl])
    out = bracket.sweep(COIN, ex=ex)
    assert out["cancelled_stranded"] == 1 and ("sweep", COIN) in ex.calls and "attached_to_entry" not in out


def test_handler_refuses_when_venue_max_leverage_is_unknown(monkeypatch):
    """Wave 2 concern: the retired executor refused when leverage could not
    be resolved; _coin_specs swallows the failure into None. Without the
    venue cap the 20x rule cannot run, so the handler must refuse, not
    fall back to the profile cap and trade blind."""
    mcp = _mcp()
    from pathiel.agents import bracket as br

    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (SimpleNamespace(max_leverage=10.0), [], None))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (None, 2))
    monkeypatch.setattr(mcp, "_live_equity", lambda: 15.21)
    monkeypatch.setattr(br, "place_bracket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not place")))
    out = json.loads(mcp.handle_place_bracket({"coin": COIN, "entry_px": 107.2, "stop_px": 106.7, "tp_px": 108.45}))
    assert out["ok"] is False and out["placed"] is False and "not trading blind" in out["error"]



# ------------------------------------------------- the account-wide slot guard
def _raw(positions=(), entries=()):
    return {"state": {"assetPositions": [{"position": {"coin": c, "szi": sz}} for c, sz in positions]},
            "orders": [{"coin": c, "reduceOnly": False, "oid": i} for i, c in enumerate(entries)]}


def test_slot_guard_counts_positions_and_resting_entries_on_other_coins():
    fetch = lambda user, dex: _raw(positions=[("xyz:GOLD", "0.5")])
    assert bracket.slot_taken_by("0xu", "xyz:BRENTOIL", 1, fetch_raw=fetch, dexes=["xyz"]) == "xyz:GOLD"
    fetch = lambda user, dex: _raw(entries=["xyz:NVDA"])
    assert bracket.slot_taken_by("0xu", "xyz:BRENTOIL", 1, fetch_raw=fetch, dexes=["xyz"]) == "xyz:NVDA"


def test_slot_guard_ignores_the_same_coin_and_reduce_only_rows():
    fetch = lambda user, dex: {"state": {"assetPositions": [{"position": {"coin": "xyz:BRENTOIL", "szi": "1.3"}}]},
                               "orders": [{"coin": "xyz:GOLD", "reduceOnly": True, "oid": 1}]}
    assert bracket.slot_taken_by("0xu", "xyz:BRENTOIL", 1, fetch_raw=fetch, dexes=["xyz"]) is None


def test_slot_guard_respects_more_slots_and_a_flat_account():
    fetch = lambda user, dex: _raw(positions=[("xyz:GOLD", "0.5")])
    assert bracket.slot_taken_by("0xu", "xyz:BRENTOIL", 2, fetch_raw=fetch, dexes=["xyz"]) is None
    assert bracket.slot_taken_by("0xu", "xyz:BRENTOIL", 1, fetch_raw=lambda u, d: _raw(), dexes=["", "xyz"]) is None


def test_handler_refuses_when_another_desk_holds_the_slot(monkeypatch):
    mcp = _mcp()
    from pathiel.agents import bracket as br
    import pathiel.client.exchange as ex

    monkeypatch.setattr(mcp, "_risk_inputs", lambda: (SimpleNamespace(max_leverage=10.0, max_open_positions=1), [], None))
    monkeypatch.setattr(mcp, "_coin_specs", lambda c: (20.0, 2))
    monkeypatch.setattr(mcp, "_live_equity", lambda: 15.21)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xu")
    monkeypatch.setattr(br, "slot_taken_by", lambda user, coin, n: "xyz:GOLD")
    monkeypatch.setattr(br, "place_bracket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not place")))
    out = json.loads(mcp.handle_place_bracket({"coin": COIN, "entry_px": 107.2, "stop_px": 106.7, "tp_px": 108.45}))
    assert out["placed"] is False and "slot taken by xyz:GOLD" in out["error"]


# ── add=True: the one sanctioned way to stack (operator, 2026-10-24) ─────────

STOP_ROW = {"coin": COIN, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "110.9", "sz": "1.62", "oid": 5}
TP_ROW = {"coin": COIN, "reduceOnly": True, "orderType": "Take Profit Market", "triggerPx": "114.0", "sz": "1.62", "oid": 6}


def test_add_tops_up_a_same_side_position_and_leaves_its_protection_alone():
    ex = FakeEx(pos=1.62, orders=[STOP_ROW, TP_ROW])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=8.8,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert out["placed"] and out["add"] is True and out["position_before"] == 1.62
    assert ("sweep", COIN) not in ex.calls, "a top-up must never cancel the live position's stop and target"
    assert ("bracket", COIN, True, 8.8, 111.40, 110.90, 114.0) in ex.calls


def test_add_refuses_the_opposite_side():
    ex = FakeEx(pos=1.62, orders=[STOP_ROW])
    out = bracket.place_bracket(COIN, entry_px=112.5, stop_px=113.2, tp_px=110.0, size=2,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert not out["placed"] and "call is short" in out["reason"] and not ex.calls


def test_add_never_widens_the_resting_stop():
    ex = FakeEx(pos=1.62, orders=[STOP_ROW, TP_ROW])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.50, tp_px=114.0, size=8.8,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert not out["placed"] and "never widen" in out["reason"] and not ex.calls
    # a tighter stop is fine
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=111.00, tp_px=114.0, size=8.8,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert out["placed"]


def test_add_still_refuses_while_a_top_up_already_rests():
    resting = {"coin": COIN, "reduceOnly": False, "limitPx": "111.4", "sz": "8.8", "oid": 7}
    ex = FakeEx(pos=1.62, orders=[STOP_ROW, resting])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=8.8,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert not out["placed"] and "already resting" in out["reason"]


def test_add_on_a_flat_coin_is_a_normal_entry():
    ex = FakeEx(pos=0.0, orders=[{"coin": COIN, "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "100", "sz": "1", "oid": 9}])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=10.4,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert out["placed"] and "add" not in out
    assert ("sweep", COIN) in ex.calls, "flat: the stale leftover IS cancelled, as always"


def test_without_add_a_position_still_blocks_a_second_trade():
    ex = FakeEx(pos=1.62, orders=[STOP_ROW])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=8.8,
                                leverage=20, coin_max_leverage=20, ex=ex)
    assert not out["placed"] and "already in a position" in out["reason"]


# ----------------------------------------- one stop and one target per position
class BookEx(FakeEx):
    """FakeEx whose resting orders change as orders are placed and cancelled."""
    def __init__(self, pos, orders, refuse=()):
        super().__init__(pos=pos, orders=[dict(o) for o in orders])
        self.refuse, self.next_oid = set(refuse), 900

    def place_hl_trigger_order(self, is_long, size, px, kind, coin):
        self.calls.append((kind, is_long, size, px))
        if kind in self.refuse:
            return {"ok": False, "error": "venue said no"}
        self.next_oid += 1
        self.orders.append({"oid": self.next_oid, "coin": coin, "reduceOnly": True, "sz": str(size),
                            "triggerPx": str(px),
                            "orderType": "Stop Market" if kind == "sl" else "Take Profit Limit"})
        return {"ok": True, "order_id": self.next_oid}

    def cancel_orders(self, oid, coin=None):
        self.calls.append(("cancel", oid))
        self.orders = [o for o in self.orders if int(o.get("oid") or 0) != oid]
        return {"ok": True}


def _leg(oid, kind, sz, px):
    return {"oid": oid, "coin": COIN, "reduceOnly": True, "sz": str(sz), "triggerPx": str(px),
            "orderType": "Stop Market" if kind == "sl" else "Take Profit Market"}


# the live book on 2026-10-28: three fills, three stops and three targets
LIVE_LEGS = [_leg(1, "sl", 11.84, 108.95), _leg(2, "tp", 11.84, 113.75),
             _leg(3, "sl", 1.05, 108.95), _leg(4, "tp", 1.05, 113.75),
             _leg(5, "sl", 0.19, 108.95), _leg(6, "tp", 0.19, 113.75)]


def test_three_fills_become_one_stop_and_one_target():
    """Operator, 2026-10-28: "IT SEEMS REDUNDANT": 108.95 x3, 113.75 x3."""
    ex = BookEx(pos=13.08, orders=LIVE_LEGS)
    out = bracket.consolidate_protection(COIN, ex=ex)
    assert out["ok"] and [m["kind"] for m in out["merged"]] == ["sl", "tp"]
    stops = [o for o in ex.orders if "Stop" in o["orderType"]]
    tps = [o for o in ex.orders if "Take Profit" in o["orderType"]]
    assert [(o["sz"], o["triggerPx"]) for o in stops] == [("13.08", "108.95")]
    assert [(o["sz"], o["triggerPx"]) for o in tps] == [("13.08", "113.75")]
    # the new stop goes on BEFORE any old one is cancelled: never unprotected
    first_cancel = next(i for i, c in enumerate(ex.calls) if c[0] == "cancel")
    assert ex.calls.index(("sl", True, 13.08, 108.95)) < first_cancel


def test_a_refused_merge_keeps_the_old_stops():
    ex = BookEx(pos=13.08, orders=LIVE_LEGS, refuse={"sl"})
    out = bracket.consolidate_protection(COIN, ex=ex)
    assert not out["ok"] and "old ones stay" in out["left"][0]
    assert sum(float(o["sz"]) for o in ex.orders if "Stop" in o["orderType"]) == pytest.approx(13.08)
    assert not any(c == ("cancel", 1) or c == ("cancel", 3) or c == ("cancel", 5) for c in ex.calls)


def test_targets_at_different_prices_are_a_scale_out_and_stay():
    legs = [_leg(1, "sl", 10, 108.95), _leg(2, "tp", 5, 112.0), _leg(3, "tp", 5, 114.0)]
    ex = BookEx(pos=10, orders=legs)
    out = bracket.consolidate_protection(COIN, ex=ex)
    assert out["merged"] == [] and "scale-out" in out["left"][0] and len(ex.orders) == 3


def test_a_short_merges_on_the_short_side_and_flat_does_nothing():
    legs = [_leg(1, "sl", 2, 115.0), _leg(2, "sl", 1, 115.0)]
    ex = BookEx(pos=-3, orders=legs)
    bracket.consolidate_protection(COIN, ex=ex)
    assert ("sl", False, 3, 115.0) in ex.calls
    flat = BookEx(pos=0.0, orders=legs)
    assert bracket.consolidate_protection(COIN, ex=flat)["merged"] == [] and not flat.calls


def test_sweep_folds_the_fills_into_one_pair_and_reports_it():
    ex = BookEx(pos=13.08, orders=LIVE_LEGS)
    out = bracket.sweep(COIN, ex=ex)
    assert out["ok"] and out["stop_px"] == ["108.95"] and out["tp_px"] == ["113.75"]
    assert out["stop_covers"] == pytest.approx(13.08) and len(out["consolidated"]["merged"]) == 2
    # an under-covered position is never touched, only flagged
    naked = BookEx(pos=13.08, orders=[_leg(1, "sl", 1.05, 108.95), _leg(2, "sl", 0.19, 108.95)])
    out = bracket.sweep(COIN, ex=naked)
    assert not out["ok"] and "consolidated" not in out and not naked.calls


def test_a_market_top_up_ends_with_one_stop_and_one_target():
    legs = [_leg(1, "sl", 11.84, 108.95), _leg(2, "tp", 11.84, 113.75)]
    ex = BookEx(pos=11.84, orders=legs)
    ex.fill, ex.mid = 1.05, 109.54
    ex._position_size_on_coin = lambda user, coin: 11.84 if not any(c[0] == "ioc" for c in ex.calls) else 12.89
    out = bracket.place_bracket(COIN, entry_px=None, stop_px=108.95, tp_px=113.75, size=1.05,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert out["placed"] and len(out["consolidated"]["merged"]) == 2
    assert [(o["sz"], o["triggerPx"]) for o in ex.orders if "Stop" in o["orderType"]] == [("12.89", "108.95")]


def test_a_standalone_target_is_a_limit_and_a_stop_is_market(monkeypatch):
    """Top-ups and the one-pair merge place their stop and target through
    place_hl_trigger_order: same rule as the bracket."""
    from pathiel.client import exchange
    sent = []

    class FakeSdk:
        def order(self, coin, is_buy, sz, limit_px, order_type, reduce_only=False):
            sent.append((is_buy, sz, limit_px, order_type, reduce_only))
            return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 7}}]}}}

    monkeypatch.setattr(exchange, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(exchange, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(exchange, "_make_exchange", lambda: FakeSdk())
    assert exchange.place_hl_trigger_order(True, 13.08, 113.75, "tp", COIN)["ok"]
    assert exchange.place_hl_trigger_order(True, 13.08, 108.95, "sl", COIN)["ok"]
    (tp_buy, _, tp_px, tp_type, tp_ro), (sl_buy, _, sl_px, sl_type, sl_ro) = sent
    assert tp_type["trigger"] == {"triggerPx": 113.75, "isMarket": False, "tpsl": "tp"} and tp_px == 113.75
    assert sl_type["trigger"] == {"triggerPx": 108.95, "isMarket": True, "tpsl": "sl"}
    assert tp_buy is False and sl_buy is False and tp_ro and sl_ro       # closing a long, reduce-only


def test_a_lone_market_target_becomes_a_limit_and_a_limit_is_left_alone():
    """2026-10-28: targets are limits now; the position's existing take-profit
    market (13.08 @ 113.75) is re-placed as a limit, new order first."""
    legs = [_leg(1, "sl", 13.08, 108.95), _leg(2, "tp", 13.08, 113.75)]     # tp is a market trigger
    ex = BookEx(pos=13.08, orders=legs)
    out = bracket.sweep(COIN, ex=ex)
    assert out["ok"] and out["consolidated"]["merged"] == [
        {"kind": "tp", "px": 113.75, "size": 13.08, "replaced": 1, "to_limit": True}]
    tps = [o for o in ex.orders if "Take Profit" in o["orderType"]]
    assert [(o["orderType"], o["sz"], o["triggerPx"]) for o in tps] == [("Take Profit Limit", "13.08", "113.75")]
    assert ex.calls.index(("tp", True, 13.08, 113.75)) < ex.calls.index(("cancel", 2))
    # the stop is untouched, and a second sweep changes nothing
    assert [o["oid"] for o in ex.orders if "Stop" in o["orderType"]] == [1]
    ex.calls.clear()
    out = bracket.sweep(COIN, ex=ex)
    assert "consolidated" not in out and not ex.calls


def test_a_top_up_never_changes_the_open_positions_leverage():
    """Live, 2026-10-28 16:08Z: a top-up asked for 19x and set_leverage ran before the
    order; the venue refused the order, but the 13.08 Brent position was left at 19x."""
    ex = FakeEx(pos=13.08, orders=[STOP_ROW, TP_ROW])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=1.0,
                                leverage=19, coin_max_leverage=20, add=True, ex=ex)
    assert out["placed"]
    assert not any(c[0] == "lev" for c in ex.calls), "a top-up must not re-lever the open position"
    # a fresh entry still sets its leverage
    flat = FakeEx(pos=0.0)
    bracket.place_bracket(COIN, entry_px=107.2, stop_px=106.7, tp_px=108.45, size=1.27,
                          leverage=20, coin_max_leverage=20, ex=flat)
    assert ("lev", COIN, 20, False) in flat.calls


def test_an_order_under_the_venue_minimum_is_refused_before_any_live_call():
    ex = FakeEx(pos=13.08, orders=[STOP_ROW, TP_ROW])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=0.07,
                                leverage=19, coin_max_leverage=20, add=True, ex=ex)
    assert not out["placed"] and "under the venue's $10 minimum" in out["reason"]
    assert not ex.calls, "nothing reaches the venue"


# ------------------------------------------------- the 0.5% target offset
def test_offset_target_matches_the_backtest_formula():
    from services.strategies.backtest.desk_tick_sim import offset_target as sim_offset
    for side in ("long", "short"):
        for px in (113.75, 98.2):
            assert bracket.offset_target(px, side, 0.005) == pytest.approx(sim_offset(px, side, 0.005))
    assert bracket.offset_target(113.75, "long", 0.005) == pytest.approx(113.18125)
    assert bracket.offset_target(100.0, "short", 0.005) == pytest.approx(100.5)
    assert bracket.offset_target(113.75, "long", 0.0) == 113.75


def test_a_new_bracket_takes_profit_0_5_percent_inside_the_structure(monkeypatch):
    """Operator, 2026-10-29: adopt the sweep's best combo (0.5% closer target)."""
    monkeypatch.setattr(bracket, "TARGET_OFFSET_FRAC", 0.005)
    ex = FakeEx(pos=0.0)
    out = bracket.place_bracket(COIN, entry_px=109.45, stop_px=108.95, tp_px=113.75, size=13.0,
                                leverage=20, coin_max_leverage=20, ex=ex)
    assert out["placed"] and out["structure_tp_px"] == 113.75
    assert out["tp_px"] == pytest.approx(113.18125)
    assert ex.calls[-1][-1] == pytest.approx(113.18125)          # the bracket carries the offset target
    # a short goes the other way
    ex = FakeEx(pos=0.0, mid=110.0)
    out = bracket.place_bracket(COIN, entry_px=110.0, stop_px=110.6, tp_px=106.0, size=1.0,
                                leverage=20, coin_max_leverage=20, ex=ex)
    assert out["tp_px"] == pytest.approx(106.53)


def test_the_offset_can_push_a_thin_setup_under_1_5r_and_it_is_refused(monkeypatch):
    monkeypatch.setattr(bracket, "TARGET_OFFSET_FRAC", 0.005)
    ex = FakeEx(pos=0.0)
    out = bracket.place_bracket(COIN, entry_px=107.2, stop_px=106.7, tp_px=108.45, size=1.27,
                                leverage=20, coin_max_leverage=20, ex=ex)
    assert not out["placed"] and "1.5R" in out["reason"] and not ex.calls


def test_a_top_up_keeps_the_positions_own_target(monkeypatch):
    monkeypatch.setattr(bracket, "TARGET_OFFSET_FRAC", 0.005)
    ex = FakeEx(pos=1.62, orders=[STOP_ROW, TP_ROW])
    out = bracket.place_bracket(COIN, entry_px=111.40, stop_px=110.90, tp_px=114.0, size=1.0,
                                leverage=20, coin_max_leverage=20, add=True, ex=ex)
    assert out["placed"] and out["tp_px"] == 114.0 and "structure_tp_px" not in out


def test_the_sims_live_params_match_the_live_bracket_rules():
    """The backtest's idea of 'live' must not drift from what place_bracket does."""
    from services.strategies.backtest.desk_tick_sim import live_params
    lp = live_params()
    import importlib
    real = importlib.reload(bracket)      # the autouse fixture zeroed the module constant
    assert lp.target_offset_frac == real.TARGET_OFFSET_FRAC == 0.005
    assert lp.stop_buffer == 0.75 and lp.target_mode == "limit" and lp.leverage == 20.0
    agent = (Path(__file__).resolve().parents[1] / ".claude" / "agents" / "desk-tick.md").read_text()
    assert "Stop 0.75 beyond the structure on Brent" in agent


# ------------------------------------------------- set_target
def test_set_target_moves_the_take_profit_new_order_first():
    """2026-10-29: the live 13.08 long moves from 113.75 to the 0.5% offset target."""
    ex = BookEx(pos=13.08, orders=[_leg(1, "sl", 13.08, 108.95), _leg(2, "tp", 13.08, 113.75)])
    ex.mid = 109.86
    out = bracket.set_target(COIN, 113.18, ex=ex)
    assert out["ok"] and out["replaced"] == ["113.75"]
    tps = [(o["sz"], o["triggerPx"]) for o in ex.orders if "Take Profit" in o["orderType"]]
    assert tps == [("13.08", "113.18")]
    assert ex.calls.index(("tp", True, 13.08, 113.18)) < ex.calls.index(("cancel", 2))
    assert [o["oid"] for o in ex.orders if "Stop" in o["orderType"]] == [1]   # stop untouched


def test_set_target_refuses_the_wrong_side_and_a_flat_coin_and_keeps_the_old_one():
    ex = BookEx(pos=13.08, orders=[_leg(2, "tp", 13.08, 113.75)])
    ex.mid = 109.86
    out = bracket.set_target(COIN, 109.0, ex=ex)
    assert not out["ok"] and "wrong side" in out["reason"] and not ex.calls
    flat = BookEx(pos=0.0, orders=[])
    assert not bracket.set_target(COIN, 113.0, ex=flat)["ok"]
    refused = BookEx(pos=13.08, orders=[_leg(2, "tp", 13.08, 113.75)], refuse={"tp"})
    refused.mid = 109.86
    out = bracket.set_target(COIN, 113.18, ex=refused)
    assert not out["ok"] and "old kept" in out["reason"]
    assert [o["oid"] for o in refused.orders] == [2]
