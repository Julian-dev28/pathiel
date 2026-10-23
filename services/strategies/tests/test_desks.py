"""Every registered desk spec validates, and the registry's own invariants
(id<->module match, >=1 primary, positive leverage rungs) hold. Also checks
the specific desk-composition requirements from the build brief: oil has
CL+BRENTOIL, btc is BTC-only, top5 has exactly 5, ai covers the named
candidates, metals is gold+silver only, indices includes xyz:XYZ100."""
from __future__ import annotations

import pytest

from services.strategies import desks


def test_registry_lists_all_seven_desks():
    assert set(desks.list_desk_ids()) == {"oil", "gas", "btc", "top5", "ai", "metals", "indices"}


@pytest.mark.parametrize("desk_id", ["oil", "gas", "btc", "top5", "ai", "metals", "indices"])
def test_every_desk_spec_validates(desk_id):
    spec = desks.get_spec(desk_id)
    assert spec is not None
    assert spec.id == desk_id
    assert any(i.role == "primary" for i in spec.instruments)
    assert all(lev > 0 for lev in spec.leverage_ladder)
    assert spec.horizon_days > 0
    # every coin referenced by a pair must also be a listed instrument
    coins = {i.coin for i in spec.instruments}
    for pair in spec.pairs:
        assert pair.long in coins
        assert pair.short in coins


def test_get_spec_unknown_returns_none():
    assert desks.get_spec("not-a-real-desk") is None


def test_require_spec_unknown_raises():
    with pytest.raises(desks.UnknownDeskError):
        desks.require_spec("not-a-real-desk")


def test_list_specs_matches_list_desk_ids():
    assert [s.id for s in desks.list_specs()] == desks.list_desk_ids()


def test_oil_desk_prices_off_brent_with_wti_as_the_spread_leg():
    """Julien, 2026-10-19: "for oil desk use the brent oil price". Brent is
    the one primary (the call, ladder, forward stats and sizing are priced
    off it) and is listed first; WTI stays as the spread's other leg."""
    spec = desks.require_spec("oil")
    coins = {i.coin: i.role for i in spec.instruments}
    assert coins["xyz:BRENTOIL"] == "primary"
    assert coins["xyz:CL"] == "secondary"
    assert [i.coin for i in spec.instruments if i.role == "primary"] == ["xyz:BRENTOIL"]
    assert spec.instruments[0].coin == "xyz:BRENTOIL"
    assert any(p.long == "xyz:BRENTOIL" and p.short == "xyz:CL" for p in spec.pairs)


def test_oil_desk_keeps_iran_in_the_gdelt_query():
    """GDELT reads only the first 6 news queries; the Brent query was added
    last so the Iran exports query stays inside them."""
    from services.intel.sources import news
    spec = desks.require_spec("oil")
    assert "Iran oil exports" in spec.news_queries[:6]
    assert "Brent crude" in spec.news_queries[:8]
    assert '"Iran oil exports"' in news._gdelt_query(spec)


def test_gas_desk_is_natgas_only_no_secondary_no_pair():
    """Build brief: primary xyz:NATGAS, no secondary, no pair."""
    spec = desks.require_spec("gas")
    assert [i.coin for i in spec.instruments] == ["xyz:NATGAS"]
    assert spec.instruments[0].role == "primary"
    assert spec.pairs == []


def test_gas_desk_leverage_ladder_and_horizon():
    spec = desks.require_spec("gas")
    assert spec.leverage_ladder == [5, 10]
    assert spec.horizon_days == 7


def test_gas_desk_catalyst_kinds_match_the_build_brief():
    spec = desks.require_spec("gas")
    assert spec.catalyst_kinds == ["eia_gas_storage", "fomc", "cftc_cot"]


def test_gas_desk_news_and_policy_cover_storage_weather_and_lng():
    """Build brief: storage report vs expectations, weather (heating/cooling
    degree days), LNG export flows must show up in the desk's own queries,
    not just be implied."""
    spec = desks.require_spec("gas")
    hay = " ".join(spec.news_queries).lower()
    assert "eia" in hay and "storage" in hay
    assert "heating degree days" in hay
    assert "cooling degree days" in hay
    assert "lng" in hay
    policy_hay = " ".join(spec.policy_topics).lower()
    assert "lng export" in policy_hay
    assert "ferc" in policy_hay
    assert "doe export" in policy_hay


def test_gas_desk_lessons_cover_the_named_concepts():
    spec = desks.require_spec("gas")
    lessons_hay = " ".join(spec.lessons).lower()
    for term in ("storage surprise", "degree days", "henry hub", "lng feedgas", "shoulder season"):
        assert term in lessons_hay


def test_btc_desk_is_btc_only():
    spec = desks.require_spec("btc")
    assert [i.coin for i in spec.instruments] == ["BTC"]


def test_top5_desk_has_exactly_five_instruments():
    spec = desks.require_spec("top5")
    assert len(spec.instruments) == 5
    assert all(i.role == "primary" for i in spec.instruments)
    assert "BTC" in {i.coin for i in spec.instruments}


def test_ai_desk_covers_named_candidates():
    spec = desks.require_spec("ai")
    coins = {i.coin for i in spec.instruments}
    for named in ("xyz:NVDA", "xyz:AMD", "xyz:MSFT", "xyz:GOOGL", "xyz:META", "xyz:TSM", "xyz:PLTR"):
        assert named in coins, f"{named} from the build brief's named list is missing"


def test_metals_desk_is_gold_and_silver_only():
    spec = desks.require_spec("metals")
    assert {i.coin for i in spec.instruments} == {"xyz:GOLD", "xyz:SILVER"}


def test_indices_desk_includes_xyz100():
    spec = desks.require_spec("indices")
    assert "xyz:XYZ100" in {i.coin for i in spec.instruments}


def test_spec_builders_are_pure_and_repeatable():
    """Calling build_spec() twice gives equal, independently-valid specs —
    no hidden network call or mutable shared state leaking between calls."""
    from services.strategies.desks import oil
    a, b = oil.build_spec(), oil.build_spec()
    assert a == b
    assert a is not b
