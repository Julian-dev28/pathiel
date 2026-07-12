"""Web UI rebuild (2026-07-12): landing one-pager, /activity journal, /news page.

Gate tests — fixture log lines + ledger rows only, no network, no live state.
Covers: event classification + the T1/T2/T3 editorial hierarchy (cards /
one-liners / coalesced groups), the 6h session strip, activity filters, the
books table payload, the news payload, page rendering (exact how-it-works
copy, self-contained assets), and the deleted /config + /operator pages (404
is the expected behavior).
"""

import json
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_trader import dashboard as db


# ── fixtures ─────────────────────────────────────────────────────────────────

RESEARCH_NEWS = {
    "ts": 100, "event": "research", "coin": "SOL", "verdict": "PASS",
    "confidence": 0.42, "reasoning": "no clean multi-TF trend — skip",
    "ai_brain_provider": "openrouter", "web_search_used": True,
    "web_search_citations": ["https://example.com/sol-article"],
    "news_risk": "elevated", "entry_px": 0, "stop_px": 0, "tp_px": 0,
}
RESEARCH_PLAIN = {
    "ts": 150, "event": "research", "coin": "BTC", "verdict": "LONG",
    "confidence": 0.71, "reasoning": "clean breakout",
    "ai_brain_provider": "openrouter", "web_search_used": False,
    "web_search_citations": [], "news_risk": "none",
    "entry_px": 100000.0, "stop_px": 98000.0, "tp_px": 104000.0,
}
EXEC_BLOCKED = {
    "ts": 200, "event": "execute", "coin": "VIRTUAL", "side": "long",
    "executed": False, "ai_verdict": "LONG", "entry_via": "ai",
    "detail": ["total notional $395 would exceed 1000% of equity ($390)"],
    "blocked_by": ["total notional $395 would exceed 1000% of equity ($390)"],
    "size_usd": None, "entry_px": None,
}
EXEC_OK = {
    "ts": 300, "event": "execute", "coin": "GRASS", "side": "long",
    "executed": True, "ai_verdict": "PASS", "entry_via": "override",
    "detail": "493436405964", "blocked_by": None, "book": "news_catalyst",
    "size_usd": 25.07, "entry_px": 0.40835, "stop_px": 0.394, "tp_px": 0.4316,
    "regime": "up",
}
DSL_EXIT = {
    "ts": 400, "event": "dsl_exit", "coin": "GRASS", "side": "long",
    "leverage": 3, "reason": "floor_breach (1x consec, floor=0.42)",
    "unrealized_pct": 2.5077, "leveraged_pct": 7.523, "executed": True,
    "fill_px": 0.41841, "entry_px": 0.40835, "realized_spot_pct": 2.4636,
    "realized_pnl_pct": 7.2407, "fees_pct": 0.15,
}
BOOK_EVT = {
    "ts": 500, "event": "neg_funding_fade", "shadow": True,
    "signals": 1, "opened": 0,
    "skipped": {"held": 1, "claimed": 0, "dedup": 0, "blocked": 0},
    "candidates": [{"coin": "TRUMP", "side": "short", "funding_8h": -0.1008,
                    "influx_vol_x": 2.0, "entry_ref_px": 1.6542}],
}
BOOK_ALIAS_EVT = {
    "ts": 600, "event": "xs_rebalance", "regime": "low",
    "longs": ["BTC", "ETH"], "shorts": ["XPL"], "close": [],
}
BOOK_OPEN_EVT = {
    "ts": 650, "event": "book_open", "book": "rally_exhaustion",
    "coin": "XPL", "side": "short", "notional_usd": 20.0,
}
UNKNOWN_EVT = {"ts": 700, "event": "mystery_event", "foo": "bar", "n": 3}

ALL_EVENTS = [RESEARCH_NEWS, RESEARCH_PLAIN, EXEC_BLOCKED, EXEC_OK,
              DSL_EXIT, BOOK_EVT, BOOK_ALIAS_EVT, BOOK_OPEN_EVT, UNKNOWN_EVT]

