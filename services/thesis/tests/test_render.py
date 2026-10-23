"""Unit tests for services/thesis/render.py: both renderers, and the
deterministic helper picks (decision_word, worst case, next catalyst) that
back "quick" mode.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contracts.strategies.models import Catalyst
from services.thesis import render


# -- decision_word --------------------------------------------------------


def test_decision_word_flat_call_is_wait(oil):
    flat = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(update={"side": "flat", "leverage": None})})
    assert render.decision_word(flat) == "WAIT"


@pytest.mark.parametrize("verdict,word", [("hold", "HOLD"), ("dip_only", "DIP"), ("intraday_only", "INTRADAY"), ("avoid", "AVOID")])
def test_decision_word_follows_the_picked_rungs_verdict(oil, verdict, word):
    lv = [lv.model_copy(update={"verdict": verdict}) if lv.leverage == 5.0 else lv for lv in oil.thesis.leverage_view]
    t = oil.thesis.model_copy(update={"leverage_view": lv})
    assert render.decision_word(t) == word


def test_decision_word_watch_when_call_leverage_is_not_on_the_ladder(oil):
    t = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(update={"leverage": 7.0})})
    assert render.decision_word(t) == "WATCH"


# -- quick render -----------------------------------------------------------


def test_render_quick_is_seven_lines_with_the_r_line(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    lines = text.splitlines()
    assert len(lines) == 7


def test_render_quick_shows_the_r_multiple_to_first_target(oil):
    """The fixture call pays 101.19/entry/stop -> 2.21R - check against the
    same check_levels call the line itself uses, not a hand-typed number."""
    from pathiel.agents.bracket import check_levels

    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    call = oil.thesis.call
    gate = check_levels(call.entry_px, call.stop_px, call.targets[0])
    assert f"Pays {gate['r_multiple']:.1f}R to {call.targets[0]:g}" in text.splitlines()


def test_render_quick_omits_the_r_line_for_a_flat_call(oil):
    flat = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(
        update={"side": "flat", "leverage": None})})
    text = render.render_markdown(flat, oil.analytics, mode="quick")
    assert not any(line.startswith("Pays ") for line in text.splitlines())


def test_render_quick_states_the_exit_and_the_instrument(oil):
    """The invalidation is the one line that keeps a leveraged trade alive;
    quick mode must never drop it. The coin lets an agent act on the call."""
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    assert f"Out if: {oil.thesis.call.invalidation}" in text.splitlines()
    assert oil.thesis.call.instrument in text


def test_render_quick_leads_with_the_one_liner(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    assert text.splitlines()[0] == oil.thesis.call.one_liner


def test_render_quick_shows_leverage_and_verdict(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    assert "5x" in text and "HOLD" in text


def test_render_quick_shows_the_worst_case_scenario(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    worst = min(oil.thesis.scenarios, key=lambda s: s.move_lo_pct)
    assert worst.description in text


@pytest.fixture
def frozen_at_fixture_time(monkeypatch, oil):
    """Pin render's clock to when the fixture thesis was written. The fixture's
    catalysts are fixed dates, so on the real clock "earliest upcoming" drifts
    as each one passes (this test went red on 2026-10-21 when c1 and c2 had)."""
    pinned = oil.thesis.created_at

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return pinned if tz is None else pinned.astimezone(tz)

    monkeypatch.setattr(render, "datetime", _Clock)


@pytest.mark.usefixtures("frozen_at_fixture_time")
def test_render_quick_shows_the_earliest_upcoming_catalyst(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    assert "API crude inventories" in text  # c1, the earliest of the three


def test_render_quick_ends_with_a_decision_line(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="quick")
    assert text.splitlines()[-1].startswith("Decision: ")


def test_render_quick_handles_no_scenarios_or_catalysts(oil):
    t = oil.thesis.model_copy(update={"scenarios": [], "catalysts": []})
    text = render.render_markdown(t, oil.analytics, mode="quick")
    assert "Biggest risk: n/a" in text
    assert "Next catalyst: none scheduled" in text


# -- full render --------------------------------------------------------------


def test_render_full_includes_every_required_section(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    for heading in (
        "Market snapshot", "Bull / bear", "Base case", "Leverage ladder",
        "Full send", "Catalyst calendar", "What changes my mind", "Sources",
    ):
        assert heading in text


def test_render_full_market_table_has_a_row_per_instrument(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    assert "| WTI |" in text and "| Brent |" in text


def test_render_full_ladder_table_has_a_row_per_rung_with_verdict(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    assert "| 5x |" in text and "HOLD" in text
    assert "| 20x |" in text and "AVOID" in text


def test_render_full_links_bull_bear_points_to_resolved_sources(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    assert "[CNBC](https://www.cnbc.com" in text


def test_render_full_catalyst_calendar_is_sorted_and_in_utc(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    calendar = text[text.index("Catalyst calendar"):]
    idx_c1 = calendar.index("API crude inventories")
    idx_c2 = calendar.index("EIA Weekly")
    idx_c3 = calendar.index("OPEC+ eight-country monthly meeting")
    assert idx_c1 < idx_c2 < idx_c3
    assert "UTC" in calendar


def test_render_full_what_changes_my_mind_lists_every_entry(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    for line in oil.thesis.what_changes_my_mind:
        assert line in text


def test_render_full_omits_empty_sections_gracefully(oil):
    t = oil.thesis.model_copy(update={"bull": [], "bear": [], "what_changes_my_mind": [], "sources": []})
    text = render.render_markdown(t, oil.analytics, mode="full")
    assert "Bull / bear" not in text
    assert "What changes my mind" not in text
    assert "### Sources" not in text


# -- shared ---------------------------------------------------------------


def test_render_markdown_rejects_an_unknown_mode(oil):
    with pytest.raises(ValueError, match="quick"):
        render.render_markdown(oil.thesis, oil.analytics, mode="essay")


@pytest.mark.parametrize("mode", ["quick", "full"])
def test_render_markdown_never_contains_an_em_dash(oil, mode):
    text = render.render_markdown(oil.thesis, oil.analytics, mode=mode)
    assert "\u2014" not in text


def test_next_catalyst_falls_back_to_earliest_when_all_are_in_the_past(oil):
    past = Catalyst(id="cold", kind="x", title="Old catalyst", at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                     importance=1, source="x")
    t = oil.thesis.model_copy(update={"catalysts": [past]})
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    cat = render._next_catalyst(t, now=now)
    assert cat.id == "cold"


def test_next_catalyst_prefers_the_nearest_future_one(oil):
    soon = Catalyst(id="soon", kind="x", title="Soon", at=datetime(2026, 10, 18, tzinfo=timezone.utc),
                     importance=1, source="x")
    later = Catalyst(id="later", kind="x", title="Later", at=datetime(2026, 10, 25, tzinfo=timezone.utc),
                      importance=1, source="x")
    t = oil.thesis.model_copy(update={"catalysts": [later, soon]})
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    cat = render._next_catalyst(t, now=now)
    assert cat.id == "soon"


def test_leverage_pick_returns_none_when_call_is_flat(oil):
    t = oil.thesis.model_copy(update={"call": oil.thesis.call.model_copy(update={"leverage": None})})
    assert render._leverage_pick(t) is None


def test_leverage_pick_matches_the_calls_leverage(oil):
    lv = render._leverage_pick(oil.thesis)
    assert lv.leverage == 5.0 and lv.verdict == "hold"


def test_render_full_send_table_has_a_row_per_rung(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    full_send = text[text.index("Full send"):]
    assert "| 5x |" in full_send and "| 10x |" in full_send and "| 20x |" in full_send


# ── ladder rows carry their side (regression 2026-10-18) ─────────────────────
# The ladder has a long and a short rung per leverage. The table had no Side
# column and pasted the thesis's reason onto BOTH rows, so the short 5x row
# of a long call read "Chosen. Liq 81.12 ..." next to the short's own 115.74.

def _ladder_section(text: str) -> str:
    return text[text.index("### Leverage ladder"):text.index("### Full send")]


def test_ladder_table_has_a_side_column_and_call_side_first(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    rows = [ln for ln in _ladder_section(text).splitlines() if ln.startswith("| ") and "---" not in ln]
    assert rows[0].startswith("| Side | Lev |")
    body = rows[1:]
    call_side = oil.thesis.call.side
    sides = [r.split("|")[1].strip().strip("*") for r in body]
    first_other = next((i for i, sd in enumerate(sides) if sd != call_side), len(sides))
    assert all(sd == call_side for sd in sides[:first_other])
    assert all(sd != call_side for sd in sides[first_other:])


def test_ladder_reasons_never_sit_in_the_other_sides_rows(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    section = _ladder_section(text)
    table = [ln for ln in section.splitlines() if ln.startswith("| ")]
    for lv in oil.thesis.leverage_view:
        assert all(lv.reason not in row for row in table)
        assert lv.reason in section            # still shown, under the table
    assert f"Why, for the {oil.thesis.call.side} call" in section


def test_full_send_table_has_a_side_column(oil):
    text = render.render_markdown(oil.thesis, oil.analytics, mode="full")
    full_send = text[text.index("### Full send"):]
    assert "| Side | Lev | P10 |" in full_send
    assert f"| **{oil.thesis.call.side}** | 5x |" in full_send   # the call's side, bolded
