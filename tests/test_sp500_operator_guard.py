"""The sp500 desk never touches the operator's own xyz:SP500 trade.

2026-11-19: the operator holds a HAND-PLACED xyz:SP500 long (0.18 @ 7171.2,
50x cross, no stop) on the account the new sp500 desk trades. Hyperliquid
nets one position per coin per account, so a desk entry would merge into
it. The rule: any xyz:SP500 position or order that is not desk-owned
(pathiel/agents/ownership.py) is the operator's. The tick then logs HOLD
with "operator position open on xyz:SP500", places nothing, and never
closes, cancels, sweeps, re-levers or moves a stop or target on it.

Three layers, each tested here with that exact position:
  1. ownership.operator_presence / check_operator_presence (the evidence),
  2. scripts/desk_slot.py (the tick's first step: slot "operator", stop),
  3. the MCP handlers (place_bracket, sweep_brackets, set_leverage,
     close_position, set_stop, set_target, cancel_order all refuse).
Gate tests: every venue call is monkeypatched, no network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pathiel.agents import ownership

ROOT = Path(__file__).resolve().parents[1]
COIN = "xyz:SP500"
USER = "0xoperator"
REASON = "operator position open on xyz:SP500"

# The operator's trade, as the venue reports it.
HAND_FILL = {"coin": COIN, "side": "B", "sz": "0.18", "px": "7171.2", "time": 1_795_000_000_000,
             "oid": 5001, "tid": 1, "cloid": None}
HAND_STATE = {"asset_positions": [{"position": {
    "coin": COIN, "szi": "0.18", "entryPx": "7171.2", "leverage": {"type": "cross", "value": 50}}}]}
FLAT_STATE = {"asset_positions": []}
DESK_CLOID = ownership.make_cloid()
DESK_FILL = {**HAND_FILL, "oid": 6001, "cloid": DESK_CLOID}
DESK_STOP = {"coin": COIN, "oid": 6002, "reduceOnly": True, "orderType": "Stop Market", "cloid": ownership.make_cloid()}
HAND_ORDER = {"coin": COIN, "oid": 5002, "reduceOnly": False, "orderType": "Limit", "cloid": None}


def _presence(state, fills, orders):
    return ownership.check_operator_presence(
        COIN, user=USER, fetch_state=lambda u, include_hip3=True: state,
        fetch_fills=lambda u, start: fills, fetch_orders=lambda u, c: orders, journal=[])


# ── 1. the evidence ──────────────────────────────────────────────────────

def test_the_hand_placed_long_is_the_operators():
    got = _presence(HAND_STATE, [HAND_FILL], [])
    assert got["operator"] is True and got["position"] is True
    assert got["reason"] == REASON
    assert (got["position_size"], got["entry_px"], got["leverage"]) == (0.18, 7171.2, 50)


def test_a_desk_position_with_its_own_stop_is_not():
    got = _presence(HAND_STATE, [DESK_FILL], [DESK_STOP])
    assert got == {"coin": COIN, "operator": False, "position": False, "position_size": None, "entry_px": None,
                   "leverage": None, "orders": [], "read_failed": False, "reason": None}


def test_a_flat_coin_with_nothing_resting_is_clear():
    assert _presence(FLAT_STATE, [], [])["operator"] is False


def test_a_resting_order_the_desk_did_not_place_is_the_operators():
    got = _presence(FLAT_STATE, [], [HAND_ORDER, DESK_STOP])
    assert got["operator"] is True and got["position"] is False and got["orders"] == [5002]
    assert got["reason"] == "operator order resting on xyz:SP500"


def test_a_journalled_oid_counts_as_the_desks():
    journal = [{"oid": 5002, "coin": COIN, "kind": "entry"}]
    got = ownership.operator_presence(COIN, {"flat": True, "desk_owned": True}, [HAND_ORDER], journal)
    assert got["operator"] is False


def test_the_desk_adding_to_the_operators_trade_is_still_the_operators():
    """A mixed position (his fill, then a desk fill) is refused like a pure one."""
    mixed = {"asset_positions": [{"position": {"coin": COIN, "szi": "0.25", "entryPx": "7172",
                                               "leverage": {"type": "cross", "value": 50}}}]}
    desk_add = {**DESK_FILL, "sz": "0.07", "time": HAND_FILL["time"] + 1}
    assert _presence(mixed, [HAND_FILL, desk_add], [])["reason"] == REASON


def test_a_failed_read_holds_rather_than_guesses():
    def boom(*a, **k):
        raise RuntimeError("venue down")
    got = ownership.check_operator_presence(COIN, user=USER, fetch_state=boom, fetch_fills=boom,
                                            fetch_orders=lambda u, c: [], journal=[])
    assert got["operator"] is True and got["read_failed"] is True and "read failed" in got["reason"]
    got = ownership.check_operator_presence(COIN, user=USER, fetch_state=lambda u, include_hip3=True: FLAT_STATE,
                                            fetch_fills=lambda u, s: [], fetch_orders=boom, journal=[])
    assert got["operator"] is True and got["read_failed"] is True


def test_only_sp500_and_hype_are_guarded():
    assert ownership.OPERATOR_GUARD_COINS == frozenset({"xyz:SP500", "HYPE"})
    assert ownership.is_guarded(COIN) and ownership.is_guarded("HYPE")
    assert not ownership.is_guarded("xyz:BRENTOIL")


# ── 2. the tick's first step ─────────────────────────────────────────────

_spec = importlib.util.spec_from_file_location("desk_slot_sp500", ROOT / "scripts" / "desk_slot.py")
SLOT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SLOT)

SHARES = {"active_desks": ["oil", "sp500"], "desk_equity_share": {"oil": 0.90, "metals": 0.05, "sp500": 0.05}}


def _raw(positions=(), orders=()):
    return {"state": {"assetPositions": [{"position": {"coin": c, "szi": sz}} for c, sz in positions]},
            "orders": list(orders)}


def _slot(monkeypatch, capsys, desk, raw, presence):
    import pathiel.agents.config_store as cs
    monkeypatch.setattr(cs, "read_agent_config", lambda: SHARES)
    assert SLOT.main(["--desk", desk], fetch_raw=lambda user, dex: raw, dexes=["xyz"], user=USER,
                     presence=presence) == 0
    return json.loads(capsys.readouterr().out)


def test_desk_slot_says_operator_and_gives_the_hold_reason(monkeypatch, capsys):
    seen = []

    def presence(coin, user=None, orders=None):
        seen.append((coin, user, orders))
        return ownership.check_operator_presence(
            coin, user=user, orders=orders, fetch_state=lambda u, include_hip3=True: HAND_STATE,
            fetch_fills=lambda u, s: [HAND_FILL], journal=[])
    out = _slot(monkeypatch, capsys, "sp500", _raw(positions=[(COIN, "0.18")]), presence)
    assert out["slot"] == "operator" and out["mine"] is False and out["holder"] == COIN
    assert out["reason"] == REASON and out["operator"]["position_size"] == 0.18
    assert out["equity_share"] == 0.05 and seen == [(COIN, USER, [])]


def test_desk_slot_is_unchanged_for_a_clear_sp500_and_never_checks_oil(monkeypatch, capsys):
    def never(*a, **k):
        raise AssertionError("no ownership read when nothing sits on the guarded coin")
    out = _slot(monkeypatch, capsys, "sp500", _raw(positions=[("xyz:BRENTOIL", "14.66")]), never)
    assert out["slot"] == "free" and "operator" not in out and out["desk_coins"] == [COIN]
    out = _slot(monkeypatch, capsys, "oil", _raw(positions=[("xyz:BRENTOIL", "14.66"), (COIN, "0.18")]), never)
    assert out["slot"] == "held" and out["mine"] is True and "operator" not in out


def test_desk_slot_lets_the_desks_own_position_through(monkeypatch, capsys):
    def presence(coin, user=None, orders=None):
        return ownership.check_operator_presence(
            coin, user=user, orders=orders, fetch_state=lambda u, include_hip3=True: HAND_STATE,
            fetch_fills=lambda u, s: [DESK_FILL], journal=[])
    out = _slot(monkeypatch, capsys, "sp500", _raw(positions=[(COIN, "0.18")], orders=[DESK_STOP]), presence)
    assert out["slot"] == "held" and out["mine"] is True and "operator" not in out


# ── 3. the MCP handlers ──────────────────────────────────────────────────

def _load():
    spec = importlib.util.spec_from_file_location("pathiel_mcp_server_sp500_guard",
                                                  ROOT / "scripts" / "pathiel-mcp-server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _call(name, args):
    resp = M.dispatch_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": args}})
    return resp["result"], json.loads(resp["result"]["content"][0]["text"])


@pytest.fixture()
def operator_long(monkeypatch, tmp_path):
    """The venue as it is today: the operator's long, no stop, nothing of the
    desk's. Every call that would change the account is a tripwire."""
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from pathiel.agents import bracket
    from pathiel.client import exchange as ex
    from pathiel.client import hl_client
    touched = []

    def trip(name):
        def _f(*a, **k):
            touched.append(name)
            raise AssertionError(f"{name} must never run on the operator's xyz:SP500 trade")
        return _f
    monkeypatch.setattr(hl_client, "resolve_user_address", lambda: USER)
    monkeypatch.setattr(hl_client, "fetch_account_state", lambda user, include_hip3=True, **k: HAND_STATE)
    monkeypatch.setattr(hl_client, "fetch_user_fills_by_time", lambda user, start, *a, **k: [HAND_FILL])
    monkeypatch.setattr(ex, "resolve_user_address", lambda: USER)
    monkeypatch.setattr(ex, "_resting_orders_for_coin", lambda user, coin: [])
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(M, "_coin_specs", lambda coin: (50.0, 3))      # venue max 50x, size step 0.001
    for name in ("place_hl_order", "place_hl_trigger_order", "place_hl_bracket_order", "cancel_orders",
                 "cancel_stranded_reduce_only_orders", "set_leverage"):
        monkeypatch.setattr(ex, name, trip(name))
    for name in ("place_bracket", "close_position", "set_stop", "set_target", "sweep", "consolidate_protection"):
        monkeypatch.setattr(bracket, name, trip("bracket." + name))
    return touched


