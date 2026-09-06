"""When every slot is full, the loop must stop PAYING to look.

THE WASTE THIS CLOSES
max_concurrent refuses a new entry at the END of the pipeline - after the candle
fetches, after news_surge_short has called coin_catalyst() once per coin (a
Google News fetch each), and after research() has spent a Claude call on every
survivor. With a 24h hold and 3 slots the book fills early and every later cycle
re-derives candidates it is structurally unable to act on.

WHAT MUST STILL RUN, AND WHY EACH ONE MATTERS
  monitor_exits   open positions still need managing. Gate this and capital sits
                  in a position nothing is watching - the stop never fires.
  the recorders   data_logger appends the funding/OI panel that every research
                  script reads. Skipping it leaves a hole for exactly the
                  periods the book was fully deployed, which is survivorship
                  bias baked into every future backtest. The most expensive
                  possible saving.

These are asserted structurally, over the AST, because the failure mode is a
future edit: someone adds a fifth book and forgets the guard, and nothing breaks
loudly - the loop just quietly goes back to paying full price. A grep would miss
it; the tree does not.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "scripts" / "trading_loop.py"
TREE = ast.parse(SRC.read_text())

GATED = ("_unlock_short_maybe_run", "_news_surge_short_maybe_run",
         "_news_surge_multi_maybe_run", "_xs_reversal_maybe_run", "scan_once")
UNGATED = ("monitor_exits", "_data_logger_maybe_log", "_unlock_maybe_record",
           "_social_trending_maybe_record")
FLAG = "_entry_budget_open"


def _guards(node) -> set[str]:
    """Names appearing in every `if` test that encloses `node`."""
    found: set[str] = set()

    def walk(n, stack):
        if n is node:
            found.update(stack)
            return True
        for child in ast.iter_child_nodes(n):
            extra = []
            if isinstance(n, ast.If) and child in n.body:
                extra = [x.id for x in ast.walk(n.test) if isinstance(x, ast.Name)]
            if walk(child, stack + extra):
                return True
        return False

    walk(TREE, [])
    return found


def _calls(name: str):
    return [n for n in ast.walk(TREE)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


@pytest.mark.parametrize("fn", GATED)
def test_paid_work_is_behind_the_slots_gate(fn):
    calls = _calls(fn)
    assert calls, f"{fn} is not called in the loop at all — did it get renamed?"
    for c in calls:
        assert FLAG in _guards(c), (
            f"{fn}() at line {c.lineno} is NOT guarded by {FLAG}. With the book "
            f"full it would fetch candles, news and AI verdicts for a trade "
            f"max_concurrent is going to refuse anyway.")


@pytest.mark.parametrize("fn", UNGATED)
def test_exits_and_recorders_are_never_gated(fn):
    calls = _calls(fn)
    assert calls, f"{fn} is not called in the loop at all — did it get renamed?"
    for c in calls:
        assert FLAG not in _guards(c), (
            f"{fn}() at line {c.lineno} is behind {FLAG}. Exits must run so open "
            f"positions still close, and the recorders must run so the research "
            f"panel has no hole for the periods the book was fully deployed.")


def test_the_gate_is_derived_from_max_concurrent():
    """The predicate must read the live config, not a constant, so raising
    max_concurrent immediately reopens the budget."""
    src = SRC.read_text()
    i = src.index(f"{FLAG} =")
    line = src[i:src.index("\n", i)]
    assert "_slots" in line and "len(positions)" in line, line
    assert 'get("max_concurrent"' in src[max(0, i - 400):i], (
        "the slot count must come from max_concurrent in the live config")


def test_zero_or_missing_max_concurrent_does_not_wedge_the_loop():
    """A missing/0 max_concurrent must mean 'no limit configured', not 'never
    trade again'. Reading it as 0 slots would silently stop all entries."""
    src = SRC.read_text()
    i = src.index(f"{FLAG} =")
    assert "_slots <= 0" in src[i:src.index("\n", i)], (
        "a 0 or absent max_concurrent must leave the entry budget OPEN")
