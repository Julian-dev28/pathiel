"""services/accounts/positions.py: dex discovery and the live-fetch leg
that backs GET /api/accounts/{id}/positions. assess() itself is covered by
tests/test_watch_wallet.py on the real venue shapes; this file covers the
parts specific to this module (dex discovery, per-dex read, one-dex-down
degradation)."""
from __future__ import annotations

from services.accounts import positions


def test_dexes_with_margin_always_includes_main_and_funded_hip3(monkeypatch):
    import pathiel.client.account_snapshot as snap

    def fake_account_state(user, include_hip3=True, **kwargs):
        return {"dex_equity": {"": 10.0, "xyz": 34.58, "km": 0.0},
                "asset_positions": [{"position": {"coin": "xyz:BRENTOIL"}}]}

    monkeypatch.setattr(snap, "account_state", fake_account_state)
    dexes = positions._dexes_with_margin("0xabc")
    assert dexes == ["", "xyz"]            # main first, km excluded (zero equity, no position)


def test_dexes_with_margin_degrades_to_main_only_on_a_flat_account(monkeypatch):
    import pathiel.client.account_snapshot as snap
    monkeypatch.setattr(snap, "account_state", lambda user, include_hip3=True, **kw: {})
    assert positions._dexes_with_margin("0xabc") == [""]


def test_fetch_positions_for_account_reads_each_dex_and_merges(monkeypatch):
    def fake_http_post(path, body):
        dex = body.get("dex", "")
        if body["type"] == "clearinghouseState":
            if dex == "xyz":
                return {"marginSummary": {"accountValue": "34.58"},
                        "assetPositions": [{"position": {
                            "coin": "xyz:BRENTOIL", "szi": "5.82", "entryPx": "107.16",
                            "liquidationPx": "104.43", "leverage": {"type": "isolated", "value": 20},
                            "positionValue": "627.16", "unrealizedPnl": "3.49"}}]}
            return {"marginSummary": {"accountValue": "0.01"}, "assetPositions": []}
        # frontendOpenOrders
        if dex == "xyz":
            return [{"coin": "xyz:BRENTOIL", "reduceOnly": True, "orderType": "Stop Market",
                     "triggerPx": "106.7", "sz": "5.82"}]
        return []

    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "_http_post", fake_http_post)

    out = positions.fetch_positions_for_account("0xabc", dexes=["", "xyz"])
    assert out["user"] == "0xabc"
    assert out["any_naked"] is False
    dexes = {a["dex"]: a for a in out["accounts"]}
    assert dexes["main"]["equity_usd"] == 0.01
    assert dexes["xyz"]["equity_usd"] == 34.58
    assert dexes["xyz"]["positions"][0]["coin"] == "xyz:BRENTOIL"
    assert dexes["xyz"]["positions"][0]["naked"] is False


def test_a_dead_dex_degrades_without_blanking_the_others(monkeypatch):
    def fake_http_post(path, body):
        dex = body.get("dex", "")
        if dex == "xyz":
            raise RuntimeError("timeout")
        if body["type"] == "clearinghouseState":
            return {"marginSummary": {"accountValue": "5.0"}, "assetPositions": []}
        return []

    import pathiel.client.hl_client as hlc
    monkeypatch.setattr(hlc, "_http_post", fake_http_post)

    out = positions.fetch_positions_for_account("0xabc", dexes=["", "xyz"])
    dexes = {a["dex"]: a for a in out["accounts"]}
    assert dexes["main"]["equity_usd"] == 5.0
    assert dexes["xyz"]["error"] and dexes["xyz"]["any_naked"] is False
    assert out["any_naked"] is False
