"""Every display read of the account goes through one shared snapshot, and
background desk work runs at background priority. The wiring half of
tests/test_account_snapshot.py.

2026-10-19, the stall: the dashboard's summary (every 6 s), positions (every
10 s), the trade banner, "Your size" and the desk analytics each did their own
fresh read (12 requests, 24 weight on a 2 weight/s budget), reads took 6-12 s,
polls queued behind each other and held the browser's six connections, so a
click on Desks waited. Offline: the venue is a fake.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from pathiel.client import account_snapshot as snap
from pathiel.client import rate_limit

ADDR = "0x" + "a1" * 20
STATE = {"equity": 15.21, "available": 0.0, "withdrawable": 0.0,
         "withdrawable_aggregated": 15.0, "dex_equity": {"": 0.01, "xyz": 15.2},
         "queried_dexes": {"", "xyz"}, "spot_usdc": 0.0, "asset_positions": [
             {"position": {"coin": "xyz:BRENTOIL", "szi": "0.38", "entryPx": "105.5",
                           "positionValue": "40.1", "unrealizedPnl": "0.0", "marginUsed": "13.4",
                           "leverage": {"type": "cross", "value": 3}}}]}


class Venue:
    def __init__(self, delay=0.0):
        self.reads = 0
        self.delay = delay
        self.lock = threading.Lock()

    def __call__(self, user, include_hip3, hip3_dexes=None, strict=False):
        with self.lock:
            self.reads += 1
        time.sleep(self.delay)
        return dict(STATE)


def _account():
    from contracts.accounts.models import Account
    return Account(id="a1", label="Main", main_address=ADDR, mode="trade", source="wallet_sign",
                   is_active=True, created_at=datetime.now(timezone.utc),
                   verified_at=datetime.now(timezone.utc))


@pytest.fixture
def venue(monkeypatch):
    v = Venue()
    monkeypatch.setattr(snap, "FETCH", v)
    monkeypatch.setattr(snap, "LIST_DEXES", lambda: ["xyz", "flx"])
    from services.accounts import view
    monkeypatch.setattr(view, "get_effective_active_account", _account)
    import pathiel.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "resolve_user_address", lambda: ADDR)
    from pathiel import dashboard
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: ADDR)
    monkeypatch.setattr(dashboard, "read_position_snapshot", lambda max_age_s=120.0: None)
    dashboard._POSITIONS_CACHE.update(ts=0.0, data=[])
    return v


def test_every_display_read_shares_one_account_read(venue):
    from pathiel import dashboard
    from services.accounts import summary
    from services.risk import api as risk_api
    from services.strategies import analytics

    assert summary.get_active_summary().equity_usd == pytest.approx(15.21)   # banner, dashboard, risk
    assert dashboard._positions_payload_uncached()[0]["coin"] == "xyz:BRENTOIL"
    assert analytics._read_account_snapshot().equity_usd == pytest.approx(15.21)
    assert risk_api._equity_from_env_account() == pytest.approx(15.21)
    assert venue.reads == 1


def test_the_mcp_still_gets_a_new_read_every_call(venue):
    from services.accounts import summary
    summary.get_active_summary()
    summary.get_active_summary(max_age_s=0)
    summary.get_active_snapshot(max_age_s=0)
    assert venue.reads == 3


def test_the_mcp_asks_for_a_new_read():
    """scripts/pathiel-mcp-server.py: a tool result is acted on, so it never
    takes the shared, possibly minutes-old snapshot."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts" / "pathiel-mcp-server.py").read_text()
    assert "get_active_summary(max_age_s=0)" in src
    assert "get_active_snapshot(max_age_s=0)" in src
    assert "get_active_snapshot()" not in src and "get_active_summary()" not in src


