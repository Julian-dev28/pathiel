"""Unit tests for services/thesis/validator.py - the anti-hallucination
gate. Covers the six required adversarial cases (invented number, wrong-side
stop, probabilities summing to 0.9, overlapping buckets, uncited source,
avoid-rung leverage) plus every other rule this validator adds, each proven
against the real oil fixture as the "good" baseline so a false positive
against the target shape would show up immediately.
"""
from __future__ import annotations

import pytest

from contracts.strategies.models import Call, Lesson, Point, Scenario
from services.thesis import validator
from services.thesis.llm_schema import LeverageReason
from services.thesis.llm_schema import ThesisDraft


def _draft_from_thesis(thesis) -> ThesisDraft:
    return ThesisDraft(
        call=thesis.call, scenarios=thesis.scenarios, bull=thesis.bull, bear=thesis.bear,
        what_changes_my_mind=thesis.what_changes_my_mind, lessons=thesis.lessons,
        leverage_reasons=[{"leverage": lv.leverage, "reason": lv.reason} for lv in thesis.leverage_view],
    )


# -- golden path: the target fixture must pass cleanly ------------------------


def test_golden_oil_fixture_passes_validate_draft(oil):
    draft = _draft_from_thesis(oil.thesis)
    assert validator.validate_draft(draft, oil.spec, oil.analytics, oil.intel) == []


def test_golden_oil_fixture_passes_validate_thesis(oil):
    assert validator.validate_thesis(oil.thesis, oil.spec, oil.analytics, oil.intel) == []


# -- adversarial case 1: invented number --------------------------------------


def test_invented_number_in_bear_text_fails(oil):
    bear = [Point(text="Oil could spike to $9999 on a secret deal", source_ids=["h1"])]
    errors = validator.check_numbers_grounded(
        call=oil.thesis.call, bull=oil.thesis.bull, bear=bear, lessons=oil.thesis.lessons,
        what_changes_my_mind=oil.thesis.what_changes_my_mind,
        leverage_reasons=[LeverageReason(leverage=lv.leverage, reason=lv.reason) for lv in oil.thesis.leverage_view],
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel,
    )
    assert any("9999" in e for e in errors)


def test_invented_number_in_one_liner_fails(oil):
    call = oil.thesis.call.model_copy(update={"one_liner": "Long WTI, target 4567 by Friday."})
    errors = validator.check_numbers_grounded(
        call=call, bull=[], bear=[], lessons=oil.thesis.lessons, what_changes_my_mind=[],
        leverage_reasons=[{"leverage": lv.leverage, "reason": lv.reason} for lv in oil.thesis.leverage_view],
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel,
    )
    assert any("4567" in e for e in errors)


def test_number_grounded_via_headline_title_text_passes(oil):
    """8.6 is not a numeric LEAF anywhere in analytics/intel - it only
    appears inside a headline's title string. Must still be allowed."""
    bull = [Point(text="8.6M bbl US crude draw", source_ids=["h2"])]
    errors = validator.check_numbers_grounded(
        call=oil.thesis.call, bull=bull, bear=[], lessons=oil.thesis.lessons,
        what_changes_my_mind=[], leverage_reasons=[{"leverage": lv.leverage, "reason": lv.reason} for lv in oil.thesis.leverage_view],
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel,
    )
    assert errors == []


def test_number_grounded_via_percent_scaled_ladder_value_passes(oil):
    """P(liq) is stored as a 0-1 fraction (0.032); prose citing "3.2%" must
    be recognized as the same number, scaled."""
    reasons = ([LeverageReason(leverage=5.0, reason="liq 81.51, P(liq) 3.2%")]
               + [LeverageReason(leverage=lv.leverage, reason=lv.reason) for lv in oil.thesis.leverage_view[1:]])
    errors = validator.check_numbers_grounded(
        call=oil.thesis.call, bull=[], bear=[], lessons=oil.thesis.lessons, what_changes_my_mind=[],
        leverage_reasons=reasons, spec=oil.spec, analytics=oil.analytics, intel=oil.intel,
    )
    assert errors == []


def test_number_grounded_when_sign_is_dropped_by_the_verb(oil):
    """Live 2026-10-17 regression: positioning.change=-10403; repair-round
    prose wrote "fell by 10403 contracts" with no minus sign, since the
    verb "fell" already carries the direction. Must be grounded via the
    absolute-value variant, not just the literal signed leaf."""
    bull = [Point(text="WTI non-commercial net long fell by 10403 contracts", source_ids=["cot1"])]
    errors = validator.check_numbers_grounded(
        call=oil.thesis.call, bull=bull, bear=[], lessons=oil.thesis.lessons, what_changes_my_mind=[],
        leverage_reasons=[{"leverage": lv.leverage, "reason": lv.reason} for lv in oil.thesis.leverage_view],
        spec=oil.spec, analytics=oil.analytics, intel=oil.intel,
    )
    assert errors == []


