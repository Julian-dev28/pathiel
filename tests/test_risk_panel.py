"""Gate tests for the risk panel (2026-08-29).

Why this exists: the landing page counted 45 mentions of pnl and 25 of equity
and ZERO of drawdown, fee drag, or win rate. It showed upside and activity and
never once showed what the account could lose. These tests pin the numbers a
person with money actually asks for, and — just as important — pin the honesty
caveat, because a drawdown number that quietly counts a withdrawal as a loss is
worse than no number at all.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hermes_trader.dashboard as db


def _hb(ts, equity):
    return {"ts": ts, "event": "loop_heartbeat", "equity": equity,
            "daily_pnl": 0.0, "open_positions": 0, "available": equity}


@pytest.fixture
def curve(monkeypatch):
    """A rise to 200 then a fall to 140: a hand-checkable -30% drawdown.

    The decline is deliberately gentle. _equity_curve_payload rejects any point
    below 70% of the trailing median as a partial-dex degraded read, and needs
    three consecutive such points before it believes a real crash. A fixture
    that fell faster would be testing that filter, not the drawdown maths.
    """
    now = int(time.time() * 1000)
    day = 86_400_000
    pts = [(now - 9 * day, 100.0), (now - 8 * day, 150.0), (now - 7 * day, 200.0),
           (now - 6 * day, 180.0), (now - 5 * day, 160.0), (now - 4 * day, 140.0)]
    monkeypatch.setattr(db, "_read_log_lines", lambda: [_hb(t, e) for t, e in pts])
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [])
    monkeypatch.setattr(db, "read_agent_config",
                        lambda: {"max_daily_loss_usd": -100, "mode": "LIVE"})
    return pts


# ── the numbers ──────────────────────────────────────────────────────────────

def test_drawdown_is_measured_from_the_peak_not_the_start(curve):
    r = db._risk_payload()
    assert r["peak_equity"] == 200.0
    assert r["drawdown_pct"] == pytest.approx(-30.0, abs=0.01)


def test_max_drawdown_walks_forward_and_never_goes_positive(curve):
    r = db._risk_payload()
    assert r["max_drawdown_pct"] == pytest.approx(-30.0, abs=0.01)
    assert r["max_drawdown_pct"] <= 0


def test_a_monotonically_rising_account_has_no_drawdown(monkeypatch):
    now = int(time.time() * 1000)
    monkeypatch.setattr(db, "_read_log_lines",
                        lambda: [_hb(now - i * 86_400_000, 100.0 + (9 - i))
                                 for i in range(9, -1, -1)])
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    r = db._risk_payload()
    assert r["drawdown_pct"] == 0.0 and r["max_drawdown_pct"] == 0.0


def test_win_rate_and_fee_drag_come_from_graded_closes(monkeypatch, curve):
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [
        {"pnl_pct": 10.0, "spot_pct": 1.0, "leverage": 10, "fees_pct": 0.5},
        {"pnl_pct": -5.0, "spot_pct": -0.5, "leverage": 10, "fees_pct": 0.5},
        {"pnl_pct": 2.0, "spot_pct": 0.2, "leverage": 10, "fees_pct": 0.5},
    ])
    r = db._risk_payload()
    assert r["trades_graded"] == 3
    assert r["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    # gross |pnl| = 10 + 5 + 2 = 17; fees = 1.5 -> 8.82%
    assert r["fee_drag_pct"] == pytest.approx(8.82, abs=0.02)


def test_kill_switch_distance_is_zero_when_green_and_one_at_the_floor(monkeypatch, curve):
    now = int(time.time() * 1000)

    def _with_daily(pnl):
        monkeypatch.setattr(db, "_read_log_lines", lambda: [
            {"ts": now, "event": "loop_heartbeat", "equity": 100.0,
             "daily_pnl": pnl, "open_positions": 0, "available": 100.0}])
        return db._risk_payload()

    assert _with_daily(5.0)["kill_used_frac"] == 0.0
    assert _with_daily(-50.0)["kill_used_frac"] == pytest.approx(0.5)
    # already past the floor clamps at 1.0 rather than reporting 250%
    assert _with_daily(-250.0)["kill_used_frac"] == 1.0


def test_empty_history_does_not_divide_by_zero(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    r = db._risk_payload()
    assert r["drawdown_pct"] == 0.0 and r["win_rate"] is None


# ── the honesty caveat ───────────────────────────────────────────────────────

def test_an_uncovered_window_still_admits_it_cannot_tell_the_difference(
        curve, monkeypatch, tmp_path):
    """When flows are not recorded across the window the panel must fall back to
    raw equity AND say so, rather than quietly upgrading its own confidence."""
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    r = db._risk_payload()
    assert r["capital_flows_tracked"] is False
    assert r["drawdown_basis"] == "equity"
    assert "not necessarily a trading loss" in r["drawdown_caveat"]


def test_a_covered_window_reports_a_flow_neutral_drawdown(curve, monkeypatch, tmp_path):
    """With flows recorded across the whole window the drawdown is computed on
    the NAV index, and the caveat goes away because it no longer applies."""
    from hermes_trader.agents import capital_flows as cf
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    cf.mark_recording_started(1)          # before the fixture's first point
    cf.append_flows([{"ts": 2, "usd": 0.0, "kind": "deposit", "key": "k"}])
    r = db._risk_payload()
    assert r["capital_flows_tracked"] is True
    assert r["drawdown_basis"] == "nav"
    assert r["drawdown_caveat"] == ""


def test_a_withdrawal_no_longer_reads_as_a_loss_on_the_panel(monkeypatch, tmp_path):
    """End to end through the real payload: the exact case that was
    mislabelled."""
    from hermes_trader.agents import capital_flows as cf
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    now = int(time.time() * 1000)
    day = 86_400_000
    vals = [(now - 5 * day, 200.0), (now - 4 * day, 190.0), (now - 3 * day, 180.0),
            (now - 2 * day, 170.0), (now - day, 160.0)]
    monkeypatch.setattr(db, "_read_log_lines", lambda: [_hb(t, e) for t, e in vals])
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    cf.mark_recording_started(now - 9 * day)
    # every dollar of the decline was withdrawn, not lost
    cf.append_flows([{"ts": t, "usd": -10.0, "kind": "withdraw", "key": f"w{i}"}
                     for i, (t, _) in enumerate(vals[1:], 1)])
    r = db._risk_payload()
    assert r["drawdown_basis"] == "nav"
    assert r["max_drawdown_pct"] == pytest.approx(0.0, abs=0.01), (
        "a pure withdrawal is still being reported as a drawdown")


# ── it reaches the page ──────────────────────────────────────────────────────

@pytest.fixture
def client():
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)


def test_risk_endpoint_serves(client, curve):
    body = client.get("/api/dashboard/risk").json()
    assert body["peak_equity"] == 200.0
    assert set(body) >= {"drawdown_pct", "max_drawdown_pct", "win_rate",
                         "fee_drag_pct", "kill_used_frac", "mode"}


def test_the_landing_page_actually_renders_the_risk_numbers(client):
    """The panel has to be ON the page, not merely available at an endpoint —
    an endpoint nobody renders is exactly the gap this work closed."""
    body = client.get("/").text
    for marker in ("risk-band", "risk-dd", "risk-maxdd", "risk-win",
                   "risk-fees", "risk-kill", "risk-caveat", "refreshRisk"):
        assert marker in body, f"landing page lost {marker}"


def test_the_page_carries_an_off_switch_that_says_what_it_does(client):
    body = client.get("/").text
    assert "kill-btn" in body and "/api/agent/stop" in body
    assert "CONFIRM STOP" in body, "a live kill needs a confirm step"
    assert "does not flatten" in body, (
        "the button must say it leaves positions open — an off switch people "
        "misread is worse than no off switch")


def test_the_stop_endpoint_is_closed_in_both_configurations(monkeypatch):
    """The off switch must be unreachable without the operator token in BOTH
    states, and the two states are different code paths:

      - token configured, none or a wrong one supplied -> 401
      - no HERMES_OPERATOR_TOKEN at all                 -> 503, surface disabled

    The second is the one that matters on a fresh box. It fails CLOSED, and this
    test exists to keep it that way: a future refactor that made a missing token
    mean "no auth required" would open a live kill switch to the internet.

    The root conftest loads the real .env.local into os.environ, so the token is
    usually present here and absent on CI. Both are pinned explicitly rather
    than left to the environment.
    """
    from hermes_trader.server import app as real_app
    client = TestClient(real_app)

    monkeypatch.setenv("HERMES_OPERATOR_TOKEN", "a-token-that-is-set")
    assert client.post("/api/agent/stop").status_code == 401
    assert client.post("/api/agent/stop",
                       headers={"X-Operator-Token": "wrong"}).status_code == 401

    monkeypatch.delenv("HERMES_OPERATOR_TOKEN", raising=False)
    r = client.post("/api/agent/stop")
    assert r.status_code == 503, "a missing token must close the surface, not open it"
    assert client.post("/api/agent/stop",
                       headers={"X-Operator-Token": "anything"}).status_code == 503


def test_a_partial_dex_blip_does_not_invent_a_drawdown(monkeypatch):
    """A HIP-3 fetch failure reports main-dex-only equity — a one-tick crater.
    The drawdown must be computed over the FILTERED curve, or every blip would
    print a terrifying and entirely fictional loss on the front page."""
    now = int(time.time() * 1000)
    day = 86_400_000
    vals = [100.0, 150.0, 200.0, 20.0, 200.0, 190.0]   # 20.0 is the bad read
    monkeypatch.setattr(db, "_read_log_lines",
                        lambda: [_hb(now - (len(vals) - i) * day, v)
                                 for i, v in enumerate(vals)])
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    r = db._risk_payload()
    assert r["max_drawdown_pct"] == pytest.approx(-5.0, abs=0.01), (
        "the 20.0 degraded read leaked into the drawdown")


# ── the structural dust floor ────────────────────────────────────────────────

def test_the_executor_refuses_to_trade_a_dust_account():
    """`mode: LIVE` against $0.03 was not a safe state just because the loop
    happened to be stopped — a restart would have fired orders HL rejects under
    its ~$10 minimum. The floor is enforced in the executor, the single choke
    point every order passes through, so no book can route around it."""
    from hermes_trader.agents.executor import (MIN_TRADABLE_EQUITY_USD,
                                               min_tradable_equity)
    assert min_tradable_equity({}) == MIN_TRADABLE_EQUITY_USD
    assert MIN_TRADABLE_EQUITY_USD >= 10.0, (
        "the floor has to clear HL's own minimum order size or it does nothing")


def test_a_bad_floor_value_falls_back_instead_of_unlocking_trading():
    """A typo in a config file must never silently disable the guard."""
    from hermes_trader.agents.executor import (MIN_TRADABLE_EQUITY_USD as M,
                                               min_tradable_equity)
    assert min_tradable_equity({"min_tradable_equity_usd": "oops"}) == M
    assert min_tradable_equity({"min_tradable_equity_usd": None}) == M
    assert min_tradable_equity({"min_tradable_equity_usd": -1}) == M
    # a deliberate, valid override still works
    assert min_tradable_equity({"min_tradable_equity_usd": 5}) == 5.0


def test_the_panel_reports_blocked_rather_than_a_green_live_badge(monkeypatch, curve):
    """A LIVE badge over an account that cannot place a trade is exactly the
    flattering misreport this panel exists to stop."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: [
        {"ts": 1, "event": "loop_heartbeat", "equity": 0.03, "daily_pnl": 0.0,
         "open_positions": 0, "available": 0.03}])
    monkeypatch.setattr(db, "read_agent_config", lambda: {"mode": "LIVE"})
    r = db._risk_payload()
    assert r["mode"] == "LIVE"
    assert r["can_trade"] is False
    assert r["min_tradable_equity"] >= 10.0


