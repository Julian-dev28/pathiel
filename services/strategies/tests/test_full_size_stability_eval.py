"""Gate tests for the full-size stability eval (services/strategies/evals/
full_size_stability_test.py): the log-line parser (every real spelling the
oil desk-tick log uses for xyz:BRENTOIL's position), the implied-uPnL
formula, the stop-in-force schedule, and the replay harness itself on a
small, inline, synthetic tick sequence (no real log file, no network --
replaying the actual /tmp/oil-loop/progress.log is the eval's own job when
run directly, not this gate suite's).
"""
from __future__ import annotations

import pytest

from services.strategies.evals import full_size_stability_test as E


# ── parse_log_line ───────────────────────────────────────────────────────

def test_parses_a_normal_long_line():
    line = ("DESK-TICK oil 2026-11-17T02:10Z coin=xyz:BRENTOIL mid=89.383 pos=14.79@89.4062 "
            "action=HOLD long at full size, stop 88.64 target 93.43 resting")
    t = E.parse_log_line(line)
    assert t == E.Tick(ts="2026-11-17T02:10Z", mid=89.383, size=14.79, entry_px=89.4062)


def test_parses_a_short_position_with_its_negative_sign():
    line = "DESK-TICK oil 2026-11-16T20:54Z coin=xyz:BRENTOIL mid=90.0085 pos=-41.22@89.9735 action=HOLD"
    t = E.parse_log_line(line)
    assert t.size == -41.22 and t.entry_px == 89.9735


def test_parses_flat_as_zero_size_and_no_entry():
    line = "DESK-TICK oil 2026-11-16T18:10Z coin=xyz:BRENTOIL mid=90.4615 pos=flat action=NONE ..."
    t = E.parse_log_line(line)
    assert t.size == 0.0 and t.entry_px is None


def test_parses_a_size_with_no_entry_on_the_line():
    """A few historical lines carry "pos=S long"/"pos=S short" with no @entry
    (the side word, no price) -- the line still names a size."""
    line = "DESK-TICK oil 2026-11-17T08:42Z coin=xyz:BRENTOIL mid=89.76 pos=14.79 long action=HOLD ..."
    t = E.parse_log_line(line)
    assert t.size == 14.79 and t.entry_px is None


def test_a_line_for_another_coin_or_desk_is_not_a_match():
    for line in (
        "DESK-TICK metals 2026-11-04T13:59Z coin=xyz:SILVER mid=52.182 pos=-5.58@51.759 action=HOLD",
        "DESK-TICK oil 2026-11-17T07:01Z action=SKIP previous tick still running",
    ):
        assert E.parse_log_line(line) is None


# ── load_ticks ───────────────────────────────────────────────────────────

def test_load_ticks_reads_only_oil_brentoil_lines(tmp_path):
    log = tmp_path / "progress.log"
    log.write_text(
        "DESK-TICK oil 2026-11-17T01:53Z coin=xyz:BRENTOIL mid=89.394 pos=14.79@89.4062 action=PLACED\n"
        "DESK-TICK metals 2026-11-17T01:59Z coin=xyz:SILVER mid=50.0 pos=1.0@49.0 action=HOLD\n"
        "DESK-TICK oil 2026-11-17T02:10Z coin=xyz:BRENTOIL mid=89.383 pos=14.79@89.4062 action=HOLD\n"
        "garbage line with no structure at all\n",
        encoding="utf-8",
    )
    ticks = E.load_ticks(str(log))
    assert [t.ts for t in ticks] == ["2026-11-17T01:53Z", "2026-11-17T02:10Z"]


def test_load_ticks_on_a_missing_file_returns_empty_not_raise(tmp_path):
    assert E.load_ticks(str(tmp_path / "does-not-exist.log")) == []


# ── implied_unrealized_pnl ───────────────────────────────────────────────

def test_implied_upnl_matches_hyperliquids_linear_formula():
    # a long up 1.48 per unit on 14.79 units
    assert E.implied_unrealized_pnl(90.886, 89.4062, 14.79) == pytest.approx(21.886, abs=0.01)