def test_concurrent_page_polls_do_not_queue_behind_a_slow_venue(venue, monkeypatch):
    """The regression test for the stall. With a 0.5 s venue, a dozen polls
    used to take 0.5 s each and make a dozen reads. Now: once warm, every
    poll answers from the snapshot at once and at most one re-read runs."""
    from fastapi.testclient import TestClient
    import pathiel.server as srv
    from services.auth import integration
    for m in integration._DEPLOYMENT_MARKERS:
        monkeypatch.delenv(m, raising=False)
    monkeypatch.setenv("PATHIEL_ACCOUNT_FRESH_S", "0")     # every poll finds it stale
    client = TestClient(srv.app, client=("127.0.0.1", 50000))
    venue.delay = 0.5
    assert client.get("/api/accounts/active/summary").status_code == 200   # the one cold read
    assert venue.reads == 1

    paths = ["/api/accounts/active/summary", "/api/dashboard/positions"] * 6

    def timed(path):
        t0 = time.monotonic()
        r = client.get(path)
        return r.status_code, time.monotonic() - t0

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(timed, paths))
    assert all(code == 200 for code, _ in results), results
    assert max(dt for _, dt in results) < 0.35, results
    assert snap.wait_idle(ADDR, timeout=5)
    assert venue.reads <= 3, f"{venue.reads} reads for 12 polls: they are not shared"


def test_server_live_equity_reads_the_shared_snapshot(monkeypatch):
    """server.py's equity (the agent state view, order sizing) is a display
    read like the others. That its handlers call it off the event loop is
    tests/test_no_blocking_on_the_event_loop.py's job."""
    import pathiel.server as srv
    asked = []

    def fake_state(user, include_hip3=True, **kw):
        asked.append((user, include_hip3))
        return {"equity": 15.21}
    monkeypatch.setattr(snap, "account_state", fake_state)
    monkeypatch.setattr(srv, "resolve_user_address", lambda: ADDR)
    monkeypatch.setattr(srv, "read_agent_config", lambda: {"enable_hip3": True})
    assert srv._live_equity() == pytest.approx(15.21)
    assert asked == [(ADDR, True)]


# ── desks: background rebuilds yield, clicked refreshes do not ─────────────

def test_the_desk_worker_rebuilds_at_background_priority(monkeypatch):
    from services.strategies import facade, pipeline
    seen = []
    monkeypatch.setattr(pipeline, "build_and_cache_analytics",
                        lambda spec, **kw: seen.append((spec.id, rate_limit.in_background())))
    with facade._LOCK:
        facade._PENDING.clear()
        facade._PENDING.append("btc")
        facade._WORKER_RUNNING = True
    facade._drain()
    assert seen == [("btc", True)]
    assert rate_limit.in_background() is False


def test_a_clicked_refresh_reads_the_market_at_foreground_priority(monkeypatch):
    from services.strategies import facade, pipeline
    seen = []

    def build(spec, **kw):
        seen.append(rate_limit.in_background())
        raise RuntimeError("stop here")                   # the market stage is all this checks
    monkeypatch.setattr(pipeline, "build_and_cache_analytics", build)
    monkeypatch.setattr(pipeline, "_log_usage", lambda *a, **k: None)
    job = facade.refresh("btc")
    assert job.state == "error" and seen == [False]


def test_a_background_desk_build_stays_background_on_its_fetch_threads():
    from contracts.strategies.models import AccountSnapshot, DeskSpec, Instrument
    from services.strategies import analytics as A
    from services.strategies.tests.conftest import make_candles

    spec = DeskSpec(id="t1", name="T", tagline="t",
                    instruments=[Instrument(coin="A", label="A", role="primary"),
                                 Instrument(coin="B", label="B", role="secondary")])
    seen = []

    def fetcher(coin, interval, count):
        seen.append((coin, threading.current_thread().name, rate_limit.in_background()))
        return make_candles([100.0 + 0.1 * i for i in range(40)], wiggle=0.01)

    with rate_limit.background_priority():
        A.build_analytics(spec, candle_fetcher=fetcher, meta_fetcher=lambda: {},
                          account_fetcher=lambda: AccountSnapshot())
    assert sorted(c for c, _, _ in seen) == ["A", "B"]
    assert all(bg for _, _, bg in seen), seen
    assert any(name != threading.current_thread().name for _, name, _ in seen), "ran on pool threads"