def test_numbers_grounded_with_no_intel_still_allows_analytics_numbers(oil):
    reasons = [LeverageReason(leverage=lv.leverage, reason=lv.reason) for lv in oil.thesis.leverage_view]
    errors = validator.check_numbers_grounded(
        call=oil.thesis.call, bull=[], bear=[], lessons=oil.thesis.lessons, what_changes_my_mind=[],
        leverage_reasons=reasons, spec=oil.spec, analytics=oil.analytics, intel=None,
    )
    assert errors == []


# -- adversarial case 2: wrong-side stop --------------------------------------


def test_wrong_side_stop_for_a_long_fails():
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=105.0,
                confidence=0.5, invalidation="i")
    errors = validator.check_stop_side(call)
    assert len(errors) == 1 and "below entry_px" in errors[0]


def test_wrong_side_stop_for_a_short_fails():
    call = Call(side="short", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=95.0,
                confidence=0.5, invalidation="i")
    errors = validator.check_stop_side(call)
    assert len(errors) == 1 and "above entry_px" in errors[0]


def test_correct_side_stop_passes():
    long_call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=95.0,
                      confidence=0.5, invalidation="i")
    short_call = Call(side="short", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=105.0,
                       confidence=0.5, invalidation="i")
    assert validator.check_stop_side(long_call) == []
    assert validator.check_stop_side(short_call) == []


def test_stop_side_skipped_for_flat_or_missing_prices():
    flat = Call(side="flat", instrument="xyz:CL", one_liner="x", confidence=0.0, invalidation="i")
    no_stop = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, confidence=0.5,
                    invalidation="i")
    assert validator.check_stop_side(flat) == []
    assert validator.check_stop_side(no_stop) == []


# -- adversarial case 3: probabilities summing to 0.9 -------------------------


def test_probabilities_summing_to_point_nine_fails(oil):
    scenarios = list(oil.thesis.scenarios)
    scenarios[0] = scenarios[0].model_copy(update={"probability": scenarios[0].probability - 0.1})
    errors = validator.check_scenarios_sum_to_one(scenarios)
    assert len(errors) == 1 and "0.9" in errors[0]


def test_probabilities_summing_to_one_passes(oil):
    assert validator.check_scenarios_sum_to_one(oil.thesis.scenarios) == []


def test_empty_scenarios_list_is_not_a_sum_error():
    assert validator.check_scenarios_sum_to_one([]) == []


# -- adversarial case 4: overlapping buckets -----------------------------------


def test_overlapping_scenario_buckets_fail():
    scenarios = [
        Scenario(name="A", probability=0.5, move_lo_pct=0.0, move_hi_pct=0.10, description="d"),
        Scenario(name="B", probability=0.5, move_lo_pct=0.05, move_hi_pct=0.20, description="d"),
    ]
    errors = validator.check_scenarios_non_overlapping(scenarios)
    assert len(errors) == 1 and "overlap" in errors[0]


def test_contiguous_non_overlapping_buckets_pass(oil):
    assert validator.check_scenarios_non_overlapping(oil.thesis.scenarios) == []


def test_inverted_bucket_bounds_fail():
    scenarios = [Scenario(name="A", probability=1.0, move_lo_pct=0.1, move_hi_pct=0.05, description="d")]
    errors = validator.check_scenarios_non_overlapping(scenarios)
    assert any("move_lo_pct >= move_hi_pct" in e for e in errors)


# -- adversarial case 5: uncited source ---------------------------------------


def test_bull_point_citing_a_nonexistent_source_id_fails(oil):
    bull = [Point(text="x", source_ids=["h999"])]
    errors = validator.check_cited_ids_exist(bull, [], oil.intel)
    assert len(errors) == 1 and "h999" in errors[0]


def test_bull_point_citing_a_real_headline_passes(oil):
    bull = [Point(text="x", source_ids=["h1"])]
    assert validator.check_cited_ids_exist(bull, [], oil.intel) == []


def test_citing_a_catalyst_or_positioning_id_is_valid_even_without_a_url(oil):
    """Catalysts/positioning have no url (SourceRef requires one) but are
    still legitimate citations - resolving a renderable link is
    assemble.py's job, not the validator's."""
    bull = [Point(text="x", source_ids=["c1", "cot1"])]
    assert validator.check_cited_ids_exist(bull, [], oil.intel) == []


def test_citing_anything_with_no_intel_packet_fails():
    bull = [Point(text="x", source_ids=["h1"])]
    errors = validator.check_cited_ids_exist(bull, [], None)
    assert len(errors) == 1


# -- adversarial case 6: avoid-rung leverage ----------------------------------


def test_avoid_rung_leverage_fails(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", leverage=20.0, confidence=0.5,
                invalidation="i")
    errors = validator.check_leverage_not_avoid(call, oil.analytics)
    assert len(errors) == 1 and "avoid" in errors[0]


def test_hold_rung_leverage_passes(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", leverage=5.0, confidence=0.5,
                invalidation="i")
    assert validator.check_leverage_not_avoid(call, oil.analytics) == []


