"""Unit tests for facade.py: the 6 stable entrypoints other agents import."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import Call, Grade, IntelPacket, Scenario, UserThesisIn
from services.strategies import desks, facade, journal
from services.strategies.tests.conftest import make_thesis, minimal_analytics

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_list_cards_one_per_desk_with_no_prior_refresh():
    cards = facade.list_cards()
    assert [c.id for c in cards] == desks.list_desk_ids()
    for c in cards:
        assert c.mid is None and c.ret_1d is None and c.one_liner is None
        assert c.track_record.n_graded == 0


def test_list_cards_reflects_cached_analytics_and_latest_thesis(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=101.5, coin="xyz:BRENTOIL"))
    facade.refresh("oil")  # populates the analytics cache (providers unavailable -> no thesis)
    journal.save_thesis(make_thesis(desk_id="oil", instrument="xyz:BRENTOIL", side="long"))

    cards = {c.id: c for c in facade.list_cards()}
    assert cards["oil"].mid == 101.5
    assert cards["oil"].call_side == "long"
    assert cards["oil"].one_liner == "test thesis"
    assert cards["btc"].mid is None  # untouched desk stays empty


def test_get_brief_renders_for_gas(stub_live_analytics):
    """Fixture-driven brief render for the gas desk, same shape as the oil
    checks above: a cached analytics snapshot + a saved thesis come back
    out of get_brief() whole, on gas's own primary coin (xyz:NATGAS)."""
    stub_live_analytics(minimal_analytics("gas", mid=3.21, coin="xyz:NATGAS", label="Nat Gas"))
    journal.save_thesis(make_thesis(desk_id="gas", instrument="xyz:NATGAS", side="short",
                                    created_at=datetime.now(timezone.utc)))

    brief = facade.get_brief("gas")
    assert brief.spec.id == "gas"
    assert brief.analytics is not None
    assert brief.analytics.instruments[0].coin == "xyz:NATGAS"
    assert brief.thesis is not None
    assert brief.thesis.call.instrument == "xyz:NATGAS"
    assert brief.thesis.call.side == "short"
    assert "intel: never fetched (run refresh)" in brief.stale  # no intel fetched yet, honestly flagged


def test_get_brief_unknown_desk_raises():
    with pytest.raises(desks.UnknownDeskError):
        facade.get_brief("not-a-real-desk")


def test_get_brief_never_refreshed_is_honestly_empty(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL"))
    brief = facade.get_brief("oil")
    assert brief.analytics is not None   # brief ALWAYS builds analytics live
    assert brief.intel is None
    assert brief.thesis is None
    assert "intel: never fetched (run refresh)" in brief.stale
    assert "thesis: none yet (run refresh)" in brief.stale


def test_get_brief_shows_cached_intel_and_thesis_after_refresh(monkeypatch, stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL"))

    written = make_thesis(desk_id="oil", thesis_id="w1", instrument="xyz:BRENTOIL", scenarios=[],
                          created_at=datetime.now(timezone.utc))  # must be unexpired "right now"

    def fake_resolve(path):
        import services.strategies.pipeline as P
        if path == P.INTEL_PROVIDER_PATH:
            return lambda spec: IntelPacket(desk_id=spec.id, as_of=NOW)
        if path == P.THESIS_PROVIDER_PATH:
            return lambda spec, a, i, p: written
        raise ImportError(path)

    import services.strategies.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "_resolve_provider", fake_resolve)

    facade.refresh("oil", force=True)
    brief = facade.get_brief("oil")
    assert brief.intel is not None
    assert brief.thesis is not None and brief.thesis.id == "w1"
    assert brief.stale == []


def test_get_brief_surfaces_missing_api_keys_in_stale(monkeypatch, stub_live_analytics):
    """A missing free key (EIA_API_KEY/FRED_API_KEY/CONGRESS_API_KEY) skips
    its source cleanly and lands in IntelPacket.sources_failed — but
    before this fix, nothing surfaced it into DeskBrief.stale, the one
    list the CLI (__main__.py) and the MCP brief both print. Regression
    for the intel-hard-data brief's "the missing-key list must be visible
    in the brief's stale/sources_failed"."""
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL"))

    def fake_resolve(path):
        import services.strategies.pipeline as P
        if path == P.INTEL_PROVIDER_PATH:
            return lambda spec: IntelPacket(
                desk_id=spec.id, as_of=NOW,
                sources_failed=["eia:crude_stocks:EIA_API_KEY_missing",
                                "policy:congress_bills:CONGRESS_API_KEY_missing",
                                "news:gdelt:http_429"],
            )
        if path == P.THESIS_PROVIDER_PATH:
            return lambda spec, a, i, p: make_thesis(
                desk_id="oil", thesis_id="w1", instrument="xyz:BRENTOIL", scenarios=[],
                created_at=datetime.now(timezone.utc))
        raise ImportError(path)

    import services.strategies.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "_resolve_provider", fake_resolve)

    facade.refresh("oil", force=True)
    brief = facade.get_brief("oil")
    missing_line = next((s for s in brief.stale if "missing API key" in s), None)
    assert missing_line is not None
    assert "EIA_API_KEY_missing" in missing_line
    assert "CONGRESS_API_KEY_missing" in missing_line
    assert "intel_keys.py" in missing_line
    other_line = next((s for s in brief.stale if "source(s) failed" in s), None)
    assert other_line is not None and "1 source(s) failed" in other_line


