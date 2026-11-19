"""services/strategies/entry_card.py: the Levels card's entry rows.

Operator, 2026-11-19, looking at the Levels card: "should add entry to FE
right?" -- the fix adds a `entry` field to GET /api/desks/{id}/levels
(services/strategies/api.py's _levels_entry) built ONLY from next_trade()
(services/strategies/next_trade.py) and event_blackout() (services/
strategies/event_blackout.py), the exact functions the desk tick and
GET /next already use. Every test here calls those two real functions on a
fixed fixture (no mocking, no network, no clock reads -- event_blackout's
calendar is pure given `at_ms`) and checks entry_card.build()'s numbers
equal theirs exactly: proof nothing is re-derived.
"""
from __future__ import annotations

import pytest

from services.strategies import entry_card as EC
from services.strategies import event_blackout as EB
from services.strategies.next_trade import next_trade

# The same Brent fixture test_next_trade.py uses: mid/24h range/thesis
# entry from the real 2026-10-30 tick family, BRENT_STOP_BUFFER (0.75).
LOW, HIGH, THESIS_ENTRY, BUF, LEV, MAXLEV = 109.01, 114.07, 113.87, 0.75, 20, 20


def _nt(mid, **kw):
    kw.setdefault("thesis_entry", THESIS_ENTRY)
    kw.setdefault("leverage", LEV)
    kw.setdefault("coin_max_leverage", MAXLEV)
    kw.setdefault("stop_buffer", BUF)
    return next_trade(kw.pop("side", "long"), mid, kw.pop("low", LOW), kw.pop("high", HIGH), **kw)


def _ms(*parts):
    from datetime import datetime, timezone
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# ── no thesis ────────────────────────────────────────────────────────────────

def test_no_side_is_the_no_thesis_status():
    for side in (None, "flat", "pair"):
        out = EC.build(mid=113.135, atr14=0.83, side=side, thesis_entry_px=None,
                       next_trade_result=None, blackout=None)
        assert out == {
            "ok": False, "has_thesis": False, "side": None, "thesis_entry_px": None,
            "live": {"status": "no_thesis", "status_text": "no thesis", "ok": False,
                    "entry_px": None, "stop_px": None, "tp_px": None, "r_multiple": None,
                    "rule_verdict": None},
            "distance": {"price": None, "pct": None, "atr": None},
        }


# ── ready: the rule trades at the mid right now ─────────────────────────────

def test_ready_matches_next_trade_exactly():
    mid = 111.30
    nt = _nt(mid, low=111.03)
    assert nt["ok"] is True and nt["blockers"] == []        # sanity: this IS the "ready" case

    out = EC.build(mid=mid, atr14=0.83, side="long", thesis_entry_px=THESIS_ENTRY,
                   next_trade_result=nt, blackout=None)
    assert out["ok"] is True and out["has_thesis"] is True
    assert out["side"] == "long" and out["thesis_entry_px"] == THESIS_ENTRY
    live = out["live"]
    assert live["status"] == "ready" and live["status_text"] == "ready" and live["ok"] is True
    # Every number is nt's own, not re-derived.
    assert live["entry_px"] == nt["entry_px"] and live["stop_px"] == nt["stop_px"]
    assert live["tp_px"] == nt["tp_px"]
    assert live["r_multiple"] == nt["r_to_targets"][0]["r_multiple"]
    assert live["rule_verdict"] == nt["verdict"] == "buys now"
    # At "ready" the live entry IS the mid: zero distance.
    assert out["distance"] == {"price": 0.0, "pct": 0.0, "atr": 0.0}


# ── waiting: R/leverage not met yet, no chase/break marker ──────────────────

