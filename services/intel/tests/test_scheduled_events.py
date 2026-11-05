"""services/intel/scheduled_events.py and its data file.

Operator, 2026-11-05: the 11-03 midterms were not on the desk's calendar and
Brent fell 9% on the result. These pin the file's shape, the blackout
windows (the real 11-03 entry included), the thesis feed and the brief line.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.intel import scheduled_events as S

ROOT = Path(__file__).resolve().parents[3]
BRENT, SILVER = "xyz:BRENTOIL", "xyz:SILVER"


def ms(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


# ── the file ────────────────────────────────────────────────────────────────

def test_every_row_is_complete_and_well_formed():
    data = S.load()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["valid_through"])
    assert data["events"], "empty calendar"
    for e in data["events"]:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e["date"]), e
        assert e["at_utc"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", e["at_utc"]), e
        assert re.fullmatch(r"[a-z][a-z0-9_]*", e["kind"]), e
        assert e["coins"] and set(e["coins"]) <= set(S.COIN_GROUPS), e
        assert e["impact"] in (1, 2, 3) and isinstance(e["verified"], bool) and e["source"], e


def test_generated_kinds_are_not_duplicated_in_the_file():
    """FOMC decisions, CPI and OPEC+ meetings come from calendar_us.py."""
    kinds = {e["kind"] for e in S.load()["events"]}
    assert not kinds & {"fomc", "cpi", "opec_meeting", "opec_plus", "eia_weekly", "eia_steo"}


def test_the_midterms_are_on_the_record():
    mid = [e for e in S.load()["events"] if e["kind"] == "election" and e["date"] == "2026-11-03"]
    assert len(mid) == 1 and mid[0]["impact"] == 3 and set(mid[0]["coins"]) == {"oil", "silver"}


# ── blackout windows ────────────────────────────────────────────────────────

def test_the_real_11_03_entry_falls_inside_the_midterm_blackout():
    """The 11-03 13:53Z Brent long lost $23.67; the window runs from 24h
    before the polls closed (11-02 23:00Z) to 11-04 18:00Z."""
    b = S.blackout(BRENT, ms(2026, 11, 3, 13, 53))
    assert b and b["kind"] == "election" and b["until_utc"] == "2026-11-04 18:00Z"
    assert S.blackout(SILVER, ms(2026, 11, 3, 13, 53))
    assert S.blackout(BRENT, ms(2026, 11, 2, 22, 59)) is None
    assert S.blackout(BRENT, ms(2026, 11, 4, 18, 0)) is None


@pytest.mark.parametrize("at,coin,kind,until", [
    (ms(2026, 11, 10, 11, 30), BRENT, "cpi", "2026-11-10 15:30Z"),          # 08:30 ET = 13:30Z, 2h either side
    (ms(2026, 11, 10, 15, 29), SILVER, "cpi", "2026-11-10 15:30Z"),
    (ms(2026, 12, 9, 17, 0), SILVER, "fomc", "2026-12-09 21:00Z"),          # 14:00 EST = 19:00Z
    (ms(2026, 11, 29, 0, 0), BRENT, "opec_meeting", "2026-11-30 00:00Z"),   # no published time: the whole day
    (ms(2026, 11, 29, 23, 59), BRENT, "opec_meeting", "2026-11-30 00:00Z"),
])
def test_impact_3_windows(at, coin, kind, until):
    b = S.blackout(coin, at)
    assert b and b["kind"] == kind and b["until_utc"] == until


@pytest.mark.parametrize("at,coin", [
    (ms(2026, 11, 10, 11, 29), BRENT),     # one minute before the CPI window
    (ms(2026, 11, 6, 13, 30), BRENT),      # jobs report: impact 2, shown, not blocking
    (ms(2026, 11, 29, 12, 0), SILVER),     # OPEC+ is oil only
    (ms(2026, 11, 26, 12, 0), BRENT),      # Thanksgiving: thin, not blocking
])
def test_not_blocked(at, coin):
    assert S.blackout(coin, at) is None


def test_event_blackout_checks_eia_first_then_the_calendar():
    from services.strategies.event_blackout import event_blackout
    eia = event_blackout(BRENT, ms(2026, 11, 12, 16, 0))          # Thu 11-12 (Veterans Day shift)
    assert eia and "EIA blackout" in eia["reason"]
    cpi = event_blackout(SILVER, ms(2026, 11, 10, 13, 0))
    assert cpi and cpi["reason"].startswith("Event blackout") and "CPI" in cpi["reason"]
    assert event_blackout(SILVER, ms(2026, 11, 12, 16, 0)) is None  # EIA never blocks silver


# ── what the desk sees ──────────────────────────────────────────────────────

def test_upcoming_events_and_the_brief_line():
    from services.strategies.event_blackout import upcoming_events
    evs = upcoming_events(SILVER, 72.0, at_ms=ms(2026, 11, 5, 11, 0))
    lines = [S.short_line(r) for r in evs]
    assert lines == ["Fri 11-06 13:30Z US jobs report (Oct NFP)"]
    cpi = upcoming_events(BRENT, 24.0, at_ms=ms(2026, 11, 10, 0, 0))
    assert any(S.short_line(r).endswith("US CPI release [blackout]") for r in cpi)


def test_unverified_rows_say_so():
    rows = S.market_events(ms(2026, 11, 11), ms(2026, 11, 12), "xyz:BRENTOIL")
    momr = [r for r in rows if r["kind"] == "opec_momr"]
    assert momr and S.short_line(momr[0]).endswith("(unverified)")


def test_calendar_warning_two_weeks_before_it_runs_out():
    assert S.calendar_warning(ms(2026, 11, 5)) is None
    w = S.calendar_warning(ms(2027, 1, 20))
    assert w and "event calendar ends 2027-01-31" in w and "event-calendar skill" in w


def test_the_thesis_gets_the_scheduled_events():
    from services.intel.sources.catalysts import calendar_catalysts
    from services.strategies.desks import get_spec
    now = datetime(2026, 11, 5, 11, tzinfo=timezone.utc)
    oil = {c.kind for c in calendar_catalysts(get_spec("oil"), now)}
    metals = {c.kind for c in calendar_catalysts(get_spec("metals"), now)}
    assert {"nfp", "opec_momr", "iea_omr", "cpi"} <= oil and "china_data" not in oil
    assert {"nfp", "china_data", "cpi"} <= metals and "opec_momr" not in metals


def test_every_entry_path_uses_the_event_blackout():
    mcp = (ROOT / "scripts" / "pathiel-mcp-server.py").read_text()
    assert "blackout = event_blackout(coin)" in mcp and "active = event_blackout(coin)" in mcp
    assert "blackout = event_blackout(coin, int(now * 1000))" in (ROOT / "scripts" / "trigger_watch.py").read_text()
    for rel in (".claude/agents/desk-tick.md", "agents/desk-tick.md"):
        text = (ROOT / rel).read_text()
        assert "Event blackout (every desk" in text and "Events next 72h" in text, rel


# ── the what-if eval ────────────────────────────────────────────────────────

def test_event_direction_math():
    from services.strategies.evals import event_direction_test as E
    H = E.HOUR_MS
    bars = [{"t": i * H, "c": 100.0 if i < 5 else 90.0} for i in range(40)]
    sc = E.score_event(bars, 3 * H, 30 * H)          # 100 -> 90
    assert sc["move_pct"] == -10.0 and sc["short_equity_pct"] == 20.0 and sc["long_equity_pct"] == -20.0
    assert E.score_event(bars, 3 * H, 100 * H) is None   # candles do not cover the close
    s = E.summarize([{"short_equity_pct": 20.0}, {"short_equity_pct": -5.0}])
    assert (s["n"], s["short_wins"], s["short_total_pct"], s["long_total_pct"]) == (2, 1, 15.0, -15.0)
