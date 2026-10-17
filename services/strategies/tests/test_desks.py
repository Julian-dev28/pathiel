"""Every registered desk spec validates, and the registry's own invariants
(id<->module match, >=1 primary, positive leverage rungs) hold. Also checks
the specific desk-composition requirements from the build brief: oil has
CL+BRENTOIL, btc is BTC-only, top5 has exactly 5, ai covers the named
candidates, metals is gold+silver only, indices includes xyz:XYZ100."""
from __future__ import annotations

import pytest

from services.strategies import desks


def test_registry_lists_all_six_desks():
    assert set(desks.list_desk_ids()) == {"oil", "btc", "top5", "ai", "metals", "indices"}


@pytest.mark.parametrize("desk_id", ["oil", "btc", "top5", "ai", "metals", "indices"])
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


def test_oil_desk_has_wti_and_brent():
    spec = desks.require_spec("oil")
    coins = {i.coin: i.role for i in spec.instruments}
    assert coins["xyz:CL"] == "primary"
    assert coins["xyz:BRENTOIL"] == "secondary"
    assert any(p.long == "xyz:BRENTOIL" and p.short == "xyz:CL" for p in spec.pairs)


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
