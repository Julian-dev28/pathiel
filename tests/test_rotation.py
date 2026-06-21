"""Tests for capital-rotation decision logic (Phase-2, Phase-1 lever)."""

from hermes_trader.agents.rotation import decide_rotation

BASE = dict(
    min_candidate_composite=40.0,
    min_hold_minutes=30.0,
    protect_winner_roe_pct=3.0,
)


def _pos(coin, roe, age):
    return {"coin": coin, "roe_pct": roe, "age_minutes": age}


def test_rotates_weakest_nonwinner_when_capital_blocked_and_strong_candidate():
    d = decide_rotation(
        candidate_coin="ZRO", candidate_composite=55,
        blocked_reasons=["total notional $1500 would exceed 1000% of equity ($1490)"],
        open_positions=[_pos("AAA", -5.0, 60), _pos("BBB", 1.0, 90), _pos("CCC", 8.0, 120)],
        **BASE,
    )
    assert d.should_rotate
    assert d.evict_coin == "AAA"  # lowest ROE among non-winners


def test_protects_winners_never_evicted():
    # All open positions are strong winners (>= protect threshold) -> no rotation.
    d = decide_rotation(
        candidate_coin="ZRO", candidate_composite=80,
        blocked_reasons=["max positions reached (4/4)"],
        open_positions=[_pos("AAA", 12.0, 200), _pos("BBB", 5.0, 200)],
        **BASE,
    )
    assert not d.should_rotate
    assert "no eligible evictee" in d.reason


def test_respects_min_hold_anti_churn():
    # Weak position but too young to evict.
    d = decide_rotation(
        candidate_coin="ZRO", candidate_composite=80,
        blocked_reasons=["total notional would exceed 300% of equity"],
        open_positions=[_pos("AAA", -9.0, 5)],
        **BASE,
    )
    assert not d.should_rotate


def test_weak_candidate_does_not_justify_rotation():
    d = decide_rotation(
        candidate_coin="ZRO", candidate_composite=22,
        blocked_reasons=["total notional would exceed 300% of equity"],
        open_positions=[_pos("AAA", -9.0, 120)],
        **BASE,
    )
    assert not d.should_rotate
    assert "not worth a rotation" in d.reason


def test_non_capital_block_is_not_rotatable():
    # A real risk veto (regime / cooldown) must NOT be bypassed by rotation.
    for reason in (
        ["counter-regime long vs down trend"],
        ["cooldown active (33min remaining)"],
        ["market 24h volume $0.77M below floor $0.80M"],
        ["total notional would exceed 300% of equity", "cooldown active (10min)"],  # mixed
    ):
        d = decide_rotation(
            candidate_coin="ZRO", candidate_composite=90,
            blocked_reasons=reason,
            open_positions=[_pos("AAA", -9.0, 120)],
            **BASE,
        )
        assert not d.should_rotate, f"should not rotate on {reason}"


def test_empty_blocked_reasons_no_rotation():
    d = decide_rotation(
        candidate_coin="ZRO", candidate_composite=90, blocked_reasons=[],
        open_positions=[_pos("AAA", -9.0, 120)], **BASE,
    )
    assert not d.should_rotate


def test_never_evicts_same_coin():
    d = decide_rotation(
        candidate_coin="AAA", candidate_composite=90,
        blocked_reasons=["max positions reached (4/4)"],
        open_positions=[_pos("AAA", -9.0, 120)], **BASE,
    )
    assert not d.should_rotate
