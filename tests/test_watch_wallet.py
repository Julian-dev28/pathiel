"""services/accounts/positions.py: the pure assessment, on the shapes
Hyperliquid returns. (Moved out of scripts/watch_wallet.py 2026-10-23; that
script is now a thin CLI importing this module.)"""
from __future__ import annotations

from services.accounts.positions import assess

# Julien's wallet on 2026-10-23 16:40 UTC, as the venue returned it.
STATE = {"marginSummary": {"accountValue": "34.581292", "totalNtlPos": "627.1632"},
         "assetPositions": [{"position": {
             "coin": "xyz:BRENTOIL", "szi": "5.82", "entryPx": "107.16", "liquidationPx": "104.4289202573",
             "leverage": {"type": "isolated", "value": 20, "rawUsd": "-592.58"},
             "positionValue": "627.1632", "unrealizedPnl": "3.492"}}]}
STOP = {"coin": "xyz:BRENTOIL", "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "106.7", "sz": "5.82"}
TP = {"coin": "xyz:BRENTOIL", "reduceOnly": True, "orderType": "Take Profit Market", "triggerPx": "108.45", "sz": "5.82"}


def test_the_real_naked_20x_position_is_flagged():
    out = assess(STATE, [], "xyz")
    assert out["any_naked"] is True
    p = out["positions"][0]
    assert p["coin"] == "xyz:BRENTOIL" and p["side"] == "long" and p["size"] == 5.82
    assert p["leverage"] == 20 and p["margin_type"] == "isolated"
    assert p["liq_dist_pct"] == 2.549 and p["stop_px"] == [] and p["naked"]
    assert out["equity_usd"] == 34.581292


def test_a_full_stop_clears_the_flag_and_a_partial_does_not():
    assert assess(STATE, [STOP, TP], "xyz")["any_naked"] is False
    half = {**STOP, "sz": "2.9"}
    out = assess(STATE, [half], "xyz")
    assert out["positions"][0]["naked"] and out["positions"][0]["stop_covers"] == 2.9


def test_only_reduce_only_stops_count_and_other_coins_are_ignored():
    entry_order = {**STOP, "reduceOnly": False}                 # a resting entry is not protection
    other = {**STOP, "coin": "xyz:CL"}
    assert assess(STATE, [entry_order, other], "xyz")["any_naked"] is True


def test_flat_account_and_short_side():
    assert assess({"marginSummary": {}, "assetPositions": []}, []) == {
        "dex": "main", "equity_usd": 0.0, "notional_usd": 0.0, "positions": [], "any_naked": False}
    short = {"marginSummary": {}, "assetPositions": [{"position": {
        "coin": "BTC", "szi": "-0.01", "entryPx": "80000", "liquidationPx": None, "leverage": 5}}]}
    p = assess(short, [])["positions"][0]
    assert p["side"] == "short" and p["liq_px"] is None and p["liq_dist_pct"] is None and p["leverage"] == 5
