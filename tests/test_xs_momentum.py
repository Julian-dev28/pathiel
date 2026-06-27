"""Cross-sectional momentum rebalancer — pure-engine tests (the validated +EV edge)."""
from hermes_trader.agents.xs_momentum import (
    trailing_return, pctk_score, rank_universe, rebalance_plan, is_empty_plan, TargetBook,
)


def _bars(closes):
    return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1} for i, c in enumerate(closes)]


def test_trailing_return_basic_and_short():
    assert abs(trailing_return(_bars([100, 110, 121]), 2) - 0.21) < 1e-9   # 121/100 - 1
    assert trailing_return(_bars([100, 110]), 5) is None                   # too short
    assert trailing_return(_bars([0, 110, 121]), 2) is None                # zero base guard


def test_rank_universe_top_long_bottom_short():
    # returns over lb=1: A +50%, B +20%, C 0%, D -30%
    cbc = {
        "A": _bars([100, 150]), "B": _bars([100, 120]),
        "C": _bars([100, 100]), "D": _bars([100, 70]),
    }
    book = rank_universe(cbc, lb=1, k=1)
    assert book.longs == ["A"]       # strongest
    assert book.shorts == ["D"]      # weakest
    assert book.scores["A"] > book.scores["D"]


def test_rank_universe_empty_when_too_few_coins():
    cbc = {"A": _bars([100, 150]), "B": _bars([100, 70])}
    assert rank_universe(cbc, lb=1, k=2).longs == []     # need >= 2k=4 coins


def test_pctk_rank_universe_top_channel_long_bottom_short():
    cbc = {
        "HIGH": _bars([100, 101, 102, 103, 104, 105]),
        "MID": _bars([100, 102, 101, 102, 101, 102]),
        "LOW": _bars([105, 104, 103, 102, 101, 100]),
        "FLAT": _bars([100, 100, 100, 100, 100, 100]),
    }
    assert pctk_score(cbc["HIGH"], 6) > pctk_score(cbc["LOW"], 6)
    book = rank_universe(cbc, lb=1, k=1, ranking="pct_k", zext_window=6)
    assert book.longs == ["HIGH"]
    assert book.shorts == ["LOW"]


def test_rebalance_plan_open_close_hold():
    book = TargetBook(longs=["A", "B"], shorts=["X", "Y"])
    plan = rebalance_plan(book, current_long=["B", "C"], current_short=["Y", "Z"])
    assert plan["open_long"] == ["A"]      # A new long
    assert plan["close_long"] == ["C"]     # C dropped
    assert plan["hold_long"] == ["B"]      # B kept
    assert plan["open_short"] == ["X"]
    assert plan["close_short"] == ["Z"]
    assert plan["hold_short"] == ["Y"]


def test_rebalance_plan_handles_side_flip():
    # F was long, now should be short → close the long AND open the short
    book = TargetBook(longs=["A"], shorts=["F"])
    plan = rebalance_plan(book, current_long=["F"], current_short=[])
    assert "F" in plan["close_long"] and "F" in plan["open_short"]


def test_is_empty_plan():
    book = TargetBook(longs=["A"], shorts=["B"])
    same = rebalance_plan(book, current_long=["A"], current_short=["B"])
    assert is_empty_plan(same) is True                     # already at target
    assert is_empty_plan(rebalance_plan(book, [], [])) is False
