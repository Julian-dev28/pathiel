"""The funded dry run must be honest at every equity, especially the awkward ones.

The account cannot trade, so the question "would these books actually trade,
and with what" has never been answered by running them. This report answers it
from config and the books' own ledgers. Its whole value is being right about
the partial cases — the ones where some books work and some do not — because
that is where an operator makes a funding decision.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        "funded_dry_run", os.path.join(ROOT, "scripts", "funded_dry_run.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


F = _load()

CFG = {
    "min_available_margin_pct": 0.10,
    "a": {"enabled": True, "notional_usd": 20.0, "shadow_only": False, "leverage": 1},
    "b": {"enabled": True, "notional_usd": 20.0, "shadow_only": False, "leverage": 1},
    "c": {"enabled": False, "notional_usd": 20.0, "shadow_only": False, "leverage": 1},
    "thin_short_relax": {"enabled": True, "some_pct": 3.0},   # a gate, not a book
}


def test_only_enabled_books_with_a_capital_path_are_counted():
    """thin_short_relax is a gate relaxation, not a book. Counting it inflated
    the derived floor from $88.89 to $111.11."""
    names = [b["book"] for b in F.book_specs(CFG)]
    assert names == ["a", "b"]


def test_at_zero_equity_nothing_can_open():
    r = F.simulate(0.0, CFG)
    assert r["holdable"] == []


def test_at_the_floor_every_book_can_hold_at_once():
    r = F.simulate(44.45, CFG)      # (20+20)/0.9 = 44.44
    assert len(r["holdable"]) == 2


def test_the_free_margin_floor_is_actually_withheld():
    """At exactly the summed margin, one book must NOT fit — the free-margin
    reserve is the difference between a working account and a liquidation."""
    r = F.simulate(40.0, CFG)
    assert len(r["holdable"]) == 1, "the 10% reserve was spent"


def test_partial_funding_reports_which_books_wait():
    r = F.simulate(25.0, CFG)
    assert len(r["holdable"]) == 1
    waiting = [b for b in r["books"] if b["book"] not in r["holdable"]]
    assert waiting and all(b["fits_alone"] for b in waiting), (
        "a book that fits alone must be reported as waiting, not as unable")


def test_a_book_under_the_exchange_minimum_is_flagged():
    """A $5 notional is not a small position, it is a rejected order."""
    cfg = dict(CFG, d={"enabled": True, "notional_usd": 5.0,
                       "shadow_only": False, "leverage": 1})
    r = F.simulate(1000.0, cfg)
    tiny = next(b for b in r["books"] if b["book"] == "d")
    assert tiny["clears_exchange_min"] is False


def test_leverage_lowers_the_margin_not_the_notional():
    cfg = {"min_available_margin_pct": 0.10,
           "a": {"enabled": True, "notional_usd": 20.0, "shadow_only": False,
                 "leverage": 5}}
    b = F.simulate(100.0, cfg)["books"][0]
    assert b["notional"] == 20.0 and b["margin"] == 4.0


def test_a_book_with_no_ledger_is_unmeasurable_not_zero(monkeypatch, tmp_path):
    """"No history" and "no signals" are different facts. Reporting 0 for a
    book that has never been graded would read as a dead book."""
    from hermes_trader.agents import shadow_ledger as SL

    monkeypatch.setattr(SL, "_book_path", lambda b: str(tmp_path / f"{b}.jsonl"))
    count, last = F.ledger_reach("never_ran")
    assert count == 0 and last is None


def test_ledger_reach_reads_a_real_book(monkeypatch, tmp_path):
    from hermes_trader.agents import shadow_ledger as SL

    f = tmp_path / "bk.jsonl"
    f.write_text('{"coin": "ETH"}\n{"coin": "SOL"}\n')
    monkeypatch.setattr(SL, "_book_path", lambda b: str(tmp_path / "bk.jsonl"))
    count, last = F.ledger_reach("bk")
    assert count == 2 and last == "SOL"


def test_a_torn_ledger_line_does_not_hide_the_rest():
    """Half a line from a crashed write must not zero out a book's history."""
    import json
    import tempfile

    from hermes_trader.agents import shadow_ledger as SL
    d = tempfile.mkdtemp()
    p = os.path.join(d, "bk.jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"coin": "ETH"}) + "\n")
        fh.write('{"coin": "SO')                       # torn write
    orig = SL._book_path
    SL._book_path = lambda b: p
    try:
        count, last = F.ledger_reach("bk")
    finally:
        SL._book_path = orig
    assert count == 1 and last == "ETH"


def test_it_never_touches_the_order_path():
    """Mode is LIVE. Driving executor.maybe_execute to 'simulate' would place
    real orders — the report must be derived, not executed."""
    import ast

    src = open(os.path.join(ROOT, "scripts", "funded_dry_run.py")).read()
    tree = ast.parse(src)
    # Strip docstrings: this file's own prose names the thing it must not call.
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body.pop(0)
    code = ast.unparse(tree)
    for banned in ("maybe_execute", "place_order", "requests.post", "httpx",
                   "exchange.order"):
        assert banned not in code, f"funded_dry_run reaches the exchange via {banned}"


def test_it_runs_against_the_live_config():
    assert F.main(["--equity", "100"]) == 0
