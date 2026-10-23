"""Unit tests for services/intel/render.py: token budget enforcement,
section ordering, and the never-truncated sources_failed line."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.strategies.models import Catalyst, DataPoint, Headline, IntelPacket, PolicyItem
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


# ── prediction-market compaction (2026-10-17 token audit) ────────────────────

def _ladder_packet(probs, vol=2_000.0, closes="2026-10-30T21:00:00+00:00", url="https://kalshi.com/markets/kxbrentmon"):
    from datetime import datetime as _dt
    from contracts.strategies.models import IntelPacket, MarketOdds
    odds = [MarketOdds(id=f"k{i}", venue="kalshi",
                       question=f"Will the brent crude oil close price be above {90 + 2 * i}.99 USD/Bbl on October 30, 2026?",
                       probability=p, volume_usd=vol, url=url, closes_at=_dt.fromisoformat(closes))
            for i, p in enumerate(probs)]
    return IntelPacket(desk_id="oil", as_of=_dt.fromisoformat("2026-10-17T02:10:00+00:00"), odds=odds)


def test_strike_ladder_collapses_to_one_line_with_only_the_uncertain_strikes():
    from services.intel.render import render_compact
    probs = [0.99, 0.99, 0.98, 0.9, 0.7, 0.5, 0.3, 0.1, 0.02, 0.01]
    text = render_compact(_ladder_packet(probs), 4000)
    lines = [ln for ln in text.splitlines() if "ladder" in ln]
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("[k0]")               # cites a real id from the packet
    assert "P(>94.99)=0.98" in line              # bracket below the uncertain zone
    assert "P(>106.99)=0.02" in line             # bracket above it
    assert "P(>90.99)" not in line               # near-certain tail dropped
    assert "10 strikes" in line


def test_thin_or_non_monotonic_ladders_are_dropped():
    from services.intel.render import render_compact
    thin = render_compact(_ladder_packet([0.9, 0.5, 0.1], vol=100.0), 4000)
    assert "ladder" not in thin
    broken = render_compact(_ladder_packet([0.9, 0.7, 0.88, 0.2]), 4000)
    assert "ladder" not in broken


def test_plain_markets_keep_the_two_most_traded_per_event():
    from datetime import datetime as _dt
    from contracts.strategies.models import IntelPacket, MarketOdds
    odds = [MarketOdds(id=f"p{i}", venue="polymarket", question=f"Will clause {i} be in a US-Iran deal?",
                       probability=0.1, volume_usd=1000.0 * i, url="https://polymarket.com/event/us-iran-deal")
            for i in range(1, 7)]
    pk = IntelPacket(desk_id="oil", as_of=_dt.fromisoformat("2026-10-17T02:10:00+00:00"), odds=odds)
    text = render_compact(pk, 4000)
    kept = [ln for ln in text.splitlines() if ln.startswith("[p")]
    assert [ln.split("]")[0] for ln in kept] == ["[p6", "[p5"]


# ── HARD DATA block (DataPoint, 1.2.0) ──────────────────────────────

def _dp(dp_id="dp1", value=-8.6, prior=-2.1, change=-6.5, unit="mm_bbl",
        note="surprise -6.5 vs 4wk avg -1.7", relevance=0.9) -> DataPoint:
    return DataPoint(id=dp_id, source="eia", label="EIA crude stocks weekly change",
                     value=value, prior=prior, change=change, unit=unit, as_of=T0, note=note,
                     relevance=relevance)


def test_hard_data_renders_first_ahead_of_headlines_and_catalysts():
    packet = IntelPacket(
        desk_id="oil", as_of=T0, datapoints=[_dp()],
        headlines=[Headline(id="h1", title="Oil rises on Hormuz attack", url="u",
                             source="Reuters", relevance=0.9)],
        catalysts=[Catalyst(id="c1", kind="eia_weekly", title="EIA report",
                             at=T0 + timedelta(days=1), importance=3, source="EIA")],
    )
    text = render_compact(packet, max_tokens=2000)
    assert text.index("HARD DATA:") < text.index("HEADLINES:") < text.index("CATALYSTS:")


def test_hard_data_line_shows_value_prior_change_unit_and_note():
    packet = IntelPacket(desk_id="oil", as_of=T0, datapoints=[_dp()])
    text = render_compact(packet, max_tokens=2000)
    assert "[dp1]" in text
    assert "eia" in text
    assert "EIA crude stocks weekly change" in text
    assert "-8.6" in text and "mm_bbl" in text
    assert "prior -2.1" in text
    assert "chg -6.5" in text
    assert "surprise -6.5 vs 4wk avg -1.7" in text


def test_hard_data_handles_missing_prior_and_change_as_na():
    dp = DataPoint(id="dp2", source="cboe", label="VIX", value=19.03, unit="index_pts", as_of=T0)
    packet = IntelPacket(desk_id="oil", as_of=T0, datapoints=[dp])
    text = render_compact(packet, max_tokens=2000)
    assert "prior n/a" in text
    assert "chg n/a" in text


def test_hard_data_outranks_a_low_relevance_headline_under_a_tight_budget():
    """DataPoint relevance maps into the 0.75-0.95 band (see render.py's
    _datapoint_lines) specifically so a hard number survives a tight
    budget ahead of a middling headline — the operator wants hard numbers
    before the headline, not buried behind 30 of them."""
    packet = IntelPacket(
        desk_id="oil", as_of=T0, datapoints=[_dp(relevance=0.1)],  # low DataPoint relevance...
        headlines=[Headline(id=f"h{i}", title=f"Minor oil story number {i} with filler text",
                             url="u", source="Unknown", relevance=0.3) for i in range(30)],
    )
    text = render_compact(packet, max_tokens=90)
    assert "[dp1]" in text  # ...still beats a 0.3-relevance headline (0.75 floor vs 0.3)


def test_hard_data_section_cap_bounds_a_flooded_packet():
    from services.intel.render import SECTION_CAPS
    dps = [_dp(dp_id=f"dp{i}", relevance=i / 20.0) for i in range(20)]
    packet = IntelPacket(desk_id="oil", as_of=T0, datapoints=dps)
    text = render_compact(packet, max_tokens=100_000)
    kept = [ln for ln in text.splitlines() if ln.startswith("[dp")]
    assert len(kept) == SECTION_CAPS["HARD DATA"]


def test_hard_data_section_omitted_when_no_datapoints():
    packet = IntelPacket(desk_id="oil", as_of=T0,
                         headlines=[Headline(id="h1", title="x", url="u", source="s", relevance=0.5)])
    text = render_compact(packet, max_tokens=2000)
    assert "HARD DATA:" not in text


def test_section_caps_bound_a_flooded_packet():
    from datetime import datetime as _dt, timedelta as _td
    from contracts.strategies.models import Headline, IntelPacket
    from services.intel.render import SECTION_CAPS, render_compact
    now = _dt.fromisoformat("2026-10-17T02:10:00+00:00")
    hs = [Headline(id=f"h{i}", title=f"headline {i}", url=f"https://x/{i}", source="s",
                   published_at=now - _td(hours=i), relevance=min(1.0, i / 50))
          for i in range(50)]
    text = render_compact(IntelPacket(desk_id="oil", as_of=now, headlines=hs), 100_000)
    kept = [ln for ln in text.splitlines() if ln.startswith("[h")]
    assert len(kept) == SECTION_CAPS["HEADLINES"]
    assert all(int(ln[2:ln.index("]")]) >= 40 for ln in kept)   # the most relevant ones