def test_leverage_not_on_the_ladder_fails(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", leverage=7.0, confidence=0.5,
                invalidation="i")
    errors = validator.check_leverage_not_avoid(call, oil.analytics)
    assert len(errors) == 1 and "not one of the ladder" in errors[0]


def test_leverage_check_skipped_for_flat_call(oil):
    call = Call(side="flat", instrument="xyz:CL", one_liner="x", confidence=0.0, invalidation="i")
    assert validator.check_leverage_not_avoid(call, oil.analytics) == []


# -- r-multiple gate (2026-10-23) ----------------------------------------------


def test_r_multiple_below_minimum_fails():
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=98.0,
                targets=[100.5], confidence=0.5, invalidation="i")
    errors = validator.check_r_multiple(call)
    assert len(errors) == 1 and "0.25R" in errors[0] and "1.5R minimum" in errors[0]


def test_r_multiple_at_exactly_the_minimum_passes():
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=98.0,
                targets=[103.0], confidence=0.5, invalidation="i")   # R = 3/2 = 1.5 exactly
    assert validator.check_r_multiple(call) == []


def test_r_multiple_well_above_the_minimum_passes():
    call = Call(side="short", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=102.0,
                targets=[94.0], confidence=0.5, invalidation="i")   # R = 6/2 = 3.0
    assert validator.check_r_multiple(call) == []


@pytest.mark.parametrize("call", [
    Call(side="flat", instrument="xyz:CL", one_liner="x", confidence=0.0, invalidation="i"),
    Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, confidence=0.5, invalidation="i"),
    Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=98.0, targets=[],
         confidence=0.5, invalidation="i"),
])
def test_r_multiple_skipped_without_enough_to_compute_it(call):
    assert validator.check_r_multiple(call) == []


def test_r_multiple_golden_oil_fixture_passes(oil):
    assert validator.check_r_multiple(oil.thesis.call) == []


def test_r_multiple_uses_the_same_gate_place_bracket_runs():
    """check_r_multiple must not duplicate the R math - pathiel.agents.
    bracket.check_levels is the single source of truth place_bracket itself
    refuses orders against."""
    from pathiel.agents.bracket import check_levels

    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=100.0, stop_px=99.0,
                targets=[100.8], confidence=0.5, invalidation="i")   # R = 0.8, under 1.5
    want = check_levels(100.0, 99.0, 100.8)
    errors = validator.check_r_multiple(call)
    assert len(errors) == 1 and f"{want['r_multiple']:g}R" in errors[0]


# -- stop-within-ladder gate (2026-10-23) --------------------------------------


def test_stop_wider_than_the_ladder_rung_fails(oil):
    """The fixture's 5x rung has entry 99.339, stop 88.64 (~10.77% away).
    A call stop farther than that at the same leverage must fail."""
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, stop_px=80.0,
                leverage=5, confidence=0.5, invalidation="i")
    errors = validator.check_stop_within_ladder(call, oil.analytics)
    assert len(errors) == 1 and "wider than" in errors[0] and "5x ladder rung" in errors[0]


def test_stop_inside_the_ladder_rung_passes(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, stop_px=95.0,
                leverage=5, confidence=0.5, invalidation="i")
    assert validator.check_stop_within_ladder(call, oil.analytics) == []


def test_stop_exactly_at_the_ladder_rung_passes(oil):
    """The ladder rung's OWN stop_px, verbatim, is the limit - exactly
    matching it must not fail on float noise."""
    rung = next(r for r in oil.analytics.ladder if r.leverage == 5.0 and r.side == "long")
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=rung.entry_px,
                stop_px=rung.stop_px, leverage=5, confidence=0.5, invalidation="i")
    assert validator.check_stop_within_ladder(call, oil.analytics) == []


def test_stop_within_ladder_golden_oil_fixture_passes(oil):
    assert validator.check_stop_within_ladder(oil.thesis.call, oil.analytics) == []


@pytest.mark.parametrize("call", [
    Call(side="flat", instrument="xyz:CL", one_liner="x", confidence=0.0, invalidation="i"),
    Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, stop_px=95.0,
         confidence=0.5, invalidation="i"),   # no leverage
    Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, stop_px=95.0,
         leverage=7.0, confidence=0.5, invalidation="i"),   # not a ladder rung
])
def test_stop_within_ladder_skipped_without_a_matching_rung(oil, call):
    assert validator.check_stop_within_ladder(call, oil.analytics) == []


# -- one_liner length ----------------------------------------------------------


def test_one_liner_over_160_chars_fails(oil):
    """Call.one_liner already has Field(max_length=160) - the realistic
    failure path is model_validate_json rejecting the draft outright (see
    test_api_e2e.py). check_one_liner_length exists as a defensive,
    independently-testable rule for a Call built without going through JSON
    validation (model_copy skips field validators), and is tested that way
    here."""
    call = oil.thesis.call.model_copy(update={"one_liner": "x" * 200})
    errors = validator.check_one_liner_length(call)
    assert len(errors) == 1 and "200" in errors[0]


