"""Gate tests for capital-flow accounting.

The defect these close: an equity curve alone cannot tell a trading loss from
the operator withdrawing USDC. The risk panel read peak $225.93 against $0.03
and had to call it "an equity decline" because it genuinely did not know which
it was. A number that cannot separate those two is not a risk metric.

The fix is a time-weighted NAV index. These tests pin the property that makes it
work — capital moving in or out must not move the index — and pin the honesty
fallback for windows the flow record does not cover.
"""
from __future__ import annotations


import pytest

from hermes_trader.agents import capital_flows as cf


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the operator's real .state/capital_flows.jsonl."""
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    return tmp_path


# ── the property that matters ────────────────────────────────────────────────

def test_a_deposit_does_not_move_the_nav_index():
    """$100 -> $200 is a doubling if it was traded and a no-op if it was funded.
    This is the entire reason the index exists."""
    pts = [{"ts": 1, "equity": 100.0}, {"ts": 2, "equity": 200.0}]
    traded = cf.nav_series(pts, flows=[])
    funded = cf.nav_series(pts, flows=[{"ts": 2, "usd": 100.0, "kind": "deposit"}])
    assert traded[-1]["nav"] == pytest.approx(2.0)
    assert funded[-1]["nav"] == pytest.approx(1.0)


def test_a_withdrawal_does_not_create_a_drawdown():
    """The exact case that was mislabelled: $200 -> $0.03 because it was taken
    out, not lost."""
    pts = [{"ts": 1, "equity": 200.0}, {"ts": 2, "equity": 0.03}]
    nav = cf.nav_series(pts, flows=[{"ts": 2, "usd": -199.97, "kind": "withdraw"}])
    assert cf.drawdown_from_nav(nav)["max_drawdown_pct"] == pytest.approx(0.0, abs=0.01)


def test_a_real_loss_still_shows_as_a_drawdown():
    """The guard against over-correcting: flow-neutrality must not launder an
    actual loss."""
    pts = [{"ts": 1, "equity": 200.0}, {"ts": 2, "equity": 100.0}]
    nav = cf.nav_series(pts, flows=[])
    assert cf.drawdown_from_nav(nav)["max_drawdown_pct"] == pytest.approx(-50.0, abs=0.01)


def test_a_deposit_that_masks_a_loss_is_not_laundered():
    """Lose half, then top the account back up. Equity ends flat; the index must
    still show the -50%."""
    pts = [{"ts": 1, "equity": 200.0}, {"ts": 2, "equity": 100.0},
           {"ts": 3, "equity": 200.0}]
    nav = cf.nav_series(pts, flows=[{"ts": 3, "usd": 100.0, "kind": "deposit"}])
    assert nav[-1]["nav"] == pytest.approx(0.5, abs=1e-6)
    assert cf.drawdown_from_nav(nav)["max_drawdown_pct"] == pytest.approx(-50.0, abs=0.01)


# ── numerical guards ─────────────────────────────────────────────────────────

def test_zero_equity_does_not_divide_by_zero():
    pts = [{"ts": 1, "equity": 0.0}, {"ts": 2, "equity": 0.0},
           {"ts": 3, "equity": 50.0}]
    nav = cf.nav_series(pts, flows=[{"ts": 3, "usd": 50.0, "kind": "deposit"}])
    assert all(p["nav"] > 0 for p in nav)


def test_a_total_loss_floors_instead_of_poisoning_later_points():
    """A non-positive return would make the index zero or negative and every
    later point meaningless."""
    pts = [{"ts": 1, "equity": 100.0}, {"ts": 2, "equity": 0.0},
           {"ts": 3, "equity": 10.0}]
    nav = cf.nav_series(pts, flows=[{"ts": 3, "usd": 10.0, "kind": "deposit"}])
    assert all(p["nav"] > 0 for p in nav)
    assert cf.drawdown_from_nav(nav)["max_drawdown_pct"] < -99


def test_flows_are_counted_once_across_chained_intervals():
    """Half-open intervals: a flow landing exactly on a point boundary must not
    be double-counted by the interval on either side."""
    flows = [{"ts": 2, "usd": 100.0, "kind": "deposit"}]
    assert cf.net_flow_between(0, 2, flows) == 100.0
    assert cf.net_flow_between(2, 5, flows) == 0.0


# ── persistence ──────────────────────────────────────────────────────────────