def test_a_funded_account_reads_tradable(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: [
        {"ts": 1, "event": "loop_heartbeat", "equity": 500.0, "daily_pnl": 0.0,
         "open_positions": 0, "available": 500.0}])
    monkeypatch.setattr(db, "_closed_trades_payload", lambda limit=20: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {"mode": "LIVE"})
    assert db._risk_payload()["can_trade"] is True


def test_the_page_shows_the_blocked_state(client):
    body = client.get("/").text
    assert "BLOCKED" in body and "can_trade" in body


# ── a degraded feed is not a quiet market ────────────────────────────────────

def test_a_blind_scan_is_not_trustworthy(monkeypatch):
    """When Hyperliquid bulk-500s, every unreadable coin returns an empty candle
    list and reads downstream as 'no signal' — indistinguishable from a market
    we looked at and passed on. Entering on that is entering on an absence of
    evidence."""
    from hermes_trader.agents import perception as P
    monkeypatch.setattr(P, "_last_scan_integrity",
                        {"ts": 1, "markets": 100, "gaps": 80, "gap_frac": 0.8,
                         "errors": 0})
    assert P.scan_is_trustworthy() is False


def test_a_healthy_scan_is_trustworthy(monkeypatch):
    from hermes_trader.agents import perception as P
    monkeypatch.setattr(P, "_last_scan_integrity",
                        {"ts": 1, "markets": 100, "gaps": 3, "gap_frac": 0.03,
                         "errors": 0})
    assert P.scan_is_trustworthy() is True


