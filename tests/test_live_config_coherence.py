"""The live config must survive its own worst case, at the FLOOR equity.

WHY THE FLOOR AND NOT TODAY'S BALANCE
A risk config sized against the current balance stops being true the moment the
account loses money - which is exactly when it is load-bearing. Every bound here
is therefore checked at `min_tradable_equity_usd`, the lowest equity at which the
system still trades. If it holds there it holds everywhere above.

THE BOUND THAT MATTERS
xyz-equity shorts are not independent bets. W-MATH2/W-MATH3 measured a mean
pairwise correlation of 0.83 (~1.28 effective bets held four ways), and on
07-21 seven of them stopped TOGETHER for -$8.59. So the book must be sized as
if every open position stops in the same move, because it has already done that
once. If that simultaneous stop costs more than the daily kill, the kill fires
AFTER the loss it exists to prevent, and it is decoration.

This test is what makes `max_concurrent` unraisable by feel. On 2026-09-06 the
operator asked for "as many trades as possible" with the xyz name cap refusing
entries; the answer was 3, and 3 is not an opinion - it is where this arithmetic
runs out. Raising it to 4 breaks this test rather than quietly breaking the
account.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

CFG = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
BOOK = "xs_reversal"


def _floor() -> float:
    return float(CFG["min_tradable_equity_usd"])


def _kill_at_floor() -> float:
    """The kill is a PERCENT and the percent wins over the USD figure in
    `effective_daily_loss_limit`, so the USD key is not the bound to test."""
    return abs(float(CFG["max_daily_loss_pct"])) * _floor()


def _per_position_stop_loss() -> float:
    b = CFG[BOOK]
    return float(b["notional_usd"]) * float(b["stop_pct"]) / 100.0


def test_all_slots_stopping_together_stays_inside_the_kill():
    slots = int(CFG["max_concurrent"])
    loss = slots * _per_position_stop_loss()
    kill = _kill_at_floor()
    assert loss < kill, (
        f"{slots} correlated stops cost ${loss:.2f} against a ${kill:.2f} kill at the "
        f"${_floor():.0f} floor. The account loses more than the kill switch exists to "
        f"prevent before it can fire. Lower max_concurrent, or lower notional/stop."
    )


def test_one_more_slot_than_configured_would_breach_the_kill():
    """Proves max_concurrent sits AT the ceiling, not arbitrarily below it.

    Without this, the previous assertion passes just as happily at 1 slot and
    the config could be needlessly throttled while looking 'safe'.
    """
    slots = int(CFG["max_concurrent"])
    loss = (slots + 1) * _per_position_stop_loss()
    assert loss >= _kill_at_floor(), (
        f"max_concurrent={slots} leaves room: {slots + 1} correlated stops cost "
        f"${loss:.2f}, still under the ${_kill_at_floor():.2f} kill. The book is "
        f"leaving trades on the table - raise max_concurrent."
    )


def test_name_cap_does_not_refuse_trades_the_slots_allow():
    """The live symptom: `xyz-short name cap reached (2/2)` with slots free.

    A name cap BELOW max_concurrent refuses entries the concurrency budget has
    already approved, which reads as a broken book rather than a risk decision.
    """
    names, slots = int(CFG["max_xyz_short_names"]), int(CFG["max_concurrent"])
    assert names >= slots, (
        f"max_xyz_short_names={names} < max_concurrent={slots}: the name cap refuses "
        f"xyz shorts while slots sit empty."
    )


def test_notional_cap_admits_a_full_book_at_the_floor():
    full = int(CFG["max_concurrent"]) * float(CFG[BOOK]["notional_usd"])
    cap = float(CFG["max_xyz_short_notional_pct"]) * _floor()
    assert full <= cap, (
        f"a full book is ${full:.2f} but the notional cap is ${cap:.2f} at the "
        f"${_floor():.0f} floor: the last slot can never be filled as equity falls."
    )


def test_notional_cap_still_refuses_one_position_too_many():
    """Second, independent control. It must agree with max_concurrent at the
    floor - two controls that disagree mean one of them is dead text."""
    over = (int(CFG["max_concurrent"]) + 1) * float(CFG[BOOK]["notional_usd"])
    cap = float(CFG["max_xyz_short_notional_pct"]) * _floor()
    assert over > cap, (
        f"notional cap ${cap:.2f} admits {int(CFG['max_concurrent']) + 1} positions "
        f"(${over:.2f}); it is not backing up max_concurrent."
    )


def test_position_size_clears_the_exchange_minimum():
    """Ties the ceiling to a real constraint: the only way past `max_concurrent`
    is smaller positions, and this is the wall that stops that."""
    from pathia.client.exchange import MIN_ORDER_USD
    assert float(CFG[BOOK]["notional_usd"]) >= MIN_ORDER_USD


@pytest.mark.parametrize("key,expected", [("enable_hip3", True), ("enable_crypto", False)])
def test_hip3_only_is_still_the_declared_mode(key, expected):
    assert CFG[key] is expected


def test_allowlist_is_exactly_xyz():
    assert CFG["hip3_dex_allowlist"] == ["xyz"], (
        "io/para/mkts and the other HIP-3 dexes are muted by operator decision; "
        "see test_hip3_dex_allowlist.py for what happens when this is not enforced."
    )
