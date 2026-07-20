"""Gate tests for the unattended evidence loop.

This script moves real money without a human reading the output, so the
decision table is pinned branch by branch. The asymmetry between promotion
and demotion is the safety property: demotion is cheap and fires on weak
evidence; promotion is expensive and demands every bar at once.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "autonomous_cycle.py"
_SPEC = importlib.util.spec_from_file_location("autonomous_cycle", _PATH)
AC = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(AC)


def _g(**ov):
    g = {"book": "engulf_short", "n": 12, "ev_real": 2.0, "ev_strict": 1.0,
         "halves": {"first": 1.0, "second": 3.0}, "mc_p": 0.01}
    g.update(ov)
    return g


# ------------------------------------------------------------------ pending
def test_below_min_n_never_acts():
    for n in (0, 1, AC.MIN_N - 1):
        d = AC.decide(_g(n=n), live=True)
        assert d["verdict"] == "PENDING" and d["action"] == "none"


def test_missing_ev_is_pending_not_refuted():
    """A grade with no EV must not be read as a refutation."""
    d = AC.decide(_g(ev_real=None), live=True)
    assert d["verdict"] == "PENDING" and d["action"] == "none"


# ------------------------------------------------------------------ demotion
@pytest.mark.parametrize("ev", [-5.0, -0.01, 0.0])
def test_non_positive_ev_demotes_a_live_book(ev):
    d = AC.decide(_g(ev_real=ev), live=True)
    assert d["verdict"] == "REFUTED" and d["action"] == "demote"


def test_demotion_needs_no_null_and_no_halves():
    """Stopping a bleed must not wait for significance — a book with a
    negative EV and NO computed null is still demoted."""
    d = AC.decide(_g(ev_real=-1.0, mc_p=None, halves={}), live=True)
    assert d["action"] == "demote"


def test_refuted_but_already_shadow_is_a_no_op():
    d = AC.decide(_g(ev_real=-1.0), live=False)
    assert d["verdict"] == "REFUTED" and d["action"] == "none"


# ------------------------------------------------------------------ promotion
def test_promotion_requires_every_bar_at_once():
    assert AC.decide(_g(), live=False)["action"] == "promote"


@pytest.mark.parametrize("bad", [
    {"halves": {"first": -0.1, "second": 3.0}},   # first half negative
    {"halves": {"first": 1.0, "second": -0.1}},   # second half negative
    {"ev_strict": -0.5},                          # dies at 25bps
    {"ev_strict": 0.0},                           # exactly breakeven at 25bps
    {"mc_p": 0.05},                               # not strictly below the bar
    {"mc_p": 0.20},                               # insignificant
    {"mc_p": None},                               # null never computed
    {"halves": {}},                               # halves missing entirely
])
def test_any_single_failure_blocks_promotion(bad):
    d = AC.decide(_g(**bad), live=False)
    assert d["action"] == "none", d
    assert d["verdict"] in ("MARGINAL", "PENDING")


def test_recorders_never_get_promoted_even_when_validated():
    """A book with no bounded capital path stays a recorder no matter how
    good its numbers look — otherwise the cycle would 'promote' something
    that has no live order path at all."""
    for book in ("whale_flow", "news_catalyst", "mover_b15_up", "young_listings"):
        d = AC.decide(_g(book=book), live=False)
        assert d["verdict"] == "VALIDATED" and d["action"] == "none"


def test_already_live_validated_book_is_left_alone():
    d = AC.decide(_g(), live=True)
    assert d["verdict"] == "VALIDATED" and d["action"] == "none"


# ------------------------------------------------------------------ config mutation
def _cfg():
    return {
        "engulf_short": {"enabled": True, "shadow_only": False, "leverage": 12,
                         "notional_usd": 20.0, "stop_pct": 20.0},
        "mover_recorders": {"pass_short_live": {"enabled": True, "shadow_only": True,
                                                "leverage": 12, "stop_pct": 15.0}},
    }


def test_demote_sets_shadow_only():
    c = _cfg()
    assert AC.apply_action(c, "engulf_short", "demote") is True
    assert c["engulf_short"]["shadow_only"] is True
    # idempotent: a second demote is a no-op, so the cycle won't churn commits
    assert AC.apply_action(c, "engulf_short", "demote") is False


def test_promote_writes_a_bounded_and_REACHABLE_geometry():
    """The promoted stop must survive executor.py's backup-SL clamp
    (entry * 60/leverage percent) or the book trades a geometry it was
    never graded at — the bug found live on 2026-07-20."""
    c = _cfg()
    assert AC.apply_action(c, "mover_pass_short", "promote") is True
    b = c["mover_recorders"]["pass_short_live"]
    assert b["shadow_only"] is False and b["enabled"] is True
    assert b["notional_usd"] == 20.0 and b["leverage"] == 10
    assert b["stop_pct"] == 6.0
    assert b["stop_pct"] <= 60.0 / b["leverage"]      # reachable
    assert b["stop_pct"] < 100.0 / b["leverage"]      # inside liquidation


def test_unknown_book_is_never_mutated():
    c = _cfg()
    assert AC.apply_action(c, "book_that_does_not_exist", "promote") is False
    assert c == _cfg()


def test_promotion_sizing_constants_are_bounded():
    assert AC.PROMOTE_NOTIONAL_USD <= 20.0
    assert AC.PROMOTE_LEVERAGE <= 10
    assert AC.PROMOTE_STOP_PCT <= 60.0 / AC.PROMOTE_LEVERAGE


def test_every_switch_target_exists_in_the_live_config():
    """A switch pointing at a missing config block would silently no-op a
    demotion — the cycle would report success while the book kept trading."""
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".agent-config.json").read_text())
    for book in AC._SWITCHES:
        assert AC._block(cfg, book) is not None, f"{book} switch points at nothing"