def test_a_cold_start_is_not_treated_as_degraded(monkeypatch):
    """The gate catches a DEGRADED feed. It must not block the first scan of a
    fresh process, which has no integrity record yet."""
    from hermes_trader.agents import perception as P
    monkeypatch.setattr(P, "_last_scan_integrity",
                        {"ts": 0, "markets": 0, "gaps": 0, "gap_frac": 0.0,
                         "errors": 0})
    assert P.scan_is_trustworthy() is True


def test_the_threshold_is_the_one_that_was_already_worth_warning_about():
    from hermes_trader.agents.perception import MAX_SCAN_GAP_FRAC
    assert MAX_SCAN_GAP_FRAC == 0.25


def test_feed_health_reaches_the_risk_payload(monkeypatch, curve):
    from hermes_trader.agents import perception as P
    monkeypatch.setattr(P, "_last_scan_integrity",
                        {"ts": 1, "markets": 40, "gaps": 30, "gap_frac": 0.75,
                         "errors": 0})
    feed = db._risk_payload()["feed"]
    assert feed["trustworthy"] is False and feed["gaps"] == 30


def test_the_page_renders_the_degraded_feed_state(client):
    body = client.get("/").text
    assert "risk-feed" in body
    assert "not a quiet market" in body, (
        "the page must name the failure mode, not just show a number")
