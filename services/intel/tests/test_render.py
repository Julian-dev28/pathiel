"""Unit tests for services/intel/render.py: token budget enforcement,
section ordering, and the never-truncated sources_failed line."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.strategies.models import Catalyst, Headline, IntelPacket, PolicyItem
from services.intel.render import estimate_tokens, render_compact

T0 = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)


def _packet_with_n_headlines(n: int, sources_failed=None) -> IntelPacket:
    headlines = [
        Headline(id=f"h{i}", title=f"Headline number {i} about oil markets and crude prices today",
                  url=f"https://example.com/{i}", source="Reuters",
                  published_at=T0 - timedelta(hours=i), stance="bull",
                  relevance=round(1.0 / (i + 1), 6))
        for i in range(n)
    ]
    return IntelPacket(desk_id="oil", as_of=T0, headlines=headlines,
                        sources_failed=sources_failed or [])


def test_estimate_tokens_is_chars_over_four():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("a") == 1  # floor of 1, never zero for non-empty text


def test_render_includes_header_and_ids():
    packet = _packet_with_n_headlines(3)
    text = render_compact(packet, max_tokens=2000)
    assert "desk=oil" in text
    assert "[h0]" in text and "[h1]" in text and "[h2]" in text


def test_render_respects_token_budget_when_content_overflows():
    packet = _packet_with_n_headlines(500)  # far more than any small budget fits
    for max_tokens in (50, 200, 1000):
        text = render_compact(packet, max_tokens)
        assert len(text) <= max_tokens * 4, f"over budget at max_tokens={max_tokens}"


def test_render_keeps_highest_relevance_headlines_under_tight_budget():
    packet = _packet_with_n_headlines(100)
    text = render_compact(packet, max_tokens=120)  # 480 chars, room for only a few lines
    assert "[h0]" in text  # highest relevance (1.0) must survive
    assert "[h99]" not in text  # lowest relevance (0.01) must not, under this budget


def test_render_sources_failed_never_dropped_even_at_tiny_budget():
    packet = _packet_with_n_headlines(200, sources_failed=["news:gdelt:timeout"])
    text = render_compact(packet, max_tokens=10)  # 40 chars — brutal budget
    assert "SOURCES_FAILED" in text
    assert "news:gdelt:timeout" in text


def test_render_sources_failed_none_is_explicit_not_blank():
    packet = _packet_with_n_headlines(1, sources_failed=[])
    text = render_compact(packet, max_tokens=500)
    assert "SOURCES_FAILED: none" in text


def test_render_empty_packet_still_renders_header():
    packet = IntelPacket(desk_id="oil", as_of=T0)
    text = render_compact(packet, max_tokens=500)
    assert "desk=oil" in text
    assert "SOURCES_FAILED: none" in text


def test_render_section_order_and_grouping():
    packet = IntelPacket(
        desk_id="oil", as_of=T0,
        headlines=[Headline(id="h1", title="Oil rises on Hormuz attack", url="u",
                             source="Reuters", relevance=0.9)],
        policy=[PolicyItem(id="p1", title="SPR release announced", url="u", body="DOE",
                            status="announced")],
        catalysts=[Catalyst(id="c1", kind="eia_weekly", title="EIA report",
                             at=T0 + timedelta(days=1), importance=3, source="EIA")],
    )
    text = render_compact(packet, max_tokens=2000)
    assert text.index("HEADLINES:") < text.index("POLICY:") < text.index("CATALYSTS:")


def test_render_skips_empty_sections_entirely():
    packet = IntelPacket(desk_id="oil", as_of=T0,
                          headlines=[Headline(id="h1", title="x", url="u", source="s", relevance=0.5)])
    text = render_compact(packet, max_tokens=2000)
    assert "POLICY:" not in text
    assert "CATALYSTS:" not in text
    assert "PREDICTION MARKETS:" not in text
    assert "POSITIONING:" not in text


def test_render_catalysts_prioritized_over_low_relevance_headlines():
    """Catalysts carry a higher base priority than headlines (see
    render.py's _catalyst_lines base of 0.80+) — under a budget that
    cannot fit everything, a high-importance catalyst should survive
    ahead of a middling headline."""
    packet = IntelPacket(
        desk_id="oil", as_of=T0,
        headlines=[Headline(id=f"h{i}", title=f"Minor oil story number {i} with some filler text",
                             url="u", source="Unknown", relevance=0.3) for i in range(30)],
        catalysts=[Catalyst(id="c1", kind="eia_weekly", title="EIA Weekly Petroleum Status Report",
                             at=T0 + timedelta(days=1), importance=3, source="EIA")],
    )
    text = render_compact(packet, max_tokens=90)
    assert "[c1]" in text