def test_get_brief_flags_expired_thesis_as_stale(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL"))
    expired = make_thesis(desk_id="oil", instrument="xyz:BRENTOIL",
                          created_at=datetime.now(timezone.utc) - timedelta(days=30), horizon_days=7)
    journal.save_thesis(expired)
    brief = facade.get_brief("oil")
    assert any("expired" in s for s in brief.stale)


def test_refresh_passes_through_to_pipeline(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0, coin="xyz:BRENTOIL"))
    job = facade.refresh("oil", force=True)
    assert job.desk_id == "oil"
    assert job.state in ("done", "skipped", "error")


def test_submit_user_thesis_creates_a_valid_journaled_thesis():
    body = UserThesisIn(
        call=Call(side="long", instrument="xyz:BRENTOIL", one_liner="my own call", entry_px=100.0,
                 stop_px=90.0, targets=[110.0], leverage=5.0, confidence=0.7,
                 invalidation="daily close below 90"),
        scenarios=[Scenario(name="up", probability=1.0, move_lo_pct=-1.0, move_hi_pct=1.0,
                            description="placeholder")],
        notes="I think Hormuz stays shut",
    )
    thesis = facade.submit_user_thesis("oil", body)
    assert thesis.author == "user"
    assert thesis.desk_id == "oil"
    assert thesis.call.one_liner == "my own call"
    spec = desks.require_spec("oil")
    assert thesis.horizon_days == spec.horizon_days
    assert (thesis.expires_at - thesis.created_at) == timedelta(days=spec.horizon_days)
    assert any("I think Hormuz stays shut" in p.text for p in thesis.bull)

    saved = journal.get_thesis("oil", thesis.id)
    assert saved == thesis


def test_submit_user_thesis_unknown_desk_raises():
    body = UserThesisIn(call=Call(side="flat", instrument="X", one_liner="x", confidence=0.5,
                                  invalidation="x"))
    with pytest.raises(desks.UnknownDeskError):
        facade.submit_user_thesis("not-a-real-desk", body)


def test_submit_user_thesis_without_notes_leaves_bull_empty():
    body = UserThesisIn(call=Call(side="flat", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
                                  invalidation="x"))
    thesis = facade.submit_user_thesis("oil", body)
    assert thesis.bull == []


def test_track_record_delegates_to_grader():
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="g1", grade=Grade(
        graded_at=NOW, outcome_px=1.0, ret_pct=0.1, direction_hit=True, mae_pct=0.0, mfe_pct=0.1)))
    tr = facade.track_record("oil")
    assert tr.n_graded == 1
    assert tr.direction_hit_rate == 1.0


def test_grade_due_is_idempotent_through_the_facade():
    created = NOW - timedelta(days=10)
    journal.save_thesis(make_thesis(desk_id="oil", thesis_id="due1", instrument="xyz:BRENTOIL",
                                    entry_px=100.0, horizon_days=1, created_at=created, scenarios=[]))

    def fetcher(coin, interval, count):
        from services.strategies.tests.conftest import make_candles
        return make_candles([100.0] * 30, start=created)

    import services.strategies.grader as grader_mod
    n1 = grader_mod.grade_all_due(hourly_fetcher=fetcher)
    assert n1 == 1
    n2 = facade.grade_due()  # real default hourly fetcher this time, but nothing left to grade
    assert n2 == 0
