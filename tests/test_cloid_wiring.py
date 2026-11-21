"""Every order pathiel places carries a pathiel-tagged Hyperliquid client
order id (cloid), and a market or resting ENTRY is journalled too -- the
cloid tag and the journal fallback are the two things is_desk_owned trusts
(pathiel.agents.ownership). Gate tests only: fake SDKs, no network.
"""
from __future__ import annotations

from typing import Any, Dict

from pathiel.agents import ownership as O
from pathiel.client import exchange as ex

COIN = "xyz:BRENTOIL"
RESTING = {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 12}}]}}}
FILLED = {"status": "ok", "response": {"data": {"statuses": [
    {"filled": {"oid": 11, "avgPx": "100.0", "totalSz": "1.0"}}]}}}
BULK_OK = {"status": "ok", "response": {"data": {"statuses": [
    {"resting": {"oid": 13}}, "waitingForFill", "waitingForFill"]}}}


class _Sdk:
    """Captures every order()/bulk_orders() call's cloid, nothing else."""

    def __init__(self, reply: Dict[str, Any]):
        self.reply = reply
        self.order_cloids = []
        self.bulk_cloids = []

    def order(self, coin, is_buy, sz, limit_px, order_type, reduce_only=False, cloid=None):
        self.order_cloids.append(cloid)
        return self.reply

    def bulk_orders(self, order_requests, grouping="na"):
        self.bulk_cloids.extend(r.get("cloid") for r in order_requests)
        return self.reply


def _patched(monkeypatch, sdk):
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(ex, "_ioc_cross_price", lambda coin, is_buy, mid: mid)
    monkeypatch.setattr(ex, "_make_exchange", lambda: sdk)


def test_place_hl_order_tags_a_pathiel_cloid(monkeypatch):
    sdk = _Sdk(FILLED)
    _patched(monkeypatch, sdk)
    out = ex.place_hl_order(True, 1.0, 100.0, COIN)
    assert out["ok"] and O.is_pathiel_cloid(out["cloid"])
    assert str(sdk.order_cloids[0]) == out["cloid"]


def test_place_hl_trigger_order_tags_a_pathiel_cloid(monkeypatch):
    sdk = _Sdk(RESTING)
    _patched(monkeypatch, sdk)
    out = ex.place_hl_trigger_order(True, 1.0, 95.0, "sl", COIN)
    assert out["ok"] and O.is_pathiel_cloid(out["cloid"])


def test_place_hl_limit_order_tags_a_pathiel_cloid(monkeypatch):
    sdk = _Sdk(RESTING)
    _patched(monkeypatch, sdk)
    out = ex.place_hl_limit_order(COIN, True, 1.0, 99.0)
    assert out["ok"] and O.is_pathiel_cloid(out["cloid"])


def test_place_hl_bracket_order_tags_all_three_legs_with_distinct_cloids(monkeypatch):
    sdk = _Sdk(BULK_OK)
    _patched(monkeypatch, sdk)
    out = ex.place_hl_bracket_order(COIN, True, 1.27, 107.2, 106.7, 108.45)
    assert out["ok"]
    cloids = [out["entry"]["cloid"], out["sl"]["cloid"], out["tp"]["cloid"]]
    assert all(O.is_pathiel_cloid(c) for c in cloids)
    assert len(set(cloids)) == 3                     # each leg is distinct
    # the SDK itself received real Cloid objects carrying the same values
    assert [str(c) for c in sdk.bulk_cloids] == cloids


def test_an_explicit_cloid_overrides_auto_generation(monkeypatch):
    sdk = _Sdk(FILLED)
    _patched(monkeypatch, sdk)
    mine = "0x" + "11" * 16
    out = ex.place_hl_order(True, 1.0, 100.0, COIN, cloid=mine)
    assert out["cloid"] == mine and str(sdk.order_cloids[0]) == mine


# ── bracket.place_bracket journals the entry (fallback for a fill the venue
#    does not echo a cloid on) ──────────────────────────────────────────────

class _JournalEx:
    """Minimal place_bracket double: flat, no resting orders, one market fill."""

    def __init__(self):
        self.calls = []

    def get_hl_price(self, coin):
        return 107.6

    def resolve_user_address(self):
        return "0xuser"

    def _position_size_on_coin(self, user, coin):
        return 0.0

    def _resting_orders_for_coin(self, user, coin):
        return []

    def cancel_stranded_reduce_only_orders(self, coin):
        return 0

    def set_leverage(self, coin, leverage, is_cross=False, strict=True):
        return {"ok": True}

    def place_hl_order(self, is_buy, size, mid_price, coin):
        self.calls.append("entry")
        return {"ok": True, "total_sz": size, "avg_px": mid_price, "order_id": "4242",
                "cloid": O.make_cloid()}

    def place_hl_trigger_order(self, is_long, size, px, kind, coin):
        self.calls.append(kind)
        return {"ok": True, "order_id": "1", "cloid": O.make_cloid()}


def test_a_market_entry_is_journalled_with_its_cloid_and_oid(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from pathiel.agents import bracket
    ex_fake = _JournalEx()
    out = bracket.place_bracket(COIN, entry_px=None, stop_px=106.7, tp_px=110.0, size=1.27,
                                leverage=20, ex=ex_fake)
    assert out["placed"]
    rows = O.load_journal()
    assert len(rows) == 1
    row = rows[0]
    assert row["coin"] == COIN and row["kind"] == "entry" and row["oid"] == 4242
    assert O.is_pathiel_cloid(row["cloid"])


def test_a_resting_limit_entry_is_journalled_before_it_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    from pathiel.agents import bracket

    class _RestingEx(_JournalEx):
        def place_hl_bracket_order(self, coin, is_buy, size, entry_px, stop_px, tp_px):
            return {"ok": True, "entry": {"ok": True, "order_id": "555", "cloid": O.make_cloid()},
                    "sl": {"ok": True}, "tp": {"ok": True}}

    ex_fake = _RestingEx()
    out = bracket.place_bracket(COIN, entry_px=107.2, stop_px=106.7, tp_px=109.5, size=1.27,
                                leverage=20, ex=ex_fake)
    assert out["placed"]
    rows = O.load_journal()
    assert len(rows) == 1 and rows[0]["oid"] == 555 and O.is_pathiel_cloid(rows[0]["cloid"])