def test_one_liner_under_160_chars_passes(oil):
    assert validator.check_one_liner_length(oil.thesis.call) == []


def test_llm_schema_itself_rejects_an_over_length_one_liner_at_parse_time(oil):
    import json
    from pydantic import ValidationError

    payload = oil.thesis.model_dump(mode="json")
    draft_payload = {
        "call": {**payload["call"], "one_liner": "x" * 200},
        "scenarios": payload["scenarios"], "bull": payload["bull"], "bear": payload["bear"],
        "what_changes_my_mind": payload["what_changes_my_mind"], "lessons": payload["lessons"],
        "leverage_reasons": [{"leverage": lv["leverage"], "reason": lv["reason"]} for lv in payload["leverage_view"]],
    }
    import pytest
    with pytest.raises(ValidationError):
        ThesisDraft.model_validate_json(json.dumps(draft_payload))


# -- entry near mid / instrument known / targets consistent -------------------


def test_entry_far_from_mid_fails(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=150.0, confidence=0.5,
                invalidation="i")
    errors = validator.check_entry_near_mid(call, oil.analytics)
    assert len(errors) == 1 and "sanity band" in errors[0]


def test_entry_at_mid_passes(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, confidence=0.5,
                invalidation="i")
    assert validator.check_entry_near_mid(call, oil.analytics) == []


def test_unknown_instrument_fails(oil):
    call = Call(side="long", instrument="xyz:NOPE", one_liner="x", confidence=0.5, invalidation="i")
    errors = validator.check_instrument_known(call, oil.analytics)
    assert len(errors) == 1 and "xyz:NOPE" in errors[0]


def test_known_pair_label_passes_instrument_known(oil):
    call = Call(side="pair", instrument="Brent-WTI spread", one_liner="x", confidence=0.5, invalidation="i")
    assert validator.check_instrument_known(call, oil.analytics) == []


def test_flat_call_skips_instrument_known(oil):
    call = Call(side="flat", instrument="anything", one_liner="x", confidence=0.0, invalidation="i")
    assert validator.check_instrument_known(call, oil.analytics) == []


def test_long_target_below_entry_fails(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, targets=[90.0],
                confidence=0.5, invalidation="i")
    errors = validator.check_targets_consistent(call, oil.analytics)
    assert any("must be above entry_px" in e for e in errors)


def test_implausibly_distant_target_fails(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, targets=[999999.0],
                confidence=0.5, invalidation="i")
    errors = validator.check_targets_consistent(call, oil.analytics)
    assert any("implausibly far" in e for e in errors)


def test_reasonable_targets_pass(oil):
    call = Call(side="long", instrument="xyz:CL", one_liner="x", entry_px=99.339, targets=[106.6, 111.0],
                confidence=0.5, invalidation="i")
    assert validator.check_targets_consistent(call, oil.analytics) == []


# -- targets reachable: venue all-time high/low + empirical reach (2026-10-30) --
#
# Root cause (operator, 2026-10-30): the live Brent desk's thesis called
# "Long Brent 5x at 113.87, stop 111.29, targets 118.87 then 122.20" on a
# venue whose all-time high was 117.58 - a price the market has literally
# never traded at. The oil fixture predates these fields entirely (every one
# is None), so every test here builds its own analytics with them populated.


def _brent_structure_with_extremes(ath=117.58, atl=60.0):
    from contracts.strategies.models import InstrumentStructure, PriceExtreme
    from datetime import datetime, timezone

    return InstrumentStructure(
        coin="xyz:BRENTOIL", mid=113.87,
        all_time_high=PriceExtreme(price=ath, at=datetime(2026, 3, 9, tzinfo=timezone.utc)),
        all_time_low=PriceExtreme(price=atl, at=datetime(2026, 3, 4, tzinfo=timezone.utc)),
    )


def _brent_forward_with_reach(long_reach=None, short_reach=None):
    from contracts.strategies.models import ForwardStats, Quantiles
    from datetime import datetime, timezone

    q = Quantiles(p10=-0.09, p25=-0.04, p50=0.01, p75=0.05, p90=0.1)
    return ForwardStats(
        coin="xyz:BRENTOIL", horizon_days=7, n_windows=200,
        sample_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sample_end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        final_ret=q, final_ret_mean=0.01,
        long_mae=Quantiles(p10=-0.1, p25=-0.06, p50=-0.03, p75=-0.01, p90=-0.003),
        short_mae=Quantiles(p10=0.003, p25=0.01, p50=0.03, p75=0.06, p90=0.1),
        long_reach=long_reach, short_reach=short_reach,
    )


def _analytics_with_structure_and_forward(oil, *, structure=None, forward=None):
    update = {}
    if structure is not None:
        update["structure"] = [s for s in oil.analytics.structure if s.coin != "xyz:BRENTOIL"] + [structure]
    if forward is not None:
        update["forward"] = [f for f in oil.analytics.forward if f.coin != "xyz:BRENTOIL"] + [forward]
    return oil.analytics.model_copy(update=update)


