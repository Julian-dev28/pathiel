"""services/intel/scheduled_events.py and its data file.

Operator, 2026-11-05: the 11-03 midterms were not on the desk's calendar and
Brent fell 9% on the result. These pin the file's shape, the blackout
windows (the real 11-03 entry included), the thesis feed and the brief line.
"""
from __future__ import annotations

import json
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
    """The curated file passes the same validator scripts/event_add.py runs
    on every scanned row (test_event_add.py pins what it rejects)."""
    data = S.load()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["valid_through"])
    assert data["events"], "empty calendar"
    assert data["scanned"] == 0, "the test state dir has no overlay"
    for e in data["events"]:
        S.validate_event(e)
        assert "origin" not in e, f"origin is the overlay's field, not the curated file's: {e}"


def test_generated_kinds_are_not_duplicated_in_the_file():
    """FOMC decisions, CPI and OPEC+ meetings come from calendar_us.py."""
    kinds = {e["kind"] for e in S.load()["events"]}
    assert not kinds & {"fomc", "cpi", "opec_meeting", "opec_plus", "eia_weekly", "eia_steo"}


def test_the_midterms_are_on_the_record():
    mid = [e for e in S.load()["events"] if e["kind"] == "election" and e["date"] == "2026-11-03"]
    assert len(mid) == 1 and mid[0]["impact"] == 3 and set(mid[0]["coins"]) == {"oil", "silver"}


# ── the scan overlay ────────────────────────────────────────────────────────

def _curated(tmp_path, rows):
    p = tmp_path / "curated.json"
    p.write_text(json.dumps({"refreshed": "2026-11-05", "valid_through": "2027-01-31", "events": rows}))
    return str(p)


def _overlay(tmp_path, rows, state=None, name="scanned_events.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"version": 1, "events": rows, "state": state or {}}))
    return str(p)


ROW = {"date": "2026-11-10", "at_utc": "2026-11-10T15:00Z", "event": "Senate Foreign Relations hearing: Iran",
       "kind": "congress_hearing", "coins": ["oil"], "impact": 1, "verified": True, "source": "https://congress.gov/x"}


def test_overlay_path_follows_the_state_dir(isolated_state_dir, monkeypatch):
    assert S.overlay_path() == str(isolated_state_dir / "intel" / "scanned_events.json")
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(isolated_state_dir / "other"))
    assert S.overlay_path() == str(isolated_state_dir / "other" / "intel" / "scanned_events.json")


def test_load_merges_the_overlay_and_a_curated_row_wins(tmp_path):
    curated = _curated(tmp_path, [dict(ROW, impact=2, note="curated")])
    overlay = _overlay(tmp_path, [
        dict(ROW, event="SENATE foreign relations hearing: Iran!", impact=1, origin="scan", scanned_at="s"),  # conflict
        dict(ROW, date="2026-11-12", event="Senate Foreign Relations markup: H.R. 1422", kind="congress_markup"),
        {"date": "bad"},                                                                                 # invalid: skipped
    ])
    data = S.load(curated, overlay)
    assert data["valid_through"] == "2027-01-31" and data["scanned"] == 1 and len(data["events"]) == 2
    assert data["events"][0]["impact"] == 2 and data["events"][0]["note"] == "curated" and "origin" not in data["events"][0]
    assert data["events"][1]["origin"] == "scan" and data["events"][1]["kind"] == "congress_markup"


def test_a_missing_overlay_is_no_overlay(tmp_path):
    curated = _curated(tmp_path, [ROW])
    data = S.load(curated, str(tmp_path / "nope" / "scanned_events.json"))
    assert len(data["events"]) == 1 and data["scanned"] == 0
    assert S.read_overlay(str(tmp_path / "nope.json")) == {"version": 1, "events": [], "state": {}}


def test_load_sees_a_rewritten_overlay_in_the_same_process(tmp_path):
    """The old lru_cache meant a running MCP server never saw a scan; the
    mtime/size key means it does on the very next call."""
    curated = _curated(tmp_path, [ROW])
    overlay = _overlay(tmp_path, [])
    first = S.load(curated, overlay)
    assert first["scanned"] == 0 and S.load(curated, overlay) is first          # cached while nothing changed
    _overlay(tmp_path, [dict(ROW, date="2026-11-12", event="Second")])
    second = S.load(curated, overlay)
    assert second is not first and second["scanned"] == 1 and second["events"][1]["event"] == "Second"
    _overlay(tmp_path, [dict(ROW, date="2026-11-13", event="Third"), dict(ROW, date="2026-11-14", event="Fourth")])
    third = S.load(curated, overlay)
    assert third["scanned"] == 2 and [e["event"] for e in third["events"][1:]] == ["Third", "Fourth"]
    (tmp_path / "scanned_events.json").unlink()
    assert S.load(curated, overlay)["scanned"] == 0                              # removed: back to the file alone


def test_scanned_rows_carry_the_scan_tag_and_curated_ones_do_not(tmp_path):
    curated = _curated(tmp_path, [ROW])
    overlay = _overlay(tmp_path, [dict(ROW, date="2026-11-12", at_utc="2026-11-12T15:00Z", event="Scanned hearing",
                                       verified=False)])
    data = S.load(curated, overlay)
    lines = [S.short_line(S._row(e)) for e in data["events"]]
    assert lines == ["Tue 11-10 15:00Z Senate Foreign Relations hearing: Iran",
                     "Thu 11-12 15:00Z Scanned hearing (unverified) (scan)"]


def test_an_impact_3_scan_row_blocks_entries_like_a_curated_one(tmp_path, isolated_state_dir):
    import json as _json
    p = isolated_state_dir / "intel" / "scanned_events.json"
    p.parent.mkdir(parents=True)
    p.write_text(_json.dumps({"version": 1, "state": {}, "events": [
        dict(ROW, date="2026-11-18", at_utc=None, event="Iran sanctions snapback deadline", kind="sanctions_deadline",
             coins=["oil", "silver"], impact=3, origin="scan", scanned_at="x")]}))
    b = S.blackout(BRENT, ms(2026, 11, 18, 9, 0))
    assert b and b["kind"] == "sanctions_deadline" and b["until_utc"] == "2026-11-19 00:00Z"
    assert S.blackout(SILVER, ms(2026, 11, 17, 23, 59)) is None


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
    assert lines[0] == "Fri 11-06 13:30Z US jobs report (Oct NFP)"
    # 2026-11-05 research: Tehran's reply to the Qatar draft deal is expected Saturday.
    assert lines[1].startswith("Sat 11-07 Iran's reply") and lines[1].endswith("[blackout] (unverified)")
    cpi = upcoming_events(BRENT, 24.0, at_ms=ms(2026, 11, 10, 0, 0))
    assert any(S.short_line(r).endswith("US CPI release [blackout]") for r in cpi)


def test_unverified_rows_say_so_and_verified_ones_do_not():
    rows = S.market_events(ms(2026, 11, 11), ms(2026, 11, 20), "xyz:BRENTOIL")
    iaea = [r for r in rows if r["kind"] == "iaea"]
    momr = [r for r in rows if r["kind"] == "opec_momr"]
    assert iaea and S.short_line(iaea[0]).endswith("(unverified)")
    # OPEC's own 2026 release-date list (read 2026-11-05): Wednesday 11 November
    assert momr and not S.short_line(momr[0]).endswith("(unverified)")


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
