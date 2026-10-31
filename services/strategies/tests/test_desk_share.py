"""services/strategies/desk_share.py: each active desk's slice of equity
(operator, 2026-10-31: "oil 95% silver 5%")."""
from __future__ import annotations

import pytest

from services.strategies import desk_share as S

LIVE = {"active_desks": ["oil", "metals"], "desk_equity_share": {"oil": 0.95, "metals": 0.05}}


def test_oil_and_silver_get_their_own_slice():
    assert S.share_for_coin("xyz:BRENTOIL", LIVE) == ("oil", 0.95)
    assert S.share_for_coin("xyz:CL", LIVE) == ("oil", 0.95)
    assert S.share_for_coin("xyz:SILVER", LIVE) == ("metals", 0.05)
    assert S.slots(LIVE, profile_slots=1) == 2


def test_no_shares_saved_keeps_the_whole_equity_rule():
    cfg = {"active_desks": ["oil"]}
    assert S.share_for_coin("xyz:BRENTOIL", cfg) == ("oil", 1.0)
    assert S.slots(cfg, profile_slots=1) == 1


@pytest.mark.parametrize("shares, msg", [
    ({"oil": 0.95, "metals": 0.10}, "adds up to 1.05"),
    ({"oil": 0.95}, "'metals' has no desk_equity_share"),
    ({"oil": 0.95, "metals": 0}, "outside (0, 1]"),
    ({"oil": "lots", "metals": 0.05}, "not a number"),
])
def test_bad_shares_fail_closed(shares, msg):
    with pytest.raises(S.DeskShareError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        S.share_for_coin("xyz:BRENTOIL", {"active_desks": ["oil", "metals"], "desk_equity_share": shares})


def test_a_coin_on_no_active_desk_has_no_share():
    with pytest.raises(S.DeskShareError, match="no active desk"):
        S.share_for_coin("BTC", LIVE)


def test_a_share_for_an_inactive_desk_is_ignored():
    cfg = {"active_desks": ["oil"], "desk_equity_share": {"oil": 0.95, "gas": 0.5}}
    assert S.desk_shares(cfg) == {"oil": 0.95}
