"""The live config must survive its own worst case, at the FLOOR equity.

WHY THE FLOOR AND NOT TODAY'S BALANCE
A risk config sized against the current balance stops being true the moment the
account loses money - which is exactly when it is load-bearing. Every bound here
is checked at `min_tradable_equity_usd`, the lowest equity at which the system
still trades. If it holds there it holds everywhere above.

WHAT CHANGED ON 2026-09-06, AND WHY THESE ASSERTIONS ARE DIFFERENT NOW
The operator asked to deploy 98% of the portfolio across the 3 slots. That is a
deliberate move from "the daily kill bounds the worst case" to "the STOP bounds
the worst case", and the two cannot both be true:

    3 slots x 32.667% margin each  = 98% of equity committed
    x 3 leverage                   = 2.94x equity in notional
    x 15% stop                     = 44.1% of equity if all three stop together

44.1% is far outside the 20% daily kill. The kill halts NEW entries; it does not
close open ones, so it cannot bound a correlated stop-out. Pretending otherwise
is how a config looks safe and is not.

So the invariant is no longer "worst case < kill". It is "worst case equals a
number the operator DECLARED", held in `max_correlated_drawdown_pct` and checked
here against the arithmetic. The risk is not smaller. It is written down, and it
cannot drift without failing a test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / ".agent-config.json").read_text())
BOOK = "xs_reversal"


def _floor() -> float:
    return float(CFG["min_tradable_equity_usd"])


def _params():
    """Resolved, not indexed. Sizing lives at the top level unless a book
    overrides it (pathia/agents/book_params.py), so indexing the book dict
    would read a key that is deliberately absent."""
    from pathia.agents.book_params import book_params
    return book_params(CFG, BOOK)


def _frac() -> float:
    return float(CFG["strategy_book_equity_frac"])


def _slots() -> int:
    return int(CFG["max_concurrent"])


def _notional_at(equity: float) -> float:
    """Per-position notional. Equity-FRACTION sizing, so this scales with the
    account instead of drifting as a fixed dollar figure would."""
    return equity * _frac() * float(_params().leverage)


# ----------------------------------------------------------------- deployment
def test_slots_and_fraction_deploy_everything_the_margin_floor_allows():
    """The operator asked for 98% deployed. 92% is what the account can take.

    `min_available_margin_pct` is 8% because of the 2026-07-22 equity bleed:
    at a 1% floor the xyz dex ran to 97% utilization and every adverse tick
    FORCE-LIQUIDATED positions at ~5%, BEFORE the backup stop could fire - the
    book's own stop never got to act, and each liquidation drained the shared
    margin into the next one. 98% deployment sits at that same utilization, on
    that same dex.

    So deployment is pinned to exactly what the floor leaves, no more and no
    less. Idle margin is wasted capital; margin past the floor is the cascade.
    The gap between the 98% asked for and the 92% taken is $2.08 at a $34.67
    balance - the price of the stop still being the thing that closes a losing
    position."""
    deployed = _slots() * _frac()
    free = float(CFG["min_available_margin_pct"])
    assert deployed == pytest.approx(1.0 - free, abs=1e-4), (
        f"{_slots()} slots x {_frac()} = {deployed:.2%} deployed against a "
        f"{free:.0%} margin floor; the two must sum to 100%.")


def test_margin_headroom_agrees_with_the_deployment():
    """A 98% deployment against a 10% free-margin floor would refuse the last
    position at the gate while the sizing insisted on taking it."""
    deployed = _slots() * _frac()
    free = float(CFG["min_available_margin_pct"])
    assert deployed + free <= 1.0 + 1e-9, (
        f"deployment {deployed:.1%} + required free margin {free:.1%} exceeds "
        f"100%: the margin gate refuses a book the sizing asks for.")


# ------------------------------------------------------------ the declared risk
def test_declared_drawdown_matches_the_arithmetic():
    p = _params()
    computed = _slots() * _frac() * p.leverage * p.stop_pct / 100.0
    declared = float(CFG["max_correlated_drawdown_pct"])
    assert declared == pytest.approx(computed, rel=1e-3), (
        f"config declares a {declared:.1%} worst case but the numbers produce "
        f"{computed:.1%}. Whichever is wrong, they must agree - a declared risk "
        f"that does not match the sizing is worse than no declaration.")


def test_the_declared_drawdown_is_survivable():
    """Not a comfort check - a solvency one. A correlated stop-out must leave
    the account above zero with room to keep trading, or the stop is a
    liquidation in slow motion."""
    dd = float(CFG["max_correlated_drawdown_pct"])
    assert dd < 0.60, (
        f"a {dd:.1%} correlated drawdown is past the point where the account "
        f"can recover; reduce leverage, the stop, or the deployment.")


def test_a_correlated_stopout_from_the_floor_does_not_go_negative():
    survivors = _floor() * (1.0 - float(CFG["max_correlated_drawdown_pct"]))
    assert survivors > 0, "a full stop-out at the floor wipes the account"


# ------------------------------------------------------------------ the caps
def test_notional_caps_admit_a_full_book_at_the_floor():
    full = _slots() * _notional_at(_floor())
    for key in ("max_total_notional_pct", "max_xyz_short_notional_pct"):
        cap = float(CFG[key]) * _floor()
        assert full <= cap, (
            f"{key} caps the book at ${cap:.2f} but a full book at the "
            f"${_floor():.0f} floor is ${full:.2f}: the last slot can never fill.")


def test_absolute_usd_ceilings_are_inactive_under_fraction_sizing():
    """A fixed-dollar ceiling silently clips fraction sizing as equity grows,
    and does it without a log line. 0 means inactive; the percentage caps
    scale instead."""
    for key in ("max_trade_notional_usd", "strategy_book_notional_usd",
                "short_notional_usd"):
        v = float(CFG.get(key, 0) or 0)
        if v <= 0:
            continue
        assert v >= _notional_at(_floor()), (
            f"{key}=${v:.2f} is under one position at the floor "
            f"(${_notional_at(_floor()):.2f}) and would clip it silently.")


def test_name_cap_does_not_refuse_trades_the_slots_allow():
    """Live symptom this came from: `xyz-short name cap reached (2/2)` while
    slots sat empty."""
    names = int(CFG["max_xyz_short_names"])
    assert names >= _slots(), (
        f"max_xyz_short_names={names} < max_concurrent={_slots()}: the name cap "
        f"refuses xyz shorts the concurrency budget already approved.")


# ------------------------------------------------------------------ mechanics
def test_position_size_clears_the_exchange_minimum_at_the_floor():
    """Checked at the floor, where the fraction produces its SMALLEST order.
    Passing at today's balance proves nothing about the balance that matters."""
    from pathia.client.exchange import MIN_ORDER_USD
    smallest = _notional_at(_floor())
    assert smallest >= MIN_ORDER_USD, (
        f"at the ${_floor():.0f} floor a position is ${smallest:.2f}, under the "
        f"${MIN_ORDER_USD} exchange minimum - orders would be refused exactly "
        f"as the account shrinks.")