def test_targets_reachable_skips_silently_when_fields_absent(oil):
    """The real oil fixture's structure/forward predate these fields -
    check_targets_reachable must not invent a violation out of None."""
    assert validator.check_targets_reachable(oil.thesis.call, oil.analytics) == []


def test_long_target_above_all_time_high_fails():
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics, InstrumentStructure, PriceExtreme

    structure = InstrumentStructure(
        coin="xyz:BRENTOIL", mid=113.87,
        all_time_high=PriceExtreme(price=117.58, at=datetime(2026, 3, 9, tzinfo=timezone.utc)))
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                targets=[118.87], confidence=0.5, invalidation="i")
    analytics = DeskAnalytics(desk_id="x", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc),
                              instruments=[], structure=[structure])
    errors = validator.check_targets_reachable(call, analytics)
    assert len(errors) == 1
    assert "all-time high" in errors[0] and "117.58" in errors[0] and "118.87" in errors[0]


def test_short_target_below_all_time_low_fails():
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics, InstrumentStructure, PriceExtreme

    structure = InstrumentStructure(
        coin="xyz:BRENTOIL", mid=113.87,
        all_time_low=PriceExtreme(price=60.0, at=datetime(2026, 3, 4, tzinfo=timezone.utc)))
    call = Call(side="short", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                targets=[55.0], confidence=0.5, invalidation="i")
    analytics = DeskAnalytics(desk_id="x", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc),
                              instruments=[], structure=[structure])
    errors = validator.check_targets_reachable(call, analytics)
    assert len(errors) == 1
    assert "all-time low" in errors[0] and "60" in errors[0]


def test_target_at_or_below_all_time_high_passes():
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics, InstrumentStructure, PriceExtreme

    structure = InstrumentStructure(
        coin="xyz:BRENTOIL", mid=113.87,
        all_time_high=PriceExtreme(price=117.58, at=datetime(2026, 3, 9, tzinfo=timezone.utc)))
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                targets=[117.58], confidence=0.5, invalidation="i")   # exactly the ATH
    analytics = DeskAnalytics(desk_id="x", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc),
                              instruments=[], structure=[structure])
    assert validator.check_targets_reachable(call, analytics) == []


def test_first_target_beyond_p75_reach_fails(oil):
    from contracts.strategies.models import Quantiles

    forward = _brent_forward_with_reach(long_reach=Quantiles(p10=0.01, p25=0.02, p50=0.03, p75=0.045, p90=0.09))
    analytics = _analytics_with_structure_and_forward(oil, forward=forward)
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                targets=[119.0], confidence=0.5, invalidation="i")   # implied +4.5...% > p75 4.5%, < p90 9%
    errors = validator.check_targets_reachable(call, analytics)
    assert len(errors) == 1 and "p75" in errors[0] and "first target" in errors[0]


def test_any_target_beyond_p90_reach_fails_even_as_a_later_target(oil):
    from contracts.strategies.models import Quantiles

    forward = _brent_forward_with_reach(long_reach=Quantiles(p10=0.01, p25=0.02, p50=0.03, p75=0.045, p90=0.06))
    analytics = _analytics_with_structure_and_forward(oil, forward=forward)
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                targets=[116.0, 122.20], confidence=0.5, invalidation="i")
    errors = validator.check_targets_reachable(call, analytics)
    assert len(errors) == 1 and "p90" in errors[0] and "122.2" in errors[0]


def test_later_target_beyond_p75_but_within_p90_is_not_an_error(oil):
    """Only the FIRST target gets the tighter p75 bound; a later one is
    allowed up to p90 - the prompt asks the writer for p75 on later targets,
    but the validator's backstop is deliberately looser there too."""
    from contracts.strategies.models import Quantiles

    forward = _brent_forward_with_reach(long_reach=Quantiles(p10=0.01, p25=0.02, p50=0.03, p75=0.04, p90=0.09))
    analytics = _analytics_with_structure_and_forward(oil, forward=forward)
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                targets=[115.5, 119.0], confidence=0.5, invalidation="i")   # 1st +1.4% (ok), 2nd +4.5% (>p75, <p90)
    assert validator.check_targets_reachable(call, analytics) == []


def test_targets_reachable_skipped_for_flat_or_missing_targets():
    flat = Call(side="flat", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.0, invalidation="i")
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics

    analytics = DeskAnalytics(desk_id="x", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc), instruments=[])
    assert validator.check_targets_reachable(flat, analytics) == []
    no_targets = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                      confidence=0.5, invalidation="i")
    assert validator.check_targets_reachable(no_targets, analytics) == []


# -- scenarios calibrated to the empirical return distribution (2026-10-30) ----


def test_scenarios_calibrated_golden_oil_fixture_passes(oil):
    assert validator.check_scenarios_calibrated(oil.thesis.scenarios, oil.spec, oil.analytics) == []


