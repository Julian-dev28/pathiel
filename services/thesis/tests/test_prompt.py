"""Unit tests for services/thesis/prompt.py: token estimation, the system
prompt, the dense analytics/intel renderers (and their budget trimming), the
incremental diff, is_small_change, and the repair prompt.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from contracts.strategies.models import Headline
from services.thesis import prompt as prompt_mod


# -- estimate_tokens ----------------------------------------------------------


def test_estimate_tokens_roughly_four_chars_per_token():
    assert prompt_mod.estimate_tokens("a" * 400) == 100


def test_estimate_tokens_never_zero_for_nonempty_text():
    assert prompt_mod.estimate_tokens("hi") >= 1


# -- extract_numbers / extract_numbers_with_flags -----------------------------


def test_extract_numbers_ignores_digits_glued_to_a_source_id():
    assert prompt_mod.extract_numbers("cites h1 and c2 and o10") == []


def test_extract_numbers_finds_standalone_numbers_including_negatives_and_decimals():
    assert prompt_mod.extract_numbers("liq 81.51, P(liq) 3.2%, down -17.95%") == [81.51, 3.2, -17.95]


def test_extract_numbers_with_flags_marks_percent_numbers():
    out = prompt_mod.extract_numbers_with_flags("price 99.34, move -10%")
    assert out == [(99.34, False), (-10.0, True)]


# -- system prompt ------------------------------------------------------------


def test_system_prompt_names_the_desk_and_its_lessons(oil):
    text = prompt_mod.build_system_prompt(oil.spec)
    assert "Oil desk" in text
    assert "Julien" in text and "ADHD" in text
    for lesson in oil.spec.lessons:
        assert lesson in text


def test_system_prompt_has_no_em_dash(oil):
    assert "\u2014" not in prompt_mod.build_system_prompt(oil.spec)


def test_system_prompt_states_the_160_char_cap_and_the_grounding_rule(oil):
    text = prompt_mod.build_system_prompt(oil.spec)
    assert "160" in text
    assert "invent" in text.lower()


def test_system_prompt_states_the_structural_stop_and_r_rules(oil):
    text = prompt_mod.build_system_prompt(oil.spec)
    assert "structural edge" in text
    assert "1.5R" in text
    assert "LIMIT" in text


def test_system_prompt_states_the_realistic_targets_and_calibration_rules(oil):
    """2026-10-30: the writer must treat targets as the most likely
    reachable structure, never an aspirational number beyond the venue's
    own trade history, and scenarios must be calibrated to the empirical
    return distribution, not an optimistic skew."""
    text = prompt_mod.build_system_prompt(oil.spec)
    assert "all-time high" in text and "all-time low" in text
    assert "REACH" in text
    assert "p50" in text and "p75" in text
    assert "calibrated" in text
    assert "median daily range" in text


# -- render_analytics_dense ---------------------------------------------------


def test_render_analytics_dense_includes_every_instrument_and_key_figures(oil):
    text = prompt_mod.render_analytics_dense(oil.spec, oil.analytics)
    assert "WTI" in text and "Brent" in text
    assert "99.339" in text  # mid
    assert "81.51" in text  # 5x liq px
    assert "AVOID" in text  # 20x verdict


def test_render_analytics_dense_has_no_em_dash(oil):
    assert "\u2014" not in prompt_mod.render_analytics_dense(oil.spec, oil.analytics)


def test_render_analytics_dense_includes_the_structure_block(oil):
    text = prompt_mod.render_analytics_dense(oil.spec, oil.analytics)
    assert "STRUCTURE" in text
    st = oil.analytics.structure[0]
    assert st.coin in text
    assert "98.60" in text or "98.6" in text   # range_8h low
    assert "ATR14 (1h)" in text
    assert "mid sits at 77% of the 24h range" in text   # 0.769 -> 77%


def test_render_analytics_dense_omits_the_structure_block_when_empty(oil):
    analytics_no_structure = oil.analytics.model_copy(update={"structure": []})
    text = prompt_mod.render_analytics_dense(oil.spec, analytics_no_structure)
    assert "STRUCTURE" not in text


def test_render_structure_omits_all_time_and_median_range_when_absent(oil):
    """The real oil fixture predates the 2026-10-30 fields - every one of
    them is None, and the render must say nothing about them rather than
    print a blank or a None."""
    text = prompt_mod.render_analytics_dense(oil.spec, oil.analytics)
    assert "ALL-TIME HIGH" not in text
    assert "ALL-TIME LOW" not in text
    assert "90d range" not in text
    assert "median daily range" not in text


def test_render_structure_includes_all_time_high_low_90d_and_median_range(oil):
    from contracts.strategies.models import PriceExtreme, PriceRange

    st = oil.analytics.structure[0].model_copy(update={
        "all_time_high": PriceExtreme(price=117.58, at=prompt_mod.utc_now().replace(
            year=2026, month=3, day=9)),
        "all_time_low": PriceExtreme(price=60.12, at=prompt_mod.utc_now().replace(
            year=2026, month=4, day=1)),
        "range_90d": PriceRange(high=112.0, low=88.0),
        "median_daily_range_30d_pct": 0.0276,
    })
    analytics2 = oil.analytics.model_copy(update={"structure": [st]})
    text = prompt_mod.render_analytics_dense(oil.spec, analytics2)
    assert "ALL-TIME HIGH 117.580" in text and "2026-03-09" in text
    assert "ALL-TIME LOW 60.120" in text and "2026-04-01" in text
    assert "no long target may sit above this" in text
    assert "no short target may sit below this" in text
    assert "90d range: 88.000 to 112.000" in text
    assert "median daily range (30d) 2.8%" in text
    assert "beyond this from entry" in text


def test_render_forward_omits_reach_lines_when_absent(oil):
    """The real oil fixture predates the 2026-10-30 reach fields (None) -
    the render must not print a REACH line with nothing behind it."""
    text = prompt_mod.render_analytics_dense(oil.spec, oil.analytics)
    assert "REACH" not in text


def test_render_forward_includes_reach_quantiles_when_present(oil):
    from contracts.strategies.models import Quantiles

    fwd = oil.analytics.forward[0].model_copy(update={
        "long_reach": Quantiles(p10=0.01, p25=0.02, p50=0.035, p75=0.06, p90=0.09),
        "short_reach": Quantiles(p10=0.015, p25=0.03, p50=0.045, p75=0.07, p90=0.11),
    })
    analytics2 = oil.analytics.model_copy(update={"forward": [fwd]})
    text = prompt_mod.render_analytics_dense(oil.spec, analytics2)
    assert "long max favourable excursion (REACH" in text
    assert "p50 +3.50%" in text
    assert "short max favourable excursion (REACH" in text
    assert "p50 +4.50%" in text
    assert "use this for a long's targets" in text
    assert "use this for a short's targets" in text


def test_render_analytics_dense_ladder_header_names_the_stop_as_a_limit(oil):
    text = prompt_mod.render_analytics_dense(oil.spec, oil.analytics)
    assert "LEVERAGE LADDER" in text
    assert "LIMIT" in text


# -- render_intel_compact (fallback renderer) ---------------------------------


def test_render_intel_compact_includes_headline_ids_and_catalysts(oil):
    text = prompt_mod.render_intel_compact(oil.intel, max_tokens=4000)
    assert "[h1]" in text and "[h2]" in text and "[h3]" in text
    assert "[c1]" in text and "[c2]" in text and "[c3]" in text
    assert "[o1]" in text
    assert "[cot1]" in text


def test_render_intel_compact_sorts_headlines_by_relevance_descending(oil):
    text = prompt_mod.render_intel_compact(oil.intel, max_tokens=4000)
    assert text.index("[h1]") < text.index("[h2]") < text.index("[h3]")  # 0.95, 0.80, 0.70


def test_render_intel_compact_truncates_under_a_tight_budget(oil, monkeypatch):
    # The fallback renderer's own budget behavior; intel's renderer (preferred
    # when importable) reserves header + sources_failed and is tested there.
    monkeypatch.setattr(prompt_mod, "_intel_render_compact", None)
    full = prompt_mod.render_intel_compact(oil.intel, max_tokens=4000)
    tight = prompt_mod.render_intel_compact(oil.intel, max_tokens=20)
    assert len(tight) < len(full)
    assert prompt_mod.estimate_tokens(tight) <= 20 + 5  # a little slack for the section title itself


def test_render_intel_compact_notes_how_many_lines_were_omitted(oil, monkeypatch):
    monkeypatch.setattr(prompt_mod, "_intel_render_compact", None)
    tight = prompt_mod.render_intel_compact(oil.intel, max_tokens=25)
    assert "omitted for the token budget" in tight


def test_render_intel_compact_prefers_services_intel_api_when_importable(monkeypatch, oil):
    calls = {}

    def _fake_render_compact(packet, max_tokens):
        calls["packet"] = packet
        calls["max_tokens"] = max_tokens
        return "RENDERED BY SERVICES.INTEL"

    monkeypatch.setattr(prompt_mod, "_intel_render_compact", _fake_render_compact)
    out = prompt_mod.render_intel_compact(oil.intel, max_tokens=500)
    assert out == "RENDERED BY SERVICES.INTEL"
    assert calls["packet"] is oil.intel
    assert calls["max_tokens"] == 500


# -- build_user_prompt --------------------------------------------------------


def test_build_user_prompt_full_mode_includes_analytics_and_intel(oil):
    text, tokens = prompt_mod.build_user_prompt(oil.spec, oil.analytics, oil.intel, None, incremental=False)
    assert "MARKET SNAPSHOT" in text
    assert "[h1]" in text
    assert tokens <= oil.spec.token_budget.max_input_tokens


def test_build_user_prompt_handles_missing_intel(oil):
    text, _ = prompt_mod.build_user_prompt(oil.spec, oil.analytics, None, None, incremental=False)
    assert "No live intel packet" in text
    assert "do not invent" in text


def test_build_user_prompt_incremental_is_smaller_than_full(oil):
    full_text, full_tokens = prompt_mod.build_user_prompt(
        oil.spec, oil.analytics, oil.intel, oil.thesis, incremental=False,
    )
    inc_text, inc_tokens = prompt_mod.build_user_prompt(
        oil.spec, oil.analytics, oil.intel, oil.thesis, incremental=True,
    )
    assert inc_tokens < full_tokens
    assert inc_tokens <= full_tokens * 0.6  # measured ~52% smaller on this fixture
    assert "SMALL update" in inc_text
    assert "PREVIOUS CALL" in inc_text


def test_build_user_prompt_incremental_includes_the_primary_structure(oil):
    text, _ = prompt_mod.build_user_prompt(oil.spec, oil.analytics, oil.intel, oil.thesis, incremental=True)
    assert "STRUCTURE" in text
    assert oil.analytics.structure[0].coin in text


def test_build_user_prompt_incremental_states_the_previous_call(oil):
    text, _ = prompt_mod.build_user_prompt(oil.spec, oil.analytics, oil.intel, oil.thesis, incremental=True)
    assert oil.thesis.call.one_liner in text
    assert str(oil.thesis.call.stop_px) in text


def test_build_user_prompt_incremental_reports_new_high_relevance_headlines(oil):
    new_headline = Headline(
        id="h99", title="Fresh Iran deal rumor spikes WTI", url="https://example.com/h99",
        source="Reuters", published_at=oil.thesis.created_at + timedelta(hours=1),
        stance="bear", relevance=0.9,
    )
    intel2 = oil.intel.model_copy(update={"headlines": [*oil.intel.headlines, new_headline]})
    text, _ = prompt_mod.build_user_prompt(oil.spec, oil.analytics, intel2, oil.thesis, incremental=True)
    assert "1 new high-relevance headline" in text
    assert "h99" in text


# -- is_small_change -----------------------------------------------------------


def test_is_small_change_true_when_price_and_news_are_quiet(oil):
    assert prompt_mod.is_small_change(oil.spec, oil.analytics, oil.intel, oil.thesis) is True


def test_is_small_change_false_on_a_material_move(oil):
    moved = oil.analytics.model_copy(update={
        "instruments": [
            snap.model_copy(update={"mid": snap.mid * 1.05}) if snap.coin == "xyz:CL" else snap
            for snap in oil.analytics.instruments
        ],
    })
    assert prompt_mod.is_small_change(oil.spec, moved, oil.intel, oil.thesis) is False


def test_is_small_change_false_on_a_new_high_relevance_headline(oil):
    new_headline = Headline(
        id="h99", title="Breaking", url="https://example.com/h99", source="Reuters",
        published_at=oil.thesis.created_at + timedelta(minutes=5), stance="bull", relevance=0.95,
    )
    intel2 = oil.intel.model_copy(update={"headlines": [*oil.intel.headlines, new_headline]})
    assert prompt_mod.is_small_change(oil.spec, oil.analytics, intel2, oil.thesis) is False


def test_is_small_change_ignores_a_low_relevance_new_headline(oil):
    new_headline = Headline(
        id="h99", title="Minor note", url="https://example.com/h99", source="Reuters",
        published_at=oil.thesis.created_at + timedelta(minutes=5), stance="bull", relevance=0.1,
    )
    intel2 = oil.intel.model_copy(update={"headlines": [*oil.intel.headlines, new_headline]})
    assert prompt_mod.is_small_change(oil.spec, oil.analytics, intel2, oil.thesis) is True


def test_is_small_change_false_when_prev_has_no_entry_price(oil):
    flat_prev = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(update={"entry_px": None})})
    assert prompt_mod.is_small_change(oil.spec, oil.analytics, oil.intel, flat_prev) is False


# -- build_repair_prompt -------------------------------------------------------


def test_build_repair_prompt_without_context_is_short_and_lists_every_error():
    errors = ["scenario probabilities sum to 0.900, must sum to 1.0", "long stop_px (105) must be below entry_px (100)"]
    text = prompt_mod.build_repair_prompt(errors)
    assert "0.900" in text and "105" in text
    assert "market data again" not in text
    assert prompt_mod.estimate_tokens(text) < 150


def test_build_repair_prompt_tells_the_model_to_delete_ungrounded_numbers():
    """A repair that swaps one derived number for another fails the same
    rule again (2026-10-25: two paid rejections in one day). Deleting the
    number is the one fix that always converges."""
    text = prompt_mod.build_repair_prompt(["bull[2].text mentions 4.8, which is not grounded in analytics or intel"])
    assert "delete the number" in text
    assert "do not replace it with another number" in text


def test_build_repair_prompt_with_context_includes_it():
    text = prompt_mod.build_repair_prompt(["bad thing"], full_context="MARKET SNAPSHOT: mid 99")
    assert "bad thing" in text
    assert "MARKET SNAPSHOT: mid 99" in text


@pytest.mark.parametrize("mode", ["full", "incremental"])
def test_build_user_prompt_never_contains_an_em_dash(oil, mode):
    text, _ = prompt_mod.build_user_prompt(
        oil.spec, oil.analytics, oil.intel, oil.thesis, incremental=(mode == "incremental"),
    )
    assert "\u2014" not in text


def test_render_intel_compact_uses_the_intel_service_once_merged(oil):
    """services/intel is merged: the prompt must use its renderer, which keeps
    source ids the thesis cites and always reports failed sources."""
    import services.intel.api as intel_api
    assert prompt_mod._intel_render_compact is intel_api.render_compact
    text = prompt_mod.render_intel_compact(oil.intel, max_tokens=4000)
    assert "SOURCES_FAILED" in text