def test_the_book_fits_in_margin_at_the_floor():
    p = _params()
    margin = _slots() * _notional_at(_floor()) / p.leverage
    assert margin <= _floor(), (
        f"the book needs ${margin:.2f} of margin against ${_floor():.0f} equity")


def test_the_stop_is_reachable_at_this_leverage():
    """backup_sl_max_frac_of_liq clamps the stop to a fraction of the distance
    to liquidation. A stop past that is silently tightened, so the book's real
    stop stops being the one it advertises."""
    p = _params()
    clamp = 100.0 * float(CFG["backup_sl_max_frac_of_liq"]) / p.leverage
    assert p.stop_pct <= clamp, (
        f"stop {p.stop_pct}% is clamped to {clamp:.1f}% at {p.leverage}x")


# ------------------------------------------------------------------ the mode
@pytest.mark.parametrize("key,expected", [("enable_hip3", True), ("enable_crypto", False)])
def test_hip3_only_is_still_the_declared_mode(key, expected):
    assert CFG[key] is expected


def test_allowlist_is_exactly_xyz():
    assert CFG["hip3_dex_allowlist"] == ["xyz"], (
        "io/para/mkts and the other HIP-3 dexes are muted by operator decision; "
        "see test_hip3_dex_allowlist.py for what happens when this is not enforced.")