def test_waiting_matches_the_10_08z_tick():
    mid = 113.135
    nt = _nt(mid)
    assert nt["ok"] is False and nt["r_now"] == 0.07   # sanity, pinned in test_next_trade.py too

    out = EC.build(mid=mid, atr14=0.83, side="long", thesis_entry_px=THESIS_ENTRY,
                   next_trade_result=nt, blackout=None)
    live = out["live"]
    assert out["ok"] is False
    assert live["status"] == "waiting" and live["status_text"] == "waiting" and live["ok"] is False
    assert live["entry_px"] == nt["trigger_px"] == pytest.approx(110.0066, abs=1e-3)
    assert live["stop_px"] == nt["stop_px"] == pytest.approx(108.26)
    assert live["tp_px"] == nt["tp_px"]
    assert live["r_multiple"] == nt["r_to_targets"][0]["r_multiple"]
    assert live["rule_verdict"] == nt["verdict"]
    # Distance from the mid to the trigger price, in price/%/ATRs -- all
    # off nt's own trigger_px, never a second formula.
    expected_price = abs(mid - nt["trigger_px"])
    assert out["distance"]["price"] == pytest.approx(expected_price, abs=1e-6)
    assert out["distance"]["pct"] == pytest.approx(expected_price / mid, abs=1e-6)
    assert out["distance"]["atr"] == pytest.approx(expected_price / 0.83, abs=1e-3)


# ── chase: mid is past the thesis's never-chase ceiling ─────────────────────

def test_chase_status_on_the_never_chase_blocker():
    mid = 111.30
    nt = _nt(mid, low=111.03, thesis_entry=111.20)
    assert nt["ok"] is False and any("never chase" in b for b in nt["blockers"])

    out = EC.build(mid=mid, atr14=0.83, side="long", thesis_entry_px=111.20,
                   next_trade_result=nt, blackout=None)
    live = out["live"]
    assert live["status"] == "chase" and live["status_text"] == "chase" and live["ok"] is False
    assert live["entry_px"] == nt["trigger_px"] == pytest.approx(111.20)


# ── break: the mid is past the 24h range's own edge ─────────────────────────

def test_break_status_when_the_mid_is_past_the_24h_edge():
    mid = 100.0
    nt = _nt(mid, low=105.0, high=120.0, thesis_entry=None, stop_buffer=1.0)
    assert nt["ok"] is False and any("no entry after a break" in b for b in nt["blockers"])
    assert not any("never chase" in b for b in nt["blockers"])   # isolates break from chase

    out = EC.build(mid=mid, atr14=1.2, side="long", thesis_entry_px=None,
                   next_trade_result=nt, blackout=None)
    live = out["live"]
    assert live["status"] == "break" and live["status_text"] == "break" and live["ok"] is False
    assert live["entry_px"] == nt["trigger_px"] and live["stop_px"] == nt["stop_px"]


# ── blackout: the real EIA calendar, a fixed timestamp, overrides "ready" ───

def test_blackout_overrides_a_ready_rule():
    """event_blackout.event_blackout is pure given `at_ms` -- no clock read,
    no network, just the sourced EIA calendar (services/strategies/backtest/
    oil_calendar.py). 2026-11-04 15:30Z sits inside that week's window
    (test_event_blackout.py's own REPORT fixture)."""
    at_ms = _ms(2026, 11, 4, 15, 30)
    blackout = EB.event_blackout("xyz:BRENTOIL", at_ms, side="long")
    assert blackout and blackout["until_utc"] == "2026-11-04 17:30Z"   # pinned in test_event_blackout.py

    mid = 111.30
    nt = _nt(mid, low=111.03)
    assert nt["ok"] is True   # the rule alone would trade now

    out = EC.build(mid=mid, atr14=0.83, side="long", thesis_entry_px=THESIS_ENTRY,
                   next_trade_result=nt, blackout=blackout)
    live = out["live"]
    assert out["ok"] is False and live["ok"] is False           # blackout wins even though the rule says ready
    assert live["status"] == "blackout"
    assert live["status_text"] == f"blackout until {blackout['until_utc']}" == "blackout until 2026-11-04 17:30Z"
    # The numbers underneath are still the rule's own (so the card can show
    # "would buy at X" alongside "blocked until HH:MMZ").
    assert live["entry_px"] == nt["entry_px"] and live["stop_px"] == nt["stop_px"]


def test_silver_never_blacks_out_on_the_eia_calendar():
    """Sanity the fixture above is EIA-specific, not a universal block: a
    non-oil coin at the same timestamp never gets the EIA window (same
    check as test_event_blackout.py::test_silver_and_other_coins_never_
    black_out; stays on eia_blackout itself, not the full event_blackout()
    chain, which also reads scheduled-event/headline state this test does
    not control)."""
    at_ms = _ms(2026, 11, 4, 15, 30)
    assert EB.eia_blackout("xyz:SILVER", at_ms) is None
