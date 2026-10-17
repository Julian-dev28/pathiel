"""Unit tests for services/thesis/assemble.py: deterministic Thesis
construction from a ThesisDraft + spec/analytics/intel.
"""
from __future__ import annotations

import json

from contracts.strategies.models import Point, Thesis, Usage
from services.thesis import assemble
from services.thesis.hashing import hash_payload
from services.thesis.llm_schema import ThesisDraft


def _draft_from_thesis(thesis) -> ThesisDraft:
    return ThesisDraft(
        call=thesis.call, scenarios=thesis.scenarios, bull=thesis.bull, bear=thesis.bear,
        what_changes_my_mind=thesis.what_changes_my_mind, lessons=thesis.lessons,
        leverage_reasons=[lv.reason for lv in thesis.leverage_view],
    )


def test_make_thesis_id_matches_the_fixture_format(oil):
    assert assemble.make_thesis_id("oil", oil.T0) == "oil-20261017T0210-m"


def test_build_leverage_view_copies_leverage_and_verdict_from_the_ladder(oil):
    reasons = ["r5", "r10", "r20"]
    out = assemble.build_leverage_view(oil.analytics, reasons)
    assert [(lv.leverage, lv.verdict, lv.reason) for lv in out] == [
        (5.0, "hold", "r5"), (10.0, "dip_only", "r10"), (20.0, "avoid", "r20"),
    ]


def test_build_leverage_view_tolerates_too_few_reasons(oil):
    out = assemble.build_leverage_view(oil.analytics, ["only one"])
    assert out[0].reason == "only one"
    assert out[1].reason == "no reason provided"


def test_resolve_sources_includes_every_linkable_cited_id(oil):
    draft = _draft_from_thesis(oil.thesis)  # cites h1, h2, h3, p1 across bull/bear
    sources = assemble.resolve_sources(draft, oil.intel)
    assert {s.id for s in sources} == {"h1", "h2", "h3", "p1"}
    for s in sources:
        assert s.url.startswith("http")


def test_resolve_sources_excludes_uncited_ids(oil):
    draft = ThesisDraft(
        call=oil.thesis.call, scenarios=oil.thesis.scenarios,
        bull=[Point(text="x", source_ids=["h1"])],
        bear=[Point(text="uncited bear point", source_ids=[])],
        what_changes_my_mind=oil.thesis.what_changes_my_mind, lessons=oil.thesis.lessons,
        leverage_reasons=[lv.reason for lv in oil.thesis.leverage_view],
    )
    sources = assemble.resolve_sources(draft, oil.intel)
    assert {s.id for s in sources} == {"h1"}


def test_resolve_sources_skips_catalyst_and_positioning_citations_no_url(oil):
    draft = ThesisDraft(
        call=oil.thesis.call, scenarios=oil.thesis.scenarios,
        bull=[Point(text="x", source_ids=["c1", "cot1"])],
        bear=[Point(text="uncited bear point", source_ids=[])],
        what_changes_my_mind=oil.thesis.what_changes_my_mind, lessons=oil.thesis.lessons,
        leverage_reasons=[lv.reason for lv in oil.thesis.leverage_view],
    )
    assert assemble.resolve_sources(draft, oil.intel) == []


def test_resolve_sources_empty_when_intel_is_none(oil):
    draft = _draft_from_thesis(oil.thesis)
    assert assemble.resolve_sources(draft, None) == []


def test_assemble_thesis_round_trips_the_contract(oil):
    draft = _draft_from_thesis(oil.thesis)
    usage = Usage(model="claude-fable-5-1", input_tokens=700, output_tokens=400, cost_usd=0.09)
    thesis = assemble.assemble_thesis(
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel, draft=draft, now=oil.T0, usage=usage,
    )
    assert isinstance(thesis, Thesis)
    round_tripped = Thesis.model_validate_json(thesis.model_dump_json())
    assert round_tripped.id == thesis.id


def test_assemble_thesis_copies_catalysts_from_intel_verbatim(oil):
    draft = _draft_from_thesis(oil.thesis)
    thesis = assemble.assemble_thesis(
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel, draft=draft, now=oil.T0, usage=Usage(),
    )
    assert thesis.catalysts == oil.intel.catalysts


def test_assemble_thesis_sets_analytics_and_intel_ref_hashes(oil):
    draft = _draft_from_thesis(oil.thesis)
    thesis = assemble.assemble_thesis(
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel, draft=draft, now=oil.T0, usage=Usage(),
    )
    assert thesis.analytics_ref.sha256 == hash_payload(oil.analytics)
    assert thesis.intel_ref.sha256 == hash_payload(oil.intel)
    assert thesis.analytics_ref.as_of == oil.analytics.as_of


def test_assemble_thesis_intel_ref_is_none_when_intel_is_none(oil):
    draft = _draft_from_thesis(oil.thesis)
    thesis = assemble.assemble_thesis(
        spec=oil.spec, analytics=oil.analytics, intel=None, draft=draft, now=oil.T0, usage=Usage(),
    )
    assert thesis.intel_ref is None
    assert thesis.catalysts == []


def test_assemble_thesis_expiry_is_created_at_plus_horizon_days(oil):
    draft = _draft_from_thesis(oil.thesis)
    thesis = assemble.assemble_thesis(
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel, draft=draft, now=oil.T0, usage=Usage(),
    )
    assert (thesis.expires_at - thesis.created_at).days == oil.spec.horizon_days


def test_hash_payload_is_order_independent():
    import contracts.strategies.fixtures._build as oil_fixture
    a = hash_payload(oil_fixture.analytics)
    reordered_dict = json.loads(oil_fixture.analytics.model_dump_json())
    reordered = dict(reversed(list(reordered_dict.items())))
    from contracts.strategies.models import DeskAnalytics
    b = hash_payload(DeskAnalytics.model_validate(reordered))
    assert a == b
