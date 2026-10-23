"""services/accounts/positions.py: dex discovery and the live-fetch leg
that backs GET /api/accounts/{id}/positions. assess() itself is covered by
tests/test_watch_wallet.py on the real venue shapes; this file covers the
parts specific to this module (dex discovery, per-dex read, one-dex-down
degradation, and the 2026-10-24 budget-starvation fixes: dust dexes are
not read, a starved read is an error rather than a flat account, and the
cache keeps the last good snapshot through a failed refresh)."""
from __future__ import annotations

import pytest

from pathiel.client.hl_client import HLRequestError
from services.accounts import positions

XYZ_STATE = {"marginSummary": {"accountValue": "63.78"},
             "assetPositions": [{"position": {
                 "coin": "xyz:BRENTOIL", "szi": "1.62", "entryPx": "111.57",
                 "liquidationPx": "102.99", "leverage": {"type": "isolated", "value": 10},
                 "positionValue": "180.6", "unrealizedPnl": "-0.13"}}]}
XYZ_ORDERS = [{"coin": "xyz:BRENTOIL", "reduceOnly": True, "orderType": "Stop Market",
               "triggerPx": "110.9", "sz": "1.62"}]


def _patch_state(monkeypatch, state):
    import pathiel.client.account_snapshot as snap
    monkeypatch.setattr(snap, "account_state", lambda user, include_hip3=True, **kw: state)


def _patch_venue(monkeypatch, fn):
    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "_http_post_strict", fn)


# ── dex discovery ────────────────────────────────────────────────────────────
def test_dexes_with_margin_includes_funded_main_and_funded_hip3(monkeypatch):
    _patch_state(monkeypatch, {"dex_equity": {"": 10.0, "xyz": 34.58, "km": 0.0},
                               "asset_positions": [{"position": {"coin": "xyz:BRENTOIL"}}]})
    assert positions._dexes_with_margin("0xabc") == ["", "xyz"]   # main first, km excluded


def test_dust_dexes_are_not_read_but_a_position_always_is(monkeypatch):
    # The real pathiel wallet on 2026-10-24: $63.78 on xyz, a few cents left
    # on four other HIP-3 dexes, nothing on main. Reading all six cost 144
    # weight a poll against a 120/min budget and starved the xyz read.
    _patch_state(monkeypatch, {"dex_equity": {"": 0.0, "cash": 0.01, "hyna": 0.005, "km": 0.007,
                                              "vntl": 0.006, "xyz": 63.78},
                               "asset_positions": [{"position": {"coin": "xyz:BRENTOIL"}}]})
    assert positions._dexes_with_margin("0xabc") == ["xyz"]
    # a dust dex that still carries a position is read regardless of equity
    positions.clear_cache()
    _patch_state(monkeypatch, {"dex_equity": {"": 0.0, "km": 0.02},
                               "asset_positions": [{"position": {"coin": "km:FOO"}}]})
    assert positions._dexes_with_margin("0xabc") == ["km"]


def test_the_total_equity_does_not_drag_an_empty_main_in(monkeypatch):
    # The snapshot's top-level equity sums every dex; main holds under a cent.
    _patch_state(monkeypatch, {"equity": 63.78, "account_value": 63.78,
                               "dex_equity": {"": 0.00856, "xyz": 63.78}, "asset_positions": []})
    assert positions._dexes_with_margin("0xabc") == ["xyz"]


def test_dexes_with_margin_degrades_to_main_only_on_a_flat_account(monkeypatch):
    _patch_state(monkeypatch, {})
    assert positions._dexes_with_margin("0xabc") == [""]


def test_dex_discovery_keeps_the_last_good_list_when_the_snapshot_read_fails(monkeypatch):
    _patch_state(monkeypatch, {"dex_equity": {"xyz": 63.78}})
    assert positions._dexes_with_margin("0xabc") == ["xyz"]
    positions._CACHE["dexes:0xabc"] = (positions._now() - positions.DEXES_TTL_S - 1,
                                       positions._CACHE["dexes:0xabc"][1])      # expire it
    import pathiel.client.account_snapshot as snap

    def boom(user, include_hip3=True, **kw):
        raise HLRequestError("rate budget exhausted for clearinghouseState")
    monkeypatch.setattr(snap, "account_state", boom)
    assert positions._dexes_with_margin("0xabc") == ["xyz"]


