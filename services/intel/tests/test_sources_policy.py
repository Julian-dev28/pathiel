"""Unit tests for services/intel/sources/policy.py against real captured
Federal Register responses and the real OFAC recent-actions HTML
structure. congress.gov is exercised only for its documented missing-key
and mocked-key paths — no live key was available to capture a real bill
list response with (see policy.py's module docstring)."""
from __future__ import annotations



from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import policy
from services.intel.tests.conftest import fixture_json, fixture_text


def _spec_with_topics(topics) -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t",
                     instruments=[Instrument(coin="xyz:CL", label="WTI")],
                     policy_topics=topics)


# ── Federal Register ─────────────────────────────────────────────────

def test_federal_register_real_fixture_maps_fields(monkeypatch):
    fixture = fixture_json("federal_register_ofac_iran.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    items, err = policy.fetch_federal_register(_spec_with_topics(["Iran sanctions"]))
    assert err is None
    assert len(items) == 5
    first = items[0]
    assert first.title == "Publication of an Iran-Related Determination"
    assert first.status == "regulation"  # type "Rule"
    assert "Treasury" in first.body
    assert first.date is not None
    assert first.url.startswith("https://www.federalregister.gov/")


def test_federal_register_type_to_status_mapping(monkeypatch):
    def fake(url, **kw):
        return {"results": [
            {"title": "A proposed rule", "type": "Proposed Rule", "publication_date": "2026-01-01",
             "html_url": "u", "agencies": [], "document_number": "1", "abstract": None},
            {"title": "A notice", "type": "Notice", "publication_date": "2026-01-01",
             "html_url": "u", "agencies": [], "document_number": "2", "abstract": None},
            {"title": "An EO", "type": "Presidential Document", "publication_date": "2026-01-01",
             "html_url": "u", "agencies": [], "document_number": "3", "abstract": None},
            {"title": "Mystery type", "type": "Something Else", "publication_date": "2026-01-01",
             "html_url": "u", "agencies": [], "document_number": "4", "abstract": None},
        ]}, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    items, err = policy.fetch_federal_register(_spec_with_topics(["x"]))
    by_title = {i.title: i.status for i in items}
    assert by_title["A proposed rule"] == "proposed"
    assert by_title["A notice"] == "announced"
    assert by_title["An EO"] == "executive_action"
    assert by_title["Mystery type"] == "unknown"


def test_federal_register_no_topics_is_clean_noop():
    items, err = policy.fetch_federal_register(_spec_with_topics([]))
    assert items == [] and err is None


def test_federal_register_error_is_isolated_per_topic(monkeypatch):
    calls = {"n": 0}

    def fake(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "timeout"
        return {"results": []}, None

    monkeypatch.setattr(http_client, "fetch_json", fake)
    items, err = policy.fetch_federal_register(_spec_with_topics(["a", "b"]))
    assert err is not None and "timeout" in err
    assert calls["n"] == 2, "a failure on one topic must not stop the others"


def test_agency_slugs_match_verified_set():
    assert policy.AGENCY_SLUGS["doe"] == "energy-department"
    assert policy.AGENCY_SLUGS["ofac"] == "foreign-assets-control-office"
    assert policy.AGENCY_SLUGS["cftc"] == "commodity-futures-trading-commission"


# ── OFAC recent actions (HTML) ────────────────────────────────────────

def test_ofac_html_parses_real_captured_rows():
    html = fixture_text("ofac_recent_actions.html")
    items = policy.parse_ofac_html(html, "oil")
    assert len(items) == 10
    first = items[0]
    assert "Venezuela" in first.title
    assert first.url == "https://ofac.treasury.gov/recent-actions/20261016"
    assert first.body == "Treasury/OFAC"
    assert first.date is not None


def test_ofac_html_unescapes_entities():
    html = fixture_text("ofac_recent_actions.html")
    items = policy.parse_ofac_html(html, "oil")
    settlement = [i for i in items if "Bank of America" in i.title]
    assert settlement, "expected the Bank of America settlement row in the fixture"
    assert "&#039;" not in settlement[0].title
    assert "Treasury's" in settlement[0].title


def test_ofac_status_mapping_by_category():
    html = fixture_text("ofac_recent_actions.html")
    items = policy.parse_ofac_html(html, "oil")
    by_title = {i.title: i.status for i in items}
    general_license = [v for k, v in by_title.items() if "General License" in k]
    sanctions_list = [v for k, v in by_title.items() if "Designation" in k and "Removal" not in k]
    assert "regulation" in general_license
    assert any(v == "executive_action" for v in sanctions_list)


def test_ofac_no_rows_parsed_is_reported_as_failure(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: ("<html>nothing here</html>", None))
    items, err = policy.fetch_ofac(_spec_with_topics(["x"]))
    assert items == [] and err == "policy:ofac:no_rows_parsed"


def test_ofac_http_failure_propagates(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (None, "timeout"))
    items, err = policy.fetch_ofac(_spec_with_topics(["x"]))
    assert items == [] and err == "policy:ofac:timeout"


def test_ofac_uses_a_timeout_that_keeps_two_attempts_under_the_packet_budget(monkeypatch):
    """Regression for the real `policy.fetch_ofac:timeout_budget_exceeded`
    failure: http_client's DEFAULT_TIMEOUT (connect=4s, read=6s) times two
    attempts plus the 1s retry backoff can cost ~21s on a slow
    treasury.gov page, well over build_packet's 12s FETCH_DEADLINE_S.
    fetch_ofac must pass an explicit timeout small enough that even two
    attempts stay comfortably inside that budget."""
    captured = {}

    def fake(url, **kw):
        captured.update(kw)
        return "<html></html>", None
    monkeypatch.setattr(http_client, "fetch_text", fake)
    policy.fetch_ofac(_spec_with_topics(["x"]))
    assert captured["timeout_s"] <= 5.0, "two attempts at this timeout plus 1s backoff must stay under 12s"


# ── congress.gov bills ─────────────────────────────────────────────

def test_congress_bills_missing_key_is_reported_not_silent(monkeypatch):
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    items, err = policy.fetch_congress_bills(_spec_with_topics(["energy"]))
    assert items == []
    assert err == "policy:congress_bills:CONGRESS_API_KEY_missing"


def test_congress_bills_with_key_parses_documented_schema(monkeypatch):
    """No live key was available to capture a real response; this fixture
    is built from congress.gov's documented v3 /bill response shape, not
    a live capture — called out explicitly per policy.py's docstring."""
    monkeypatch.setenv("CONGRESS_API_KEY", "fake-test-key")

    def fake(url, **kw):
        return {"bills": [{
            "congress": 119, "type": "hr", "number": "1234",
            "title": "Strategic Petroleum Reserve Modernization Act",
            "latestAction": {"actionDate": "2026-09-01", "text": "Referred to committee."},
        }]}, None

    monkeypatch.setattr(http_client, "fetch_json", fake)
    items, err = policy.fetch_congress_bills(_spec_with_topics(["Strategic Petroleum Reserve"]))
    assert err is None
    assert len(items) == 1
    assert items[0].status == "in_committee"
    assert items[0].url == "https://www.congress.gov/bill/119th-congress/hr/1234"


def test_congress_status_hints():
    assert policy._congress_status("Became Public Law No: 119-1.") == "enacted"
    assert policy._congress_status("Passed Senate without amendment.") == "passed_chamber"
    assert policy._congress_status("Referred to the Committee on Energy.") == "in_committee"
    assert policy._congress_status("Introduced in House.") == "proposed"
    assert policy._congress_status("Something unrecognized.") == "unknown"


# ── dedup_policy ─────────────────────────────────────────────────────

def test_dedup_policy_collapses_near_duplicate_titles():
    from datetime import datetime, timezone
    from contracts.strategies.models import PolicyItem
    items = [
        PolicyItem(id="1", title="G7 agrees 100M bbl emergency stock release", url="u1",
                   body="IEA", date=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        PolicyItem(id="2", title="G7 agrees 100M bbl emergency stock release over four months",
                   url="u2", body="IEA", date=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ]
    out = policy.dedup_policy(items)
    assert len(out) == 1
    assert out[0].id == "2"  # newer-first sort keeps the later one


def test_dedup_policy_empty():
    assert policy.dedup_policy([]) == []
