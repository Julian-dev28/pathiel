"""Online tests: real Hyperliquid reads, deselected by default per
pyproject's addopts. Run with:

    PATHIEL_STATE_DIR=$(mktemp -d) .venv/bin/python -m pytest -q -m online services/strategies

Validates the build brief's measurable outcome #1 directly: `brief oil`
builds a full DeskAnalytics from live HL data in under 10 seconds.
"""
from __future__ import annotations

import time

import pytest

from services.strategies import desks, facade
from services.strategies.analytics import build_analytics

pytestmark = pytest.mark.online


def test_build_analytics_for_oil_completes_under_10s_with_real_data():
    spec = desks.require_spec("oil")
    t0 = time.monotonic()
    result = build_analytics(spec)
    elapsed = time.monotonic() - t0

    assert elapsed < 10.0, f"oil analytics build took {elapsed:.1f}s, budget is 10s"
    assert len(result.instruments) >= 2          # at least CL + BRENTOIL came back
    cl = next((i for i in result.instruments if i.coin == "xyz:CL"), None)
    assert cl is not None and cl.mid > 0
    assert result.pairs, "Brent-WTI pair should resolve with live data"
    assert result.ladder, "primary instrument should produce ladder rungs"
    assert result.account.source in ("venue", "assumed")


def test_get_brief_oil_end_to_end_under_10s_with_real_data():
    t0 = time.monotonic()
    brief = facade.get_brief("oil")
    elapsed = time.monotonic() - t0

    assert elapsed < 10.0, f"get_brief('oil') took {elapsed:.1f}s, budget is 10s"
    assert brief.analytics is not None
    assert brief.spec.id == "oil"


def test_list_cards_real_desks_all_validate():
    """Every registered desk's spec should resolve to a real, validated
    DeskCard shape even cold (no refresh has run) — exercised with the real
    registry, not fakes, as a final sanity sweep."""
    cards = facade.list_cards()
    assert len(cards) == len(desks.list_desk_ids())