def test_implied_upnl_is_zero_when_flat_regardless_of_price():
    assert E.implied_unrealized_pnl(123.0, 89.0, 0.0) == 0.0


def test_implied_upnl_sign_is_correct_for_a_short():
    # short 10 units, entry 100, mark 90: profit = (90-100)*(-10) = 100
    assert E.implied_unrealized_pnl(90.0, 100.0, -10.0) == pytest.approx(100.0)


# ── stop_in_force ────────────────────────────────────────────────────────

def test_stop_in_force_before_and_after_the_operators_raise():
    assert E.stop_in_force("2026-11-17T16:14Z") == E.STOP_BEFORE_RAISE
    assert E.stop_in_force("2026-11-17T16:15Z") == E.STOP_AFTER_RAISE
    assert E.stop_in_force("2026-11-17T16:20Z") == E.STOP_AFTER_RAISE


# ── replay ───────────────────────────────────────────────────────────────

def test_replay_old_swings_with_upnl_new_stays_flat():
    """A small, synthetic three-tick sequence that mirrors the live shape
    (one held position, mid rallying then easing back, no thesis change):
    OLD full size swings with the mark, NEW does not."""
    ticks = [
        E.Tick(ts="2026-11-17T02:00Z", mid=89.40, size=14.79, entry_px=89.4062),   # ~flat uPnL
        E.Tick(ts="2026-11-17T14:48Z", mid=91.3685, size=14.79, entry_px=None),   # big rally
        E.Tick(ts="2026-11-17T16:20Z", mid=90.886, size=14.79, entry_px=None),    # eases back
    ]
    rows = E.replay(ticks, base_equity_usd=70.0, desk_share=0.90, leverage=20.0)
    assert len(rows) == 3
    old_sizes = [r.old_full_size for r in rows]
    new_sizes = [r.new_full_size for r in rows]
    old_swing = max(old_sizes) - min(old_sizes)
    new_swing = max(new_sizes) - min(new_sizes)
    assert old_swing > 3.0, "the synthetic rally should visibly move the OLD (buggy) full size"
    assert new_swing < 0.5, "full size must stay flat under the fix"
    assert new_swing < old_swing * 0.2


def test_replay_carries_the_entry_forward_when_a_line_omits_it():
    """desk_share.py-style lines sometimes drop the @entry; the position's
    real entry (from an earlier tick, or ENTRY_PX for the first) still
    applies."""
    ticks = [
        E.Tick(ts="t0", mid=89.4062, size=14.79, entry_px=89.4062),
        E.Tick(ts="t1", mid=91.0, size=14.79, entry_px=None),   # no @entry on this line
    ]
    rows = E.replay(ticks, base_equity_usd=70.0, desk_share=0.90, leverage=20.0)
    assert rows[1].implied_upnl_usd == pytest.approx((91.0 - 89.4062) * 14.79, abs=0.01)


def test_replay_topup_room_is_full_size_minus_held_never_negative():
    ticks = [E.Tick(ts="t0", mid=89.4062, size=999.0, entry_px=89.4062)]   # already past full size
    rows = E.replay(ticks, base_equity_usd=70.0, desk_share=0.90, leverage=20.0)
    assert rows[0].old_topup_room == 0.0 and rows[0].new_topup_room == 0.0


def test_full_size_does_not_change_when_the_position_first_opens():
    """At the fill tick itself (uPnL == 0, since mark == entry), OLD and NEW
    must agree: the bug only ever showed up once the mark moved away from
    the fill."""
    flat = E.Tick(ts="t0", mid=89.4062, size=0.0, entry_px=None)
    just_filled = E.Tick(ts="t1", mid=89.4062, size=14.79, entry_px=89.4062)
    rows = E.replay([flat, just_filled], base_equity_usd=70.0, desk_share=0.90, leverage=20.0)
    assert rows[0].old_full_size == rows[0].new_full_size
    assert rows[1].old_full_size == pytest.approx(rows[1].new_full_size, abs=0.01)