def test_scenario_set_skewed_far_bullish_of_the_empirical_distribution_fails(oil):
    """xyz:CL's final_ret p90 is +10.59% (the real fixture). A scenario set
    whose own implied median sits WAY above that - all probability loaded
    onto a big-up bucket - is an optimistic skew dressed as a probability
    distribution, exactly what the operator flagged on 2026-10-30."""
    skewed = [
        Scenario(name="Moon", probability=0.9, move_lo_pct=0.20, move_hi_pct=0.60, description="d"),
        Scenario(name="Chop", probability=0.1, move_lo_pct=-0.02, move_hi_pct=0.20, description="d"),
    ]
    errors = validator.check_scenarios_calibrated(skewed, oil.spec, oil.analytics)
    assert len(errors) == 1 and "outside the empirical p10-p90 band" in errors[0]


def test_scenarios_calibrated_skipped_when_no_forward_stats():
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics, DeskSpec, Instrument

    spec = DeskSpec(id="xx", name="X", tagline="t",
                    instruments=[Instrument(coin="A", label="A", role="primary")])
    analytics = DeskAnalytics(desk_id="x", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc), instruments=[])
    scenarios = [Scenario(name="s", probability=1.0, move_lo_pct=0.5, move_hi_pct=1.0, description="d")]
    assert validator.check_scenarios_calibrated(scenarios, spec, analytics) == []


def test_scenarios_calibrated_skipped_for_empty_scenarios(oil):
    assert validator.check_scenarios_calibrated([], oil.spec, oil.analytics) == []


# -- regression: the exact 2026-10-30 live call must now be rejected ----------


def test_live_2026_10_30_brent_call_is_rejected_for_targets_beyond_the_all_time_high():
    """The exact call the operator flagged: "Long Brent 5x at 113.87, stop
    111.29, targets 118.87 then 122.20" on a venue whose all-time high was
    117.58 (set 2026-03-09) - BOTH targets sit above a price Brent has
    literally never traded at on this venue. Must be rejected."""
    call = Call(
        side="long", instrument="xyz:BRENTOIL",
        one_liner="Long Brent 5x at 113.87, stop 111.29, targets 118.87 then 122.20.",
        entry_px=113.87, stop_px=111.29, targets=[118.87, 122.20], leverage=5, confidence=0.5,
        invalidation="Daily close below 111.29.",
    )
    structure = _brent_structure_with_extremes()
    from contracts.strategies.models import Quantiles

    forward = _brent_forward_with_reach(
        long_reach=Quantiles(p10=0.01, p25=0.02, p50=0.03, p75=0.045, p90=0.06))
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics

    analytics = DeskAnalytics(desk_id="oil", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc),
                              instruments=[], structure=[structure], forward=[forward])
    errors = validator.check_targets_reachable(call, analytics)
    assert errors, "the 2026-10-30 call must be rejected, not pass silently"
    assert any("all-time high" in e and "117.58" in e for e in errors)
    assert any("118.87" in e for e in errors) and any("122.2" in e for e in errors)
    assert any("p90" in e for e in errors)   # the second target also blows the empirical reach


def test_a_realistic_version_of_the_same_call_passes():
    """Same desk, same entry/stop, but a target picked from the empirical
    reach instead of an aspirational number, and under the venue's
    all-time high - the shape the fix is supposed to produce."""
    call = Call(
        side="long", instrument="xyz:BRENTOIL",
        one_liner="Long Brent 5x at 113.87, stop 111.29, target 116.5.",
        entry_px=113.87, stop_px=112.5, targets=[116.5], leverage=5, confidence=0.5,
        invalidation="Daily close below 112.5.",
    )
    structure = _brent_structure_with_extremes()
    from contracts.strategies.models import Quantiles

    forward = _brent_forward_with_reach(
        long_reach=Quantiles(p10=0.01, p25=0.02, p50=0.03, p75=0.045, p90=0.09))
    from datetime import datetime, timezone

    from contracts.strategies.models import DeskAnalytics

    analytics = DeskAnalytics(desk_id="oil", as_of=datetime(2026, 10, 30, tzinfo=timezone.utc),
                              instruments=[], structure=[structure], forward=[forward])
    assert validator.check_targets_reachable(call, analytics) == []
    assert validator.check_r_multiple(call) == []


# -- lessons / leverage_reasons count ------------------------------------------


def test_zero_lessons_fails():
    assert len(validator.check_lessons_count([])) == 1


def test_two_lessons_fails():
    lesson = Lesson(term="t", explanation="e", tied_to="x")
    assert len(validator.check_lessons_count([lesson, lesson])) == 1


def test_exactly_one_lesson_passes(oil):
    assert validator.check_lessons_count(oil.thesis.lessons) == []


def test_leverage_reasons_count_mismatch_fails(oil):
    errors = validator.check_leverage_reasons_count(
        [LeverageReason(leverage=5.0, reason="only one reason")], oil.analytics)
    assert len(errors) == 1 and "10" in errors[0]


