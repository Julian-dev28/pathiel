"""Unit tests for services/thesis/desk_lookup.py's is_fresh_extreme
(2026-10-31, price-discovery rule): an all-time high/low set within
PRICE_DISCOVERY_WINDOW_DAYS of ``as_of`` is FRESH (the venue is still in
price discovery on that side, not a real ceiling/floor); older than that
window it is STALE (today's existing hard-cap behaviour)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.strategies.models import PriceExtreme
from services.thesis import desk_lookup

AS_OF = datetime(2026, 10, 30, 16, 0, tzinfo=timezone.utc)


def test_none_extreme_is_never_fresh():
    assert desk_lookup.is_fresh_extreme(None, AS_OF) is False


def test_extreme_set_one_day_ago_is_fresh():
    extreme = PriceExtreme(price=51.856, at=AS_OF - timedelta(days=1))
    assert desk_lookup.is_fresh_extreme(extreme, AS_OF) is True


def test_extreme_set_today_is_fresh():
    extreme = PriceExtreme(price=51.856, at=AS_OF)
    assert desk_lookup.is_fresh_extreme(extreme, AS_OF) is True


def test_extreme_set_exactly_at_the_window_boundary_is_stale():
    """< PRICE_DISCOVERY_WINDOW_DAYS is fresh; exactly at the window is
    stale (today's existing cap behaviour), so the boundary leans toward
    the hard cap, not the exception."""
    extreme = PriceExtreme(price=51.856, at=AS_OF - timedelta(days=desk_lookup.PRICE_DISCOVERY_WINDOW_DAYS))
    assert desk_lookup.is_fresh_extreme(extreme, AS_OF) is False


def test_extreme_set_one_day_past_the_window_is_stale():
    extreme = PriceExtreme(
        price=51.856, at=AS_OF - timedelta(days=desk_lookup.PRICE_DISCOVERY_WINDOW_DAYS + 1))
    assert desk_lookup.is_fresh_extreme(extreme, AS_OF) is False


def test_brent_ath_eight_months_old_is_stale():
    """The exact Brent shape from the 2026-10-30 live incident: all-time
    high set 2026-03-09, as_of 2026-10-30 - about 8 months old, must stay
    STALE (the hard cap the operator explicitly wants for oil)."""
    extreme = PriceExtreme(price=117.58, at=datetime(2026, 3, 9, tzinfo=timezone.utc))
    assert desk_lookup.is_fresh_extreme(extreme, AS_OF) is False