FIXTURE_CONFIG = {
    "xs_momentum": {"enabled": True, "k_per_leg": 4},
    "extreme_fade": {"enabled": True, "equity_fraction": 0.4, "leverage": 1},
    "rally_exhaustion": {"enabled": True, "notional_usd": 20.0, "leverage": 1},
    "crash_continue_div_short": {"enabled": True, "shadow_only": False,
                                 "notional_usd": 20.0, "leverage": 1},
    "engulf_short": {"enabled": True, "shadow_only": False,
                     "notional_usd": 20.0, "leverage": 1},
    "neg_funding_fade": {"enabled": True, "shadow_only": True,
                         "notional_usd": 20.0, "leverage": 1},
    "funding_spike_short": {"enabled": True, "shadow_only": False,
                            "notional_usd": 20.0, "leverage": 1},
    "majors_swing": {"enabled": True, "shadow_only": False,
                     "equity_fraction": 0.25, "leverage": 25},
    "young_listings": {"enabled": True, "shadow_only": True,
                       "notional_usd": 15.0, "leverage": 1},
    "unlock_short": {"enabled": True, "shadow_only": False,
                     "notional_usd": 20.0, "leverage": 1},
    "news_catalyst": {"enabled": False, "notional_usd": 20.0, "leverage": 1},
    "mover_recorders": {"pass_live": {"enabled": True, "shadow_only": False,
                                      "notional_usd": 20.0, "leverage": 1}},
}


@pytest.fixture(autouse=True)
def _clear_ttl_cache():
    with db._TTL_CACHE_LOCK:
        db._TTL_CACHE.clear()
    yield
    with db._TTL_CACHE_LOCK:
        db._TTL_CACHE.clear()


@pytest.fixture()
def client():
    app = FastAPI()
    db.register_routes(app)
    return TestClient(app)


# ── activity classification ──────────────────────────────────────────────────

