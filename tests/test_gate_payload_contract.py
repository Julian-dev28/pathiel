"""The executor's position payload must carry the keys the gates actually read.

xyz_short_concentration_gate has a notional cap — combined xyz-equity short
notional above 25% of equity blocks a new short. It has NEVER fired in
production. The gate reads `positionValue` / `notional` / `notional_usd`; the
executor built positions with `coin` / `side` / `size_usd` only. The lookup
always failed, `held_notional` stayed 0, and the `if held_notional > 0` guard
skipped the cap every time.

The gate's own docstring calls the cap "best-effort (skipped if positions carry
no value field) so a missing field never wrongly blocks" — so the code did
exactly what it said, while the only production caller guaranteed the field was
always missing. Best-effort degraded into never.

Unit tests passed throughout because they hand-build fixtures containing
`positionValue`, a shape production never produced. That is the specific failure
this file exists to prevent: testing a gate against a payload nobody sends it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from hermes_trader.agents import risk_gates
from hermes_trader.agents.executor import _position_notional

ROOT = Path(__file__).resolve().parents[1]


def _executor_position_keys() -> set[str]:
    """Keys the executor puts on each position dict handed to the gates.

    Read from the AST rather than by running maybe_execute, which needs a live
    account. If the literal moves, this test fails loudly rather than passing
    on a stale assumption.
    """
    tree = ast.parse((ROOT / "hermes_trader" / "agents" / "executor.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.ListComp):
            continue
        if not isinstance(node.elt, ast.Dict):
            continue
        keys = {k.value for k in node.elt.keys if isinstance(k, ast.Constant)}
        if "coin" in keys and "side" in keys:
            return keys
    raise AssertionError("could not find the executor's position payload literal")


def _gate_value_keys() -> set[str]:
    """Keys xyz_short_concentration_gate tries when looking up a notional."""
    src = inspect.getsource(risk_gates.xyz_short_concentration_gate)
    tree = ast.parse(src.lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple):
            vals = {e.value for e in node.elts if isinstance(e, ast.Constant)}
            if "positionValue" in vals:
                return vals
    raise AssertionError("could not find the gate's value-key tuple")


def test_the_executor_sends_a_key_the_notional_gate_reads():
    """The contract. Without an overlap the cap is permanently inert."""
    sent, wanted = _executor_position_keys(), _gate_value_keys()
    assert sent & wanted, (
        f"executor sends {sorted(sent)} but the notional cap only reads "
        f"{sorted(wanted)} — the cap can never fire")


# ── the cap actually blocks, using the payload production sends ──────────────

class _Ctx:
    trade_side = "short"
    coin = "xyz:NVDA"
    equity = 1000.0
    trade_notional_usd = 100.0

    def __init__(self, positions):
        self.current_positions = positions


def _production_position(coin: str, notional: float) -> dict:
    """Exactly what the executor builds — not a hand-tuned fixture."""
    return {"coin": coin, "side": "short",
            "size_usd": notional, "positionValue": notional}


def test_the_notional_cap_blocks_over_the_limit():
    ctx = _Ctx([_production_position("xyz:TSLA", 200.0),
                _production_position("xyz:AAPL", 200.0)])
    r = risk_gates.xyz_short_concentration_gate(ctx, max_names=5, max_notional_pct=0.25)
    assert r["pass"] is False, "held $400 + new $100 vs a $250 cap must block"
    assert "notional" in r["reason"]


def test_the_notional_cap_allows_under_the_limit():
    ctx = _Ctx([_production_position("xyz:TSLA", 50.0)])
    assert risk_gates.xyz_short_concentration_gate(
        ctx, max_names=5, max_notional_pct=0.25)["pass"] is True


def test_longs_and_crypto_are_untouched_by_the_cap():
    """The cap is xyz-shorts only. Widening it would be a behaviour change."""
    long_ctx = _Ctx([_production_position("xyz:TSLA", 900.0)])
    long_ctx.trade_side = "long"
    assert risk_gates.xyz_short_concentration_gate(
        long_ctx, max_names=5, max_notional_pct=0.25)["pass"] is True

    crypto = _Ctx([_production_position("xyz:TSLA", 900.0)])
    crypto.coin = "BTC"
    assert risk_gates.xyz_short_concentration_gate(
        crypto, max_names=5, max_notional_pct=0.25)["pass"] is True


# ── the notional itself ──────────────────────────────────────────────────────

def test_notional_prefers_hyperliquid_own_value():
    assert _position_notional({"position": {"positionValue": "1234.5", "szi": "2"}}) == 1234.5


def test_notional_falls_back_to_size_times_entry():
    assert _position_notional({"position": {"szi": "2", "entryPx": "100"}}) == 200.0


def test_a_short_reports_a_positive_notional():
    assert _position_notional({"position": {"szi": "-3", "entryPx": "50"}}) == 150.0


def test_a_degraded_read_under_reports_rather_than_raising():
    """These gates only ever BLOCK, so a low value fails open — a trade is
    allowed that maybe should not have been. An exception would take the whole
    executor down, which is strictly worse."""
    for bad in ({}, {"positionValue": "x", "szi": "y"}, {"szi": None}):
        assert _position_notional({"position": bad}) == 0.0


def test_the_old_computation_is_gone():
    """It multiplied EVERY held position's size by the entry price of the coin
    being evaluated — meaningless for any other coin."""
    src = (ROOT / "hermes_trader" / "agents" / "executor.py").read_text()
    assert 'analysis.get("entry_px") or 0)' not in src.split("positions = [")[1][:400]
