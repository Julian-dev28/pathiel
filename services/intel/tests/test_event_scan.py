"""scripts/event_scan.py: the deterministic half of the 12-hour event scan.

Operator, 2026-11-05: "a scan every 12 hours to get new events and wire them
into our trading decision". Fixtures are shaped from live api.congress.gov
v3 responses captured that day (services/intel/tests/fixtures/congress_*).
Every network call is injected; nothing here touches the real APIs or the
live state dir (conftest points PATHIEL_STATE_DIR at tmp_path).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from services.intel import scheduled_events as S
from services.intel.tests.conftest import fixture_json

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = _load("event_scan")
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
TODAY, END = NOW.date(), date(2026, 11, 26)
DETAILS = fixture_json("congress_meeting_details.json")


class FakeFetch:
    """Answers by URL from the fixtures; records every call; can fail a URL."""

    def __init__(self, *, fail=None, actions=None, house=None, details=None):
        self.calls = []
        self.fail = fail or {}
        self.actions = actions or fixture_json("congress_bill_actions_hr1422.json")
        self.house = house if house is not None else {"committeeMeetings": [], "pagination": {"count": 0}}
        self.details = details or DETAILS

    def __call__(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        for needle, err in self.fail.items():
            if needle in url:
                return None, err
        if url.endswith("/committee-meeting/119/senate"):
            return fixture_json("congress_meetings_senate.json"), None
        if url.endswith("/committee-meeting/119/house"):
            return self.house, None
        if "/committee-meeting/119/senate/" in url:
            return self.details[url.rsplit("/", 1)[1]], None
        if url.endswith("/bill/119/hr/1422/actions"):
            return self.actions, None
        if url.endswith("/bill/119/hr/1422"):
            return fixture_json("congress_bill_hr1422.json"), None
        if "/bill/" in url and url.endswith("/actions"):
            return {"actions": [], "pagination": {"count": 0}}, None
        if "/bill/" in url:
            return {"bill": {"title": ""}}, None
        if "federalregister.gov" in url:
            return fixture_json("federal_register_dated.json"), None
        return None, f"unexpected_url:{url}"


def _scan(tmp_path, fetch, **kw):
    kw.setdefault("api_key", "test-key")
    kw.setdefault("terms", ("Iran sanctions",))
    kw.setdefault("overlay_path", str(tmp_path / "scanned_events.json"))
    return E.scan(NOW, fetch=fetch, log_path=str(tmp_path / "progress.log"), **kw)


# ── pure parts ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("d,n", [
    (date(2025, 1, 2), 118), (date(2025, 1, 3), 119), (date(2026, 11, 5), 119),
    (date(2027, 1, 2), 119), (date(2027, 1, 3), 120),
])
def test_congress_number(d, n):
    assert E.congress_number(d) == n


def test_date_in_text_finds_a_coming_date_only():
    assert E.date_in_text("Floor consideration expected Thursday, November 12, 2026.", TODAY, END) == date(2026, 11, 12)
    assert E.date_in_text("as amended on March 16, 2026", TODAY, END) is None            # past
    assert E.date_in_text("the Committee on December 25", TODAY, END) is None           # past the window
    assert E.date_in_text("cloture on November 9", TODAY, END) == date(2026, 11, 9)     # no year: this year
    assert E.date_in_text("Read twice and referred.", TODAY, END) is None


def test_keyword_filter_keeps_sfrc_and_drops_veterans_affairs():
    kind, row = E.classify_meeting(DETAILS["338805"], TODAY, END)
    assert kind == "add" and row["kind"] == "congress_hearing" and row["impact"] == 1
    assert row["event"].startswith("Senate Foreign Relations hearing: Hearings to examine pending nominations")
    assert row["date"] == "2026-11-10" and row["at_utc"] == "2026-11-10T15:00Z" and row["verified"] is True
    assert row["source"] == "https://www.congress.gov/event/119th-congress/senate-event/338805"
    assert "419 Dirksen" in row["note"]
    assert E.classify_meeting(DETAILS["338810"], TODAY, END) is None       # Veterans' Affairs: not ours


def test_markup_on_a_watched_bill_is_impact_2_and_watches_its_bills():
    kind, row = E.classify_meeting(DETAILS["338820"], TODAY, END)
    assert kind == "add" and row["kind"] == "congress_markup" and row["impact"] == 2
    assert row["event"].startswith("Senate Foreign Relations markup: Business meeting to consider H.R. 1422")
    assert set(row["coins"]) == {"oil", "silver"} and row["_bills"] == ["119/hr/1422", "119/s/4830"]


def test_canceled_and_past_meetings():
    kind, row = E.classify_meeting(DETAILS["338830"], TODAY, END)
    assert kind == "cancel" and row["verified"] is False and "Strategic Petroleum Reserve" in row["event"]
    assert E.classify_meeting(DETAILS["338840"], TODAY, END) is None       # 10-28: before today


def test_classify_action_rows():
    acts = fixture_json("congress_bill_actions_hr1422.json")["actions"]
    url = "https://www.congress.gov/bill/119th-congress/house-bill/1422"
    rows = E.classify_action(acts[0], "H.R. 1422 Enhanced Iran Sanctions Act of 2025", url, TODAY, END)
    assert [r["kind"] for r in rows] == ["bill_action", "congress_floor"]
    assert rows[0]["date"] == "2026-11-04" and rows[0]["impact"] == 2 and set(rows[0]["coins"]) == {"oil", "silver"}
    assert rows[1]["date"] == "2026-11-12" and rows[1]["impact"] == 2 and "floor consideration" in rows[1]["event"]
    assert E.classify_action(acts[1], "x", url, TODAY, END) == []                      # IntroReferral: noise
    passage = E.classify_action(acts[2], "H.R. 1422", url, TODAY, END)
    assert len(passage) == 1 and passage[0]["impact"] == 3 and passage[0]["date"] == "2026-03-16"


def test_classify_fr_doc():
    docs = fixture_json("federal_register_dated.json")["results"]
    iran = E.classify_fr_doc(docs[0], TODAY, END)
    assert len(iran) == 1 and iran[0]["date"] == "2026-11-12" and iran[0]["impact"] == 2
    assert iran[0]["event"].startswith("Federal Register effective: Iran-Related Designations")
    doe = E.classify_fr_doc(docs[1], TODAY, END)
    assert len(doe) == 1 and doe[0]["date"] == "2026-11-20" and doe[0]["impact"] == 1 and "comments close" in doe[0]["event"]
    assert E.classify_fr_doc(docs[2], TODAY, END) == []                                 # effective 10-01: past


# ── the scan end to end (injected fetch) ────────────────────────────────────

def test_scan_writes_the_overlay_and_a_second_scan_adds_nothing(tmp_path):
    fetch = FakeFetch()
    out = _scan(tmp_path, fetch)
    added = {(r["date"], r["kind"]) for s, _, r in out["results"] if s == "added"}
    assert added == {("2026-11-10", "congress_hearing"), ("2026-11-12", "congress_markup"),
                     ("2026-11-04", "bill_action"), ("2026-11-12", "congress_floor"),
                     ("2026-11-12", "federal_register"), ("2026-11-20", "federal_register")}
    assert out["counts"]["added"] == 6 and out["counts"]["removed"] == 0 and out["failed"] == []
    assert out["line"].startswith("EVENT-SCAN 2026-11-05 12:00Z added=6 updated=0 removed=0 sources_ok=congress_meetings(5 checked)")
    assert "failed=-" in out["line"] and "skipped" not in out["line"]
    overlay = json.loads((tmp_path / "scanned_events.json").read_text())
    assert len(overlay["events"]) == 6 and all(e["origin"] == "scan" and e["scanned_at"] for e in overlay["events"])
    for e in overlay["events"]:
        S.validate_event(e)
    # The bill's title came from the API, the actions are remembered, the markup's bills are watched.
    assert overlay["state"]["bill_titles"]["119/hr/1422"] == "H.R. 1422 Enhanced Iran Sanctions Act of 2025"
    assert len(overlay["state"]["bill_actions"]["119/hr/1422"]) == 3
    assert overlay["state"]["watched_bills"] == ["119/s/4830"]
    assert (tmp_path / "progress.log").read_text().startswith("EVENT-SCAN 2026-11-05 12:00Z added=6")
    # Second scan: same data, nothing added or updated, the overlay is unchanged.
    before = (tmp_path / "scanned_events.json").read_text()
    out2 = _scan(tmp_path, FakeFetch())
    # The meeting and Federal Register rows come back unchanged; the bill's
    # actions are already in the state block, so they are not proposed again.
    assert out2["counts"]["added"] == 0 and out2["counts"]["updated"] == 0 and out2["counts"]["unchanged"] == 4
    assert (tmp_path / "scanned_events.json").read_text() == before


def test_a_new_bill_action_becomes_one_row_once(tmp_path):
    _scan(tmp_path, FakeFetch())
    acts = fixture_json("congress_bill_actions_hr1422.json")
    acts["actions"].insert(0, {"actionDate": "2026-11-05", "sourceSystem": {"name": "Senate"},
                               "text": "Passed Senate with an amendment by Yea-Nay Vote. 68 - 30. Record Vote Number: 512.",
                               "type": "Floor"})
    out = _scan(tmp_path, FakeFetch(actions=acts))
    new = [r for s, src, r in out["results"] if s == "added"]
    assert len(new) == 1 and new[0]["kind"] == "bill_action" and new[0]["impact"] == 3
    assert new[0]["event"].startswith("H.R. 1422 Enhanced Iran Sanctions Act of 2025: Passed Senate")
    out3 = _scan(tmp_path, FakeFetch(actions=acts))
    assert out3["counts"]["added"] == 0
    assert len(json.loads((tmp_path / "scanned_events.json").read_text())["events"]) == 7


def test_a_canceled_meeting_removes_its_row(tmp_path):
    live = json.loads(json.dumps(DETAILS["338830"]))
    live["committeeMeeting"]["meetingStatus"] = "Scheduled"
    out = _scan(tmp_path, FakeFetch(details=dict(DETAILS, **{"338830": live})))
    assert ("2026-11-17", "congress_hearing") in {(r["date"], r["kind"]) for s, _, r in out["results"] if s == "added"}
    out2 = _scan(tmp_path, FakeFetch())                # now the API says Canceled
    assert out2["counts"]["removed"] == 1 and "removed=1" in out2["line"]
    dates = {e["date"] for e in json.loads((tmp_path / "scanned_events.json").read_text())["events"]}
    assert "2026-11-17" not in dates


def test_a_failing_source_is_reported_not_fatal(tmp_path):
    out = _scan(tmp_path, FakeFetch(fail={"/committee-meeting/119/house": "http_500",
                                          "federalregister.gov": "timeout"}))
    assert out["counts"]["added"] == 4                           # Senate rows and the bill rows still landed
    assert "congress_meetings:house:http_500" in out["failed"]
    assert any(f.startswith("policy:federal_register:") and f.endswith("timeout") for f in out["failed"])
    assert "failed=congress_meetings:house:http_500,policy:federal_register:Iran sanctions:timeout" in out["line"]
    assert "congress_meetings(5 checked)" in out["line"]


def test_one_bad_meeting_detail_does_not_stop_the_chamber(tmp_path):
    out = _scan(tmp_path, FakeFetch(fail={"/senate/338810": "http_404"}))
    assert out["counts"]["added"] == 6 and "congress_meetings:senate:338810:http_404" in out["failed"]


def test_missing_key_skips_congress_cleanly(tmp_path):
    fetch = FakeFetch()
    out = _scan(tmp_path, fetch, api_key=None)
    assert out["skipped"] == ["congress_meetings(no CONGRESS_API_KEY)", "congress_bills(no CONGRESS_API_KEY)"]
    assert all("congress.gov" not in url for url, _ in fetch.calls)
    assert out["counts"]["added"] == 2 and {r["kind"] for _, _, r in out["results"]} == {"federal_register"}
    assert "skipped=congress_meetings(no CONGRESS_API_KEY),congress_bills(no CONGRESS_API_KEY)" in out["line"]


def test_dry_run_writes_nothing(tmp_path):
    out = _scan(tmp_path, FakeFetch(), dry_run=True)
    assert out["counts"]["added"] == 6 and out["line"].endswith(" dry_run")
    assert not (tmp_path / "scanned_events.json").exists() and not (tmp_path / "progress.log").exists()


def test_a_curated_row_is_not_duplicated_by_the_scan(tmp_path):
    curated = {"valid_through": "2027-01-31", "events": [{
        "date": "2026-11-10", "at_utc": "2026-11-10T15:00Z",
        "event": "Senate Foreign Relations hearing: Hearings to examine pending nominations.",
        "kind": "congress_hearing", "coins": ["oil"], "impact": 1, "verified": True, "source": "https://x"}]}
    (tmp_path / "curated.json").write_text(json.dumps(curated))
    out = _scan(tmp_path, FakeFetch(), curated_path=str(tmp_path / "curated.json"))
    assert out["counts"]["curated"] == 1 and out["counts"]["added"] == 5


def test_the_api_key_never_reaches_the_summary_or_the_log(tmp_path):
    out = _scan(tmp_path, FakeFetch(fail={"/committee-meeting/119/house": "http_403"}), api_key="SECRET-KEY-123")
    assert "SECRET-KEY-123" not in out["line"] and "SECRET-KEY-123" not in (tmp_path / "progress.log").read_text()
    assert "SECRET-KEY-123" not in (tmp_path / "scanned_events.json").read_text()


# ── the scan's rows reach the desk ──────────────────────────────────────────

def test_scanned_rows_show_in_the_brief_with_a_scan_tag_and_in_the_thesis(tmp_path, isolated_state_dir):
    from services.intel.sources.catalysts import scheduled_catalysts
    from services.strategies.desks import get_spec
    from services.strategies.event_blackout import upcoming_events
    _scan(tmp_path, FakeFetch(), overlay_path=S.overlay_path())
    at = int(datetime(2026, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 72h: through 11-12 23:59Z
    lines = [S.short_line(r) for r in upcoming_events("xyz:BRENTOIL", 72.0, at_ms=at)]
    assert any(ln.startswith("Tue 11-10 15:00Z Senate Foreign Relations hearing") and ln.endswith("(scan)") for ln in lines)
    assert any(ln.startswith("Thu 11-12 14:30Z Senate Foreign Relations markup") and ln.endswith("(scan)") for ln in lines)
    kinds = {c.kind for c in scheduled_catalysts(get_spec("oil"), datetime(2026, 11, 10, 0, tzinfo=timezone.utc))}
    assert {"congress_hearing", "congress_markup", "congress_floor", "federal_register"} <= kinds


# ── the eval's pure part ────────────────────────────────────────────────────

def test_eval_missing_curated():
    from services.intel.evals.eval_event_scan import missing_curated
    curated = [{"date": "2026-11-06", "kind": "nfp", "event": "US jobs report"},
               {"date": "2026-11-20", "kind": "pce", "event": "PCE"},
               {"date": "2026-11-03", "kind": "election", "event": "midterms"}]
    merged = [dict(curated[0], origin="scheduled_events.json"), {"date": "2026-11-07", "kind": "x", "event": "y", "origin": "scan"}]
    assert missing_curated(curated, merged, date(2026, 11, 5), days=7) == []        # 11-20 and 11-03 are outside
    assert missing_curated(curated, [], date(2026, 11, 5), days=7) == [curated[0]]
    assert missing_curated(curated, merged, date(2026, 11, 14), days=7) == [curated[1]]


# ── the skills ──────────────────────────────────────────────────────────────

def test_desk_loops_runs_the_scan_every_30_minutes():
    text = (ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md").read_text()
    assert "`17,47 * * * *`" in text and "`/event-calendar scan`" in text
    assert "python3 scripts/event_scan.py" in text
    assert text.index("python3 scripts/event_scan.py") < text.index("Price watcher")


def test_event_calendar_skill_has_the_scan_section():
    text = (ROOT / ".claude" / "skills" / "event-calendar" / "SKILL.md").read_text()
    assert "## Scan (every 30 minutes)" in text
    scan = text.split("## Scan (every 30 minutes)", 1)[1]
    assert "python3 scripts/event_scan.py" in scan and "scripts/event_add.py" in scan
    assert "one general-purpose agent" in scan.lower() or "one general-purpose research agent" in scan.lower()
    assert "never edits `services/intel/data/scheduled_events.json`" in " ".join(scan.split())
    assert "--allow-impact-3" in scan and "/tmp/oil-loop/progress.log" in scan
    assert "21 days" in scan
