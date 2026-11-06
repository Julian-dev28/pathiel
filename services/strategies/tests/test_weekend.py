"""Weekend flat (services/strategies/weekend.py), operator 2026-11-05.

The window (Fri 16:00 to Sun 18:00 New York) across both DST switches, the
coin scope (oil only), the live gate's order (EIA > weekend > scheduled >
headline), the sim/live parity minute by minute, the live Params and the
agent text. Synthetic clocks only, no network.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.strategies import event_blackout as EB
from services.strategies import weekend as W
from services.strategies.backtest import desk_tick_sim as SIM
from services.strategies.backtest.desk_tick_sim import (
    live_params, live_params_before_no_break, live_params_before_weekend_flat)
from services.strategies.evals import live_params as LP
from services.strategies.evals.market_scout import scouting_params

ROOT = Path(__file__).resolve().parents[3]
MIN = 60_000
BRENT, WTI = "xyz:BRENTOIL", "xyz:CL"


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


# ── the window ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("when,closed", [
    # this coming weekend, US winter (EST): Fri 21:00Z to Sun 23:00Z
    ((2026, 11, 6, 20, 59), False), ((2026, 11, 6, 21, 0), True), ((2026, 11, 7, 12, 0), True),
    ((2026, 11, 8, 22, 59), True), ((2026, 11, 8, 23, 0), False),
    # US summer (EDT): Fri 20:00Z to Sun 22:00Z
    ((2026, 10, 23, 19, 59), False), ((2026, 10, 23, 20, 0), True), ((2026, 10, 25, 21, 59), True),
    ((2026, 10, 25, 22, 0), False),
    # the fall-back weekend (2026-11-01): closes on summer time, reopens on winter time
    ((2026, 10, 30, 19, 59), False), ((2026, 10, 30, 20, 0), True), ((2026, 11, 1, 22, 59), True),
    ((2026, 11, 1, 23, 0), False),
    # the spring-forward weekend (2027-03-14): closes on winter time, reopens on summer time
    ((2027, 3, 12, 20, 59), False), ((2027, 3, 12, 21, 0), True), ((2027, 3, 14, 21, 59), True),
    ((2027, 3, 14, 22, 0), False),
    # weekdays, holidays included (Thanksgiving 2026-11-26, Christmas Friday 2026-12-25 before 16:00 ET)
    ((2026, 11, 26, 12, 0), False), ((2026, 12, 25, 20, 59), False), ((2026, 12, 25, 21, 0), True),
])
def test_weekend_closed_edges(when, closed):
    assert W.weekend_closed(ms(*when)) is closed


def test_this_weekends_window_in_utc():
    """Fri 2026-11-06 / Sun 2026-11-08, US winter time."""
    nxt = W.next_oil_weekend(BRENT, ms(2026, 11, 5, 16))
    assert (nxt["start_utc"], nxt["until_utc"], nxt["active"]) == ("2026-11-06 21:00Z", "2026-11-08 23:00Z", False)
    assert nxt["start_ms"] == ms(2026, 11, 6, 21) and nxt["end_ms"] == ms(2026, 11, 8, 23)
    inside = W.next_oil_weekend(WTI, ms(2026, 11, 7, 3))
    assert inside["active"] and (inside["start_ms"], inside["end_ms"]) == (nxt["start_ms"], nxt["end_ms"])
    after = W.next_oil_weekend(BRENT, ms(2026, 11, 8, 23))          # the reopen minute: next week
    assert after["start_utc"] == "2026-11-13 21:00Z" and not after["active"]


def test_window_spans_the_dst_switches():
    fall = W.weekend_window(ms(2026, 10, 31, 12))
    assert fall == (ms(2026, 10, 30, 20), ms(2026, 11, 1, 23))        # 51 hours: closes EDT, reopens EST
    spring = W.weekend_window(ms(2027, 3, 13, 12))
    assert spring == (ms(2027, 3, 12, 21), ms(2027, 3, 14, 22))       # 49 hours
    assert W.weekend_window(ms(2026, 11, 6, 20, 59)) == (ms(2026, 11, 6, 21), ms(2026, 11, 8, 23))


def test_window_and_closed_flag_agree_minute_by_minute_for_a_year():
    """`weekend_closed(t)` (the sim's definition) is exactly `start <= t <
    end` of `weekend_window(t)`: every 15 minutes for a year, every minute
    around both DST switches and across the first and last weekend."""
    start, end = ms(2026, 6, 1), ms(2027, 6, 1)
    probes = list(range(start, end, 15 * MIN))
    for a, b in ((ms(2026, 10, 30, 12), ms(2026, 11, 2, 12)), (ms(2027, 3, 12, 12), ms(2027, 3, 15, 12)),
                 (ms(2026, 6, 5, 12), ms(2026, 6, 8, 12)), (ms(2027, 5, 28, 12), ms(2027, 5, 31, 12))):
        probes += range(a, b, MIN)
    for t in probes:
        s, e = W.weekend_window(t)
        assert (s <= t < e) == W.weekend_closed(t), datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        assert s > t or W.weekend_closed(t)                 # a weekday points at a future window


def test_the_sim_imports_this_definition():
    assert SIM.weekend_closed is W.weekend_closed
    assert (SIM.WEEKEND_CLOSE_NY, SIM.WEEKEND_OPEN_NY) == ((4, 16 * 60), (6, 18 * 60))


# ── coin scope and the blackout shape ──────────────────────────────────────

SAT = ms(2026, 11, 7, 12)


def test_oil_weekend_is_oil_only():
    for coin in (BRENT, WTI):
        hit = W.oil_weekend(coin, SAT)
        assert hit["flatten"] is True and hit["kind"] == "weekend" and hit["coin"] == coin
        assert hit["reason"] == "Weekend flat: no new oil entries until 2026-11-08 23:00Z (oil futures weekend)"
        assert hit["until_utc"] == "2026-11-08 23:00Z" and hit["start_utc"] == "2026-11-06 21:00Z"
    for coin in ("xyz:GOLD", "xyz:SILVER", "BTC", "xyz:NATGAS", ""):
        assert W.oil_weekend(coin, SAT) is None and W.next_oil_weekend(coin, SAT) is None
    assert W.oil_weekend(BRENT, ms(2026, 11, 6, 20, 59)) is None
    assert W.oil_weekend(BRENT, ms(2026, 11, 8, 23)) is None


def test_event_blackout_clock_wrappers(monkeypatch):
    monkeypatch.setattr(EB, "now_ms", lambda: SAT)
    assert EB.weekend_blackout(BRENT)["kind"] == "weekend" and EB.weekend_blackout("xyz:GOLD") is None
    assert EB.next_weekend(BRENT)["active"] is True
    monkeypatch.setattr(EB, "now_ms", lambda: ms(2026, 11, 5, 16))
    assert EB.weekend_blackout(BRENT) is None and EB.next_weekend(BRENT)["start_utc"] == "2026-11-06 21:00Z"
    assert EB.OIL_COINS is W.OIL_COINS


# ── the live gate's order: EIA > weekend > scheduled > headline ────────────

def test_event_blackout_order(monkeypatch):
    from services.intel import headline_watch, scheduled_events
    sched = {"kind": "fomc", "reason": "Event blackout: fomc"}
    head = {"kind": "headline", "reason": "Headline blackout"}
    monkeypatch.setattr(scheduled_events, "blackout", lambda coin, t, path=None: sched)
    monkeypatch.setattr(headline_watch, "headline_blackout", lambda coin, t, side=None, path=None: head)
    # Saturday: the weekend outranks a scheduled event and a headline on oil; gold sees the event
    assert EB.event_blackout(BRENT, SAT)["kind"] == "weekend"
    assert EB.event_blackout("xyz:GOLD", SAT) is sched
    # the EIA window outranks everything (Wed 2026-11-04 15:30Z, report time)
    eia = ms(2026, 11, 4, 15, 30)
    assert EB.event_blackout(BRENT, eia)["event"] == "EIA weekly petroleum report"
    # a weekday with nothing scheduled: the headline is last
    monkeypatch.setattr(scheduled_events, "blackout", lambda coin, t, path=None: None)
    assert EB.event_blackout(BRENT, ms(2026, 11, 5, 16)) is head


def test_event_blackout_is_none_on_a_quiet_weekday(monkeypatch):
    from services.intel import headline_watch, scheduled_events
    monkeypatch.setattr(scheduled_events, "blackout", lambda coin, t, path=None: None)
    monkeypatch.setattr(headline_watch, "headline_blackout", lambda coin, t, side=None, path=None: None)
    assert EB.event_blackout(BRENT, ms(2026, 11, 5, 16)) is None
    assert EB.event_blackout(BRENT, ms(2026, 11, 6, 20, 59)) is None
    assert EB.event_blackout(BRENT, ms(2026, 11, 6, 21))["flatten"] is True


# ── sim and live in step ───────────────────────────────────────────────────

def test_live_params_carry_the_weekend_flat_and_the_replays_do_not():
    assert live_params().weekend_off == "flatten"
    assert LP.live_brent_params().weekend_off == "flatten"
    assert live_params_before_weekend_flat().weekend_off is None
    assert live_params_before_no_break().weekend_off is None and live_params_before_no_break().no_break_entry is False
    assert live_params_before_weekend_flat().no_break_entry is True


def test_silver_and_gold_params_trade_the_weekend():
    assert scouting_params(25.0, 0.5).weekend_off is None
    assert LP.live_silver_params(25.0, 0.5).weekend_off is None
    assert LP.live_gold_params(25.0, 12.0).weekend_off is None
    assert LP.live_silver_params(25.0, 0.5).risk_cap_frac == pytest.approx(0.15)    # the cap still rides along


def test_the_agent_text_and_the_plugin_copy_carry_the_rule():
    for rel in (".claude/agents/desk-tick.md", "agents/desk-tick.md"):
        text = (ROOT / rel).read_text()
        assert "Weekend flat (oil coins only, since 2026-11-05" in text, rel
        assert "scripts/weekend.py --coin COIN" in text, rel
        assert '`"flatten": true` AND the desk holds a\n  position on COIN: `close_position {coin: COIN}`' in text, rel
        assert "action=CLOSED weekend flat until <until_utc>" in text, rel
        assert "Gold and\n  silver are never flattened for it" in text, rel
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/weekend.py' in (ROOT / "agents" / "desk-tick.md").read_text()
    loops = (ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md").read_text()
    assert '"reason": "weekend_flat"' in loops and "run `/desk-tick oil`" in loops
    doc = (ROOT / "docs" / "strategy" / "OIL_STRATEGY.md").read_text()
    assert "**Live: weekend flat.**" in doc and "5 of 8" in doc