def test_leverage_reasons_duplicate_leverage_fails(oil):
    dup = [LeverageReason(leverage=5.0, reason="a"), LeverageReason(leverage=5.0, reason="b"),
           LeverageReason(leverage=20.0, reason="c")]
    assert validator.check_leverage_reasons_count(dup, oil.analytics)


def test_leverage_reasons_count_match_passes(oil):
    reasons = [LeverageReason(leverage=lv.leverage, reason=lv.reason) for lv in oil.thesis.leverage_view]
    assert validator.check_leverage_reasons_count(reasons, oil.analytics) == []


# -- scenario count -------------------------------------------------------------


def test_two_scenarios_fails_count_check():
    s = Scenario(name="A", probability=1.0, move_lo_pct=0.0, move_hi_pct=0.1, description="d")
    assert len(validator.check_scenario_count([s])) == 1


def test_three_to_five_scenarios_passes_count_check(oil):
    assert validator.check_scenario_count(oil.thesis.scenarios) == []


# -- scenario-number derived range (price vs percent) --------------------------


def test_scenario_price_number_outside_its_own_bucket_fails(oil):
    scenarios = list(oil.thesis.scenarios)
    scenarios[0] = scenarios[0].model_copy(update={"description": "WTI spikes to 9999 on secret deal"})
    errors = validator.check_scenario_numbers(scenarios, oil.spec, oil.analytics)
    assert any("9999" in e for e in errors)


def test_scenario_percent_number_checked_against_percent_range_not_price_range(oil):
    """'-10% to -15%' in the real fixture's most bearish bucket (-50%/-8%)
    must pass - it is a percent, not a price, and must not be measured
    against a derived PRICE range."""
    errors = validator.check_scenario_numbers(oil.thesis.scenarios, oil.spec, oil.analytics)
    assert errors == []


def test_scenario_percent_number_outside_its_own_bucket_fails(oil):
    s = Scenario(name="A", probability=1.0, move_lo_pct=0.0, move_hi_pct=0.05,
                 description="expect a wild -40% day")
    errors = validator.check_scenario_numbers([s], oil.spec, oil.analytics)
    assert any("-40" in e for e in errors)


def test_scenario_abbreviated_k_number_matches_its_own_derived_range():
    """Live 2026-10-17 regression (synthetic BTC eval): a scenario wrote
    bare "120" meaning "$120k". Must be read as 120_000 and checked against
    that scenario's OWN derived range, not rejected as a tiny invented
    number near 120."""
    from services.thesis.evals.btc_fixture import build_btc_packet
    btc_spec, btc_analytics, _ = build_btc_packet()
    s = Scenario(name="Breakout", probability=1.0, move_lo_pct=0.03, move_hi_pct=0.21,
                 description="BTC breaks above 120 on ETF flows.")
    assert validator.check_scenario_numbers([s], btc_spec, btc_analytics) == []


def test_scenario_abbreviated_k_number_still_fails_outside_its_range():
    """The abbreviation allowance is scenario-specific, not a blanket
    'small numbers are fine' rule: 50 (meaning $50k) is nowhere near this
    scenario's own +3%/+21% bucket off a ~$108k mid, so it must still fail."""
    from services.thesis.evals.btc_fixture import build_btc_packet
    btc_spec, btc_analytics, _ = build_btc_packet()
    s = Scenario(name="Breakout", probability=1.0, move_lo_pct=0.03, move_hi_pct=0.21,
                 description="BTC crashes to 50 on a black swan.")
    errors = validator.check_scenario_numbers([s], btc_spec, btc_analytics)
    assert any("50" in e for e in errors)


def test_scenario_day_of_month_style_number_is_tolerated(oil):
    scenarios = list(oil.thesis.scenarios)
    scenarios[0] = scenarios[0].model_copy(update={"description": "No deal before Nov 1 meeting."})
    errors = validator.check_scenario_numbers(scenarios, oil.spec, oil.analytics)
    assert errors == []


# -- aggregation: validate_draft / validate_thesis ----------------------------


def test_validate_draft_collects_errors_from_multiple_rules_at_once(oil):
    bad_call = oil.thesis.call.model_copy(update={"stop_px": 150.0})  # wrong side for a long
    draft = _draft_from_thesis(oil.thesis).model_copy(update={"call": bad_call})
    errors = validator.validate_draft(draft, oil.spec, oil.analytics, oil.intel)
    assert any("below entry_px" in e for e in errors)


def test_validate_thesis_matches_validate_draft_on_the_same_content(oil):
    draft = _draft_from_thesis(oil.thesis)
    assert validator.validate_draft(draft, oil.spec, oil.analytics, oil.intel) == \
        validator.validate_thesis(oil.thesis, oil.spec, oil.analytics, oil.intel)


# -- scenario numbers: grounded facts pass (live finding 2026-10-17) ----------

