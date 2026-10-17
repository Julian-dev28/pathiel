"""Unit tests for services/thesis/fallback.py: the two ways write_thesis()
degrades without ever returning anything but a valid Thesis.
"""
from __future__ import annotations

from contracts.strategies.models import Thesis
from services.thesis import fallback


def test_reject_with_prev_returns_a_copy_not_the_same_object(oil):
    out = fallback.reject_with_prev(oil.thesis, "validator rejected after repair: x")
    assert out is not oil.thesis
    assert out.usage.skipped_reason == "validator rejected after repair: x"


def test_reject_with_prev_does_not_mutate_the_original(oil):
    original_reason = oil.thesis.usage.skipped_reason
    fallback.reject_with_prev(oil.thesis, "some reason")
    assert oil.thesis.usage.skipped_reason == original_reason


def test_reject_with_prev_keeps_every_other_field_identical(oil):
    out = fallback.reject_with_prev(oil.thesis, "x")
    assert out.call == oil.thesis.call
    assert out.scenarios == oil.thesis.scenarios
    assert out.id == oil.thesis.id


def test_build_fallback_thesis_is_a_valid_contract_thesis(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, oil.intel, "LLM call failed: timeout", oil.T0)
    assert isinstance(t, Thesis)
    round_tripped = Thesis.model_validate_json(t.model_dump_json())
    assert round_tripped.id == t.id


def test_build_fallback_thesis_is_flat_zero_confidence(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, oil.intel, "reason", oil.T0)
    assert t.call.side == "flat"
    assert t.call.confidence == 0.0
    assert t.call.leverage is None
    assert t.scenarios == []


def test_build_fallback_thesis_has_a_short_ttl_not_the_full_horizon(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, oil.intel, "reason", oil.T0)
    assert (t.expires_at - t.created_at).total_seconds() == 3600
    assert (t.expires_at - t.created_at).days < oil.spec.horizon_days


def test_build_fallback_thesis_records_the_reason_in_usage(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, oil.intel, "LLM call failed: boom", oil.T0)
    assert t.usage.skipped_reason == "LLM call failed: boom"


def test_build_fallback_thesis_still_copies_catalysts_from_intel(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, oil.intel, "reason", oil.T0)
    assert t.catalysts == oil.intel.catalysts


def test_build_fallback_thesis_works_with_no_intel_at_all(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, None, "reason", oil.T0)
    assert t.catalysts == []
    assert t.intel_ref is None


def test_build_fallback_thesis_leverage_view_covers_every_ladder_rung(oil):
    t = fallback.build_fallback_thesis(oil.spec, oil.analytics, oil.intel, "reason", oil.T0)
    assert [lv.leverage for lv in t.leverage_view] == [r.leverage for r in oil.analytics.ladder]
    assert [lv.verdict for lv in t.leverage_view] == [r.verdict for r in oil.analytics.ladder]
