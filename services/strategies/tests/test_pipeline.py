"""Unit tests for pipeline.py: the needs_rewrite token gate (every branch)
and refresh() end to end with fake providers (success, degrade, error)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from contracts.strategies.models import (
    DeskSpec, Headline, Instrument, IntelPacket, TokenBudget, Usage,
)
from services.strategies import journal, pipeline
from services.strategies.tests.conftest import make_thesis, minimal_analytics

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _spec(**tb_kwargs) -> DeskSpec:
    return DeskSpec(id="t1", name="T1", tagline="t",
                    instruments=[Instrument(coin="XYZ", label="XYZ", role="primary")],
                    token_budget=TokenBudget(**tb_kwargs))


def _prev(entry_px=100.0, created_at=None, horizon_days=7):
    created_at = created_at or (NOW - timedelta(hours=2))
    return make_thesis(desk_id="t1", instrument="XYZ", entry_px=entry_px,
                       created_at=created_at, horizon_days=horizon_days, scenarios=[])


# ── needs_rewrite: every branch ─────────────────────────────────────────────

def test_needs_rewrite_forced_wins_over_everything():
    spec = _spec(refresh_min_interval_min=600)  # would otherwise block
    prev = _prev()
    result = minimal_analytics("t1", mid=100.0)
    decision, reason = pipeline.needs_rewrite(spec, result, None, prev, now=NOW, force=True)
    assert (decision, reason) == (True, "forced")


def test_needs_rewrite_no_prior_thesis():
    spec = _spec()
    result = minimal_analytics("t1", mid=100.0)
    decision, reason = pipeline.needs_rewrite(spec, result, None, None, now=NOW)
    assert (decision, reason) == (True, "no_prior_thesis")


def test_needs_rewrite_expired():
    spec = _spec()
    prev = _prev(created_at=NOW - timedelta(days=10), horizon_days=7)  # expired 3 days ago
    result = minimal_analytics("t1", mid=100.0)
    decision, reason = pipeline.needs_rewrite(spec, result, None, prev, now=NOW)
    assert (decision, reason) == (True, "expired")


def test_needs_rewrite_min_interval_blocks_even_a_real_move():
    spec = _spec(refresh_min_interval_min=600, material_move_pct=1.0)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(minutes=5), horizon_days=7)
    result = minimal_analytics("t1", mid=150.0)  # +50% move, well past the threshold
    decision, reason = pipeline.needs_rewrite(spec, result, None, prev, now=NOW)
    assert (decision, reason) == (False, "min_interval_not_elapsed")


def test_needs_rewrite_material_move_triggers():
    spec = _spec(refresh_min_interval_min=1, material_move_pct=1.5)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(hours=2), horizon_days=7)
    result = minimal_analytics("t1", mid=102.0)  # +2% >= 1.5%
    decision, reason = pipeline.needs_rewrite(spec, result, None, prev, now=NOW)
    assert (decision, reason) == (True, "material_move")


def test_needs_rewrite_move_below_threshold_does_not_trigger():
    spec = _spec(refresh_min_interval_min=1, material_move_pct=1.5)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(hours=2), horizon_days=7)
    result = minimal_analytics("t1", mid=101.0)  # +1% < 1.5%
    decision, reason = pipeline.needs_rewrite(spec, result, None, prev, now=NOW)
    assert (decision, reason) == (False, "no_trigger")


def test_needs_rewrite_high_relevance_headline_triggers():
    spec = _spec(refresh_min_interval_min=1, material_move_pct=50.0, min_headline_relevance=0.7)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(hours=2), horizon_days=7)
    result = minimal_analytics("t1", mid=100.0)  # no move at all
    intel = IntelPacket(desk_id="t1", as_of=NOW, headlines=[
        Headline(id="h1", title="big news", url="http://x", source="x",
                 published_at=NOW - timedelta(minutes=5), relevance=0.9),
    ])
    decision, reason = pipeline.needs_rewrite(spec, result, intel, prev, now=NOW)
    assert (decision, reason) == (True, "high_relevance_headline")


def test_needs_rewrite_headline_before_prior_thesis_does_not_trigger():
    spec = _spec(refresh_min_interval_min=1, material_move_pct=50.0, min_headline_relevance=0.7)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(hours=2), horizon_days=7)
    result = minimal_analytics("t1", mid=100.0)
    intel = IntelPacket(desk_id="t1", as_of=NOW, headlines=[
        Headline(id="h1", title="old news", url="http://x", source="x",
                 published_at=NOW - timedelta(hours=5), relevance=0.95),  # before prev.created_at
    ])
    decision, reason = pipeline.needs_rewrite(spec, result, intel, prev, now=NOW)
    assert (decision, reason) == (False, "no_trigger")


def test_needs_rewrite_headline_below_relevance_does_not_trigger():
    spec = _spec(refresh_min_interval_min=1, material_move_pct=50.0, min_headline_relevance=0.7)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(hours=2), horizon_days=7)
    result = minimal_analytics("t1", mid=100.0)
    intel = IntelPacket(desk_id="t1", as_of=NOW, headlines=[
        Headline(id="h1", title="meh", url="http://x", source="x",
                 published_at=NOW - timedelta(minutes=5), relevance=0.5),
    ])
    decision, reason = pipeline.needs_rewrite(spec, result, intel, prev, now=NOW)
    assert (decision, reason) == (False, "no_trigger")


def test_needs_rewrite_quiet_market_no_trigger():
    spec = _spec(refresh_min_interval_min=1, material_move_pct=1.5, min_headline_relevance=0.7)
    prev = _prev(entry_px=100.0, created_at=NOW - timedelta(hours=2), horizon_days=7)
    result = minimal_analytics("t1", mid=100.3)
    decision, reason = pipeline.needs_rewrite(spec, result, None, prev, now=NOW)
    assert (decision, reason) == (False, "no_trigger")


# ── provider resolution ─────────────────────────────────────────────────────

def test_resolve_provider_resolves_a_real_module_attr():
    import json as json_mod
    fn = pipeline._resolve_provider("json:dumps")
    assert fn is json_mod.dumps


def test_resolve_provider_missing_module_raises_importerror():
    with pytest.raises(ImportError):
        pipeline._resolve_provider("services.no_such_module_for_tests.api:build_packet")


def test_resolve_provider_missing_attr_raises_attributeerror():
    with pytest.raises(AttributeError):
        pipeline._resolve_provider("json:not_a_real_attribute")


# ── refresh(): end to end with fake providers ───────────────────────────────

def test_refresh_unknown_desk_is_an_error_job():
    job = pipeline.refresh("not-a-real-desk")
    assert job.state == "error"
    assert "unknown desk" in job.message


def test_refresh_analytics_failure_is_an_error_job(monkeypatch):
    def boom(spec, **kwargs):
        raise RuntimeError("HL is down")
    monkeypatch.setattr(pipeline.analytics_mod, "build_analytics", boom)

    job = pipeline.refresh("oil")
    assert job.state == "error"
    assert "analytics build failed" in job.message


def test_refresh_degrades_when_providers_missing(monkeypatch, stub_live_analytics):
    """A deploy without services.intel / services.thesis (or a broken import)
    must not crash refresh(), and must say clearly what it skipped."""
    monkeypatch.setattr(pipeline, "INTEL_PROVIDER_PATH", "services.no_such_intel.api:build_packet")
    monkeypatch.setattr(pipeline, "THESIS_PROVIDER_PATH", "services.no_such_thesis.api:write_thesis")
    stub_live_analytics(minimal_analytics("oil", mid=99.0))
    job = pipeline.refresh("oil")
    assert job.state == "done"
    assert "unavailable" in job.message
    assert journal.latest_thesis("oil") is None  # no thesis was written


def test_refresh_writes_thesis_when_gate_passes_and_providers_available(
       monkeypatch, stub_live_analytics):
    result = minimal_analytics("oil", mid=99.0)
    stub_live_analytics(result)

    written = make_thesis(desk_id="oil", thesis_id="written-1", instrument="xyz:CL",
                          scenarios=[], created_at=NOW)
    written = written.model_copy(update={"usage": Usage(model="fake", input_tokens=100,
                                                        output_tokens=50, duration_s=0.01)})

    def fake_resolve(path):
        if path == pipeline.INTEL_PROVIDER_PATH:
            return lambda spec: IntelPacket(desk_id=spec.id, as_of=NOW)
        if path == pipeline.THESIS_PROVIDER_PATH:
            return lambda spec, a, i, p: written
        raise ImportError(path)

    monkeypatch.setattr(pipeline, "_resolve_provider", fake_resolve)
    job = pipeline.refresh("oil", force=True)

    assert job.state == "done"
    assert job.usage.input_tokens == 100
    saved = journal.latest_thesis("oil")
    assert saved is not None and saved.id == "written-1"
    # intel got cached too
    assert pipeline.load_cached_intel("oil") is not None


def test_refresh_skips_when_gate_blocks(monkeypatch, stub_live_analytics):
    result = minimal_analytics("oil", mid=99.0, coin="xyz:CL")
    stub_live_analytics(result)
    prev = make_thesis(desk_id="oil", thesis_id="prev-1", instrument="xyz:CL", entry_px=99.0,
                       created_at=datetime.now(timezone.utc) - timedelta(minutes=1), horizon_days=7,
                       scenarios=[])
    journal.save_thesis(prev)

    called = {"thesis": False}

    def fake_resolve(path):
        if path == pipeline.INTEL_PROVIDER_PATH:
            raise ImportError("no intel")
        if path == pipeline.THESIS_PROVIDER_PATH:
            called["thesis"] = True
            raise AssertionError("thesis provider must not be called when the gate blocks")
        raise ImportError(path)

    monkeypatch.setattr(pipeline, "_resolve_provider", fake_resolve)
    job = pipeline.refresh("oil", force=False)

    assert job.state == "skipped"
    # analytics -> intel both still run before the gate check (intel's note
    # rides along in the message even on a skip); the thesis provider must not.
    assert job.message.startswith("min_interval_not_elapsed")
    assert not called["thesis"]


def test_refresh_logs_usage_jsonl(stub_live_analytics):
    stub_live_analytics(minimal_analytics("oil", mid=99.0))
    pipeline.refresh("oil")
    path = pipeline._usage_log_path()
    with open(path) as fh:
        rows = [json.loads(line) for line in fh]
    assert len(rows) == 1
    assert rows[0]["desk"] == "oil"
    assert rows[0]["decision"] in ("written", "skipped", "degraded", "error")


def test_refresh_caches_analytics_for_later_reads(stub_live_analytics):
    result = minimal_analytics("oil", mid=123.0)
    stub_live_analytics(result)
    pipeline.refresh("oil")
    cached = pipeline.load_cached_analytics("oil")
    assert cached is not None
    assert cached.instruments[0].mid == 123.0


def test_refresh_degrades_when_thesis_provider_raises(stub_live_analytics):
    """The gate-safe default thesis provider raises; refresh() records why and
    writes no thesis, never crashes."""
    stub_live_analytics(minimal_analytics("oil", mid=99.0))
    job = pipeline.refresh("oil", force=True)
    assert job.state == "done"
    assert "thesis provider raised" in job.message
    assert journal.latest_thesis("oil") is None