def test_recording_is_idempotent_over_overlapping_windows():
    rows = [{"ts": 1, "usd": 10.0, "kind": "deposit", "key": "a"},
            {"ts": 2, "usd": -5.0, "kind": "withdraw", "key": "b"}]
    assert cf.append_flows(rows) == 2
    assert cf.append_flows(rows) == 0, "a re-run double-counted a deposit"
    assert len(cf.load_flows()) == 2


def test_a_torn_last_line_does_not_lose_the_history():
    cf.append_flows([{"ts": 1, "usd": 10.0, "kind": "deposit", "key": "a"}])
    with open(cf._flows_path(), "a") as fh:
        fh.write('{"ts": 2, "usd": 5.0')          # process died mid-write
    assert len(cf.load_flows()) == 1


def test_classify_signs_the_direction_correctly():
    u = "0xabc"
    assert cf.classify({"time": 1, "delta": {"type": "deposit", "usdcValue": "50"}}, u)[1] == 50.0
    assert cf.classify({"time": 1, "delta": {"type": "withdraw", "usdcValue": "50"}}, u)[1] == -50.0
    out = cf.classify({"time": 1, "delta": {"type": "send", "usdcValue": "20",
                                            "user": u, "destination": "0xdef"}}, u)
    assert out[1] == -20.0
    inn = cf.classify({"time": 1, "delta": {"type": "send", "usdcValue": "20",
                                            "user": "0xdef", "destination": u}}, u)
    assert inn[1] == 20.0


def test_a_self_transfer_between_own_pools_is_not_a_flow():
    """Moving USDC from main to a HIP-3 dex is not capital entering or leaving
    the account, and counting it would corrupt every interval it touches."""
    u = "0xabc"
    assert cf.classify({"time": 1, "delta": {"type": "send", "usdcValue": "20",
                                             "user": u, "destination": u}}, u) is None


def test_a_fill_or_funding_event_is_not_a_capital_flow():
    assert cf.classify({"time": 1, "delta": {"type": "funding", "usdcValue": "3"}}, "0xa") is None


def test_record_flows_survives_a_ledger_outage():
    """A ledger outage must not take down whatever called this."""
    def boom(user, since):
        raise RuntimeError("500 Server Error")

    res = cf.record_flows("0xabc", 0, fetcher=boom)
    assert res["status"] == "fetch_failed" and res["written"] == 0


def test_record_flows_persists_what_it_classifies():
    events = [
        {"time": 1000, "hash": "h1", "delta": {"type": "deposit", "usdcValue": "100"}},
        {"time": 2000, "hash": "h2", "delta": {"type": "withdraw", "usdcValue": "40"}},
        {"time": 3000, "hash": "h3", "delta": {"type": "funding", "usdcValue": "1"}},
    ]
    res = cf.record_flows("0xabc", 0, fetcher=lambda u, s: events)
    assert res["written"] == 2, "funding was recorded as a capital flow"
    assert sum(float(r["usd"]) for r in cf.load_flows()) == pytest.approx(60.0)


# ── coverage honesty ─────────────────────────────────────────────────────────

def test_an_unrecorded_window_is_not_claimed_as_covered():
    """If recording started after the equity window opened, the earlier part is
    still flow-blind and the panel must keep saying so."""
    pts = [{"ts": 1000, "equity": 100.0}, {"ts": 5000, "equity": 50.0}]
    cf.mark_recording_started(3000)
    assert cf.coverage(pts)["covered"] is False


def test_a_fully_recorded_window_is_covered():
    pts = [{"ts": 5000, "equity": 100.0}, {"ts": 9000, "equity": 50.0}]
    cf.mark_recording_started(1000)
    assert cf.coverage(pts)["covered"] is True


def test_no_flow_record_at_all_is_never_claimed_as_covered():
    pts = [{"ts": 1, "equity": 100.0}]
    assert cf.coverage(pts, flows=[])["covered"] is False


def test_the_marker_distinguishes_no_deposits_from_no_recording():
    """An account with genuinely zero deposits since inception must not look
    identical to one where nothing was ever recorded."""
    assert cf._recording_started_at() is None
    cf.mark_recording_started(1234)
    assert cf._recording_started_at() == 1234
    cf.mark_recording_started(9999)          # idempotent
    assert cf._recording_started_at() == 1234
