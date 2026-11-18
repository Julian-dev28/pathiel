"""Enforcement, in code: close_position, cancel_order, set_stop, set_target,
place_bracket(add=true) and bracket_sweep in scripts/pathiel-mcp-server.py
all refuse to act on a position or order the desk did not place, unless the
caller passes operator_override=true in that same call.

2026-11-17 23:25Z: a desk tick found the operator's own hand-placed Brent
long, sweep_brackets reported it "NOT FULLY COVERED", and the tick called
close_position on it. Until now the only thing stopping a repeat was a text
note pasted into every tick prompt. These are the regression tests for the
structural fix. Gate tests: every Hyperliquid call monkeypatched, no network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "pathiel-mcp-server.py"
COIN = "xyz:BRENTOIL"


def _load():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_ownership", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _req(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _call(name, args=None):
    resp = M.dispatch_message(_req("tools/call", {"name": name, "arguments": args or {}}))
    return resp["result"], json.loads(resp["result"]["content"][0]["text"])


OPERATOR_OWNED = {"coin": COIN, "flat": False, "desk_owned": False, "size": 14.66,
                  "entry_px": 90.7165, "leverage": 20}
DESK_OWNED = {"coin": COIN, "flat": False, "desk_owned": True, "size": 14.66,
             "entry_px": 90.7165, "leverage": 20}
FLAT = {"coin": COIN, "flat": True, "desk_owned": True, "size": 0.0, "entry_px": None, "leverage": None}


@pytest.fixture()
def operator_position(monkeypatch):
    """Every ownership check in this test sees an operator-owned position."""
    from pathiel.agents import ownership
    monkeypatch.setattr(ownership, "check_position_ownership", lambda coin, **kw: dict(OPERATOR_OWNED))
    return ownership


@pytest.fixture()
def desk_position(monkeypatch):
    from pathiel.agents import ownership
    monkeypatch.setattr(ownership, "check_position_ownership", lambda coin, **kw: dict(DESK_OWNED))
    return ownership


@pytest.fixture()
def flat_coin(monkeypatch):
    from pathiel.agents import ownership
    monkeypatch.setattr(ownership, "check_position_ownership", lambda coin, **kw: dict(FLAT))
    return ownership


# ── close_position ───────────────────────────────────────────────────────────

def test_close_position_refuses_an_operator_position(operator_position):
    result, body = _call("close_position", {"coin": COIN})
    assert body["ok"] is False and body.get("operator_position") is True
    assert "not placed by the desk" in body["error"] and "close_position" in body["error"]
    assert result["isError"] is True


def test_close_position_proceeds_with_operator_override(operator_position, monkeypatch):
    from pathiel.agents import bracket
    monkeypatch.setattr(bracket, "close_position",
                        lambda coin, **kw: {"ok": True, "coin": coin, "noop": None})
    _, body = _call("close_position", {"coin": COIN, "operator_override": True})
    assert "operator_position" not in body


def test_close_position_proceeds_on_a_flat_coin(flat_coin, monkeypatch):
    from pathiel.agents import bracket
    monkeypatch.setattr(bracket, "close_position", lambda coin, **kw: {"ok": True, "noop": "already_flat"})
    _, body = _call("close_position", {"coin": COIN})
    assert "operator_position" not in body


def test_close_position_proceeds_on_a_desk_owned_position(desk_position, monkeypatch):
    from pathiel.agents import bracket
    monkeypatch.setattr(bracket, "close_position",
                        lambda coin, **kw: {"ok": True, "coin": coin, "fill_px": 90.0})
    _, body = _call("close_position", {"coin": COIN})
    assert body["closed"] is True and "operator_position" not in body


# ── set_stop / set_target ────────────────────────────────────────────────────

def test_set_stop_refuses_an_operator_position(operator_position):
    _, body = _call("set_stop", {"coin": COIN, "stop_px": 90.0})
    assert body["ok"] is False and body.get("operator_position") is True
    assert "set_stop" in body["error"]


def test_set_target_refuses_an_operator_position(operator_position):
    _, body = _call("set_target", {"coin": COIN, "tp_px": 95.0})
    assert body["ok"] is False and body.get("operator_position") is True
    assert "set_target" in body["error"]


def test_set_stop_proceeds_with_operator_override(operator_position, monkeypatch):
    from pathiel.agents import bracket
    monkeypatch.setattr(bracket, "set_stop", lambda coin, px: {"ok": True, "coin": coin, "stop_px": px})
    _, body = _call("set_stop", {"coin": COIN, "stop_px": 90.0, "operator_override": True})
    assert body["ok"] is True and "operator_position" not in body


# ── cancel_order ─────────────────────────────────────────────────────────────

def test_cancel_order_refuses_an_order_with_no_cloid_and_no_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from pathiel.client import exchange as ex
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr(ex, "_resting_orders_for_coin",
                        lambda user, coin: [{"oid": 777, "coin": coin, "cloid": None}])
    _, body = _call("cancel_order", {"order_id": 777, "coin": COIN})
    assert body["cancelled"] is False and body.get("operator_order") is True
    assert "777" in body["error"]


def test_cancel_order_proceeds_for_a_desk_tagged_order(monkeypatch):
    from pathiel.client import exchange as ex
    from pathiel.agents import ownership
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr(ex, "_resting_orders_for_coin",
                        lambda user, coin: [{"oid": 778, "coin": coin, "cloid": ownership.make_cloid()}])
    monkeypatch.setattr(ex, "cancel_orders", lambda oid, coin=None, asset_idx=None: {"ok": True})
    _, body = _call("cancel_order", {"order_id": 778, "coin": COIN})
    assert body["cancelled"] is True


def test_cancel_order_proceeds_for_an_order_journalled_by_oid(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from pathiel.client import exchange as ex
    from pathiel.agents import ownership
    ownership.record_desk_order(COIN, ownership.make_cloid(), "entry", oid=779)
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr(ex, "_resting_orders_for_coin",
                        lambda user, coin: [{"oid": 779, "coin": coin, "cloid": None}])
    monkeypatch.setattr(ex, "cancel_orders", lambda oid, coin=None, asset_idx=None: {"ok": True})
    _, body = _call("cancel_order", {"order_id": 779, "coin": COIN})
    assert body["cancelled"] is True


def test_cancel_order_proceeds_when_the_order_is_not_resting_any_more(monkeypatch):
    """Nothing here to misjudge -- the venue answers on its own (e.g. already filled/cancelled)."""
    from pathiel.client import exchange as ex
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr(ex, "_resting_orders_for_coin", lambda user, coin: [])
    monkeypatch.setattr(ex, "cancel_orders", lambda oid, coin=None, asset_idx=None: {"ok": False, "error": "unknown oid"})
    _, body = _call("cancel_order", {"order_id": 999, "coin": COIN})
    assert "operator_order" not in body


def test_cancel_order_proceeds_with_operator_override(monkeypatch):
    from pathiel.client import exchange as ex
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr(ex, "_resting_orders_for_coin",
                        lambda user, coin: [{"oid": 777, "coin": coin, "cloid": None}])
    monkeypatch.setattr(ex, "cancel_orders", lambda oid, coin=None, asset_idx=None: {"ok": True})
    _, body = _call("cancel_order", {"order_id": 777, "coin": COIN, "operator_override": True})
    assert body["cancelled"] is True


# ── place_bracket(add=true) ──────────────────────────────────────────────────

def test_place_bracket_add_refuses_a_topup_onto_an_operator_position(operator_position):
    _, body = _call("place_bracket", {"coin": COIN, "stop_px": 89.0, "tp_px": 95.0, "add": True})
    assert body["ok"] is False and body.get("operator_position") is True
    assert "place_bracket(add=true)" in body["error"]


def test_place_bracket_without_add_is_unaffected_by_an_operator_position(operator_position, monkeypatch):
    """add is not requested: place_bracket's own "already in a position" gate
    handles it (no second entry regardless of ownership); ownership is never
    even consulted on this path. Same no-network guard as
    tests/test_mcp_overhaul.py's live-money tests -- this handler's sizing
    path reaches for HIP-3 dex metadata whether or not `add` is set, which
    is unrelated to ownership and must not hit a real socket in a gate
    test."""
    import requests

    def _boom(self, method, url, *a, **kw):
        raise AssertionError(f"test tried to make a real {method} request to {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", _boom)
    _, body = _call("place_bracket", {"coin": COIN, "stop_px": 89.0, "tp_px": 95.0, "dry_run": True})
    assert "operator_position" not in body


# ── bracket_sweep: reports an operator position, changes nothing ───────────

def test_bracket_sweep_reports_an_uncovered_operator_position_without_acting(monkeypatch):
    from pathiel.agents import ownership, bracket
    monkeypatch.setattr(ownership, "check_position_ownership",
                        lambda coin, **kw: dict(OPERATOR_OWNED))
    monkeypatch.setattr("pathiel.client.exchange.resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr("pathiel.client.exchange._resting_orders_for_coin",
                        lambda user, coin: [{"reduceOnly": True, "orderType": "Stop Market",
                                             "sz": "7.33", "triggerPx": "88.0"}])
    monkeypatch.setattr(M, "_coin_specs", lambda coin: (20.0, 2))
    sweep_called = []
    monkeypatch.setattr(bracket, "sweep", lambda coin, ex=None: sweep_called.append(coin))
    _, body = _call("bracket_sweep", {"coin": COIN})
    assert sweep_called == []                       # bracket.sweep() never ran
    assert body["operator_position"] is True and body["changed"] is False
    assert body["ok"] is False                       # 7.33 of 14.66 covered: uncovered
    assert "operator trade, uncovered" in body["reason"]
    assert body["position"] == 14.66 and body["entry_px"] == 90.7165


def test_bracket_sweep_reports_a_covered_operator_position_without_acting(monkeypatch):
    from pathiel.agents import ownership, bracket
    monkeypatch.setattr(ownership, "check_position_ownership",
                        lambda coin, **kw: dict(OPERATOR_OWNED))
    monkeypatch.setattr("pathiel.client.exchange.resolve_user_address", lambda: "0xabc")
    monkeypatch.setattr("pathiel.client.exchange._resting_orders_for_coin",
                        lambda user, coin: [{"reduceOnly": True, "orderType": "Stop Market",
                                             "sz": "14.66", "triggerPx": "88.0"}])
    monkeypatch.setattr(M, "_coin_specs", lambda coin: (20.0, 2))
    sweep_called = []
    monkeypatch.setattr(bracket, "sweep", lambda coin, ex=None: sweep_called.append(coin))
    _, body = _call("bracket_sweep", {"coin": COIN})
    assert sweep_called == []
    assert body["ok"] is True and body["operator_position"] is True
    assert "operator trade, covered" in body["reason"]


def test_bracket_sweep_runs_normally_on_a_desk_owned_position(desk_position, monkeypatch):
    from pathiel.agents import bracket
    monkeypatch.setattr(M, "_bounded", lambda fn, *a: fn(*a))
    monkeypatch.setattr(bracket, "sweep", lambda coin, ex=None: {"ok": True, "coin": coin, "position": 14.66})
    _, body = _call("bracket_sweep", {"coin": COIN})
    assert "operator_position" not in body and body["ok"] is True


def test_bracket_sweep_runs_normally_on_a_flat_coin(flat_coin, monkeypatch):
    from pathiel.agents import bracket
    monkeypatch.setattr(M, "_bounded", lambda fn, *a: fn(*a))
    monkeypatch.setattr(bracket, "sweep", lambda coin, ex=None: {"ok": True, "coin": coin, "position": 0.0})
    _, body = _call("bracket_sweep", {"coin": COIN})
    assert "operator_position" not in body


def test_bracket_sweep_proceeds_with_operator_override(monkeypatch):
    from pathiel.agents import ownership, bracket
    monkeypatch.setattr(ownership, "check_position_ownership",
                        lambda coin, **kw: dict(OPERATOR_OWNED))
    monkeypatch.setattr(M, "_bounded", lambda fn, *a: fn(*a))
    monkeypatch.setattr(bracket, "sweep", lambda coin, ex=None: {"ok": True, "coin": coin, "position": 14.66})
    _, body = _call("bracket_sweep", {"coin": COIN, "operator_override": True})
    assert "operator_position" not in body and body["ok"] is True


def test_bracket_sweep_falls_through_to_normal_sweep_on_an_ownership_read_failure(monkeypatch):
    """A read failure never silently skips housekeeping on a REAL position:
    it falls through to bracket.sweep() as before this feature existed."""
    from pathiel.agents import ownership, bracket
    monkeypatch.setattr(ownership, "check_position_ownership",
                        lambda coin, **kw: {"coin": coin, "flat": False, "desk_owned": False,
                                            "size": None, "entry_px": None, "leverage": None,
                                            "read_failed": True})
    monkeypatch.setattr(M, "_bounded", lambda fn, *a: fn(*a))
    monkeypatch.setattr(bracket, "sweep", lambda coin, ex=None: {"ok": True, "coin": coin, "ran": True})
    _, body = _call("bracket_sweep", {"coin": COIN})
    assert body.get("ran") is True
