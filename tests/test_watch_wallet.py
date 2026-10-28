"""services/accounts/positions.py: the pure assessment, on the shapes
Hyperliquid returns. (Moved out of scripts/watch_wallet.py 2026-10-23; that
script is now a thin CLI importing this module.)"""
from __future__ import annotations

from pathlib import Path

from services.accounts.positions import assess

# Julien's wallet on 2026-10-23 16:40 UTC, as the venue returned it.
STATE = {"marginSummary": {"accountValue": "34.581292", "totalNtlPos": "627.1632"},
         "assetPositions": [{"position": {
             "coin": "xyz:BRENTOIL", "szi": "5.82", "entryPx": "107.16", "liquidationPx": "104.4289202573",
             "leverage": {"type": "isolated", "value": 20, "rawUsd": "-592.58"},
             "positionValue": "627.1632", "unrealizedPnl": "3.492", "marginUsed": "31.3582"}}]}
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
    # margin in use, per position and per dex (2026-10-24: shown on the Wallets card)
    assert p["margin_usd"] == 31.36
    assert out["margin_used_usd"] == 31.36 and out["margin_used_pct"] == 90.7


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
        "dex": "main", "equity_usd": 0.0, "notional_usd": 0.0, "positions": [],
        "margin_used_usd": 0.0, "margin_used_pct": None, "any_naked": False,
        "resting_entries": [], "stranded_orders": 0}
    short = {"marginSummary": {}, "assetPositions": [{"position": {
        "coin": "BTC", "szi": "-0.01", "entryPx": "80000", "liquidationPx": None, "leverage": 5}}]}
    p = assess(short, [])["positions"][0]
    assert p["side"] == "short" and p["liq_px"] is None and p["liq_dist_pct"] is None and p["leverage"] == 5



def test_mark_fields_show_where_the_position_stands():
    # 2026-10-23: Julien asked to see entry vs mark vs stop vs target at a glance.
    out = assess(STATE, [STOP, TP], "xyz", mids={"xyz:BRENTOIL": "108.45"})
    p = out["positions"][0]
    assert p["mark_px"] == 108.45
    assert p["pnl_pct"] == 1.204                      # long: (108.45-107.16)/107.16
    assert p["to_stop_pct"] == 1.614                  # (108.45-106.70)/108.45
    assert p["to_tp_pct"] == 0.0                      # mark sits on the target
    assert p["r_now"] == 2.8                          # 1.29 gained over 0.46 risk
    # a short reads the other way, and the NEAREST stop/target is the one measured
    short = {"marginSummary": {}, "assetPositions": [{"position": {"coin": "xyz:GOLD", "szi": "-0.5", "entryPx": "4028"}}]}
    stops = [{"coin": "xyz:GOLD", "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "4042", "sz": "0.5"},
             {"coin": "xyz:GOLD", "reduceOnly": True, "orderType": "Stop Market", "triggerPx": "4060", "sz": "0.5"}]
    q = assess(short, stops, "xyz", mids={"xyz:GOLD": 4000})["positions"][0]
    assert q["pnl_pct"] == 0.695 and q["to_stop_pct"] == 1.05 and q["r_now"] == 2.0 and q["to_tp_pct"] is None


def test_without_mids_the_mark_fields_are_none_and_nothing_else_changes():
    p = assess(STATE, [STOP, TP], "xyz")["positions"][0]
    assert p["mark_px"] is None and p["pnl_pct"] is None and p["r_now"] is None and p["naked"] is False


def test_pnl_percent_is_on_the_margin_like_hyperliquids_frontend():
    """Operator, 2026-10-28: show "+$21.74 (+30.4%)" like Hyperliquid, not
    the 0.3% the price moved. The venue's returnOnEquity is used as-is."""
    live = {"marginSummary": {"accountValue": "90"}, "assetPositions": [{"position": {
        "coin": "xyz:BRENTOIL", "szi": "13.08", "entryPx": "109.4579", "leverage": {"type": "isolated", "value": 20},
        "positionValue": "1453.4", "marginUsed": "71.6", "unrealizedPnl": "21.74",
        "returnOnEquity": "0.30373", "liquidationPx": "106.6556"}}]}
    p = assess(live, [], "xyz", mids={"xyz:BRENTOIL": "111.12"})["positions"][0]
    assert p["roe_pct"] == 30.37 and p["upnl_usd"] == 21.74
    # no returnOnEquity from the venue: the price move times leverage
    del live["assetPositions"][0]["position"]["returnOnEquity"]
    q = assess(live, [], "xyz", mids={"xyz:BRENTOIL": "111.12"})["positions"][0]
    assert q["roe_pct"] == round(q["pnl_pct"] * 20, 2)
    # neither: None, never a made-up number
    assert assess(live, [], "xyz")["positions"][0]["roe_pct"] is None


def test_the_wallets_table_prints_pnl_as_dollars_and_return_on_margin():
    js = (Path(__file__).resolve().parents[1] / "pathiel" / "static" / "oil_desk.js").read_text()
    assert "fmtPnl(p.upnl_usd, p.roe_pct)" in js
    assert '<th class="num">P&amp;L</th>' in js and '<th class="num">uPnL</th>' not in js