@pytest.mark.parametrize("args", [
    {"coin": COIN, "stop_px": 7140.0, "tp_px": 7260.0},
    {"coin": COIN, "stop_px": 7140.0, "tp_px": 7260.0, "dry_run": True},
    {"coin": COIN, "entry_px": 7160.0, "stop_px": 7130.0, "tp_px": 7260.0},
    {"coin": COIN, "stop_px": 7140.0, "tp_px": 7260.0, "add": True},
    {"coin": COIN, "stop_px": 7200.0, "tp_px": 7100.0},                      # the short side too
])
def test_place_bracket_places_nothing_while_the_operator_is_in(operator_long, args):
    _, body = _call("place_bracket", args)
    assert body["ok"] is False and body["placed"] is False and body["operator_position"] is True
    assert body["reason"] == REASON and REASON in body["error"]
    assert body["position"] == 0.18 and body["entry_px"] == 7171.2
    assert operator_long == []


def test_sweep_close_stop_target_and_leverage_never_touch_it(operator_long):
    _, sweep = _call("sweep_brackets", {"coin": COIN})
    assert sweep["operator_position"] is True and sweep["changed"] is False
    assert sweep["position"] == 0.18 and sweep["stop_covers"] == 0     # uncovered, and left that way
    assert sweep["liq_px"] == pytest.approx(7171.2 * (1 - 0.0101), abs=0.5)   # 50x: liquidation 1.01% away
    for tool, args in (("close_position", {"coin": COIN}), ("set_stop", {"coin": COIN, "stop_px": 7100.0}),
                       ("set_target", {"coin": COIN, "tp_px": 7300.0}), ("set_leverage", {"coin": COIN, "leverage": 20})):
        _, body = _call(tool, args)
        assert body["ok"] is False and body["operator_position"] is True, tool
        assert tool in body["error"]
    assert operator_long == []


