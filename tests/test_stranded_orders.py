"""Stranded order cleanup, on every dex.

Two leaks, both fixed 2026-10-19:

1. cancel_open_orders_for_coin read `openOrders` with no `dex`. Hyperliquid
   answers that for the main dex only, so after the bot closed xyz:CL its
   SL/TP bracket on xyz was never seen and never cancelled.
2. When one half of a bracket filled on the exchange (or the operator closed
   by hand), the bot recorded the vanished position and dropped its tracker,
   but nothing cancelled the other half. A reduce-only trigger left on a flat
   coin closes the NEXT position opened on that coin at a stale price.

Every read here is faked at pathiel.client.exchange._http_post: no network.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List

import pytest

import pathiel.agents.executor as ex
import pathiel.client.exchange as xc

ROOT = Path(__file__).resolve().parents[1]
USER = "0xUSER"


class FakeInfo:
    """Answers /info like Hyperliquid: per type, per dex ("" = main)."""

    def __init__(self) -> None:
        self.orders: Dict[str, List[Dict[str, Any]]] = {}
        self.positions: Dict[str, List[Dict[str, Any]]] = {}
        self.fail: set = set()             # request types that answer None
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, path: str, payload: Dict[str, Any], timeout: int = 5) -> Any:
        assert path == "/info"
        self.calls.append(dict(payload))
        kind, dex = payload["type"], payload.get("dex", "")
        if kind in self.fail:
            return None
        if kind in ("frontendOpenOrders", "openOrders"):
            return list(self.orders.get(dex, []))
        if kind == "clearinghouseState":
            return {"assetPositions": [{"position": p} for p in self.positions.get(dex, [])],
                    "marginSummary": {"accountValue": "10"}}
        raise AssertionError(f"unexpected /info request {payload}")

    def asked(self, kind: str) -> List[str]:
        return [c.get("dex", "") for c in self.calls if c["type"] == kind]


@pytest.fixture
def info(monkeypatch) -> FakeInfo:
    fake = FakeInfo()
    monkeypatch.setattr(xc, "_http_post", fake)
    monkeypatch.setattr(xc, "resolve_user_address", lambda: USER)
    return fake


@pytest.fixture
def cancels(monkeypatch) -> List[tuple]:
    seen: List[tuple] = []

    def fake_cancel(oid, coin=None, asset_idx=None):
        seen.append((oid, coin))
        return {"ok": True}

    monkeypatch.setattr(xc, "cancel_orders", fake_cancel)
    return seen


def order(oid: int, coin: str, *, reduce_only: bool, trigger: bool = True) -> Dict[str, Any]:
    return {"coin": coin, "oid": oid, "side": "A", "sz": "0.12", "limitPx": "98.0",
            "reduceOnly": reduce_only, "isTrigger": trigger,
            "orderType": "Stop Market" if trigger else "Limit"}


# ── 1. the close-path cleanup reads the coin's own dex ──────────────────────

def test_cleanup_after_closing_xyz_cl_reads_xyz_and_cancels_its_bracket(info, cancels):
    info.orders["xyz"] = [order(11, "xyz:CL", reduce_only=True),
                          order(12, "xyz:CL", reduce_only=True),
                          order(13, "xyz:GOLD", reduce_only=True)]
    info.orders[""] = [order(21, "BTC", reduce_only=True)]

    assert xc.cancel_open_orders_for_coin("xyz:CL") == 2

    assert info.asked("frontendOpenOrders") == ["xyz"]   # was: one main-dex read, 0 cancelled
    assert cancels == [(11, "xyz:CL"), (12, "xyz:CL")]


def test_main_dex_cleanup_sends_no_dex(info, cancels):
    info.orders[""] = [order(21, "BTC", reduce_only=True), order(22, "ETH", reduce_only=True)]
    assert xc.cancel_open_orders_for_coin("BTC") == 1
    assert [c for c in info.calls if c["type"] == "frontendOpenOrders"] == [
        {"type": "frontendOpenOrders", "user": USER}]
    assert cancels == [(21, "BTC")]


def test_a_bare_symbol_inside_the_dex_read_still_matches(info, cancels):
    info.orders["xyz"] = [order(31, "CL", reduce_only=True)]
    assert xc.cancel_open_orders_for_coin("xyz:CL") == 1
    assert cancels == [(31, "xyz:CL")]          # cancelled under the SDK's prefixed name


def test_a_failed_order_read_cancels_nothing_and_never_raises(info, cancels):
    info.fail.add("frontendOpenOrders")
    assert xc.cancel_open_orders_for_coin("xyz:CL") == 0
    assert cancels == []


def test_a_cancel_that_raises_never_escapes(info, monkeypatch):
    info.orders["xyz"] = [order(11, "xyz:CL", reduce_only=True)]
    monkeypatch.setattr(xc, "cancel_orders", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    assert xc.cancel_open_orders_for_coin("xyz:CL") == 0


# ── 2. the stranded sweep: reduce-only only, and only when truly flat ───────

def test_sweep_cancels_the_reduce_only_leftovers_of_a_flat_xyz_coin(info, cancels):
    info.positions["xyz"] = [{"coin": "xyz:GOLD", "szi": "0.5"}]          # another coin, open
    info.orders["xyz"] = [order(41, "xyz:CL", reduce_only=True),           # TP left after the SL filled
                          order(42, "xyz:CL", reduce_only=False, trigger=False),   # a manual entry
                          order(43, "xyz:GOLD", reduce_only=True)]         # GOLD's live stop

    assert xc.cancel_stranded_reduce_only_orders("xyz:CL") == 1

    assert cancels == [(41, "xyz:CL")]
    assert info.asked("clearinghouseState") == ["xyz"]
    assert info.asked("frontendOpenOrders") == ["xyz"]


def test_sweep_keeps_the_stops_of_a_position_that_is_still_open(info, cancels):
    info.positions["xyz"] = [{"coin": "xyz:CL", "szi": "0.12"}]
    info.orders["xyz"] = [order(41, "xyz:CL", reduce_only=True)]
    assert xc.cancel_stranded_reduce_only_orders("xyz:CL") == 0
    assert cancels == [] and info.asked("frontendOpenOrders") == []


def test_sweep_does_nothing_when_the_position_read_fails(info, cancels, caplog):
    """Unknown is not flat: a failed read must never strip a live position's
    stops, and the skip is logged so a leftover that survives is explainable."""
    info.fail.add("clearinghouseState")
    info.orders["xyz"] = [order(41, "xyz:CL", reduce_only=True)]
    with caplog.at_level("WARNING", logger=xc.logger.name):
        assert xc.cancel_stranded_reduce_only_orders("xyz:CL") == 0
    assert cancels == [] and info.asked("frontendOpenOrders") == []
    assert "xyz:CL: position read failed" in caplog.text


def test_sweep_on_a_main_dex_coin(info, cancels):
    info.orders[""] = [order(51, "BTC", reduce_only=True), order(52, "BTC", reduce_only=False)]
    assert xc.cancel_stranded_reduce_only_orders("BTC") == 1
    assert cancels == [(51, "BTC")]
    assert info.asked("clearinghouseState") == [""]


def test_sweep_never_raises(info, monkeypatch):
    info.orders["xyz"] = [order(41, "xyz:CL", reduce_only=True)]
    monkeypatch.setattr(xc, "cancel_orders", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert xc.cancel_stranded_reduce_only_orders("xyz:CL") == 0


def test_no_trading_account_means_no_reads(monkeypatch, info, cancels):
    monkeypatch.setattr(xc, "resolve_user_address", lambda: "")
    assert xc.cancel_stranded_reduce_only_orders("xyz:CL") == 0
    assert xc.cancel_open_orders_for_coin("xyz:CL") == 0
    assert info.calls == [] and cancels == []


# ── 3. the executor: flat closes sweep, vanished positions are settled ──────

@pytest.fixture
def executor_env(monkeypatch):
    swept: List[str] = []
    cleared: List[str] = []
    dropped: List[tuple] = []
    monkeypatch.setattr(ex, "resolve_user_address", lambda: USER)
    monkeypatch.setattr(ex, "deregister_position", lambda coin, side: dropped.append((coin, side)))
    monkeypatch.setattr(ex, "cancel_stranded_reduce_only_orders", lambda coin: swept.append(coin) or 1)
    monkeypatch.setattr(ex, "cancel_open_orders_for_coin", lambda coin: cleared.append(coin) or 0)
    return {"swept": swept, "cleared": cleared, "dropped": dropped}


def test_closing_an_already_flat_coin_sweeps_its_bracket(monkeypatch, executor_env):
    monkeypatch.setattr(ex, "fetch_account_state", lambda u, **kw: {"asset_positions": []})
    res = ex.close_position_market("xyz:CL")
    assert res == {"ok": True, "coin": "xyz:CL", "noop": "already_flat"}
    assert executor_env["swept"] == ["xyz:CL"] and executor_env["cleared"] == []
    assert ("xyz:CL", "long") in executor_env["dropped"]


def test_closing_a_zero_size_position_sweeps_its_bracket(monkeypatch, executor_env):
    monkeypatch.setattr(ex, "fetch_account_state", lambda u, **kw: {
        "asset_positions": [{"position": {"coin": "xyz:CL", "szi": "0", "entryPx": "98.8"}}]})
    assert ex.close_position_market("xyz:CL")["noop"] == "zero_szi"
    assert executor_env["swept"] == ["xyz:CL"]


def test_a_filled_close_cancels_every_order_on_the_coin(monkeypatch, executor_env, tmp_path):
    from pathiel.agents import dsl_exit
    monkeypatch.setattr(dsl_exit, "_active_positions", {})
    monkeypatch.setattr(ex, "fetch_account_state", lambda u, **kw: {
        "asset_positions": [{"position": {"coin": "xyz:CL", "szi": "0.12", "entryPx": "98.8"}}]})
    monkeypatch.setattr(ex, "get_hl_price", lambda c: 99.0)
    monkeypatch.setattr(ex, "place_hl_order", lambda *a, **kw: {"ok": True, "order_id": "c1"})
    assert ex.close_position_market("xyz:CL")["ok"] is True
    assert executor_env["cleared"] == ["xyz:CL"] and executor_env["swept"] == []


def test_settle_vanished_positions_records_then_sweeps(monkeypatch, executor_env):
    recorded: List[str] = []

    def record(stale, user=None):
        recorded.append(stale["coin"])
        if stale["coin"] == "BAD":
            raise RuntimeError("fills read failed")
        return {"ok": True}

    monkeypatch.setattr(ex, "record_external_position_close", record)
    rows = ex.settle_vanished_positions(
        [{"coin": "xyz:CL", "side": "long"}, {"coin": "BAD", "side": "short"},
         {"coin": "xyz:LADDER", "side": "long"}],
        exempt_coins={"xyz:LADDER"}, user=USER)

    assert recorded == ["xyz:CL", "BAD", "xyz:LADDER"]
    assert executor_env["swept"] == ["xyz:CL", "BAD"]       # a failed record still sweeps
    assert rows == [{"coin": "xyz:CL", "recorded": True, "cancelled": 1},
                    {"coin": "BAD", "recorded": False, "cancelled": 1},
                    {"coin": "xyz:LADDER", "recorded": True, "cancelled": 0}]


def test_settle_vanished_positions_handles_nothing(executor_env):
    assert ex.settle_vanished_positions([], user=USER) == []
    assert executor_env["swept"] == []


# ── 4. the trading loop wires it in ─────────────────────────────────────────

def test_the_loop_settles_vanished_positions_with_the_books_exempt():
    tree = ast.parse((ROOT / "scripts" / "trading_loop.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "settle_vanished_positions"]
    assert len(calls) == 1
    (call,) = calls
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "stale_trackers"
    kw = {k.arg: k.value for k in call.keywords}
    assert isinstance(kw["exempt_coins"], ast.Name) and kw["exempt_coins"].id == "_ladder_coins"
    names = {getattr(n.func, "id", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "record_external_position_close" not in names   # only through settle_vanished_positions