def test_activity_newest_first_and_types(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    out = db._activity_payload(limit=50)
    evs = out["events"]
    assert [e["ts"] for e in evs] == sorted((e["ts"] for e in ALL_EVENTS), reverse=True)
    by_ts = {e["ts"]: e for e in evs}
    assert by_ts[100]["type"] == "research"
    assert by_ts[100]["web_search_used"] is True
    assert by_ts[100]["citations"] == ["https://example.com/sol-article"]
    assert by_ts[100]["provider"] == "openrouter"
    assert by_ts[200]["type"] == "execute" and by_ts[200]["executed"] is False
    assert by_ts[200]["gates"] == ["total notional $395 would exceed 1000% of equity ($390)"]
    assert by_ts[300]["type"] == "execute" and by_ts[300]["book"] == "news_catalyst"
    assert by_ts[400]["type"] == "close" and by_ts[400]["pnl_pct"] == pytest.approx(7.2407)
    assert by_ts[500]["type"] == "book" and by_ts[500]["shadow"] is True
    assert by_ts[500]["candidates"][0]["coin"] == "TRUMP"
    assert by_ts[650]["type"] == "book" and by_ts[650]["subtype"] == "open"
    assert set(out["books"]) == db._KNOWN_BOOK_NAMES


def test_activity_alias_maps_to_book(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    out = db._activity_payload(book="xs_momentum")
    assert len(out["events"]) == 1
    e = out["events"][0]
    assert e["type"] == "book" and e["book"] == "xs_momentum"
    # xs_rebalance-specific keys land in `extra` for the kv renderer
    assert e["extra"]["longs"] == ["BTC", "ETH"]


def test_activity_filters_and_limit(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    research = db._activity_payload(etype="research")["events"]
    assert [e["coin"] for e in research] == ["BTC", "SOL"]
    nff = db._activity_payload(book="neg_funding_fade")["events"]
    assert len(nff) == 1 and nff[0]["ts"] == 500
    # execute events filter by their book field too
    news = db._activity_payload(book="news_catalyst")["events"]
    assert [e["ts"] for e in news] == [300]
    limited = db._activity_payload(limit=2)["events"]
    assert len(limited) == 2 and limited[0]["ts"] == 700


def test_tier_hierarchy(monkeypatch):
    """T1 = card-worthy, T2 = one-liner, T3 = coalesce-only, per operator order."""
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    by_ts = {e["ts"]: e for e in db._activity_payload(limit=50)["events"]}
    assert by_ts[100]["tier"] == 2          # research PASS → compact line
    assert by_ts[150]["tier"] == 1          # research LONG → card
    assert by_ts[200]["tier"] == 1          # blocked execute → card
    assert by_ts[300]["tier"] == 1          # filled execute → card
    assert by_ts[400]["tier"] == 1          # dsl close → card
    assert by_ts[500]["tier"] == 2          # book cycle with signals → line
    assert by_ts[600]["tier"] == 2          # xs_rebalance (content, no counts) → line
    assert by_ts[650]["tier"] == 1          # book_open → card
    assert by_ts[700]["tier"] == 2          # unknown shape → line


def test_quiet_book_cycles_coalesce(monkeypatch):
    quiet = {"ts": 10, "event": "neg_funding_fade", "shadow": True,
             "signals": 0, "opened": 0,
             "skipped": {"held": 0, "claimed": 0}, "candidates": []}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [quiet])
    e = db._activity_payload()["events"][0]
    assert e["tier"] == 3 and e["gkey"] == "quiet|neg_funding_fade"


def test_gate_skips_group_by_coin_and_reason(monkeypatch):
    """The operator's paste: the same CASHCAT+SNX pair rendered ~40 times.
    That exact tape must resolve to exactly TWO group keys."""
    events = []
    for i in range(40):
        # varying digits (bar counts, scores) must NOT split the groups
        events.append({"ts": 1000 + i * 120_000, "event": "entry_preflight",
                       "coin": "CASHCAT", "reason": f"history floor ({2 + i % 3}d<60d)"})
        events.append({"ts": 1001 + i * 120_000, "event": "entry_preflight",
                       "coin": "SNX", "reason": f"runner_gate_blocked (score={50 + i})"})
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    out = db._activity_payload(limit=200)["events"]
    assert len(out) == 80 and all(e["tier"] == 3 for e in out)
    assert len({e["gkey"] for e in out}) == 2


def test_scans_bucket_hourly_and_heartbeats_tier3(monkeypatch):
    h = 3_600_000
    events = [
        {"ts": 1 * h + 100, "event": "scan", "triggers": 2},
        {"ts": 1 * h + 200, "event": "scan", "triggers": 0},
        {"ts": 2 * h + 100, "event": "scan", "triggers": 1},
        {"ts": 2 * h + 200, "event": "loop_heartbeat", "equity": 39.91,
         "open_positions": 0},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    out = db._activity_payload()["events"]
    scans = [e for e in out if e["type"] == "scan"]
    assert {e["gkey"] for e in scans} == {"scan|1", "scan|2"}
    hb = next(e for e in out if e["type"] == "heartbeat")
    assert hb["tier"] == 3


def test_loop_start_never_carries_the_config_dump(monkeypatch):
    evt = {"ts": 5, "event": "loop_start", "scan_interval": 60, "min_score": 20,
           "config": {"mode": "LIVE", "leverage": 15, "coin_blocklist": [],
                      "dsl_exit": {"max_loss_pct": 2.5}, "equity_fraction_per_trade": 0.5}}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [evt])
    e = db._activity_payload()["events"][0]
    assert e["type"] == "system" and e["tier"] == 2
    assert e["fields"] == {"mode": "LIVE", "scan_interval": 60, "min_score": 20}
    assert "leverage" not in json.dumps(e)   # the dump must never reach the page


def test_session_strip(monkeypatch):
    now = int(time.time() * 1000)
    events = [   # chronological, one event outside the 6h window first
        {"ts": now - 7 * 3_600_000, "event": "scan", "triggers": 99},
        {"ts": now - 9000, "event": "dsl_exit", "leveraged_pct": -2.2},
        {"ts": now - 8000, "event": "dsl_exit", "realized_pnl_pct": 7.2},
        {"ts": now - 7000, "event": "entry_preflight", "coin": "C"},
        {"ts": now - 6000, "event": "execute", "executed": False},
        {"ts": now - 5000, "event": "execute", "executed": True},
        {"ts": now - 4000, "event": "research", "coin": "X"},
        {"ts": now - 3000, "event": "scan", "triggers": 1},
        {"ts": now - 2000, "event": "scan", "triggers": 3},
        {"ts": now - 1000, "event": "loop_heartbeat", "equity": 39.91, "open_positions": 1},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    s = db._session_strip()
    assert s["scans"] == 2 and s["candidates"] == 4      # 99 is outside the window
    assert s["researched"] == 1 and s["opened"] == 1 and s["closed"] == 2
    assert s["blocks"] == 2                              # blocked execute + preflight
    assert s["realized_pnl_pct"] == pytest.approx(5.0)
    assert s["equity"] == 39.91 and s["open_positions"] == 1


def test_activity_since_returns_only_newer(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    out = db._activity_payload(since_ts=500)
    assert [e["ts"] for e in out["events"]] == [700, 650, 600]
    assert db._activity_payload(since_ts=700)["events"] == []
    # since + filter compose: only newer events of the requested type
    only_book = db._activity_payload(etype="book", since_ts=500)["events"]
    assert [e["ts"] for e in only_book] == [650, 600]


def test_unknown_event_graceful_key_value(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: [UNKNOWN_EVT])
    e = db._activity_payload()["events"][0]
    assert e["type"] == "other" and e["name"] == "mystery_event"
    assert e["fields"] == {"foo": "bar", "n": 3}


def test_dsl_exit_falls_back_to_estimated_pnl(monkeypatch):
    evt = {k: v for k, v in DSL_EXIT.items() if k != "realized_pnl_pct"}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [evt])
    e = db._activity_payload()["events"][0]
    assert e["pnl_pct"] == pytest.approx(7.523)  # leveraged_pct fallback


def test_ai_close_classified_as_close(monkeypatch):
    evt = {"ts": 10, "event": "ai_close", "coin": "LIT", "executed": True,
           "reasoning": "structure flipped"}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [evt])
    e = db._activity_payload()["events"][0]
    assert e["type"] == "close" and e["source"] == "ai_close"
    assert e["reason"] == "structure flipped"


# ── books payload ────────────────────────────────────────────────────────────

def test_books_payload_statuses_and_sizes(monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: dict(FIXTURE_CONFIG))
    rows = {r["name"]: r for r in db._books_payload()}
    assert set(rows) == db._KNOWN_BOOK_NAMES and len(rows) == 11
    assert rows["young_listings"]["status"] == "shadow"
    assert rows["engulf_short"]["status"] == "live"
    assert rows["rally_exhaustion"]["status"] == "live"   # no shadow_only key → live
    assert rows["news_catalyst"]["status"] == "off"       # enabled: false
    assert rows["mover_pass"]["status"] == "live"         # nested pass_live config
    assert rows["xs_momentum"]["size"] == "4/leg basket"
    assert rows["extreme_fade"]["size"] == "0.4x eq @ 1x"
    assert rows["majors_swing"]["size"] == "0.25x eq @ 25x"
    assert rows["unlock_short_runin"]["size"] == "$20 @ 1x"
    assert all(r["thesis"] for r in rows.values())
    # mover_pass trades LIVE now — thesis must say so, not "recorder"
    assert "PASSed" in rows["mover_pass"]["thesis"]
    assert "recorder" not in rows["mover_pass"]["thesis"].lower()


def test_books_payload_missing_config_is_off(monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    rows = db._books_payload()
    assert len(rows) == 11 and all(r["status"] == "off" for r in rows)


# ── news payload ─────────────────────────────────────────────────────────────

def _write_news_ledger(rows):
    from hermes_trader.agents import shadow_ledger
    path = shadow_ledger._book_path("news_catalyst")
    if os.path.exists(path):
        os.remove(path)
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_news_payload_newest_first_breaking_flagged(monkeypatch):
    _write_news_ledger([
        {"ts": 1000, "book": "news_catalyst", "coin": "VIRTUAL", "side": "long",
         "entry_ref_px": 0.63671,
         "meta": {"breaking": False, "n_recent": 1, "surge_x": 0.51,
                  "shadow": True, "top3_titles": ["Robinhood AI agent - Cryptonews"]}},
        {"ts": 2000, "book": "news_catalyst", "coin": "GRASS", "side": "long",
         "entry_ref_px": 0.39463,
         "meta": {"breaking": True, "n_recent": 3, "surge_x": 2.4,
                  "shadow": True, "top3_titles": ["Big headline", "Second headline"]}},
    ])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [RESEARCH_NEWS, RESEARCH_PLAIN])
    payload = db._news_payload(limit=10)
    items = payload["items"]
    assert [i["coin"] for i in items] == ["GRASS", "VIRTUAL"]   # newest first
    assert items[0]["breaking"] is True and items[1]["breaking"] is False
    assert items[0]["n_recent"] == 3 and items[0]["surge_x"] == 2.4
    assert items[0]["titles"] == ["Big headline", "Second headline"]
    # research context: only the event with citations / non-none news_risk
    ctx = payload["research_context"]
    assert [c["coin"] for c in ctx] == ["SOL"]
    assert ctx[0]["citations"] == ["https://example.com/sol-article"]
    assert ctx[0]["news_risk"] == "elevated"


def test_news_payload_empty_ledger(monkeypatch):
    _write_news_ledger([])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    payload = db._news_payload()
    assert payload["items"] == [] and payload["research_context"] == []


# ── pages + endpoints ────────────────────────────────────────────────────────

HOW_IT_WORKS = (
    "Autonomous trading agent on Hyperliquid perpetuals — crypto, equities, "
    "commodities. Every minute the engine scans 60+ markets for statistical "
    "triggers (volume spikes, breakouts, momentum bursts), runs a free TA "
    "filter, and only spends AI tokens on confirmed setups. Trades clear 11 "
    "risk gates, size by half-Kelly, and exit through a two-phase dynamic "
    "stop-loss (loss protection → profit locking with one-way trailing "
    "floor). Live on one wallet. Not financial advice."
)


def test_landing_page_copy_and_removed_chrome(client):
    r = client.get("/")
    assert r.status_code == 200
    assert HOW_IT_WORKS in r.text                 # exact operator copy
    assert "live books" in r.text
    assert "hermes-modal" not in r.text           # terminal window removed
    assert "operator-toggle" not in r.text        # operator chrome removed
    assert "matrix-feed" not in r.text            # old sidebar feed removed
    assert 'data-nav="/activity"' in r.text and 'data-nav="/news"' in r.text
    # nav is DASHBOARD · ACTIVITY · NEWS — config/operator tabs deleted
    assert 'data-nav="/config"' not in r.text
    assert 'data-nav="/operator"' not in r.text
    # token entry moved to the landing footer (localStorage only)
    assert "op-token-btn" in r.text


def test_config_and_operator_pages_are_gone(client):
    """Operator order 2026-07-12: /config and /operator are deleted — 404 is
    the EXPECTED behavior, and no page links to them anymore."""
    assert client.get("/config").status_code == 404
    assert client.get("/operator").status_code == 404
    assert client.get("/api/dashboard/config").status_code == 404
    assert client.get("/api/dashboard/operator/trackers").status_code == 404
    assert client.post("/api/dashboard/operator/terminal",
                       json={"command": "status"}).status_code == 404
    for path in ("/", "/activity", "/news"):
        page = client.get(path).text
        assert 'data-nav="/config"' not in page, path
        assert 'data-nav="/operator"' not in page, path


def test_all_pages_render_and_are_self_contained(client):
    for path in ("/", "/activity", "/news"):
        r = client.get(path)
        assert r.status_code == 200, path
        for banned in ("unpkg.com", "fonts.googleapis", "fonts.gstatic",
                       "https://cdn"):
            assert banned not in r.text, f"{path} references CDN: {banned}"


def test_activity_endpoint_filters(client, monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    r = client.get("/api/dashboard/activity?type=book&book=neg_funding_fade")
    assert r.status_code == 200
    data = r.json()
    assert len(data["events"]) == 1
    assert data["events"][0]["book"] == "neg_funding_fade"
    assert "types" in data and "book" in data["types"]
    # session strip rides along on every activity response
    assert "session" in data and data["session"]["window_h"] == 6


def test_activity_endpoint_since_bypasses_cache(client, monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: list(ALL_EVENTS))
    r = client.get("/api/dashboard/activity?since=500")
    assert r.status_code == 200
    assert [e["ts"] for e in r.json()["events"]] == [700, 650, 600]
    # incremental polls carry a fresh `since` every time — they must NOT
    # accumulate one-shot keys in the TTL cache
    with db._TTL_CACHE_LOCK:
        assert not any(k.startswith("activity:") for k in db._TTL_CACHE)


def test_landing_has_equity_curve(client):
    r = client.get("/")
    assert 'id="equity-chart"' in r.text
    assert "/static/chart.umd.min.js" in r.text
    assert "/static/chartjs-adapter-date-fns.min.js" in r.text
    assert "equity-curve?range_s=" in r.text     # wired to the live endpoint


def test_stream_pages_flow_and_respect_reduced_motion(client):
    act = client.get("/activity").text
    news = client.get("/news").text
    for page in (act, news):
        assert "prefers-reduced-motion" in page   # CSS-only animations, opt-out honored
        assert "ev-enter" in page                 # arrival animation class
    assert "function ingest" in act               # polls merge/prepend, no full re-render
    assert "flash-green" in act and "flash-red" in act   # trade emphasis
    assert "session-strip" in act                 # pinned last-6h answer
    assert "quiet cycle" in act                   # signal-less book runs coalesce
    assert "steady" in act                        # unchanged-heartbeat divider
    assert "nothing actionable since" in act      # empty-tape mode copy
    assert "blocked <span" in act                 # gate groups: "COIN blocked ×N"
    assert "quiet stream" in news                 # sparse-ledger empty state copy
    assert "breaking-pulse" in news               # stronger pulse on breaking items
    assert "quiet read" in news                   # non-breaking reads coalesce per coin/hour


def test_books_endpoint(client, monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: dict(FIXTURE_CONFIG))
    r = client.get("/api/dashboard/books")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 11
    assert {"name", "status", "size", "thesis"} <= set(rows[0])


def test_news_endpoint(client, monkeypatch):
    _write_news_ledger([
        {"ts": 1, "coin": "VIRTUAL", "side": "long",
         "meta": {"breaking": True, "n_recent": 2, "surge_x": 1.5,
                  "top3_titles": ["t1"]}},
    ])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    r = client.get("/api/dashboard/news?limit=5")
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["coin"] == "VIRTUAL"
    assert data["items"][0]["breaking"] is True