def test_the_operators_resting_order_is_never_cancelled_or_swept(operator_long, monkeypatch):
    from pathiel.client import exchange as ex
    from pathiel.client import hl_client
    monkeypatch.setattr(hl_client, "fetch_account_state", lambda user, include_hip3=True, **k: FLAT_STATE)
    monkeypatch.setattr(ex, "_resting_orders_for_coin", lambda user, coin: [HAND_ORDER])
    _, cancel = _call("cancel_order", {"order_id": 5002, "coin": COIN})
    assert cancel["cancelled"] is False and cancel["operator_order"] is True
    _, sweep = _call("sweep_brackets", {"coin": COIN})
    assert sweep["ok"] is True and sweep["swept"] is False and sweep["operator_orders"] == [5002]
    assert sweep["reason"] == "operator order resting on xyz:SP500" and "error" not in sweep
    _, entry = _call("place_bracket", {"coin": COIN, "stop_px": 7140.0, "tp_px": 7260.0})
    assert entry["placed"] is False and entry["reason"] == "operator order resting on xyz:SP500"
    assert operator_long == []


def test_the_guard_reads_nothing_for_brent(monkeypatch):
    """Oil's path is unchanged: the guard returns before any venue read."""
    def boom(*a, **k):
        raise AssertionError("no operator-presence read on an unguarded coin")
    monkeypatch.setattr(ownership, "check_operator_presence", boom)
    assert M._operator_guard_refusal("xyz:BRENTOIL", {}, "place_bracket") is None
    assert M._operator_guard_refusal("xyz:GOLD", {}, "sweep_brackets") is None


def test_operator_override_is_the_one_way_through(monkeypatch):
    monkeypatch.setattr(ownership, "check_operator_presence",
                        lambda coin, **k: {"operator": True, "position": True, "reason": REASON})
    assert M._operator_guard_refusal(COIN, {}, "place_bracket")["reason"] == REASON
    assert M._operator_guard_refusal(COIN, {"operator_override": True}, "place_bracket") is None