def test_scenario_text_may_quote_grounded_numbers_outside_its_range(oil):
    """Polymarket odds or another instrument's price in a scenario's text are
    facts from the payload, not scenario targets: they must not force repair."""
    from services.thesis import validator as V
    brent_mid = next(i.mid for i in oil.analytics.instruments if i.coin == "xyz:BRENTOIL")
    odds_p = oil.intel.odds[0].probability
    sc = oil.thesis.scenarios[2].model_copy(update={
        "description": f"Brent holds {brent_mid}; deal odds {odds_p} stay low."})
    pool = V.build_ground_truth_pool(oil.analytics, oil.intel)
    assert V.check_scenario_numbers([sc], oil.spec, oil.analytics, pool=pool) == []


def test_scenario_text_with_an_invented_price_still_fails(oil):
    from services.thesis import validator as V
    sc = oil.thesis.scenarios[2].model_copy(update={"description": "WTI collapses to 61.37."})
    pool = V.build_ground_truth_pool(oil.analytics, oil.intel)
    errs = V.check_scenario_numbers([sc], oil.spec, oil.analytics, pool=pool)
    assert errs and "61.37" in errs[0]


def test_points_may_cite_analytics_sections(oil):
    """Live 2026-10-17: a point cited "forward" (the forward-window stats) and
    forced a repair. Analytics section ids are valid citations; made-up ids
    still fail."""
    ok = [Point(text="forward p50 +1.31%", source_ids=["forward"]),
          Point(text="5x liq far below", source_ids=["ladder", "h1"])]
    assert validator.check_cited_ids_exist(ok, [], oil.intel) == []
    bad = [Point(text="x", source_ids=["made_up_id"])]
    assert validator.check_cited_ids_exist(bad, [], oil.intel)


def test_analytics_citations_never_become_links(oil):
    from services.thesis import assemble
    draft = ThesisDraft(
        call=oil.thesis.call, scenarios=oil.thesis.scenarios,
        bull=[Point(text="forward p50", source_ids=["forward"])], bear=oil.thesis.bear,
        what_changes_my_mind=oil.thesis.what_changes_my_mind, lessons=oil.thesis.lessons,
        leverage_reasons=[{"leverage": lv.leverage, "reason": lv.reason} for lv in oil.thesis.leverage_view])
    assert "forward" not in {s.id for s in assemble.resolve_sources(draft, oil.intel)}


def test_call_distance_from_mid_is_grounded(oil):
    """Live 2026-10-17: "stop 94.33 (-4.6% from mid)" in the invalidation
    forced a repair. Distances between the call's own prices and the mid or
    entry are arithmetic on validated numbers; an unrelated percent still fails."""
    mid = next(i.mid for i in oil.analytics.instruments if i.coin == "xyz:CL")
    call = oil.thesis.call
    d = (call.stop_px / mid - 1.0) * 100.0
    ok = call.model_copy(update={"invalidation": f"Close below {call.stop_px} ({d:.1f}% from mid)."})
    errs = validator.check_numbers_grounded(
        call=ok, bull=[], bear=[], lessons=oil.thesis.lessons, what_changes_my_mind=[],
        leverage_reasons=[], spec=oil.spec, analytics=oil.analytics, intel=oil.intel)
    assert errs == []
    bad = call.model_copy(update={"invalidation": "Close below 37.77% drawdown."})
    errs = validator.check_numbers_grounded(
        call=bad, bull=[], bear=[], lessons=oil.thesis.lessons, what_changes_my_mind=[],
        leverage_reasons=[], spec=oil.spec, analytics=oil.analytics, intel=oil.intel)
    assert errs and "37.77" in errs[0]


def test_call_distance_in_price_units_and_r_multiples_are_grounded(oil):
    """2026-10-25: two refreshes in one day were rejected for arithmetic on
    the call's own prices. "$4.60 above entry" and "pays 7.7R" are not
    invented facts; a number unrelated to any of them still fails."""
    call = oil.thesis.call
    target = call.targets[0]
    dollars = abs(target - call.entry_px)
    r_mult = abs(target - call.entry_px) / abs(call.entry_px - call.stop_px)
    text = f"Target {target} sits {dollars:.2f} above entry and pays {r_mult:.1f}R."
    errs = validator.check_numbers_grounded(
        call=call, bull=[Point(text=text, source_ids=[])], bear=[], lessons=oil.thesis.lessons,
        what_changes_my_mind=[], leverage_reasons=[], spec=oil.spec, analytics=oil.analytics,
        intel=oil.intel)
    assert errs == [], errs
    pool = validator._call_distance_pool(call, oil.spec, oil.analytics)
    assert not validator._is_grounded(r_mult * 10 + 37.0, pool)   # an unrelated number is still out


def test_unsigned_thousands_shorthand_is_grounded(oil):
    """Eval 2026-10-17: COT change -10403 written as "fell 10.4k contracts"."""
    pool = validator.build_ground_truth_pool(oil.analytics, oil.intel)
    assert validator._is_grounded(10.4, pool)
    assert validator._is_grounded(-10.4, pool)
