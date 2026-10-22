"""Unit tests for trades.py: coin/direction resolution, the pure fill-grading
function, window computation, idempotent grade_due(), storage round-trips,
and TrackRecord enrichment.

One test (test_grade_account_fills_on_the_real_brent_fixture) replays the
real, trimmed userFillsByTime fixture (services/strategies/tests/fixtures/
watch_wallet_brent_fills.json, captured 2026-10-23 for the operator's
watch-only wallet) through grade_account_fills -- offline, no network, but
against real numbers so the n_fills/n_opens/n_closes/realized_pnl_usd math
is checked against a hand-verified total, not just a synthetic fixture.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from contracts.accounts.models import Account
from contracts.strategies.models import Call, TrackRecord
from services.strategies import desks, journal, trades as T
from services.strategies.tests.conftest import make_thesis

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _account(**kw) -> Account:
    base = dict(id="acc_test", label="Test", main_address="0x" + "a" * 40, mode="watch",
               source="watch", created_at=T0)
    base.update(kw)
    return Account(**base)


def _fill(coin="xyz:BRENTOIL", px=100.0, side="B", dir_="Open Long", t=0, closed_pnl=0.0, fee=0.1):
    return {"coin": coin, "px": str(px), "sz": "1.0", "side": side, "time": t, "dir": dir_,
           "closedPnl": str(closed_pnl), "fee": str(fee), "tid": t}


# ── coin / direction resolution ─────────────────────────────────────────────

def test_coins_for_call_single_instrument():
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
               invalidation="x")
    assert T._coins_for_call(call, None) == ["xyz:BRENTOIL"]


def test_coins_for_call_pair_resolves_both_legs():
    spec = desks.get_spec("oil")
    call = Call(side="pair", instrument="Brent-WTI spread", one_liner="x", confidence=0.5,
               invalidation="x")
    assert T._coins_for_call(call, spec) == ["xyz:BRENTOIL", "xyz:CL"]


def test_coins_for_call_pair_without_spec_falls_back_to_instrument_label():
    call = Call(side="pair", instrument="Brent-WTI spread", one_liner="x", confidence=0.5,
               invalidation="x")
    assert T._coins_for_call(call, None) == ["Brent-WTI spread"]


@pytest.mark.parametrize("side,coin,expected", [
    ("long", "xyz:BRENTOIL", "B"),
    ("long", "xyz:CL", None),            # a long call has no direction on another coin
    ("short", "xyz:BRENTOIL", "A"),
    ("flat", "xyz:BRENTOIL", None),
])
def test_expected_open_side_long_short_flat(side, coin, expected):
    call = Call(side=side, instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
               invalidation="x")
    assert T._expected_open_side(call, coin, None) == expected


def test_expected_open_side_pair_long_leg_buys_short_leg_sells():
    spec = desks.get_spec("oil")
    call = Call(side="pair", instrument="Brent-WTI spread", one_liner="x", confidence=0.5,
               invalidation="x")
    assert T._expected_open_side(call, "xyz:BRENTOIL", spec) == "B"
    assert T._expected_open_side(call, "xyz:CL", spec) == "A"
    assert T._expected_open_side(call, "BTC", spec) is None


# ── grade_account_fills: the pure per-account math ──────────────────────────

def test_grade_account_fills_counts_opens_closes_and_follow_rate():
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
               invalidation="x", entry_zone=[99.0, 101.0])
    fills = [
        _fill(side="B", dir_="Open Long", px=100.0, t=1_000, closed_pnl=0.0, fee=0.1),
        _fill(side="A", dir_="Open Short", px=102.0, t=2_000, closed_pnl=0.0, fee=0.1),  # against the call
        _fill(side="A", dir_="Close Long", px=105.0, t=3_000, closed_pnl=5.0, fee=0.1),
        _fill(side="B", dir_="Close Short", px=101.0, t=4_000, closed_pnl=1.0, fee=0.1),
    ]
    grade = T.grade_account_fills(_account(), call, None, fills, 0, 10_000)
    assert grade.n_fills == 4
    assert grade.n_opens == 2 and grade.n_closes == 2
    assert grade.follow_rate == pytest.approx(0.5)   # 1 of 2 opens was "B" (the long call's side)
    assert grade.first_entry_px == pytest.approx(100.0)
    assert grade.entered_in_zone is True
    assert grade.max_adverse_fill == pytest.approx(0.0)  # worst closedPnl among all 4 is the two 0.0 opens
    # realized = sum(closedPnl) - sum(fee) = (0+0+5+1) - (0.1*4)
    assert grade.realized_pnl_usd == pytest.approx(5.6)


def test_grade_account_fills_clips_to_window_and_coin():
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
               invalidation="x")
    fills = [
        _fill(coin="xyz:BRENTOIL", t=500, dir_="Open Long"),    # before the window
        _fill(coin="xyz:BRENTOIL", t=5_000, dir_="Open Long"),  # in the window
        _fill(coin="xyz:CL", t=5_000, dir_="Open Long"),        # wrong coin
        _fill(coin="xyz:BRENTOIL", t=50_000, dir_="Open Long"), # after the window
    ]
    grade = T.grade_account_fills(_account(), call, None, fills, 1_000, 10_000)
    assert grade.n_fills == 1


def test_grade_account_fills_no_fills_is_all_none_not_an_error():
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
               invalidation="x")
    grade = T.grade_account_fills(_account(), call, None, [], 0, 10_000)
    assert grade.n_fills == 0 and grade.n_opens == 0 and grade.n_closes == 0
    assert grade.realized_pnl_usd == 0.0
    assert grade.follow_rate is None
    assert grade.first_entry_px is None
    assert grade.entered_in_zone is None
    assert grade.max_adverse_fill is None


def test_grade_account_fills_flat_call_has_no_follow_rate():
    call = Call(side="flat", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.5,
               invalidation="x")
    fills = [_fill(side="B", dir_="Open Long", t=1_000), _fill(side="A", dir_="Open Short", t=2_000)]
    grade = T.grade_account_fills(_account(), call, None, fills, 0, 10_000)
    assert grade.n_opens == 2
    assert grade.follow_rate is None   # flat has no direction to follow


# ── grade_thesis_trades: window + fetcher wiring ────────────────────────────

def test_grade_thesis_trades_window_end_is_next_thesis_created_at():
    thesis = make_thesis(side="long", instrument="xyz:BRENTOIL", created_at=T0,
                         horizon_days=30)  # expires far in the future
    next_created_at = T0 + timedelta(days=5)   # a later thesis supersedes it first
    seen_windows = []

    def fetcher(user, start_ms, end_ms):
        seen_windows.append((start_ms, end_ms))
        return []

    result = T.grade_thesis_trades(thesis, None, [_account()], fetcher,
                                   next_created_at=next_created_at,
                                   now=T0 + timedelta(days=10))
    assert result.window_start == T0
    assert result.window_end == next_created_at   # not expires_at, not now
    assert seen_windows == [(int(T0.timestamp() * 1000), int(next_created_at.timestamp() * 1000))]


def test_grade_thesis_trades_window_end_clips_to_now_when_still_open():
    thesis = make_thesis(side="long", instrument="xyz:BRENTOIL", created_at=T0, horizon_days=30)
    now = T0 + timedelta(days=3)
    result = T.grade_thesis_trades(thesis, None, [], lambda u, s, e: [], now=now)
    assert result.window_end == now


def test_grade_thesis_trades_one_row_per_account():
    thesis = make_thesis(side="long", instrument="xyz:BRENTOIL", created_at=T0, horizon_days=1)
    accounts = [_account(id="a1"), _account(id="a2", main_address="0x" + "b" * 40)]
    result = T.grade_thesis_trades(thesis, None, accounts, lambda u, s, e: [],
                                   now=thesis.expires_at)
    assert [a.account_id for a in result.accounts] == ["a1", "a2"]


# ── storage round-trip ───────────────────────────────────────────────────────

def test_save_get_list_thesis_trades_round_trip():
    thesis = make_thesis(side="long", instrument="xyz:BRENTOIL", created_at=T0, horizon_days=1)
    result = T.grade_thesis_trades(thesis, None, [_account()], lambda u, s, e: [],
                                   now=thesis.expires_at)
    assert T.get_thesis_trades("oil", thesis.id) is None
    T.save_thesis_trades(result)
    got = T.get_thesis_trades("oil", thesis.id)
    assert got == result
    assert T.list_thesis_trades("oil") == [result]
    assert T.list_thesis_trades("no-such-desk") == []


# ── grade_due: the idempotent sweep ──────────────────────────────────────────

def test_grade_due_skips_a_thesis_not_yet_due(monkeypatch):
    monkeypatch.setattr("services.accounts.view.list_all_accounts", lambda: [_account()])
    thesis = make_thesis(desk_id="oil", created_at=T0, horizon_days=30)  # not expired
    journal.save_thesis(thesis)
    graded = T.grade_due(fills_fetcher=lambda u, s, e: [], now=T0 + timedelta(days=1))
    assert graded == 0
    assert T.get_thesis_trades("oil", thesis.id) is None


def test_grade_due_grades_an_expired_thesis_once(monkeypatch):
    monkeypatch.setattr("services.accounts.view.list_all_accounts", lambda: [_account()])
    thesis = make_thesis(desk_id="oil", created_at=T0, horizon_days=1)
    journal.save_thesis(thesis)
    now = thesis.expires_at + timedelta(hours=1)

    graded = T.grade_due(fills_fetcher=lambda u, s, e: [], now=now)
    assert graded == 1
    assert T.get_thesis_trades("oil", thesis.id) is not None

    # idempotent: a second sweep finds nothing left to grade
    graded_again = T.grade_due(fills_fetcher=lambda u, s, e: [], now=now)
    assert graded_again == 0


def test_grade_due_uses_the_next_thesis_as_the_window_end(monkeypatch):
    monkeypatch.setattr("services.accounts.view.list_all_accounts", lambda: [_account()])
    old = make_thesis(desk_id="oil", thesis_id="old", created_at=T0, horizon_days=30)
    new = make_thesis(desk_id="oil", thesis_id="new", created_at=T0 + timedelta(days=2),
                      horizon_days=30)
    journal.save_thesis(old)
    journal.save_thesis(new)
    now = T0 + timedelta(days=3)

    # `old` isn't expired yet, but it IS graded -- that alone makes it due.
    from services.strategies import grader
    journal.save_thesis(old.model_copy(update={
        "grade": grader.grade_thesis(old, hourly_fetcher=lambda *a: [], now=now)}))

    T.grade_due(fills_fetcher=lambda u, s, e: [], now=now)
    old_trades = T.get_thesis_trades("oil", "old")
    assert old_trades.window_end == new.created_at   # superseded, not its own expiry


def test_grade_due_scoped_to_one_desk(monkeypatch):
    monkeypatch.setattr("services.accounts.view.list_all_accounts", lambda: [_account()])
    oil_thesis = make_thesis(desk_id="oil", created_at=T0, horizon_days=1)
    btc_thesis = make_thesis(desk_id="btc", created_at=T0, horizon_days=1, instrument="BTC")
    journal.save_thesis(oil_thesis)
    journal.save_thesis(btc_thesis)
    now = T0 + timedelta(days=2)

    graded = T.grade_due(fills_fetcher=lambda u, s, e: [], now=now, desk_id="oil")
    assert graded == 1
    assert T.get_thesis_trades("oil", oil_thesis.id) is not None
    assert T.get_thesis_trades("btc", btc_thesis.id) is None


# ── TrackRecord enrichment ───────────────────────────────────────────────────

def test_aggregate_trade_stats_empty_desk_is_all_none():
    stats = T._aggregate_trade_stats("oil")
    assert stats == {"n_trades": 0, "realized_pnl_usd": None, "follow_rate": None}


def test_enrich_track_record_sums_across_theses(monkeypatch):
    monkeypatch.setattr("services.accounts.view.list_all_accounts", lambda: [_account()])
    t1 = make_thesis(desk_id="oil", thesis_id="t1", created_at=T0, horizon_days=1)
    t2 = make_thesis(desk_id="oil", thesis_id="t2", created_at=T0 + timedelta(days=2),
                     horizon_days=1)
    journal.save_thesis(t1)
    journal.save_thesis(t2)
    fills = [_fill(side="B", dir_="Open Long", px=100.0, t=int(t1.created_at.timestamp() * 1000) + 1,
                   closed_pnl=0.0, fee=1.0),
             _fill(side="A", dir_="Close Long", px=110.0, t=int(t1.created_at.timestamp() * 1000) + 2,
                   closed_pnl=10.0, fee=1.0)]
    now = t2.expires_at + timedelta(days=1)
    T.grade_due(fills_fetcher=lambda u, s, e: fills, now=now)

    track = T.enrich_track_record(TrackRecord(n_graded=0), "oil")
    assert track.n_trades == 2
    assert track.realized_pnl_usd == pytest.approx(8.0)   # (0+10) - (1+1)
    assert track.follow_rate == pytest.approx(1.0)        # both theses: the one open was "B" (long)


def test_track_record_for_desk_combines_grader_and_trades():
    # No theses at all -> grader side is all-default, trades side is all-None/0.
    track = T.track_record_for_desk("oil")
    assert isinstance(track, TrackRecord)
    assert track.n_graded == 0
    assert track.n_trades == 0
    assert track.realized_pnl_usd is None


# ── the real, trimmed watch-wallet fixture ──────────────────────────────────

def test_grade_account_fills_on_the_real_brent_fixture():
    """services/strategies/tests/fixtures/watch_wallet_brent_fills.json: 40
    real xyz:BRENTOIL fills for the operator's watch-only wallet, captured
    2026-10-23. Hand-verified totals (python, summed straight off the raw
    JSON before trimming): 11 Open Long, 13 Open Short, 8 Close Long, 8
    Close Short; sum(closedPnl) = -203.507038, sum(fee) = 5.274728."""
    raw = json.loads((FIXTURES / "watch_wallet_brent_fills.json").read_text())
    fills = raw["fills"]
    assert len(fills) == 40

    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="Brent, long", confidence=0.6,
               invalidation="x")
    grade = T.grade_account_fills(_account(main_address=raw["user"]), call, None, fills, 0,
                                  2**53)
    assert grade.n_fills == 40
    assert grade.n_opens == 11 + 13
    assert grade.n_closes == 8 + 8
    assert grade.realized_pnl_usd == pytest.approx(-203.507038 - 5.274728, abs=1e-4)
    # 11 of the 24 opens were "B" (the long call's own direction)
    assert grade.follow_rate == pytest.approx(11 / 24)
    assert grade.max_adverse_fill is not None and grade.max_adverse_fill < 0