# ── per-dex read ─────────────────────────────────────────────────────────────
def test_fetch_positions_for_account_reads_each_dex_and_merges(monkeypatch):
    def fake(path, body, **kw):
        dex = body.get("dex", "")
        if body["type"] == "clearinghouseState":
            return XYZ_STATE if dex == "xyz" else {"marginSummary": {"accountValue": "0.01"}, "assetPositions": []}
        if body["type"] == "frontendOpenOrders":
            return XYZ_ORDERS if dex == "xyz" else []
        return {"xyz:BRENTOIL": "111.5"} if dex == "xyz" else {}
    _patch_venue(monkeypatch, fake)

    out = positions.fetch_positions_for_account("0xabc", dexes=["", "xyz"])
    assert out["user"] == "0xabc" and out["any_naked"] is False and "errors" not in out
    dexes = {a["dex"]: a for a in out["accounts"]}
    assert dexes["main"]["equity_usd"] == 0.01
    assert dexes["xyz"]["equity_usd"] == 63.78
    p = dexes["xyz"]["positions"][0]
    assert p["coin"] == "xyz:BRENTOIL" and p["naked"] is False and p["mark_px"] == 111.5


def test_a_dead_dex_degrades_without_blanking_the_others_and_is_reported(monkeypatch):
    def fake(path, body, **kw):
        if body.get("dex") == "xyz":
            raise HLRequestError("rate budget exhausted for frontendOpenOrders (weight=20, waited 30s)")
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "5.0"}, "assetPositions": []}
        return [] if body["type"] == "frontendOpenOrders" else {}
    _patch_venue(monkeypatch, fake)

    out = positions.fetch_positions_for_account("0xabc", dexes=["", "xyz"])
    dexes = {a["dex"]: a for a in out["accounts"]}
    assert dexes["main"]["equity_usd"] == 5.0
    assert dexes["xyz"]["error"] and dexes["xyz"]["any_naked"] is False
    assert out["any_naked"] is False
    assert out["errors"] == ["xyz"]                 # the page says "read failed", not "flat"


def test_a_starved_read_is_an_error_not_a_flat_account(monkeypatch):
    """Before 2026-10-24 _fetch_raw used the lenient _http_post, which turns a
    budget refusal into None -> {} -> 'equity 0, no positions'. The live
    Brent long vanished from the Desk page every time the xyz read starved."""
    def fake(path, body, **kw):
        raise HLRequestError("rate budget exhausted for clearinghouseState (weight=2, waited 30s)")
    _patch_venue(monkeypatch, fake)
    with pytest.raises(HLRequestError):
        positions._fetch_raw("0xabc", "xyz")


# ── the cache keeps the last good snapshot ───────────────────────────────────
def test_cache_serves_the_last_good_snapshot_through_a_failed_refresh(monkeypatch):
    calls = {"n": 0}

    def fake(path, body, **kw):
        calls["n"] += 1
        if calls["n"] > 3:                                     # first poll ok, then the venue starves
            raise HLRequestError("rate budget exhausted for frontendOpenOrders (weight=20, waited 30s)")
        if body["type"] == "clearinghouseState":
            return XYZ_STATE
        return XYZ_ORDERS if body["type"] == "frontendOpenOrders" else {"xyz:BRENTOIL": "111.5"}
    _patch_venue(monkeypatch, fake)

    first = positions.fetch_positions_for_account("0xabc", dexes=["xyz"])
    assert first["accounts"][0]["positions"][0]["coin"] == "xyz:BRENTOIL" and "stale_s" not in first
    key = "positions:0xabc:xyz"
    positions._CACHE[key] = (positions._now() - positions.POSITIONS_TTL_S - 1, positions._CACHE[key][1])

    second = positions.fetch_positions_for_account("0xabc", dexes=["xyz"])
    assert second["accounts"][0]["positions"][0]["coin"] == "xyz:BRENTOIL"   # still shows the position
    assert second["stale_s"] >= positions.POSITIONS_TTL_S and "errors" not in second
    # the failed snapshot was NOT cached: the next call retries the venue
    before = calls["n"]
    positions.fetch_positions_for_account("0xabc", dexes=["xyz"])
    assert calls["n"] > before


def test_cache_does_not_store_a_failed_snapshot_when_there_is_nothing_better(monkeypatch):
    calls = {"n": 0}

    def fake(path, body, **kw):
        calls["n"] += 1
        raise HLRequestError("rate budget exhausted")
    _patch_venue(monkeypatch, fake)

    out = positions.fetch_positions_for_account("0xabc", dexes=["xyz"])
    assert out["errors"] == ["xyz"] and "stale_s" not in out
    n = calls["n"]
    positions.fetch_positions_for_account("0xabc", dexes=["xyz"])
    assert calls["n"] > n                                      # retried at once, not served for 15 s
