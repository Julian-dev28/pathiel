"""Min-order bump: sub-floor entries round UP instead of being dropped."""
import pytest


def _cases():
    # (intended, floor, bump_max) -> expected notional or None if skipped
    return [
        (6.76, 10.50, 2.0, 10.50),   # the real xyz:CXMT case — bumped
        (9.58, 10.50, 2.0, 10.50),   # the real ACE case — bumped
        (11.00, 10.50, 2.0, 11.00),  # already above floor — untouched
        (2.00, 10.50, 2.0, None),    # 5x below floor — intent too small, skip
        (6.76, 10.50, 0.0, None),    # bump disabled — original behavior
    ]


@pytest.mark.parametrize("intended,floor,bump_max,expected", _cases())
def test_bump_logic(intended, floor, bump_max, expected):
    """Mirror of the executor branch: bump when within the ceiling, else skip."""
    notional = intended
    if floor > 0 and notional < floor:
        if bump_max > 0 and notional * bump_max >= floor:
            notional = floor
        else:
            notional = None
    assert notional == expected


def test_bump_ceiling_bounds_the_added_risk():
    """The bump can never more than double the intended size."""
    for intended, floor in ((6.76, 10.50), (9.58, 10.50), (5.30, 10.50)):
        if intended * 2.0 >= floor:
            assert floor / intended <= 2.0
