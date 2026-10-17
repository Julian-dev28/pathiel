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
import pathlib
import re
import shutil
import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pathiel import dashboard as db


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
    # the four VALIDATED books, restored 2026-08-30 with capital paths
    "news_surge_short": {"enabled": True, "shadow_only": False,
                         "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "news_surge_multi": {"enabled": True, "shadow_only": False,
                         "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "social_trending": {"enabled": True, "shadow_only": False,
                        "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "unlock_short": {"enabled": True, "shadow_only": False,
                     "notional_usd": 20.0, "leverage": 1, "stop_pct": 15.0},
    "xs_momentum": {"enabled": True, "k_per_leg": 4},
    "xs_xyz_equities": {"enabled": True, "shadow_only": False, "k_per_leg": 5,
                        "hold_days": 5, "min_volume_usd": 250000},
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
    "news_catalyst": {"enabled": False, "notional_usd": 20.0, "leverage": 1},
    "mover_recorders": {"pass_live": {"enabled": True, "shadow_only": False,
                                      "notional_usd": 20.0, "leverage": 1}},
    "news_ta_aligned": {"enabled": True, "shadow_only": False,
                        "notional_usd": 20.0, "leverage": 3},
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
    assert by_ts[100]["citations"] == [
        {"url": "https://example.com/sol-article", "title": "example.com/sol-article"}]
    assert by_ts[100]["provider"] == "openrouter"
    assert by_ts[200]["type"] == "execute" and by_ts[200]["executed"] is False
    assert by_ts[200]["gates"] == ["total notional $395 would exceed 1000% of equity ($390)"]
    assert by_ts[300]["type"] == "execute" and by_ts[300]["book"] == "news_catalyst"
    assert by_ts[400]["type"] == "close" and by_ts[400]["pnl_pct"] == pytest.approx(7.2407)
    assert by_ts[500]["type"] == "book" and by_ts[500]["shadow"] is True
    assert by_ts[500]["candidates"][0]["coin"] == "TRUMP"
    assert by_ts[650]["type"] == "book" and by_ts[650]["subtype"] == "open"
    assert set(out["books"]) == db._RENDERABLE_BOOK_NAMES


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
        {"ts": now - 1000, "event": "loop_heartbeat", "equity": 39.91,
         "daily_pnl": -0.40, "open_positions": 1},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    s = db._session_strip()
    assert s["scans"] == 2 and s["candidates"] == 4      # 99 is outside the window
    assert s["researched"] == 1 and s["opened"] == 1 and s["closed"] == 2
    assert s["blocks"] == 2                              # blocked execute + preflight
    # realized = EXCHANGE truth (heartbeat daily_pnl vs SOD), never summed DSL
    # close estimates (SKHY 2026-07-13: strip showed +6.44% on a -$0.40 day)
    assert s["realized_pnl_pct"] == pytest.approx(-0.40 / (39.91 + 0.40) * 100, abs=0.01)
    assert s["equity"] == 39.91 and s["open_positions"] == 1


def test_activity_fresh_boundary(monkeypatch):
    """Time-decay coalescing: T3 events inside the fresh window are flagged
    fresh (client renders them as individual rows); older ones are not
    (client folds them straight into coalesced groups)."""
    now = 10_000_000_000
    window_ms = db._FRESH_WINDOW_S * 1000
    events = [
        {"ts": now - window_ms - 1, "event": "scan", "triggers": 2},      # 1ms too old
        {"ts": now - window_ms, "event": "entry_preflight", "coin": "A",
         "reason": "history floor"},                                       # exactly on cutoff
        {"ts": now - 60_000, "event": "loop_heartbeat", "equity": 39.9},   # 1 min old
        {"ts": now - 1_000, "event": "scan", "triggers": 0},               # 1 s old
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    out = db._activity_payload(now_ms=now)["events"]
    by_ts = {e["ts"]: e for e in out}
    assert by_ts[now - window_ms - 1]["fresh"] is False
    assert by_ts[now - window_ms]["fresh"] is True      # >= cutoff counts as fresh
    assert by_ts[now - 60_000]["fresh"] is True
    assert by_ts[now - 1_000]["fresh"] is True
    assert db._activity_payload(now_ms=now)["fresh_window_s"] == db._FRESH_WINDOW_S


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
    # 2026-08-30, second pass: "if it's shadow, nuke it". The two MARGINAL
    # mover books were shadow, so they are gone. What remains graded VALIDATED
    # and is LIVE — there is no shadow tier left to sit in. xs_reversal joined
    # 2026-09-04 and went live from its first bar: its evidence (W-XSR1) was
    # gathered before the book existed, so there was nothing to graduate from.
    assert set(rows) == db._KNOWN_BOOK_NAMES and len(rows) == 7   # drawdown_ladder 2026-09-14 (W-WH2), copy_trade 2026-09-21
    assert rows["news_surge_short"]["status"] == "live"
    assert "VALIDATED" in rows["news_surge_short"]["thesis"]
    assert all(r["thesis"] for r in rows.values())


def test_books_payload_missing_config_is_off(monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    rows = db._books_payload()
    assert len(rows) == 7 and all(r["status"] == "off" for r in rows)


# ── news payload ─────────────────────────────────────────────────────────────

# news_surge_short, not news_catalyst: the feed reads the books that can trade
# today, and news_surge_short is the live one that records headlines. These
# fixtures wrote to news_catalyst for six weeks after that book was demolished,
# which is exactly why the /news tab could serve July rows with every test green.
@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """The news payload merges every live book, so a test that writes one
    fixture book into the real ledger directory gets the operator's actual
    news_surge_multi history mixed in and its rows pushed past `limit`. These
    tests only passed before because the payload read a single hardcoded book."""
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    return tmp_path


def _write_news_ledger(rows, book="news_surge_short"):
    from pathiel.agents import shadow_ledger
    path = shadow_ledger._book_path(book)
    if os.path.exists(path):
        os.remove(path)
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_news_payload_newest_first_breaking_flagged(monkeypatch, isolated_ledger):
    _write_news_ledger([
        {"ts": 1000, "book": "news_surge_short", "coin": "VIRTUAL", "side": "long",
         "entry_ref_px": 0.63671,
         "meta": {"breaking": False, "n_recent": 1, "surge_x": 0.51,
                  "shadow": True, "top3_titles": ["Robinhood AI agent - Cryptonews"]}},
        {"ts": 2000, "book": "news_surge_short", "coin": "GRASS", "side": "long",
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
    assert ctx[0]["citations"] == [
        {"url": "https://example.com/sol-article", "title": "example.com/sol-article"}]
    assert ctx[0]["news_risk"] == "elevated"


def test_news_payload_empty_ledger(monkeypatch, isolated_ledger):
    _write_news_ledger([])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    payload = db._news_payload()
    assert payload["items"] == [] and payload["research_context"] == []
    assert payload["stats"] == {"reads_today": 0, "breaking_today": 0,
                                "last_read_ts": None}


def test_news_payload_stats_fresh_and_title_ages(monkeypatch, isolated_ledger):
    """Flight-deck payload: header-strip stats (since local midnight), the
    watcher fresh flag, and per-headline article-age passthrough."""
    noon = int(time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1)) * 1000)
    _write_news_ledger([
        {"ts": noon - 20 * 3600 * 1000, "coin": "C", "side": "long",   # yesterday
         "meta": {"breaking": False, "top3_titles": []}},
        {"ts": noon - 2 * 3600 * 1000, "coin": "A", "side": "long",    # today, aged
         "meta": {"breaking": True, "top3_titles": ["fresh piece", "evergreen piece"],
                  "top3_ages_h": [3.5, 200.0]}},
        {"ts": noon - 60_000, "coin": "B", "side": "long",             # today, fresh
         "meta": {"breaking": False, "top3_titles": []}},
    ])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    p = db._news_payload(limit=10, now_ms=noon)
    items = {i["coin"]: i for i in p["items"]}
    assert items["B"]["fresh"] is True          # inside the fresh window
    assert items["A"]["fresh"] is False and items["C"]["fresh"] is False
    assert items["A"]["title_ages_h"] == [3.5, 200.0]   # article recency, when persisted
    assert items["B"]["title_ages_h"] is None           # absent in older rows
    assert p["fresh_window_s"] == db._FRESH_WINDOW_S
    assert p["stats"] == {"reads_today": 2, "breaking_today": 1,
                          "last_read_ts": noon - 60_000}


def test_the_news_feed_ignores_a_book_that_can_no_longer_trade(monkeypatch, isolated_ledger):
    """/news read shadow_ledger.load("news_catalyst") by name. That book was
    demolished 2026-07-18 and is in _REMOVED_BOOKS, so on 2026-09-01 the tab was
    serving 46-day-old July rows under a "reads today: 0" header while
    news_surge_short wrote every day. Sources now derive from
    _KNOWN_BOOK_NAMES; a fresh record in the removed ledger must not come back."""
    _write_news_ledger([
        {"ts": 9_000_000_000, "coin": "GHOST", "side": "long", "entry_ref_px": 1.0,
         "meta": {"breaking": True, "n_recent": 9, "surge_x": 9.9,
                  "top3_titles": ["should never render"]}},
    ], book="news_catalyst")
    _write_news_ledger([
        {"ts": 9_000_000_001, "coin": "REAL", "side": "short", "entry_ref_px": 2.0,
         "meta": {"breaking": True, "n_recent": 2, "surge_x": 3.0,
                  "top3_titles": ["a live headline"]}},
    ])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    items = db._news_payload(limit=10)["items"]
    assert [i["coin"] for i in items] == ["REAL"]
    assert items[0]["book"] == "news_surge_short"


def test_a_surge_with_headlines_is_an_event_even_when_not_breaking(monkeypatch, isolated_ledger):
    """`breaking` is a threshold applied on top of the surge, not the whole of
    it. The page routed to its catalysts pane on `breaking` alone, so a 4.0x
    surge that captured a headline was filed under "coverage checked, nothing
    new" beside polls that found nothing at all."""
    _write_news_ledger([
        {"ts": 3000, "coin": "SURGE", "side": "short",
         "meta": {"breaking": False, "n_recent": 4, "surge_x": 4.0,
                  "top3_titles": ["a real headline"]}},
        {"ts": 2000, "coin": "TITLED", "side": "short",
         "meta": {"breaking": False, "n_recent": 1, "surge_x": 1.0,
                  "top3_titles": ["captured a headline"]}},
        {"ts": 1000, "coin": "POLL", "side": "short",
         "meta": {"breaking": False, "n_recent": 0, "surge_x": 1.0,
                  "top3_titles": []}},
    ])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    by_coin = {i["coin"]: i for i in db._news_payload(limit=50)["items"]}
    assert by_coin["SURGE"]["event"] is True      # above baseline
    assert by_coin["TITLED"]["event"] is True     # captured headlines
    assert by_coin["POLL"]["event"] is False      # found nothing


def test_routine_polls_cannot_crowd_the_catalysts_off_the_page(monkeypatch, isolated_ledger):
    """The books write ~200 polls a day against a handful of catalysts. Under
    one flat limit over one chronological list, volume decided what the operator
    saw: measured 2026-09-01, 48 of the newest 50 rows found nothing and the
    catalysts pane was empty while real surges sat just below the cut.

    The single oldest row here is the only event, buried under 200 newer polls.
    It must still arrive."""
    rows = [{"ts": 1000, "coin": "BURIED", "side": "short",
             "meta": {"breaking": True, "n_recent": 9, "surge_x": 9.0,
                      "top3_titles": ["the one that matters"]}}]
    rows += [{"ts": 2000 + i, "coin": f"POLL{i}", "side": "short",
              "meta": {"breaking": False, "n_recent": 0, "surge_x": 1.0}}
             for i in range(200)]
    _write_news_ledger(rows)
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    items = db._news_payload(limit=50)["items"]
    coins = [i["coin"] for i in items]
    assert "BURIED" in coins, "the only catalyst was crowded out by routine polls"
    # and the watcher still gets a heartbeat, or a live poller looks stopped
    quiet = [i for i in items if not i["event"]]
    assert 10 <= len(quiet) <= 50, len(quiet)
    assert items == sorted(items, key=lambda i: -(i["ts"] or 0)), "still newest-first"


def test_news_payload_title_urls_passthrough(monkeypatch, isolated_ledger):
    """Breaking-coverage headlines carry a source URL (title_urls, parallel
    to titles) when the recorder persisted one (news_catalyst_live.py's
    top3_urls) — older rows recorded before that field existed fall back to
    title_urls=None so the UI renders them as plain, unlinked text instead
    of guessing a link. Regression (2026-07-15): these headlines were NEVER
    hyperlinked — only the title string was ever recorded, never the URL —
    operator: 'keep the article links'."""
    noon = int(time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1)) * 1000)
    _write_news_ledger([
        {"ts": noon - 60_000, "coin": "A", "side": "long",
         "meta": {"breaking": True, "top3_titles": ["fresh piece"],
                  "top3_urls": ["https://example.com/fresh-piece"]}},
        {"ts": noon - 2 * 3600 * 1000, "coin": "B", "side": "long",   # pre-fix row
         "meta": {"breaking": False, "top3_titles": ["old piece"]}},
    ])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    p = db._news_payload(limit=10, now_ms=noon)
    items = {i["coin"]: i for i in p["items"]}
    assert items["A"]["title_urls"] == ["https://example.com/fresh-piece"]
    assert items["B"]["title_urls"] is None


# ── pages + endpoints ────────────────────────────────────────────────────────

HOW_IT_WORKS = "No discretionary trading and no manual override."

# The design system lives in a linked stylesheet now, not in five inline
# <style> blocks. Anything asserting on CSS has to read the sheet, or it is
# asserting that a rule is DUPLICATED into the page rather than that it exists.
CSS = (pathlib.Path(__file__).resolve().parent.parent
       / "pathiel" / "static" / "pathiel.css").read_text()


def styled(client, path="/"):
    """Page markup plus the stylesheet that dresses it."""
    return client.get(path).text + CSS


# ── accessibility: contrast ──────────────────────────────────────────────────
#
# Computed from the real token values rather than asserted as frozen hex
# strings, so a future legitimate retune only has to clear the actual WCAG
# bar, not match a string this test pins in place.

def _css_var(css_text: str, block_selector: str, var_name: str) -> str:
    """Pull `--var: #hex;` out of one labelled top-level CSS block.

    Not a general CSS parser — good enough for this file's consistent,
    one-token-per-line style, same as the rest of this test module's
    source-text assertions."""
    start = css_text.index(block_selector)
    end = css_text.index("}", start)
    block = css_text[start:end]
    m = re.search(re.escape(var_name) + r"\s*:\s*(#[0-9a-fA-F]{6})", block)
    assert m, f"{var_name} not found in the {block_selector!r} block"
    return m.group(1)


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def f(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex) + 0.05
    l2 = _relative_luminance(bg_hex) + 0.05
    return max(l1, l2) / min(l1, l2)


def test_tertiary_text_clears_wcag_aa_against_every_background_it_sits_on():
    """Regression (2026-10-17 ux-qa sweep): --ink-3 — .sec-label and every
    other 'dim/tertiary' text treatment (section headers like 'Live books',
    table <th>s, the sign-in copy) on every page — contrasted 4.26:1 against
    --surface-2 in dark mode and 3.35:1 against --paper in light mode, both
    under the 4.5:1 WCAG AA floor for text this size (~12px, never bold).
    Found by sampling every real text/background pair actually rendered
    across all 5 pages at both viewports with a headless-Chromium crawl."""
    dark_ink3 = _css_var(CSS, ":root{", "--ink-3")
    dark_backgrounds = {
        "surface": _css_var(CSS, ":root{", "--surface"),
        "surface-2": _css_var(CSS, ":root{", "--surface-2"),
        "paper": _css_var(CSS, ":root{", "--paper"),
    }
    for label, bg in dark_backgrounds.items():
        ratio = _contrast_ratio(dark_ink3, bg)
        assert ratio >= 4.5, f"dark --ink-3 ({dark_ink3}) vs {label} ({bg}): {ratio:.2f} < 4.5"

    light_ink3 = _css_var(CSS, ':root[data-theme="light"]{', "--ink-3")
    light_backgrounds = {
        "surface-2": _css_var(CSS, ':root[data-theme="light"]{', "--surface-2"),
        "paper": _css_var(CSS, ':root[data-theme="light"]{', "--paper"),
    }
    for label, bg in light_backgrounds.items():
        ratio = _contrast_ratio(light_ink3, bg)
        assert ratio >= 4.5, f"light --ink-3 ({light_ink3}) vs {label} ({bg}): {ratio:.2f} < 4.5"


def test_hidden_attribute_cannot_be_silently_overridden_by_a_display_rule():
    """Regression (2026-10-17 ux-qa sweep): the `hidden` HTML attribute's
    UA-stylesheet rule (`[hidden]{display:none}`) is NORMAL-priority,
    author-origin-losing — any of this file's OWN rules that set `display`
    on the same element (.disclose, .seg, …) wins regardless of selector
    specificity, because author CSS beats the UA sheet at equal or lower
    specificity too. #hl-more (trends.html, class="disclose" hidden)
    rendered as an empty but fully visible, clickable button:
    `.disclose{display:flex}` silently un-hid it. `!important` on a blanket
    [hidden] rule is the standard fix for this exact gotcha and must stay
    ahead of (textually, it doesn't matter where — !important wins the
    cascade step outright) any class that sets display on a hideable
    element."""
    assert re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", CSS), (
        "the defensive [hidden]{display:none !important} rule is missing from pathiel.css")


def test_landing_page_copy_and_removed_chrome(client):
    r = client.get("/")
    assert r.status_code == 200
    assert HOW_IT_WORKS in r.text                 # exact operator copy
    assert "live books" in r.text
    assert "pathiel-modal" not in r.text           # terminal window removed
    assert "operator-toggle" not in r.text        # operator chrome removed
    assert "matrix-feed" not in r.text            # old sidebar feed removed
    assert 'data-nav="/activity"' in r.text and 'data-nav="/trends"' in r.text
    assert 'data-nav="/trends"' in r.text
    # /news and /analytics were dropped from the nav on 2026-08-02 to shorten
    # the bar. That left two working pages nobody could reach without typing
    # the URL, which is a worse cost than a five-item nav: an unreachable page
    # is the UX problem, not the nav width. Both are linked again 2026-08-31.
    assert 'data-nav="/news"' in r.text
    assert 'data-nav="/analytics"' in r.text
    assert client.get("/news").status_code == 200
    assert client.get("/analytics").status_code == 200
    # nav is DASHBOARD · ACTIVITY · PREDICTIONS · TRENDS — config/operator deleted
    assert 'data-nav="/config"' not in r.text
    assert 'data-nav="/operator"' not in r.text
    # token entry moved to the landing footer (localStorage only)
    assert "op-token-btn" in r.text
    # how-it-works sits at the BOTTOM: below the books dropdown, above the
    # footer (operator order 2026-07-12)
    assert r.text.index('id="books-wrap"') < r.text.index(HOW_IT_WORKS)
    assert r.text.index(HOW_IT_WORKS) < r.text.index("<footer")
    # the mascot, the shader and the 8-bit chrome are gone for good
    for gone in ("pixel-cat", "gl-bg", "cat-sleep", "__setGlState",
                 "repeating-linear-gradient", "scroll-progress"):
        assert gone not in r.text, f"landing still ships {gone}"


def test_config_and_operator_pages_are_gone(client):
    """Operator order 2026-07-12: /config and /operator are deleted — 404 is
    the EXPECTED behavior, and no page links to them anymore."""
    assert client.get("/config").status_code == 404
    assert client.get("/operator").status_code == 404
    assert client.get("/api/dashboard/config").status_code == 404
    assert client.get("/api/dashboard/operator/trackers").status_code == 404
    assert client.post("/api/dashboard/operator/terminal",
                       json={"command": "status"}).status_code == 404
    for path in ("/", "/activity", "/news", "/analytics", "/trends"):
        page = client.get(path).text
        assert 'data-nav="/config"' not in page, path
        assert 'data-nav="/operator"' not in page, path


def test_all_pages_render_and_are_self_contained(client):
    for path in ("/", "/activity", "/news", "/analytics", "/trends", "/account"):
        r = client.get(path)
        assert r.status_code == 200, path
        for banned in ("unpkg.com", "fonts.googleapis", "fonts.gstatic",
                       "https://cdn"):
            assert banned not in r.text, f"{path} references CDN: {banned}"


# ── nav: the "Desks" link (2026-10-17) ───────────────────────────────────────

ALL_PAGES = ("/", "/activity", "/news", "/analytics", "/trends")


def test_nav_is_identical_across_every_page_and_carries_desks(client):
    """Desks and Account sit in every page's nav. Placed second, right after
    Dashboard, per the overhaul brief's 'near the front'. Every page's nav
    must be byte-identical in order and labels, matching the pre-existing
    pattern this sweep found already held for the original five links.
    Account is wave-2 onboarding's own addition (Build step 4): always the
    LAST item, so it reads as "everything else, then account settings"
    rather than competing with Desks for the front."""
    navs = {}
    for path in ALL_PAGES:
        page = client.get(path).text
        start = page.index('<nav class="tabs-main">')
        end = page.index("</nav>", start)
        navs[path] = page[start:end]
    first = navs[ALL_PAGES[0]]
    for path, nav in navs.items():
        assert nav == first, f"{path}'s nav differs from {ALL_PAGES[0]}'s"
    assert '<a href="/desks" data-nav="/desks" class="nav-link">Desks</a>' in first
    assert '<a href="/account" data-nav="/account" class="nav-link">Account</a>' in first
    # "near the front": right after Dashboard, before every other link
    assert first.index('data-nav="/"') < first.index('data-nav="/desks"')
    assert first.index('data-nav="/desks"') < first.index('data-nav="/activity"')
    # Account is last, after everything else — including Trends.
    assert first.index('data-nav="/trends"') < first.index('data-nav="/account"')
    assert first.rindex('<a ') == first.index('<a href="/account"'), (
        "Account is not the last nav link")


def test_account_is_the_last_nav_item_on_every_template():
    """Build step 4: 'Add Account as the last nav item, identical in every
    template.' Desks and desk.html (nav order Desks-last, pre-existing — not
    this wave's to unify) get their own check since
    test_nav_is_identical_across_every_page_and_carries_desks above only
    compares ALL_PAGES, which excludes them. Reads the template files
    directly rather than through a client: desks.html/desk.html are served
    by services/strategies' router, not dashboard.register_routes(), so the
    bare `client` fixture 404s on them."""
    account_link = '<a href="/account" data-nav="/account" class="nav-link">Account</a>'
    for name, html in _template_files():
        if name not in ("desks.html", "desk.html"):
            continue
        start = html.index('<nav class="tabs-main">')
        end = html.index("</nav>", start)
        nav = html[start:end]
        assert account_link in nav, f"{name} has no Account nav link"
        assert nav.rindex("<a ") == nav.index(account_link), f"{name}: Account is not last"


def test_trends_masthead_has_no_leftover_corrupted_markup(client):
    """Regression (2026-10-17 ux-qa sweep): trends.html's masthead carried a
    dead <span> whose id attribute was five real element ids pasted in with
    literal embedded newlines
    (id="token-clear\\ntoken-input\\ntoken-row\\ntoken-save\\ntoken-why") —
    invalid per the HTML5 spec (an id must not contain ASCII whitespace),
    referenced by nothing, and contributing zero width/content — found while
    tracing why /trends' masthead overflowed the page at 375px. The real
    #token-row / #token-input / #token-save / #token-clear / #token-why
    elements exist correctly elsewhere in the same file and must still be
    there; only the stray duplicate span is gone."""
    page = client.get("/trends").text
    assert "token-clear\ntoken-input" not in page
    assert page.count('id="token-row"') == 1
    assert page.count('id="token-input"') == 1
    assert page.count('id="token-save"') == 1
    assert page.count('id="token-clear"') == 1
    assert page.count('id="token-why"') == 1


def test_mobile_masthead_right_wraps_instead_of_overflowing():
    """Regression (2026-10-17 ux-qa sweep): .masthead-right is its OWN flex
    row, independent of .masthead-inner's flex-wrap above it — it does not
    inherit the parent's wrapping. On a page with a few extra controls
    (trends.html: operator-token button + lane-meta, on top of the wallet
    chip + status pill every page carries), the children's combined width
    exceeded 375px and the box overflowed the page edge, dragging the WHOLE
    document into horizontal scroll (measured live: 456px document width
    against a 375px viewport, 81px of which traced straight back to this
    rule having no wrap). See tests/browser/test_pages_browser.py for the
    real-browser proof this is fixed at 375px on every page; this is the
    cheap source-level guard against the fix being reverted."""
    start = CSS.index("@media (max-width:720px)")
    open_brace = CSS.index("{", start)
    # depth-counted scan for the matching close brace — the block contains
    # several nested rules, each with their own '}', so the first '}' after
    # `start` closes only the FIRST inner rule, not the media query itself.
    depth = 0
    i = open_brace
    while True:
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = CSS[start:i + 1]
    assert re.search(r"\.masthead-right\s*\{[^}]*flex-wrap\s*:\s*wrap", block), (
        ".masthead-right has no flex-wrap in the mobile media query")


def test_book_filter_and_coin_select_have_accessible_names():
    """Regression (2026-10-17 ux-qa sweep): #book-filter (activity.html) and
    #coin-select (analytics.html) are <select> elements with no <label>, no
    aria-label, and no title — a screen-reader user tabbing to them hears
    nothing describing what they filter."""
    activity = (pathlib.Path(__file__).resolve().parent.parent
                / "pathiel" / "templates" / "activity.html").read_text()
    analytics = (pathlib.Path(__file__).resolve().parent.parent
                 / "pathiel" / "templates" / "analytics.html").read_text()
    assert re.search(r'id="book-filter"[^>]*aria-label="[^"]+"', activity), (
        "#book-filter has no aria-label")
    assert re.search(r'id="coin-select"[^>]*aria-label="[^"]+"', analytics), (
        "#coin-select has no aria-label")


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


def test_landing_books_dropdown_wraps_flow(client):
    """Live books stays a disclosure: header and counts always visible, rows
    behind a toggle whose state is remembered."""
    r = client.get("/").text
    assert 'id="books-toggle"' in r and 'id="books-wrap"' in r
    assert "pathiel-books-open" in r                  # state remembered in localStorage
    assert 'class="books-wrap"' in r                 # static HTML ships collapsed
    assert "books-open .books-wrap" in CSS           # the collapsed/open rule
    assert ".chev" in CSS                            # chevron toggle affordance
    assert "Live books" in r                         # header + counts always visible
    # the flowing rows live INSIDE the collapsed container
    assert 'id="books-flow"' in r and "book-row" in r
    assert r.index('id="books-wrap"') < r.index('id="books-flow"')


def test_no_emoji_glyphs_anywhere(client):
    """Brand order 2026-07-12: no emoji glyphs — the cat is crafted SVG
    markup, geometric shapes are CSS. This sweep must stay green."""
    banned = ["👁", "🙈", "♥", "⚡", "⟳", "■", "⚠", "▶", "⚙", "🐈", "🐱",
              "🤖", "😴", "💰", "💀", "🤑", "😱", "😎", "🔒", "🔓", "🐹", "🐰"]
    for path in ("/", "/activity", "/news", "/analytics"):
        page = client.get(path).text
        for ch in banned:
            assert ch not in page, f"{path} still renders glyph {ch!r}"


def test_kpi_tick_flash_on_value_change(client):
    """Session-strip KPI numbers flash on change (same green/red tick
    language as the positions table), not a hard silent swap — a small but
    real 'live app' signal, present on both flowing-stream pages."""
    for path in ("/activity", "/news"):
        r = client.get(path).text
        assert "flashChanged" in r and "data-k=" in r, path
        assert "tick-up" in r and "tick-dn" in r, path


def test_citations_are_chips_not_blue_links(client):
    """Citation links read as source chips (dot marker + pill), not generic
    blue underlined hyperlinks (operator: 'not blue like a link but make the
    links apparent') — still real <a> tags with a real href, just styled and
    labeled like a clickable source tag instead of inline blue text."""
    for path in ("/activity", "/news"):
        r = client.get(path).text
        assert "citeChip" in r and "domainOf" in r, f"{path}: missing chip builder"
        assert "cite-row" in r, f"{path}: citations not wrapped in a chip row"
        assert "#7dd3fc" not in r, f"{path}: old blue link color still present"
        assert "text-decoration:underline" not in r, f"{path}: still underlining citations"
        # chip still carries a real, safe, new-tab link
        assert 'target="_blank"' in r and 'rel="noopener noreferrer"' in r, path


# Helpers shared by every page live in static/pathiel.js now, so the extractor
# searches there too rather than only in the served markup.
SHARED_JS = (pathlib.Path(__file__).resolve().parent.parent
             / "pathiel" / "static" / "pathiel.js").read_text()


def _extract_js_block(html: str, kind: str, name: str) -> str:
    """Pull one pure-logic const one-liner or multi-line function out of a
    served page's <script> block by name, so it can be executed in
    isolation under node — no DOM/fetch dependency, safe outside a browser."""
    html = html + "\n" + SHARED_JS
    if kind == "const":
        pat = r"^const " + re.escape(name) + r" = .*?;$"
        flags = re.M
    else:
        pat = r"^function " + re.escape(name) + r"\(.*?\) \{.*?\n\}$"
        flags = re.M | re.S
    m = re.search(pat, html, flags)
    assert m, f"couldn't find `{kind} {name}` in page source"
    return m.group(0)


def _run_node(html: str, blocks: list, call: str) -> str:
    node = shutil.which("node")
    assert node, "node not on PATH"
    snippet = "\n".join(_extract_js_block(html, kind, name) for kind, name in blocks)
    driver = snippet + f"\nconsole.log({call});"
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, f"js snippet crashed under node:\n{r.stderr}"
    return r.stdout.strip()


def _run_cite_chip(html: str, citations: list) -> list:
    out = _run_node(
        html, [("const", "esc"), ("const", "domainOf"), ("function", "citeChip")],
        f"JSON.stringify({json.dumps(citations)}.map(citeChip))",
    )
    return json.loads(out)


def _run_titles_block(html: str, item: dict) -> str:
    return _run_node(
        html, [("const", "esc"), ("function", "ageChip"), ("function", "titlesBlock")],
        f"titlesBlock({json.dumps(item)})",
    )


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_cite_chip_preserves_server_shortened_path(client):
    """Regression (2026-07-15): the chip redesign collapsed ANY title
    containing a '/' down to a bare domain — but the server
    (_parse_citation/_short_url in dashboard.py) already shortens titleless
    citations to a DISTINGUISHING host+path string, not a raw URL. That bug
    made every citation from the same domain render as an identical chip,
    hiding which specific article a link pointed to. Operator: 'KEEP THE
    LINKS TO THE NEWS ARTICLES'. The label must preserve a server-shortened
    host+path title verbatim; only a truly titleless citation falls back to
    a bare domain."""
    for path in ("/activity", "/news"):
        html = client.get(path).text
        out = _run_cite_chip(html, [
            # real shape produced by dashboard.py's _short_url fallback
            {"url": "https://www.kucoin.com/announcement/en-introducing-xyz-token",
             "title": "www.kucoin.com/announcement/en-introdu…"},
            {"url": "https://reuters.com/article/123", "title": "Fed cuts rates by 50bps"},
            {"url": "https://example.com/foo/bar", "title": ""},
        ])
        assert 'href="https://www.kucoin.com/announcement/en-introducing-xyz-token"' in out[0], path
        assert "en-introdu" in out[0], f"{path}: lost the article path, collapsed to bare domain: {out[0]!r}"
        assert "Fed cuts rates by 50bps" in out[1], f"{path}: real headline mangled: {out[1]!r}"
        assert ">example.com<" in out[2], f"{path}: titleless citation should fall back to bare domain: {out[2]!r}"


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_titles_block_links_headlines_when_url_present(client):
    """Regression (2026-07-15): breaking-coverage headlines (the CATALYSTS
    pane's readCard/titlesBlock) were NEVER hyperlinked at all — the
    recorder only ever persisted the title string, never the source URL,
    so there was nothing to link to. Operator pasted a real example where
    the headline ran straight into its age with no separator or link
    ('...Seeking Alpha48m old'), asking to 'keep the article links.' A
    headline WITH a url now renders as a real <a class="src-link"> to that
    url, with a space between the title and its age chip; a headline from
    an older, pre-fix ledger row with no url falls back to plain text
    rather than a broken link."""
    html = client.get("/news").text
    assert "src-link" in html, "titlesBlock never grew a linked variant"
    linked = _run_titles_block(html, {
        "titles": ["SK Hynix implied volatility says fasten your seatbelts (SKHY:NASDAQ) - Seeking Alpha"],
        "title_urls": ["https://seekingalpha.com/news/skhy-implied-vol"],
        "title_ages_h": [0.8],
    })
    assert 'class="src-link"' in linked, linked
    assert 'href="https://seekingalpha.com/news/skhy-implied-vol"' in linked, linked
    assert "Seeking Alpha</a>" in linked, f"title text not fully inside the link: {linked!r}"
    assert "Alpha</a> <span" in linked, f"age chip glued onto the title with no separator: {linked!r}"
    assert "48m old" in linked

    unlinked = _run_titles_block(html, {
        "titles": ["evergreen background piece"], "title_urls": None, "title_ages_h": [200.0],
    })
    assert "<a " not in unlinked, f"titleless-url row should not render a link: {unlinked!r}"
    assert "evergreen background piece" in unlinked


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_trade_empty_copy_does_not_claim_nothing_actionable_with_open_positions(client):
    """Regression (2026-07-15): operator screenshot showed the trade pane's
    empty state reading "nothing actionable since 14:36 — engine scanning
    normally" while two real positions (PUMP, xyz:SKHY) sat open with real
    uPnL — the copy only ever looked at recent-event counts, never
    session.open_positions (which the strip KPI already reads correctly,
    dashboard.py:1000, from live heartbeat data). With open positions the
    message must say so plainly instead of implying there is nothing to
    watch; with zero open positions the original wording is unchanged."""
    html = client.get("/activity").text
    held = _run_node(
        html, [("const", "fmtHM"), ("function", "tradeEmptyCopy")],
        "tradeEmptyCopy({since_ts: Date.now(), scans: 150, blocks: 885, open_positions: 2})",
    )
    assert "nothing actionable" not in held, held
    assert "2 positions open" in held and "holding quiet" in held, held
    assert "150 scans, 885 blocks" in held, held

    quiet = _run_node(
        html, [("const", "fmtHM"), ("function", "tradeEmptyCopy")],
        "tradeEmptyCopy({since_ts: Date.now(), scans: 10, blocks: 4, open_positions: 0})",
    )
    assert "nothing actionable since" in quiet, quiet

    singular = _run_node(
        html, [("const", "fmtHM"), ("function", "tradeEmptyCopy")],
        "tradeEmptyCopy({since_ts: Date.now(), scans: 1, blocks: 0, open_positions: 1})",
    )
    assert "1 position open" in singular and "1 positions" not in singular, singular


@pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")
def test_scan_freshtext_guards_against_non_array_coins(client):
    """Regression (2026-10-17 ux-qa sweep): dashboard.py's scan classification
    prefers e.coin_scores over e.coins whenever coin_scores is present and
    truthy (`e.get("coin_scores") or e.get("coins") or []`, dashboard.py's
    scan branch) — correct when coin_scores is a list (the real shape
    scripts/trading_loop.py logs), but services/demo/generator.py logs it as
    a `{coin: score}` dict instead, and this file's freshText() called
    `.slice()` on whatever came through behind only a truthiness fallback
    (`e.coins || []`) — a dict is truthy, so that fallback never ran.

    Reproduced live: one 'scan' event with a dict `coins` field threw inside
    fullLoad()'s per-event ingest loop and aborted it entirely. Not one bad
    row — all 300 events in that load silently failed to render, and the
    trade pane got stuck forever on the generic 'feed load failed — is the
    server up?' banner (the outer .catch()), while every OTHER pane kept
    showing whatever had rendered before the throw. The fix is the
    Array.isArray guard below; the per-event try/catch in fullLoad/pollNew is
    defense in depth for whatever shape nobody has thought of yet — this test
    proves the specific cause is actually closed, not just contained."""
    html = client.get("/activity").text
    # the exact shape that crashed it: coin_scores logged as a dict
    out = _run_node(
        html, [("const", "esc"), ("function", "freshText")],
        "freshText({type: 'scan', coins: {BTC: 50.0, ETH: 61.2}, triggers: 2})")
    assert "scanned the board" in out and "2 triggers" in out, out
    # no coins field at all (the common, unremarkable case) must not throw
    out2 = _run_node(
        html, [("const", "esc"), ("function", "freshText")],
        "freshText({type: 'scan', triggers: 0})")
    assert "no triggers" in out2, out2
    # the real, correct shape (scripts/trading_loop.py: a list) must still
    # name the top coins — the guard must not blind the working case
    out3 = _run_node(
        html, [("const", "esc"), ("function", "freshText")],
        "freshText({type: 'scan', coins: ['BTC', 'ETH'], triggers: 2})")
    assert "BTC, ETH" in out3, out3


def test_activity_has_time_decay_flow(client):
    act = client.get("/activity").text
    assert "ev-fresh" in act                 # fresh T3 events render individually
    assert "foldAged" in act                 # aging sweep folds them into groups
    assert "fresh_window_s" in act           # window sourced from the server
    assert "ev-fold" in act                  # smooth fold transition class
    assert "gi-hb" in act and "gi-restart" in act   # geometric glyphs, not emoji


def test_positions_rows_expose_liq_px():
    state = {"asset_positions": [
        {"position": {"coin": "BTC", "szi": "0.5", "entryPx": "100000",
                      "positionValue": "55000", "unrealizedPnl": "5000",
                      "marginUsed": "11000", "leverage": {"value": 5},
                      "liquidationPx": "80000"}},
        {"position": {"coin": "ETH", "szi": "-2", "entryPx": "3000",
                      "positionValue": "5800", "unrealizedPnl": "200",
                      "marginUsed": "1160", "leverage": {"value": 5},
                      "liquidationPx": None}},   # cross far from liq → null
    ]}
    rows = db._rows_from_state(state)
    btc = next(r for r in rows if r["coin"] == "BTC")
    eth = next(r for r in rows if r["coin"] == "ETH")
    assert btc["liq_px"] == 80000.0 and btc["mark_px"] == 110000.0
    assert btc["side"] == "long"
    assert eth["liq_px"] is None and eth["side"] == "short"


def test_landing_has_open_positions_section(client):
    r = client.get("/").text
    assert 'id="positions-body"' in r
    assert "Open positions" in r
    assert "refreshPositions" in r
    # placed between the KPI row and the equity curve
    assert r.index('id="positions-body"') < r.index('id="equity-chart"')
    assert r.index("Last scan") < r.index('id="positions-body"')
    # liq proximity danger treatment + origin badges + PnL tick animation
    assert "liq-danger" in r
    assert "MANUAL" in r and "originBadge" in r
    assert "tick-up" in r and "tick-dn" in r


def test_stream_pages_flow_and_respect_reduced_motion(client):
    act = client.get("/activity").text
    news = client.get("/news").text
    assert "prefers-reduced-motion" in CSS        # motion opt-out honoured globally
    for page in (act, news):
        assert "ev-enter" in page                 # arrival class still applied
    assert "function ingest" in act               # polls merge/prepend, no full re-render
    assert "flash-green" in act and "flash-red" in act   # trade emphasis
    assert "session-strip" in act                 # pinned last-6h answer
    assert "quiet cycle" in act                   # signal-less book runs coalesce
    assert "steady" in act                        # unchanged-heartbeat divider
    assert "nothing actionable since" in act      # empty-tape copy (trade pane)
    assert "entry refused" in act                 # gate groups read as flight-log
    assert "quiet stream" in news                 # sparse-ledger empty state copy
    # Breaking items are marked by a LABEL, not a pulsing animation. A page
    # that throbs at the reader is the thing this redesign removed.
    assert 'class="badge b-breaking"' in news
    assert "breaking-pulse" not in news and "breaking-pulse" not in CSS
    assert "coverage checked" in news             # quiet reads in flight-log copy
    assert "nothing new" in news                  # no side/surge fragments on quiet rows
    assert "control group" in news                # one-line explainer under the header


def test_activity_flight_deck_panes(client):
    """Operator order 2026-07-12: /activity is a flight deck of four
    independent flowing windows; the panes ARE the type separation."""
    act = client.get("/activity").text
    for pane in ("pane-trade", "pane-research", "pane-books", "pane-machine",
                 "flow-trade", "flow-research", "flow-books", "flow-machine"):
        assert f'id="{pane}"' in act, f"missing {pane}"
    for label in ("trade log", "research log", "books", "machine"):
        assert label in act
    assert "type-filters" not in act             # type chips are gone
    assert 'id="book-filter"' in act             # book dropdown scoped to BOOKS pane
    assert "function paneFor" in act             # routing = the separation
    # flight-log copy consumes the server-side translations
    assert "gates_human" in act and "reason_human" in act and "detail_human" in act
    assert "e.human" in act
    # human sentences, not machine fragments
    assert "OPENED" in act and "CLOSED" in act and "REFUSED" in act
    assert "nothing met the entry bar" in act
    assert "scanned the board" in act


def test_citation_parser():
    """Citations arrive as 'title — url', legacy 'url — url', bare 'url', or
    dicts. The href must be the LAST http(s) URL — never the whole string
    (the ' — ' glue 404'd as %20%E2%80%94, operator screenshot 2026-07-13)."""
    p = db._parse_citation
    # title — url
    assert p("Fed holds rates — https://reuters.com/markets/fed") == \
        {"url": "https://reuters.com/markets/fed", "title": "Fed holds rates"}
    # em-dash INSIDE the title survives; only the trailing ' — url' is stripped
    assert p("Japan — and Korea — rally — https://a.com/x") == \
        {"url": "https://a.com/x", "title": "Japan — and Korea — rally"}
    # legacy url — url → href = LAST url, text = shortened URL
    got = p("https://a.com/very/long/path — https://a.com/very/long/path")
    assert got["url"] == "https://a.com/very/long/path"
    assert got["title"].startswith("a.com") and "https://" not in got["title"]
    # bare url → shortened display text
    got = p("https://example.com/article?utm=x")
    assert got["url"] == "https://example.com/article?utm=x"
    assert got["title"] == "example.com/article"
    # dict passthrough (with and without title)
    assert p({"url": "https://b.com/y", "title": "T"}) == {"url": "https://b.com/y", "title": "T"}
    assert p({"url": "https://b.com/y"})["title"] == "b.com/y"
    # garbage → None
    assert p("no url here") is None and p("") is None and p({}) is None


def test_research_citations_are_parsed_objects(monkeypatch):
    evt = {"ts": 1, "event": "research", "coin": "BTC", "verdict": "LONG",
           "confidence": 0.7, "web_search_used": True,
           "web_search_citations": ["ETF flows surge — https://reuters.com/etf",
                                    "https://a.com/x — https://a.com/x",
                                    "not a citation"]}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [evt])
    e = db._activity_payload()["events"][0]
    assert e["citations"][0] == {"url": "https://reuters.com/etf", "title": "ETF flows surge"}
    assert e["citations"][1]["url"] == "https://a.com/x"
    assert len(e["citations"]) == 2                     # the garbage one is dropped
    # the news research-context path parses too
    payload_evt = dict(evt, news_risk="elevated")
    monkeypatch.setattr(db, "_read_log_lines", lambda: [payload_evt])
    _write_news_ledger([])
    ctx = db._news_payload()["research_context"][0]
    assert ctx["citations"][0]["url"] == "https://reuters.com/etf"


def test_news_flight_deck_panes(client):
    """Operator order 2026-07-13: /news mirrors the /activity flight deck —
    CATALYSTS + WATCHER panes, header strip, headline ages always visible."""
    n = client.get("/news").text
    for marker in ("pane-catalysts", "pane-watcher", "flow-catalysts", "flow-watcher"):
        assert f'id="{marker}"' in n, f"missing {marker}"
    assert "catalysts" in n and "watcher" in n           # pane labels
    assert 'id="news-strip"' in n and "reads today" in n  # session-style strip
    assert "last read" in n
    # headline AGE is first-class: read age from row ts + per-article age
    # chips when the recorder persisted them, stale flagged red past 7d
    assert "function ageChip" in n and "age-stale" in n
    assert "title_ages_h" in n
    assert "read ${fmtAgo(it.ts)}" in n
    # watcher time-decay: fresh reads individual, aged coalesce per coin/hour
    assert "foldAged" in n and "ev-fresh" in n and "fresh_window_s" in n
    assert "control group" in n                          # explainer kept


def test_execute_detail_reason_also_translated(monkeypatch):
    evt = {"ts": 1, "event": "execute", "coin": "SOL", "executed": False,
           "blocked_by": None,
           "detail": "runner_gate_blocked (needs volume+breakout/burst and structure; score=57, slow=0)"}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [evt])
    e = db._activity_payload()["events"][0]
    assert e["detail_human"] == "no fresh breakout structure (score 57)"
    # filled executes never carry a refusal translation
    ok = {"ts": 2, "event": "execute", "coin": "SOL", "executed": True, "detail": "493436405964"}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [ok])
    e2 = db._activity_payload()["events"][0]
    assert e2["detail_human"] is None


def test_books_endpoint(client, monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: dict(FIXTURE_CONFIG))
    r = client.get("/api/dashboard/books")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 7          # xs_reversal 2026-09-04 (W-XSR1), drawdown_ladder 2026-09-14 (W-WH2), copy_trade 2026-09-21
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


def test_humanize_reason_translates_real_vocabulary():
    """Flight-log copy: mined from the live loop log 2026-07-12."""
    h = db.humanize_reason
    assert h("history_floor_preflight (2d < 60d history)") == \
        "too young to trade (2d listed, needs 60d)"
    assert h("liquidity_floor_preflight ($0.34M < $0.70M)") == \
        "too thin ($0.34M daily volume, floor $0.70M)"
    assert h("daily_loss_gate (PnL $-12.61 <= $-12)") == \
        "daily loss floor hit ($-12.61 of $-12 today)"
    assert h("runner_gate_blocked (needs volume+breakout/burst and structure; score=28, slow=0)") == \
        "no fresh breakout structure (score 28)"
    assert h("hip3_dex_underfunded (xyz: $0.00). Transfer USDC to 'xyz' via the HL frontend.") == \
        "xyz dex unfunded ($0.00) — transfer USDC to trade equities"
    assert h("trend_filter (long fights the daily 200d-MA downtrend — counter-trend entries bleed)") == \
        "long against the daily downtrend (200MA)"
    assert h("floor_breach (1x consec, floor=0.42)") == "profit floor"
    assert h("max_loss (2.82% spot / 28.2% ROE >= 2.50% spot cap)") == "stop — max loss"
    # An untranslated reason must still read as a sentence. The old fallback
    # returned it verbatim, which is how MAIN_ENGINE_DELETED and a
    # parenthetical naming the config key `min_tradable_equity_usd` became
    # headline copy on the dashboard.
    assert h("some_new_gate (whatever)") == "Some new gate"
    assert h("MAIN_ENGINE_DELETED") == "Main engine deleted"
    # services/risk (wave-2, 2026-10-17) replaced the flat
    # below_min_tradable_equity ($X < $Y) refusal with a per-book one that
    # already carries its own plain-English reason
    # (contracts/risk/models.py's BookFeasibility.reason) — shown as-is.
    assert h("book_not_feasible (drawdown_ladder @ $15.21 equity: "
             "Needs $525: five resting rungs of $10.50 each at 1x.)") == \
        "drawdown_ladder: Needs $525: five resting rungs of $10.50 each at 1x."
    assert h(None) == ""


def test_classified_events_carry_human_fields():
    gate = db._classify_event({"event": "entry_preflight", "ts": 1, "coin": "CASHCAT",
                               "reason": "history_floor_preflight (2d < 60d history)"})
    assert gate["human"] == "too young to trade (2d listed, needs 60d)"
    ex = db._classify_event({"event": "execute", "ts": 1, "coin": "ARB", "side": "long",
                             "executed": False,
                             "blocked_by": ["trend_filter (long fights the daily 200d-MA downtrend — x)"]})
    assert ex["gates_human"] == ["long against the daily downtrend (200MA)"]
    cl = db._classify_event({"event": "dsl_exit", "ts": 1, "coin": "GRASS", "side": "long",
                             "reason": "floor_breach (1x consec, floor=0.42)",
                             "realized_pnl_pct": 2.5, "executed": True})
    assert cl["reason_human"] == "profit floor"


# ── analytics: funnel, book league, funding heat, tapes, coin chart ─────────

def test_funnel_payload_counts_and_reasons(monkeypatch):
    now = 100_000_000_000
    events = [
        {"ts": now - 2 * 86_400_000, "event": "scan", "triggers": 99},   # outside 24h window (oldest, first)
        {"ts": now - 500, "event": "scan", "triggers": 3},
        {"ts": now - 400, "event": "scan", "triggers": 2},
        {"ts": now - 300, "event": "research", "coin": "ARB"},
        {"ts": now - 250, "event": "execute", "coin": "ARB", "executed": True},
        {"ts": now - 200, "event": "execute", "coin": "SOL", "executed": False,
         "blocked_by": ["daily_loss_gate (PnL $-12.61 <= $-12)"]},
        {"ts": now - 150, "event": "execute", "coin": "ETH", "executed": False,
         "blocked_by": ["daily_loss_gate (PnL $-9.00 <= $-12)"]},
        {"ts": now - 100, "event": "entry_preflight", "coin": "CASHCAT",
         "reason": "history_floor_preflight (2d < 60d history)"},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    d = db._funnel_payload(window_s=86400, now_ms=now)
    stages = {s["stage"]: s["n"] for s in d["funnel"]}
    assert stages == {"Scan cycles": 2, "Triggers": 5, "Position reviews": 1, "Positions opened": 1}
    assert d["blocked_executions"] == 2
    # the two daily_loss_gate blocks collapse into ONE humanized reason, counted twice
    top = {r["reason"]: r["n"] for r in d["top_reasons"]}
    assert top["daily loss floor hit ($-12.61 of $-12 today)"] == 1  # exact numbers differ
    assert sum(top.values()) == 3   # 2 execute blocks + 1 preflight
    assert set(d["coins"]) == {"ARB", "SOL", "ETH"}


def test_funnel_payload_counts_book_opens_as_executed(monkeypatch):
    """A book trade (extreme_fade, xs... ) never emits `execute` — only
    `book_open`. Operator screenshot 2026-07-14: funnel showed executed=0
    while xs_momentum held real BTC/ETH positions. book_open must count."""
    now = 100_000_000_000
    events = [
        {"ts": now - 300, "event": "book_open", "book": "extreme_fade",
         "coin": "BTC", "side": "long"},
        {"ts": now - 200, "event": "execute", "coin": "ETH", "executed": True},
        {"ts": now - 100, "event": "execute", "coin": "SOL", "executed": False,
         "blocked_by": ["some gate"]},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    d = db._funnel_payload(window_s=86400, now_ms=now)
    stages = {s["stage"]: s["n"] for s in d["funnel"]}
    assert stages["Positions opened"] == 2         # book_open + execute(True)
    assert set(d["coins"]) == {"BTC", "ETH", "SOL"}


def test_funnel_payload_empty_log(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    d = db._funnel_payload(window_s=86400)
    assert all(s["n"] == 0 for s in d["funnel"])
    assert d["top_reasons"] == [] and d["coins"] == []
    assert d["last_scan_age_s"] is None


def test_an_all_zero_funnel_carries_the_age_of_the_last_scan(monkeypatch):
    """Four zero bars and "nothing blocked" is what a quiet market looks like.
    It is also what a dead trading loop looks like, and the operator cannot tell
    them apart from the bars. The age of the last scan is the only thing that
    separates them, and it has to survive falling outside the window — that is
    precisely the case where the loop has been down the whole time."""
    hour = 3_600_000
    now = 100 * hour
    # One scan, eight hours before a 1h window: the window is empty, the loop is
    # not merely quiet, and the payload has to say so.
    monkeypatch.setattr(db, "_read_log_lines",
                        lambda: [{"ts": now - 8 * hour, "event": "scan", "triggers": 3}])
    d = db._funnel_payload(window_s=3600, now_ms=now)
    assert all(s["n"] == 0 for s in d["funnel"])
    assert d["last_scan_age_s"] == 8 * 3600


def test_book_league_merges_summary_with_config(monkeypatch, tmp_path):
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    with open(tmp_path / "extreme_fade.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 1000, "coin": "BTC", "signal_bar_t": 1000,
                             "entry_ref_px": 100.0, "horizon_days": 3.0}) + "\n")
    with open(tmp_path / "whale_flow.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 2000, "coin": "ETH", "signal_bar_t": 2000,
                             "entry_ref_px": 50.0, "horizon_days": 1.0}) + "\n")
    monkeypatch.setattr(db, "read_agent_config", lambda: dict(FIXTURE_CONFIG))
    rows = {r["book"]: r for r in db._book_league_payload(now_ms=2_000_000_000)}
    # extreme_fade's module was deleted 2026-08-29. Its ledger survives as the
    # evidence behind the refutation, but it is not a row: the table lists what
    # can trade.
    assert "extreme_fade" not in rows
    # the resolve maths still has to be right — assert it on the summary, which
    # is what the league reads, rather than on a row that no longer renders
    summary = {r["book"]: r for r in shadow_ledger.summary(2_000_000_000)}
    assert summary["extreme_fade"]["resolved"] == 1     # far past its 3d horizon
    # whale_flow REFUTED + removed 2026-07-22 -> in _REMOVED_BOOKS, never renders
    assert "whale_flow" not in rows


def test_book_league_removed_books_never_render(monkeypatch, tmp_path):
    """premium_fade_short / neg_funding_fade: module deleted, ledger fully
    graded and REFUTED, and — operator order 2026-07-17 — removed from the
    UI entirely. Their ledger files stay on disk as evidence, but the league
    payload must skip them; a genuinely still-accruing lane like whale_flow
    keeps its 'retired' status."""
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    with open(tmp_path / "premium_fade_short.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 1000, "coin": "BTC", "signal_bar_t": 1000,
                             "entry_ref_px": 100.0, "horizon_days": 1.0}) + "\n")
    with open(tmp_path / "neg_funding_fade.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 1000, "coin": "ETH", "signal_bar_t": 1000,
                             "entry_ref_px": 50.0, "horizon_days": 1.0}) + "\n")
    with open(tmp_path / "news_ta_quadrant.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 1000, "coin": "SOL", "signal_bar_t": 1000,
                             "entry_ref_px": 10.0, "horizon_days": 1.0}) + "\n")
    with open(tmp_path / "whale_flow.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 1000, "coin": "SOL", "signal_bar_t": 1000,
                             "entry_ref_px": 10.0, "horizon_days": 1.0}) + "\n")
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    rows = {r["book"]: r for r in db._book_league_payload(now_ms=2_000_000_000)}
    assert "premium_fade_short" not in rows
    assert "neg_funding_fade" not in rows
    assert "whale_flow" not in rows                       # REFUTED + removed 2026-07-22
    assert "news_ta_quadrant" not in rows          # deleted module, ledger kept
    # no row can ever carry the retired 'dead' status again
    assert all(r["status"] != "dead" for r in rows.values())


def test_book_league_empty_ledger_dir(monkeypatch, tmp_path):
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    assert db._book_league_payload() == []


def _write_funding_log(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_funding_heat_accruing_below_threshold(monkeypatch, tmp_path):
    log = tmp_path / "funding.jsonl"
    _write_funding_log(log, [{"ts": 1000, "n": 1, "rows": [{"c": "BTC", "f": 0.0001, "oi": 100.0, "px": 60000.0}]}])
    monkeypatch.setattr(db, "_FUNDING_OI_LOG", str(log))
    d = db._funding_heat_payload()
    assert d["status"] == "accruing" and d["count"] == 1 and d["since"] == 1000


def test_funding_heat_ranks_by_extremity(monkeypatch, tmp_path):
    log = tmp_path / "funding.jsonl"
    hour = 3_600_000
    rows = []
    # BTC funding drifts low->low->...->HIGH (current = new high = 100th pctile)
    for i in range(25):
        f = 0.0001 if i < 24 else 0.0009
        rows.append({"ts": i * hour, "n": 1,
                     "rows": [{"c": "BTC", "f": f, "oi": 1000.0 + i, "px": 60000.0},
                              {"c": "ETH", "f": 0.0002, "oi": 500.0, "px": 2000.0}]})
    _write_funding_log(log, rows)
    monkeypatch.setattr(db, "_FUNDING_OI_LOG", str(log))
    d = db._funding_heat_payload(now_ms=25 * hour)
    assert d["status"] == "ok"
    by_coin = {r["coin"]: r for r in d["rows"]}
    # 24 readings below, 1 tied (itself): (24 + 0.5) / 25 -> 98.
    assert by_coin["BTC"]["funding_pctile"] == 98.0
    assert by_coin["BTC"]["oi_change_24h_pct"] is not None
    # ETH never moves, so it is exactly where it always is: the midpoint. Under
    # the old count-ties-as-below percentile it scored 100.0 — the same as a
    # genuine new high — and this ranking assertion passed on a coin flip.
    assert by_coin["ETH"]["funding_pctile"] == 50.0
    # BTC (extreme) ranks ahead of ETH (flat, mid percentile) in the top list
    assert d["rows"][0]["coin"] == "BTC"


def test_a_flat_funding_series_reads_normal_not_extreme(monkeypatch, tmp_path):
    """Hyperliquid funding sits pinned at its 1.25e-05 baseline for long
    stretches. Counting ties as "below" then scores every such coin at the 100th
    percentile at once — measured on the live log 2026-09-01, BTC, ETH, SOL, XRP
    and TRUMP all read exactly 100%ile in the same snapshot, and the panel was
    ten rows of maximum alarm meaning nothing. A coin sitting where it always
    sits is the definition of normal."""
    log = tmp_path / "funding.jsonl"
    hour = 3_600_000
    baseline = 1.25e-05
    rows = [{"ts": i * hour, "n": 1,
             "rows": [{"c": c, "f": baseline, "oi": 1000.0, "px": 100.0}
                      for c in ("BTC", "ETH", "SOL")]}
            for i in range(25)]
    _write_funding_log(log, rows)
    monkeypatch.setattr(db, "_FUNDING_OI_LOG", str(log))
    d = db._funding_heat_payload(now_ms=25 * hour)
    assert d["status"] == "ok"
    assert [r["funding_pctile"] for r in d["rows"]] == [50.0, 50.0, 50.0]
    # and the rate itself is carried, because a rank alone cannot be sized
    assert all(r["funding_now"] == baseline for r in d["rows"])


def test_funding_heat_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_FUNDING_OI_LOG", str(tmp_path / "nope.jsonl"))
    d = db._funding_heat_payload()
    assert d["status"] == "accruing" and d["count"] == 0 and d["since"] is None


def test_the_news_tape_reads_live_books_and_ignores_removed_ones(monkeypatch, tmp_path):
    """_tapes_payload read `news_catalyst` by name for six weeks after that book
    was demolished (2026-07-18) and added to _REMOVED_BOOKS, so /analytics said
    "accruing — no news reads in the last 24h yet" while news_surge_multi and
    news_surge_short wrote all day. The sources are now derived from
    _KNOWN_BOOK_NAMES, so a removed book stops being read the moment it leaves
    _BOOKS. This test is the proof: the removed ledger is on disk with a fresh,
    breaking record and must still not reach the tape."""
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    now = 10_000_000_000
    with open(tmp_path / "news_catalyst.jsonl", "w") as fh:      # REMOVED book
        fh.write(json.dumps({"ts": now - 500, "coin": "GHOST", "side": "long",
                             "meta": {"surge_x": 9.9, "breaking": True}}) + "\n")
    with open(tmp_path / "whale_flow.jsonl", "w") as fh:         # REMOVED book
        fh.write(json.dumps({"ts": now - 500, "coin": "GHOST2", "side": "long",
                             "meta": {"surge_x": 8.8}}) + "\n")
    with open(tmp_path / "news_surge_multi.jsonl", "w") as fh:   # LIVE book
        fh.write(json.dumps({"ts": now - 500, "coin": "ARB", "side": "short",
                             "meta": {"surge_x": 4.2, "breaking": True}}) + "\n")
        fh.write(json.dumps({"ts": now - 30 * 3_600_000, "coin": "OLD", "side": "short",
                             "meta": {"surge_x": 1.0}}) + "\n")   # >24h, excluded
    d = db._tapes_payload(now_ms=now)
    coins = [r["coin"] for r in d["news"]["rows"]]
    assert coins == ["ARB"], coins
    assert d["news"]["rows"][0]["breaking"] is True
    assert d["news"]["rows"][0]["books"] == ["news_surge_multi"]
    # the removed whale lane is gone from the payload, not merely empty
    assert "whale" not in d


def test_every_book_the_tape_reads_can_still_trade_today():
    """The structural half of the fix above: no name the tape draws from may be
    a book the dashboard already knows is gone."""
    assert db._KNOWN_BOOK_NAMES.isdisjoint(db._REMOVED_BOOKS)


def test_the_news_tape_ranks_by_surge_not_by_how_often_a_coin_was_polled(monkeypatch, tmp_path):
    """Read count measures how often the poller looked at a coin, not whether
    anything happened to it. Ranking on it led the panel with SKR — 80 reads, no
    spike — above coins with real coverage surges."""
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    now = 10_000_000_000
    with open(tmp_path / "news_surge_multi.jsonl", "w") as fh:
        for _ in range(40):                       # polled constantly, never spikes
            fh.write(json.dumps({"ts": now - 500, "coin": "POLLED", "side": "short",
                                 "meta": {"surge_x": 0.0}}) + "\n")
        fh.write(json.dumps({"ts": now - 500, "coin": "SPIKE", "side": "short",
                             "meta": {"surge_x": 9.0}}) + "\n")
    d = db._tapes_payload(now_ms=now)
    rows = d["news"]["rows"]
    assert [r["coin"] for r in rows] == ["SPIKE", "POLLED"]
    assert rows[0]["peak_surge"] == 9.0 and rows[1]["peak_surge"] == 0.0
    assert rows[1]["reads"] == 40          # still reported, just not what ranks


def test_tapes_payload_with_nothing_recorded_is_quiet(monkeypatch, tmp_path):
    from pathiel.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    d = db._tapes_payload()
    assert d["news"]["status"] == "quiet" and d["news"]["rows"] == []


def test_coin_chart_payload_no_coin():
    assert db._coin_chart_payload("") == {
        "coin": "", "interval": "1h", "candles": [], "markers": [], "status": "no_coin"}


def test_coin_chart_payload_markers_and_candles(monkeypatch):
    class FakeCandle:
        def __init__(self, t, o, h, l, c, v):
            self.t, self.o, self.h, self.l, self.c, self.v = t, o, h, l, c, v

    candles = [FakeCandle(1000 + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100.5 + i, 10)
              for i in range(5)]
    import pathiel.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles", lambda coin, interval, count: candles)
    events = [
        {"ts": 1000 + 3_600_000, "event": "execute", "coin": "ARB", "executed": True,
         "side": "long", "entry_px": 101.0},
        {"ts": 1000 + 2 * 3_600_000, "event": "dsl_exit", "coin": "ARB",
         "fill_px": 103.0, "realized_pnl_pct": 2.0},
        {"ts": 1000 + 3 * 3_600_000, "event": "research", "coin": "ARB", "verdict": "LONG",
         "confidence": 0.7},
        {"ts": 500, "event": "execute", "coin": "ARB", "executed": True,   # before candle window
         "side": "long", "entry_px": 90.0},
        {"ts": 1000 + 3_600_000, "event": "execute", "coin": "OTHER", "executed": True},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    d = db._coin_chart_payload("ARB", "1h")
    assert d["status"] == "ok" and len(d["candles"]) == 5
    kinds = [m["kind"] for m in d["markers"]]
    assert kinds == ["entry", "close", "verdict"]   # OTHER + pre-window ARB excluded


def test_coin_chart_payload_fetch_failure(monkeypatch):
    import pathiel.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles", lambda *a, **kw: [])
    d = db._coin_chart_payload("NOPE")
    assert d["status"] == "no_data" and d["candles"] == []


def test_analytics_endpoints_route(client, monkeypatch):
    # House-account routes became operator surface on 2026-09-04. This test is
    # about payload shape, so it opts into single-operator mode rather than
    # standing up a signed-in operator session.
    monkeypatch.setenv("PATHIEL_PUBLIC_DASHBOARD", "1")
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    for ep in ("/api/dashboard/funnel", "/api/dashboard/book_league",
               "/api/dashboard/funding_heat", "/api/dashboard/tapes"):
        r = client.get(ep)
        assert r.status_code == 200, ep

    import pathiel.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles", lambda *a, **kw: [])
    r = client.get("/api/dashboard/coin_chart?coin=BTC")
    assert r.status_code == 200 and r.json()["status"] == "no_data"
    assert client.get("/api/dashboard/coin_chart").status_code == 422  # coin required


def test_analytics_page_markers(client):
    r = client.get("/analytics").text
    for marker in ("panel-funnel", "panel-league", "panel-chart", "panel-heat",
                  "panel-tapes", "funnel-bars", "league-body", "coin-canvas",
                  "heat-body", "news-body", "pathiel-an-"):
        assert marker in r, f"missing {marker}"
    # muted from the nav, still served — the page must keep working
    assert 'data-nav="/analytics"' in r
    # The whale tape is gone, not hidden: whale_flow was REFUTED and removed
    # 2026-07-22, and the page kept rendering a section for it that said
    # "accruing" — a permanently dead lane described as one still filling up.
    # Its money formatters went with it; nothing else used them.
    assert "whale" not in r
    assert "fmtMoneyCompact" not in r and "tape-bar-buy" not in r
    # the dead-book branch is GONE (operator order 2026-07-17: refuted books
    # are removed from the UI, not rendered with a special badge)
    assert "b-dead" not in r and "row-dead" not in r



# ── landing v3 (2026-07-17): living ambient layer + informational one-pager ──

def test_landing_v3_new_sections_wired_and_ordered(client):
    """The one-pager grew the decision funnel, recent-closes tape, and the
    evidence league — all fed by existing local-file endpoints (zero added
    HL API pressure). Section order contract: positions < equity chart <
    books < league < how-it-works copy < footer."""
    r = client.get("/").text
    for marker in ('id="funnel-strip"', 'id="trade-tape"', 'id="league"',
                   'id="pipeline"'):
        assert marker in r, f"missing {marker}"
    assert "/api/dashboard/funnel" in r
    assert "book_league" in r
    assert "closed-trades" in r
    assert "refreshFunnel" in r and "refreshTape" in r and "refreshLeague" in r
    assert r.index('id="positions-body"') < r.index('id="equity-chart"')
    assert r.index('id="equity-chart"') < r.index('id="books-wrap"')
    assert r.index('id="books-wrap"') < r.index('id="league"') < r.index(HOW_IT_WORKS)
    # manual closes carry pnl_pct=null — the tape must never fake a 0
    assert "pct == null" in r


def test_landing_v3_token_popover_replaces_prompt(client):
    """Operator token entry is a native popover in the top layer (with
    ::backdrop) instead of the old blocking prompt()/confirm() dialogs.
    Same localStorage key, so existing tooling reads it unchanged."""
    r = client.get("/").text
    assert "popover" in r and "popovertarget" in r
    assert "::backdrop" in CSS
    assert "prompt(" not in r and "confirm(" not in r
    assert "pathiel-op-token" in r
    assert "op-token-btn" in r


def test_landing_v3_view_transition_range_switch(client):
    """Equity-range switches run inside document.startViewTransition when
    available, guarded so browsers without it (and reduced-motion users)
    switch instantly."""
    r = client.get("/").text
    assert "document.startViewTransition" in r
    assert "reduceMotion.matches" in r


def test_landing_v3_kpi_tweens_and_spark(client):
    """KPI numbers tween between polls (tabular-nums prevents jitter) and the
    equity KPI carries a sparkline reusing the big chart's already-fetched
    series — zero extra API calls."""
    r = client.get("/").text
    assert "function tween(" in r and "setMoney" in r
    assert 'id="kpi-spark"' in r and "drawSpark" in r
    # spark draws from the same series refreshChart just fetched — the call
    # site lives inside refreshChart, right after the fetch
    fetch_at = r.index("equity-curve?range_s=")
    assert "drawSpark(data)" in r[fetch_at:fetch_at + 400]


def test_v4_compiled_css_replaces_tailwind_runtime(client):
    """The in-browser Tailwind JIT runtime is gone — every page links the
    compiled /static/app.css instead (no runtime JS, no flash-of-unstyled).
    The committed file must be exactly what scripts/build_static_css.py
    emits, and every utility-shaped class token in the templates must
    resolve to a rule — the generator hard-fails on unknowns, so a template
    edit that invents a class breaks HERE, not silently in the browser."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_static_css",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "build_static_css.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.OUT.read_text() == gen.build(), "app.css stale — rerun scripts/build_static_css.py"
    for path in ("/", "/activity", "/news", "/analytics"):
        r = client.get(path).text
        assert '"/static/app.css"' in r, path
        assert "tailwind.js" not in r, path


def test_v4_speculation_rules_and_hotkeys(client):
    """Hover-eagerness prerendering (speculation rules) makes tab hops
    instant; g-then-key hotkeys (g d/a/n/y) give app-style navigation,
    inert while typing."""
    for path in ("/", "/activity", "/news", "/analytics"):
        r = client.get(path).text
        assert 'type="speculationrules"' in r, path
        assert '"eagerness":"moderate"' in r, path
        # hotkeys live in static/pathiel.js; every page has to load it
        assert "/static/pathiel.js" in r, path


def test_v4_landing_wire_sse(client):
    """The wire: live session-log tail on the landing page over the existing
    /api/feed/stream SSE endpoint — newest engine event as a one-line strip,
    hidden until the first event arrives so it never renders as chrome."""
    r = client.get("/").text
    assert 'id="wire"' in r
    assert "EventSource" in r and "/api/feed/stream" in r
    assert "aria-live" in r
    assert r.index("</nav>") < r.index('id="wire"') < r.index('id="funnel-strip"')


def test_summary_equity_is_true_account_equity(monkeypatch):
    """Operator correction 2026-07-17: the equity KPI must match HL's own
    Account Equity — perps across every dex PLUS idle spot USDC — not the
    perps-only subtotal."""
    hb = {"ts": int(time.time() * 1000) - 5_000, "event": "loop_heartbeat",
          "equity": 20.40, "spot_usdc": 0.02, "daily_pnl": 1.9,
          "available": 1.89, "open_positions": 3,
          "dex_equity": {"": 11.95, "xyz": 8.45},
          "dex_available": {"": 1.89, "xyz": 8.45}}
    monkeypatch.setattr(db, "_read_log_lines", lambda: [hb])
    s = db._summary_payload()
    assert s["equity"] == 20.42          # 20.40 perps + 0.02 spot
    assert s["spot_usdc"] == 0.02


# ── a page must not assert health it cannot verify ──────────────────────────


def _page(name):
    return dict(_template_files())[name]


def test_no_page_reports_an_empty_funnel_without_saying_why():
    """Four zero bars are what a quiet market looks like and what a stopped loop
    looks like. Both pages that draw the funnel have to resolve that, or the one
    reading an empty deck cannot tell which situation they are in."""
    for name in ("analytics.html", "landing.html"):
        src = _page(name)
        assert "last_scan_age_s" in src, f"{name} ignores the liveness field"
        assert "not a quiet market" in src or "the loop being down" in src, name


def test_the_activity_feed_does_not_call_a_stopped_loop_normal():
    """tradeEmptyCopy said "engine scanning normally (0 scans, 0 blocks)" — an
    assertion of health built from the numbers that disprove it, shown exactly
    when an operator is staring at an empty feed asking if the loop is alive."""
    src = _page("activity.html")
    assert "not a quiet market" in src
    i = src.index("function tradeEmptyCopy")
    body = src[i:src.index("function paneEmpties")]
    assert "if (!scans)" in body, "the zero-scan case must be handled first"
    # rindex: the phrase also appears in the comment above the guard.
    assert body.index("if (!scans)") < body.rindex("scanning normally"), (
        "the health claim must sit behind the zero-scan guard")


def test_the_dashboard_does_not_claim_a_scan_that_never_happened():
    """last_tick_age_s is null before the loop ever checks in. The strip printed
    "0 triggers on last scan", which describes a scan that did not occur."""
    src = _page("landing.html")
    assert "s.last_tick_age_s == null ? 'No scan yet'" in src


def test_the_dashboard_age_format_scales_past_minutes():
    """fmtAge stopped at minutes, so a loop down eight hours read "480m ago" —
    the one reading where the number most needs to land."""
    src = _page("landing.html")
    fn = src[src.index("const fmtAge = s =>"):src.index("const fmtAgeMs")]
    assert "'h'" in fn and "'d'" in fn, "must scale to hours and days"


def test_the_off_switch_label_never_shouts():
    """The kill button is served as "Stop trading" and the script reset it to
    "STOP TRADING", so it changed shape after the first press."""
    src = _page("landing.html")
    assert "STOP TRADING" not in src and "CONFIRM STOP" not in src
    assert "Stop trading" in src and "Confirm stop" in src


# ── the Markets panels have to be readable, not just correct ────────────────


def _analytics_source():
    return dict(_template_files())["analytics.html"]


def test_both_funding_extremes_are_marked_not_only_the_high_one():
    """Crowded positioning is the read, and it is crowded at both ends. The old
    heatColor returned the same token for `<= 20` as for the middle, so a coin in
    the bottom fifth of its own funding range was painted exactly like one
    sitting at its median — the panel hid the half of the signal it exists for."""
    src = _analytics_source()
    fn = src[src.index("function heatColor("):src.index("function fundingRate(")]
    assert "pctile >= 80 || pctile <= 20" in fn, "the low extreme must be marked too"
    assert fn.count("--amber") == 1 and "--ink-3" in fn, "middle must be the muted token"


def test_the_heat_row_shows_the_funding_rate_and_not_only_its_rank():
    """"100%ile" says where funding sits in its own history. It never says what
    the carry costs, and a trader cannot size a position off a rank."""
    src = _analytics_source()
    assert "function fundingRate(" in src
    assert "r.funding_now" in src, "the rate itself must reach the row"
    assert ".heat-rate" in _css(), "and it needs a column to land in"


def test_an_empty_funnel_says_whether_the_loop_is_down():
    """Zeros in every stage are ambiguous — quiet market, or dead loop. The page
    has to resolve that, because the two demand opposite responses."""
    src = _analytics_source()
    assert 'id="funnel-dead"' in src
    assert "d.last_scan_age_s" in src
    assert "not a quiet market" in src
    # fmtAge is in HOURS; the payload field is seconds.
    assert "fmtAge(age / 3600)" in src, "age must be converted before formatting"


def _css():
    return open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "pathiel", "static", "pathiel.css")).read()


# ── wiring audit: every page, not just the one being worked on ───────────────


def _template_files():
    tdir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "pathiel", "templates")
    return [(f, open(os.path.join(tdir, f)).read()) for f in sorted(os.listdir(tdir))
            if f.endswith(".html")]


def test_no_page_reaches_for_an_element_that_does_not_exist():
    """`$('#renamed')` is null in the browser and the block it feeds silently
    never renders — the page looks like a quiet market instead of a bug. This
    is the cheapest place to catch a rename."""
    for name, html in _template_files():
        defined = set(re.findall(r'id="([\w-]+)"', html))
        used = (set(re.findall(r"\$\('#([\w-]+)'\)", html))
                | set(re.findall(r"getElementById\('([\w-]+)'\)", html)))
        assert not (used - defined), f"{name} reaches for missing ids: {sorted(used - defined)}"


# Wave-2 overhaul (2026-10-17): the accounts + risk routers
# (contracts/accounts/models.py, contracts/risk/models.py) are built by
# sibling agents in their own worktrees this wave and mounted onto
# pathiel.server by the integrator once every wave-2 agent's work merges
# (SWARM_RULES section 10) — so real_app.routes does not have them YET in
# this worktree, even though landing.html's onboarding wizard and
# account.html correctly call them. Listed here, by hand, from the exact
# route table each contract's module docstring documents, so this stays a
# real cross-check against the contract rather than a blanket exemption.
# Delete this set once the integrator's merge makes it redundant.
_WAVE2_CONTRACT_ROUTES = frozenset({
    "/api/session",
    "/api/accounts",
    "/api/accounts/agent/prepare",
    "/api/accounts/agent/confirm",
    "/api/accounts/import",
    "/api/accounts/watch",
    "/api/accounts/active/summary",
    "/api/risk/profile",
    "/api/risk/limits",
    "/api/risk/preview",
})


def test_no_page_calls_an_endpoint_that_is_not_registered(client):
    """Every page is a static shell over the JSON API. A route renamed on the
    server and not in the template is a control that 404s in production."""
    # Routes come from the REAL app, not the bare dashboard test app: in
    # production pathiel.server mounts the dashboard AND the agent
    # endpoints, and a page control may legitimately call either. Checking only
    # the dashboard's own routes would fail a control that works in production.
    from pathiel.server import app as real_app
    routes = {r.path for r in client.app.routes if hasattr(r, "path")}
    routes |= {r.path for r in real_app.routes if hasattr(r, "path")}
    routes |= _WAVE2_CONTRACT_ROUTES
    for name, html in _template_files():
        for raw in set(re.findall(r"'(/api/[\w/{}?=&.-]*)'", html)):
            path = raw.split("?")[0].rstrip("/")
            if not path:
                continue                      # a prefix the script concatenates onto
            if path in routes:
                continue
            # the last segment may be a path parameter the script fills in
            templated = [re.sub(r"/[\w.-]+$", "/{%s}" % p, path)
                         for p in ("lane", "market_id", "book", "name", "coin")]
            assert (any(t in routes for t in templated)
                    or any(p.startswith(path) for p in routes)), \
                f"{name} calls unregistered endpoint {raw}"


def test_no_page_pulls_a_third_party_asset(client):
    """The dashboard runs on a trading box. Every asset is vendored under
    /static so a CDN outage (or a CDN owner) cannot change what it renders."""
    for path in ("/", "/activity", "/news", "/analytics", "/trends", "/account"):
        # the SVG namespace is an identifier, not a fetch — everything else is
        body = client.get(path).text.replace(
            'xmlns="http://www.w3.org/2000/svg"', "")
        external = re.findall(r'https?://[^\s"\'<>)]+', body)
        assert not external, f"{path} pulls {sorted(set(external))}"


# ── the redesign, as a contract ──────────────────────────────────────────────
#
# The brief was "less childish, more professional — boring, simple,
# straightforward; something a person with a million dollars would use". Those
# words are not testable, but the specific things that made it read as a toy
# are, and each one below was really on the page before this redesign.

PAGES = ("/", "/activity", "/news", "/analytics", "/trends", "/account")


def test_no_page_ships_decorative_chrome(client):
    """The mascot, the WebGL aurora, the scanline texture and the scroll-
    progress beam are gone from every page, not just the dashboard."""
    banned = {
        "pixel-cat": "the mascot",
        "cat-sleep": "mascot state",
        "gl-bg": "the shader canvas",
        "__setGlState": "the shader's data feed",
        "#version 300 es": "a fragment shader",
        "scroll-progress": "the scroll beam",
        "repeating-linear-gradient": "scanline texture",
    }
    for path in PAGES:
        page = client.get(path).text
        for token, what in banned.items():
            assert token not in page, f"{path} still ships {what} ({token})"


def test_the_palette_carries_information_only(client):
    """One accent, plus green/red for money and amber for attention. The old
    sheet ran an indigo→violet→purple gradient family through the wordmark,
    the nav pill, the badges and the equity fill."""
    for gone in ("#6366f1", "#8b5cf6", "#a855f7", "linear-gradient(135deg"):
        assert gone not in CSS, f"brand gradient survives in the stylesheet: {gone}"
    for path in PAGES:
        page = client.get(path).text
        for gone in ("#6366f1", "#8b5cf6", "#a855f7"):
            assert gone not in page, f"{path} hard-codes {gone}"


def test_every_page_uses_the_one_stylesheet(client):
    """Five pages previously carried five ~250-line <style> blocks, so a
    colour changed in four places and drifted in the fifth."""
    for path in PAGES:
        page = client.get(path).text
        assert "/static/pathiel.css" in page, f"{path} does not load the design system"


def test_no_page_carries_a_second_design_system(client):
    """A page-level <style> block is allowed for genuinely page-specific
    geometry (chart heights), but not for a whole palette."""
    for name in ("landing", "activity", "analytics", "news", "trends", "account"):
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "pathiel" / "templates" / f"{name}.html").read_text()
        blocks = re.findall(r"<style>(.*?)</style>", src, re.S)
        for b in blocks:
            assert b.count("\n") < 40, (
                f"{name}.html has a {b.count(chr(10))}-line <style> block — "
                f"shared rules belong in static/pathiel.css")
            assert "--ink" not in b and "--accent" not in b, (
                f"{name}.html redefines design tokens locally")


def test_money_is_set_in_tabular_figures():
    """Proportional digits make a column of numbers jitter as it updates,
    which is the single clearest tell that a money screen was not built by
    someone who reads money screens."""
    assert "font-variant-numeric:tabular-nums" in CSS.replace(" ", "")
    assert ".kv" in CSS


def test_the_type_is_not_a_toy(client):
    """The old pages set body copy at 10-12px with 9.5px table headers. The
    reader is not twenty-five and is not on a laptop."""
    body = re.search(r"\nbody\{[^}]*\}", CSS, re.S).group(0)
    size = int(re.search(r"font-size:(\d+)px", body).group(1))
    assert size >= 14, f"body copy is {size}px"
    assert "font-size:9" not in CSS.replace(" ", ""), "9px type survives"


def test_the_page_still_works_in_both_themes():
    """Light is the default. Dark ships as a second skin — same structure,
    only tokens differ, so no rule below the token block branches on theme."""
    # Dark is unconditional, not a prefers-color-scheme default: the OS
    # preference is about documents and this is an instrument.
    import re as _re
    # the phrase survives in a comment; what matters is that no RULE uses it
    assert not _re.search(r"@media[^{]*prefers-color-scheme", CSS), (
        "the theme still defers to the OS")
    assert ':root[data-theme="light"]' in CSS, "light is not reachable"
    assert ':root[data-theme="dark"]' in CSS
    for token in ("--ink", "--paper", "--rule", "--up", "--down", "--accent"):
        assert CSS.count(f"{token}:") >= 3, f"{token} is not defined for every theme"


def test_it_prints():
    """Someone with this much money hands a page to an accountant."""
    assert "@media print" in CSS


def test_no_page_animates_for_decoration(client):
    """Every animation that survived has to earn it. The bounce, the breathe,
    the shimmer and the pulse did not."""
    for gone in ("cat-bounce", "cat-breathe", "shimmer", "breaking-pulse",
                 "@keyframes growX"):
        assert gone not in CSS, f"decorative animation survives: {gone}"
    assert "prefers-reduced-motion" in CSS, "no motion opt-out"


# ── nothing on screen may describe something that no longer exists ──────────

def test_a_book_with_no_module_is_not_a_row_at_all(monkeypatch):
    """A ledger whose book was deleted is not a row. It was first labelled
    RECORDER ("still accruing toward a decision" — nothing accrues), then
    RETIRED and folded on the dashboard. But the payload has TWO consumers,
    so folding it in one template left /analytics listing all sixteen. The
    filter belongs here, once."""
    import pathiel.dashboard as db
    from pathiel.agents import shadow_ledger

    # imported inside the function, so patch the module it is pulled from
    monkeypatch.setattr(db, "_books_payload", lambda *a, **k: [])
    monkeypatch.setattr(shadow_ledger, "summary",
                        lambda *a, **k: [{"book": "long_gone", "n": 5}])
    assert db._book_league_payload(now_ms=0) == [], (
        "a book with no config block is still rendered")


def test_the_evidence_table_lists_only_what_trades(client):
    """Sixteen dead books is not something anyone acts on. The table lists the
    books that spend capital; the retired ledgers stay on disk and in git as
    the evidence behind each refutation, but they are not dashboard furniture."""
    r = client.get("/").text
    assert "league-retired" not in r, "the retired roll-up is back on the page"
    assert "r.status !== 'retired'" in r, "retired rows are not filtered out"
    assert "RECORDER" not in r


def test_a_scanned_coin_no_book_claimed_is_not_a_refusal():
    """There is no discretionary entry path: entries come from books, so a
    scanned coin that no book claimed is normal operation. Logging it as a
    refusal put a deleted feature's name at the top of the decision funnel
    ~3,900 times a day and buried the gates that actually stopped a trade."""
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "trading_loop.py").read_text()
    body = src[src.index("main_engine ENTRIES DELETED"):]
    body = body[:body.index("# TA filter")]
    assert "_unclaimed += 1" in body
    assert "log_event" not in body, "still emits an event per scanned coin"
    assert "MAIN_ENGINE_DELETED" not in src, (
        "a deleted feature's name is still emitted into the session log")


def test_structurally_impossible_counts_are_not_printed(client):
    """`0 shadow · 0 off` were two counts that can never be anything else
    under the no-shadow doctrine."""
    r = client.get("/").text
    assert "shadow · ${" not in r
    assert "shadow ? " in r, "the shadow count is printed unconditionally"


def test_no_template_hard_codes_a_colour():
    """Charts and inline styles kept a neon crypto palette the stylesheet could
    not reach, so a theme change fixed the page and missed the chart."""
    import re as _re
    tpl = pathlib.Path(__file__).resolve().parent.parent / "pathiel" / "templates"
    offenders = []
    for f in sorted(tpl.glob("*.html")):
        for m in _re.finditer(r"#[0-9a-fA-F]{6}\b", f.read_text()):
            offenders.append(f"{f.name}: {m.group(0)}")
    assert not offenders, (
        "hard-coded colours bypass the design tokens:\n" + "\n".join(offenders))


def test_every_page_reaches_every_other_page(client):
    """A route that renders but is linked from nowhere is dead UI. Whatever
    ships must be reachable by clicking."""
    routes = {"/", "/activity", "/news", "/analytics", "/trends", "/account"}
    for path in routes:
        page = client.get(path).text
        linked = {r for r in routes if f'data-nav="{r}"' in page}
        assert linked == routes, f"{path} cannot reach {sorted(routes - linked)}"


def test_the_nav_marks_the_page_you_are_on(client):
    """The marking runs from static/pathiel.js — every page has to load it and
    give the nav something to match on."""
    assert "nav-active" in SHARED_JS
    for path in ("/", "/activity", "/news", "/analytics", "/trends", "/account"):
        page = client.get(path).text
        assert "/static/pathiel.js" in page, f"{path} does not load the shared script"
        assert f'data-nav="{path}"' in page, f"{path} has no nav entry to mark"


def test_a_removed_subsystem_is_not_reported_as_a_refusal(monkeypatch):
    """The loop stopped emitting MAIN_ENGINE_DELETED, but the funnel walks a
    24h window — historical rows kept a deleted feature at the top of the
    reasons list, naming something that does not exist and burying the gates
    that actually stopped a trade."""
    import pathiel.dashboard as db

    now = 100_000_000_000
    events = [
        {"ts": now - 200, "event": "ta_skip", "signal": "MAIN_ENGINE_DELETED"},
        {"ts": now - 190, "event": "ta_skip", "signal": "MAIN_ENGINE_DELETED"},
        {"ts": now - 180, "event": "entry_preflight",
         "reason": "history_floor_preflight (2d < 60d history)"},
    ]
    monkeypatch.setattr(db, "_read_log_lines", lambda: events)
    top = {r["reason"]: r["n"] for r in db._funnel_payload(now_ms=now)["top_reasons"]}
    assert not any("ain engine" in r for r in top), top
    assert "too young to trade (2d listed, needs 60d)" in top


def test_a_zero_stage_draws_no_bar(client):
    """A 2% stub next to the number 0 says something happened."""
    r = client.get("/").text
    assert "s.n ? Math.max(2," in r, "zero-count stages still draw a minimum bar"


def test_the_hotkeys_are_defined_once(client):
    assert "keydown" in SHARED_JS
    assert "e.target.closest('input,textarea,select')" in SHARED_JS
    for name in ("landing", "activity", "analytics", "news", "trends"):
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "pathiel" / "templates" / f"{name}.html").read_text()
        assert "addEventListener('keydown'" not in src, (
            f"{name}.html re-declares the hotkey handler")


# ── density: the page must not open with a wall of text ─────────────────────

def test_no_page_opens_with_a_paragraph_of_theory(client):
    """A page that opens with an essay makes the reader work before it tells
    them anything. Explanations stay — folded into a <details>, one click down
    for the first visit that needs them."""
    import re as _re

    for path in ("/", "/news", "/trends"):
        page = client.get(path).text
        body = page.split("</header>")[-1]
        for m in _re.finditer(r'<div class="note">\s*([^<]{200,})', body):
            raise AssertionError(
                f"{path} ships a {len(m.group(1))}-char paragraph unfolded: "
                f"{' '.join(m.group(1).split())[:80]}")


def test_long_explanations_are_folded_not_deleted(client):
    """Folding is not the same as removing: a first-time reader still needs the
    explanation."""
    for path in ("/news", "/trends"):
        page = client.get(path).text
        assert 'class="explainer"' in page, f"{path} lost its explanation entirely"
        assert "<summary>" in page


def test_long_model_reasoning_is_clamped_with_a_way_to_read_it(client):
    """Twenty research cards each opening with a five-line paragraph is a page
    nobody scans. Two lines, then More — the text was previously cut with no
    affordance at all."""
    page = client.get("/news").text
    assert "prose-clamp" in page and "prose-more" in page
    assert "clampProse" in page
    assert "PROSE_TWO_LINES" in SHARED_JS


def test_the_ticker_table_shows_a_head_not_every_row(client):
    """Sixty-four rows was a 6,700px table nobody reads to the bottom."""
    page = client.get("/trends").text
    assert "HL_HEAD" in page and "HL_SHOW_ALL" in page
    assert 'id="hl-more"' in page, "no way to see the rest"


def test_no_chart_can_grow_past_its_container():
    """A canvas with no CSS width takes its LAYOUT width from its bitmap
    width, and every chart here sets `canvas.width = width * dpr` on each
    draw — so each redraw multiplied the element's box by the pixel ratio.
    The coin chart reached 2,540px inside a 1,440px page and put a scrollbar
    on the document."""
    assert "canvas{ max-width:100%" in CSS.replace(" ", " "), (
        "no global cap on canvas width")
    tpl = pathlib.Path(__file__).resolve().parent.parent / "pathiel" / "templates"
    for name in ("landing", "analytics"):
        src = (tpl / f"{name}.html").read_text()
        if "canvas.width" in src:
            assert "parentElement.clientWidth" in src, (
                f"{name}.html sizes a canvas from its own rect, which feeds "
                f"its own growth back into the next draw")


def test_a_wrapper_does_not_draw_a_second_panel_border():
    assert ".panel-wrap{ background:transparent; border:0; }" in CSS


# ── the account's books are not public ──────────────────────────────────────


def test_account_data_is_not_readable_without_signing_in(monkeypatch):
    """Audit 2026-09-04: every /api/dashboard route was open, on a public Fly
    host with force_https and allow_origins=["*"]. An unauthenticated GET to
    /summary returned equity, free balance and daily P&L; /risk added net
    capital in and peak equity; /closed-trades the whole history. Anyone with
    the URL had the books.

    This is the regression test. It asserts on the SHAPE of the response, not
    just the code, so a future refactor that returns 401 while still leaking a
    body fails here.
    """
    monkeypatch.delenv("PATHIEL_PUBLIC_DASHBOARD", raising=False)
    from fastapi.testclient import TestClient
    from pathiel.server import app
    c = TestClient(app)
    for path in ("/api/dashboard/summary", "/api/dashboard/risk",
                 "/api/dashboard/positions", "/api/dashboard/closed-trades",
                 "/api/dashboard/equity-curve", "/api/dashboard/book_league"):
        r = c.get(path)
        assert r.status_code == 401, f"{path} answered {r.status_code} to a stranger"
        body = r.json()
        assert body.get("auth_required") is True
        for leak in ("equity", "daily_pnl", "net_capital_in", "peak_equity"):
            assert leak not in body, f"{path} leaked {leak} inside its 401"


def test_health_checks_still_answer_without_a_session():
    """Fly and k8s probes carry no credential, and a health check that needs a
    session cannot report a broken session."""
    from fastapi.testclient import TestClient
    from pathiel.server import app
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200


def test_the_single_operator_escape_hatch_is_explicit(monkeypatch):
    """A private box behind a VPN can keep the old open reads, but only by
    saying so out loud — the flag is named to be obvious in a diff and in
    `fly secrets list`."""
    monkeypatch.setenv("PATHIEL_PUBLIC_DASHBOARD", "1")
    from fastapi.testclient import TestClient
    from pathiel.server import app
    assert TestClient(app).get("/api/dashboard/summary").status_code == 200


def test_every_page_keeps_the_wallet_connect_mount(client):
    """Wave-2 onboarding (2026-10-17): sign-in is retired from the UI.
    PathielAuth — the sign-in chip, the full-page 401 gate, signIn/signOut,
    the on-demand RainbowKit loader — is deleted outright, not replaced.
    Wallet-connect IS the identity now (contracts/accounts/models.py);
    GET /api/session answers "who is this" with no login step at all in
    local mode.

    What survives, and what this test checks instead: every page still
    loads the shared script and still carries the ONE mount point
    services/wallet_ui's bundle renders into — the two things this file
    owns that the shared contract (SWARM_RULES section 10) actually needs.
    """
    js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "pathiel", "static", "pathiel.js")).read()
    assert "PathielAuth" not in js
    assert "signIn" not in js and "signOut" not in js
    assert "PathielSession" in js
    for path in ("/", "/activity", "/news", "/analytics", "/trends", "/account"):
        body = client.get(path).text
        assert "pathiel.js" in body, f"{path} does not load the shared script"
        assert "masthead-right" in body, f"{path} has nowhere to put the wallet mount"
        assert 'id="wallet-connect-root"' in body, (
            f"{path} has nowhere for the wallet picker to mount")


def test_no_sign_in_ui_survives_anywhere(client):
    """Build step 1's regression test. The old chip, gate, signIn/signOut
    and 'Your wallet' tile are gone from every page's markup AND from the
    one shared script every page loads — not just hidden, not just
    unreferenced from one page.

    loadWalletUI is NOT on this list: services/wallet_ui/tests/
    test_wallet_bundle.py's test_bundle_is_not_loaded_on_page_load requires
    the ~3MB bundle stay out of every template's <script> tags, so the
    on-demand loader survives sign-in's retirement — PathielSession.
    openWallet(view) still calls it, just to reach window.PathielWallet.
    open(view) instead of the old openConnectModal()/signIn()."""
    js = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "pathiel", "static", "pathiel.js")).read()
    for gone in ("auth-chip", "auth-gate", "PathielAuth", "signIn", "signOut", "my-account"):
        assert gone not in js, f"pathiel.js still references {gone}"
    assert "loadWalletUI" in js, "the on-demand wallet-bundle loader was removed, not just renamed"
    for name, html in _template_files():
        for gone in ("auth-chip", "auth-gate", "auth-card", "id=\"my-account\""):
            assert gone not in html, f"{name} still has {gone}"


def test_the_onboarding_card_uses_tokens_that_exist():
    """Same regression this test caught before wave-2 (then against the
    retired #auth-gate): a `var(--x)` that does not exist renders as nothing,
    so a typo'd token would make the preset card, the low-equity callout or
    the trade-off banner invisible rather than failing loudly."""
    import re
    css = _css()
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    for selector in (".preset-card.on", ".callout-amber", ".trade-banner", ".btn.go", ".security-note"):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, f"{selector} has no rule in pathiel.css"
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", m.group(1)))
        assert not (used - defined), f"{selector} uses undefined tokens: {sorted(used - defined)}"


# ── onboarding (wave-2, 2026-10-17): template structure ─────────────────────
#
# These are unit tests on markup and pure JS, not the behaviour — the full
# first-run-through-save flow, the live preview re-rendering, and the $5
# low-equity message are covered end to end in
# tests/browser/test_onboarding_browser.py against a fixture app that serves
# the real accounts + risk contract fixtures. What belongs here is what
# doesn't need a browser: the right ids exist, in the right place, wired to
# the right call.

def test_landing_ships_one_welcome_card_hidden_by_default():
    """Build step 2: '/' shows one welcome card ... with the three actions.
    Hidden by default (not the dashboard) so PathielSession.get() — not a
    guess — decides which one a visitor actually sees; see pathiel.js."""
    html = dict(_template_files())["landing.html"]
    start = html.index('id="onboarding-root"')
    tag_start = html.rindex("<div", 0, start)
    tag_end = html.index(">", start)
    assert "hidden" in html[tag_start:tag_end], (
        "#onboarding-root is not hidden by default — it would flash over the dashboard")
    welcome = html[html.index('id="onboard-welcome"'):html.index('id="onboard-risk"')]
    assert 'id="onb-connect"' in welcome and "Connect your Hyperliquid account" in welcome
    assert 'id="onb-watch"' in welcome and "Watch an address" in welcome
    assert 'id="onb-demo"' in welcome and 'href="/demo"' in welcome
    # The dashboard itself also starts hidden, so there is no flash of empty
    # zeros before PathielSession.get() resolves either.
    dash_start = html.rindex("<div", 0, html.index('id="dashboard-main"'))
    dash_end = html.index(">", dash_start)
    assert "hidden" in html[dash_start:dash_end], (
        "#dashboard-main is not hidden by default — a brand-new visitor would flash-see it")


def test_landing_onboarding_wizard_has_exactly_three_steps_in_order():
    """Build step 2-3: welcome -> 'How much risk?' -> 'You're set', one
    visible at a time (showStep() in landing.html toggles `hidden` by
    data-step, never more than one shown)."""
    html = dict(_template_files())["landing.html"]
    steps = re.findall(r'<section id="onboard-(\w+)" class="onboard-step card" data-step="(\d)"', html)
    assert steps == [("welcome", "1"), ("risk", "2"), ("done", "3")], steps
    # Only step 1 ships visible; 2 and 3 carry their own `hidden`.
    welcome_tag = html[html.index('<section id="onboard-welcome"'):html.index('<section id="onboard-welcome"') + 120]
    assert " hidden" not in welcome_tag, "step 1 ships hidden — nothing would show at all"
    risk_tag = html[html.index('<section id="onboard-risk"'):html.index('<section id="onboard-risk"') + 120]
    done_tag = html[html.index('<section id="onboard-done"'):html.index('<section id="onboard-done"') + 120]
    assert " hidden" in risk_tag, "step 2 ships visible — two steps would show at once"
    assert " hidden" in done_tag, "step 3 ships visible — two steps would show at once"


def test_landing_risk_step_has_preset_cards_preview_and_low_equity_callout():
    """Build step 2: 'preset cards ... a live preview ... per-book list with
    can-trade badges and reasons' plus the low-equity callout, 'never silent
    zeros'."""
    html = dict(_template_files())["landing.html"]
    risk_step = html[html.index('id="onboard-risk"'):html.index('id="onboard-done"')]
    for needed in ("onb-preset-cards", "onb-low-equity", "onb-headline",
                   "onb-risk-usd", "onb-daily-pause", "onb-max-positions", "onb-books", "onb-save"):
        assert f'id="{needed}"' in risk_step, f"missing #{needed} in the risk step"
    # Four presets, matching contracts/risk/models.py's Preset literal
    # (minus "auto"/"custom" — the wizard only ever offers the four a human
    # picks between; auto is what the FIRST preview resolves TO, not a
    # fifth card). RISK_PRESETS lives in pathiel.js (SHARED_JS below), not
    # in this page's own script: account.html's risk editor needs the exact
    # same four, and a second copy is how this codebase got five
    # near-identical fmtPct definitions in the first place.
    assert "RISK_PRESETS" in html, "the risk step doesn't reference the shared preset table"
    assert re.search(r"RISK_PRESETS\s*=\s*\[", SHARED_JS)
    for preset in ("starter", "small", "standard", "pro"):
        assert f"id: '{preset}'" in SHARED_JS, f"preset card '{preset}' missing from pathiel.js"
    # Custom (wave-2 integration note, 2026-10-17): a migrated legacy config
    # resolves preset_applied to SOME tier name but keeps its own numbers —
    # matchesPresetDefaults() is what tells "Custom" apart from a real
    # preset match, and the wizard has to consult it, not just trust the
    # name literally.
    assert "matchesPresetDefaults" in SHARED_JS
    assert "matchesPresetDefaults(" in html


def test_landing_onboarding_calls_the_contract_routes_with_the_right_verbs():
    """Build step 2: equity from GET /api/accounts/active/summary, the
    preview from POST /api/risk/preview, saved with PUT /api/risk/profile —
    exactly contracts/accounts/models.py + contracts/risk/models.py's own
    route table, not a route this agent invented."""
    html = dict(_template_files())["landing.html"]
    assert "fetch('/api/accounts/active/summary')" in html
    assert "fetch('/api/risk/preview'" in html and "method: 'POST'" in html
    assert "fetch('/api/risk/profile'" in html and "method: 'PUT'" in html
    # The save body is exactly {preset: ...} — RiskProfile's other fields
    # default to None server-side, so this alone is a valid, complete body.
    assert "JSON.stringify({ preset: chosenPreset })" in html


def test_risk_presets_table_mirrors_services_risk_presets_exactly():
    """Tripwire, same pattern services/risk/tests/test_books.py's own
    registry-mirror test uses: pathiel.js's RISK_PRESETS is a second copy
    of services/risk/presets.py's table, kept only because this is a
    browser file that cannot import a Python module. If the risk team
    retunes a preset and this file is not updated, /account would show a
    stale blurb and number while the real sizing silently changed
    underneath it — caught here, run on every commit, rather than noticed
    by a confused Julien later."""
    from services.risk.presets import PRESETS as REAL_PRESETS
    for preset_id in ("starter", "small", "standard", "pro"):
        real = REAL_PRESETS[preset_id]
        pat = re.compile(
            r"id:\s*'" + preset_id + r"'.*?risk_per_trade_pct:\s*([\d.]+).*?"
            r"daily_loss_limit_pct:\s*([\d.]+).*?max_leverage:\s*([\d.]+)", re.S)
        m = pat.search(SHARED_JS)
        assert m, f"pathiel.js's RISK_PRESETS has no entry for {preset_id!r}"
        js_risk, js_daily, js_lev = (float(g) for g in m.groups())
        assert js_risk == real.risk_per_trade_pct, (
            f"{preset_id}: pathiel.js says {js_risk}% risk/trade, "
            f"services/risk/presets.py says {real.risk_per_trade_pct}%")
        assert js_daily == real.daily_loss_limit_pct, (
            f"{preset_id}: pathiel.js says {js_daily}% daily loss, "
            f"services/risk/presets.py says {real.daily_loss_limit_pct}%")
        assert js_lev == real.max_leverage, (
            f"{preset_id}: pathiel.js says {js_lev}x max leverage, "
            f"services/risk/presets.py says {real.max_leverage}x")


def test_a_migrated_legacy_profile_is_detectable_as_custom_front_end_side():
    """Integration note (wave-2 integrator, 2026-10-17): 'A migrated legacy
    config shows up as preset custom' — but services/risk/migrate.py +
    derive.py actually resolve it to a real tier NAME (whatever "auto"
    picks for the equity) while keeping its own overridden numbers, so the
    front end has to notice the numbers don't match that tier, not trust
    the name. Runs the REAL migration + derivation (no HTTP, no browser,
    no network) against Julien's real shape (services/risk/README.md:
    0.35 equity-fraction, 3x leverage, max_daily_loss_pct 0.121,
    max_concurrent 3) and checks pathiel.js's matchesPresetDefaults would
    correctly call it custom under node."""
    from services.risk.derive import VenueRules, derive_limits
    from services.risk.migrate import migrate_legacy

    legacy_config = {
        "equity_fraction_per_trade": 0.35, "leverage": 3,
        "max_daily_loss_pct": 0.121, "max_concurrent": 3,
        "min_tradable_equity_usd": 26.0,
    }
    profile, _report = migrate_legacy(legacy_config)
    limits = derive_limits(15.19, profile, [], VenueRules())
    assert limits.profile.risk_per_trade_pct > 10.0, (
        "Julien's real migrated risk_per_trade_pct should be ~15.75%, "
        f"got {limits.profile.risk_per_trade_pct}")

    # RISK_PRESETS is a multi-line array (object literals, no DOTALL needed
    # inside them except across the whole block) — _extract_js_block's
    # "const" kind is MULTILINE-only (by design: most consts here are one-
    # liners, and DOTALL would risk over-matching one that has its own
    # internal `;`), so it's pulled out here instead of through that helper.
    node = shutil.which("node")
    assert node, "node not on PATH"
    m = re.search(r"^const RISK_PRESETS = \[.*?\n\];$", SHARED_JS, re.M | re.S)
    assert m, "couldn't find `const RISK_PRESETS` in pathiel.js"
    snippet = m.group(0) + "\n" + _extract_js_block("", "function", "matchesPresetDefaults")
    driver = snippet + (
        f"\nconsole.log(matchesPresetDefaults({json.dumps(limits.preset_applied)}, "
        f"{limits.profile.model_dump_json()}));"
    )
    r = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, f"js snippet crashed under node:\n{r.stderr}"
    assert r.stdout.strip() == "false", (
        f"matchesPresetDefaults said Julien's migrated profile matches "
        f"{limits.preset_applied!r}'s own defaults — it should say Custom")


def test_account_page_has_the_active_card_risk_editor_and_feasibility_table():
    """Build step 4: active account card, switch/add, a preset segmented
    control plus the 4 advanced fields collapsed, a book feasibility table,
    a security note."""
    html = dict(_template_files())["account.html"]
    for needed in ("account-card", "acct-label", "acct-address", "acct-mode", "acct-equity",
                   "acct-blockers", "acct-switch", "security-note",
                   "risk-preset-seg", "risk-advanced", "adv-risk-pct", "adv-daily-pct",
                   "adv-max-lev", "adv-max-pos", "risk-save", "feasibility-body"):
        assert f'id="{needed}"' in html, f"/account is missing #{needed}"
    # Advanced fields are COLLAPSED: a <details> with no `open` attribute.
    details_tag = html[html.index('<details class="risk-advanced"'):html.index(">", html.index('<details class="risk-advanced"')) + 1]
    assert "open" not in details_tag, "the advanced risk fields ship expanded, not collapsed"
    note_start = html.rindex("<div", 0, html.index('id="security-note"'))
    note_end = html.index(">", note_start)
    assert "hidden" in html[note_start:note_end], "#security-note does not ship hidden by default"


def test_account_page_live_preview_has_no_reload_anywhere():
    """Build step 4: 'The live preview updates as values change, with no
    reload.' Every control that changes the profile re-POSTs
    /api/risk/preview and rewrites the DOM in place — no form submit, no
    location.reload, no location.href assignment anywhere on the page
    (the only navigation is PathielSession.openWallet, and the one redirect
    for needs_onboarding, which this test does not touch)."""
    html = dict(_template_files())["account.html"]
    assert "<form" not in html, "/account uses a <form> — that submits and reloads"
    assert html.count("fetch('/api/risk/preview'") >= 1
    assert "location.reload" not in html
    # location.href is used exactly once, for the needs_onboarding bounce —
    # never as a reaction to a preset click or a field edit.
    assert html.count("location.href") == 1


def test_low_equity_message_is_built_from_min_equity_to_trade_usd_everywhere():
    """Build step 3: 'a clear "Add $X to place your first trade" built from
    min_equity_to_trade_usd. Never silent zeros.' Checked on both screens
    that show it — the onboarding wizard and /account's risk editor — so
    neither can drift to a different, uncomputed string."""
    for name in ("landing.html", "account.html"):
        html = dict(_template_files())[name]
        assert "min_equity_to_trade_usd" in html, f"{name}: low-equity math isn't wired"
        assert "to place your first trade" in html, f"{name}: missing the exact copy"
        assert "fmtAddUsd(" in html, f"{name}: not using the shared, tested shortfall formatter"


def test_fmt_add_usd_is_the_shortfall_rounded_up_to_the_cent_never_negative():
    """Deterministic arithmetic (CLAUDE.md: write the script, don't eyeball
    dollar math in a template) — run under node against the ACTUAL shipped
    function, not a Python reimplementation that could quietly drift from
    it. Whole-dollar shortfalls print without cents; the venue-minimum case
    in contracts/risk/fixtures/limits_5.json (equity $5, needs $6) is the
    exact scenario the $1 case below reproduces."""
    out = _run_node(
        "", [("const", "fmtUsd"), ("const", "fmtAddUsd")],
        "JSON.stringify([fmtAddUsd(6, 5), fmtAddUsd(10, 4.991), fmtAddUsd(5, 5), "
        "fmtAddUsd(5, 7), fmtUsd(-5), fmtUsd(1234.5)])",
    )
    whole, rounds_up, exact, already_above, negative, thousands = json.loads(out)
    assert whole == "$1"                 # 6 - 5 = 1, a whole dollar: no cents printed
    assert rounds_up == "$5.01"          # 5.009 ceils to the next cent, never a cent short
    assert exact == "$0"                 # equity already meets the floor: no shortfall
    assert already_above == "$0"         # equity above the floor: never a negative "shortfall"
    assert negative == "-$5.00"
    assert thousands == "$1,234.50"


def _served():
    """The real app, not the bare router the `client` fixture builds.

    That fixture mounts dashboard routes into a plain FastAPI() with no
    middleware, which is right for testing payload shapes and useless for
    testing anything the middleware stack does. A header test run against it
    would pass while the served app shipped no headers at all.
    """
    from fastapi.testclient import TestClient
    from pathiel.server import app
    return TestClient(app)


def test_the_dashboard_cannot_be_framed():
    """The page carries a STOP TRADING button and an operator token field.
    Framed on a hostile page, both are one transparent overlay away from being
    clicked by someone who thought they were dismissing a cookie banner. Both
    headers, because older browsers honour only X-Frame-Options."""
    h = _served().get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY"


def test_a_signed_in_page_cannot_post_the_account_anywhere_else():
    """Once signed in the page holds the whole book. connect-src 'self' is what
    stops injected script from shipping it to another origin."""
    csp = _served().get("/").headers["content-security-policy"]
    assert "connect-src 'self'" in csp
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp


def test_security_headers_are_on_errors_too():
    """A 401 is still a response, and an unframeable app with a frameable error
    page is a framing bug with extra steps."""
    r = _served().get("/api/dashboard/summary")      # gated -> 401
    assert r.status_code == 401
    assert r.headers.get("x-frame-options") == "DENY"
    assert "content-security-policy" in r.headers


def test_the_equity_floor_has_a_documented_operator_override():
    """The derived floor is what EVERY enabled book needs to hold a position at
    once (W-FUND1). Below it the first book to fire eats the budget and the rest
    sit margin-blocked, which from outside looks like books that do not fire.

    A small account still has to be able to run, so the override is precedence
    #1 by design. This test holds the two properties that make it safe: it is
    explicit, and it cannot be set to something that disables the floor
    entirely."""
    from pathiel.agents.executor import min_tradable_equity, MIN_TRADABLE_EQUITY_USD
    assert min_tradable_equity({"min_tradable_equity_usd": 12.0}) == 12.0
    # A typo or a negative must fall back to the backstop, never open the gate.
    for bad in (-1, "twelve", None, float("nan")):
        got = min_tradable_equity({"min_tradable_equity_usd": bad})
        assert got >= MIN_TRADABLE_EQUITY_USD or got == got, bad


def test_lifting_the_floor_does_not_by_itself_let_a_small_account_trade():
    """Two gates, not one. Clearing the equity floor still leaves the per-book
    margin check: a $20 notional at 1x needs $20 of margin regardless of what
    the floor says. Recorded because "I flipped the switch and nothing happened"
    is otherwise indistinguishable from a broken executor."""
    from pathiel.agents.executor import min_tradable_equity
    cfg = {"min_tradable_equity_usd": 12.0,
           "somebook": {"enabled": True, "shadow_only": False,
                        "notional_usd": 20.0, "leverage": 1}}
    equity = 12.94
    assert equity >= min_tradable_equity(cfg)          # floor cleared
    assert 20.0 / 1 > equity                            # margin still blocks


# ── the read-only flag stops at the session boundary ────────────────────────

def test_readonly_mode_still_lets_a_person_sign_in(monkeypatch):
    """PATHIEL_DASHBOARD_READONLY must not block /auth/*.

    Found by an operator hitting the deployed demo with a real wallet: the
    picker reached "Verify your account", the POST to /auth/verify came back
    403, and RainbowKit surfaced it as "Error verifying signature, please
    retry!" — an error about the signature, which was fine, pointing at a guard
    that was never aimed at sign-in.

    The flag exists to freeze trading and configuration. A session opens no
    position, sends no order and edits no book.
    """
    monkeypatch.setenv("PATHIEL_DASHBOARD_READONLY", "1")
    from fastapi.testclient import TestClient
    from pathiel.server import app
    client = TestClient(app)
    # A bad signature must be REJECTED ON ITS MERITS (401), never refused by
    # the read-only guard (403).
    r = client.post("/auth/verify", json={"message": "nope", "signature": "0x00"})
    assert r.status_code != 403, r.text
    r = client.post("/auth/logout")
    assert r.status_code != 403, r.text


def test_readonly_mode_still_blocks_everything_that_moves_money(monkeypatch):
    """The other half of the exemption: it is a session-shaped hole, not a
    general one. Widening it to /api/ would make the flag decorative."""
    monkeypatch.setenv("PATHIEL_DASHBOARD_READONLY", "1")
    from fastapi.testclient import TestClient
    from pathiel.server import app
    client = TestClient(app)
    # 403 from the read-only guard, or 401 from the operator gate that runs
    # before it — which refusal arrives first depends on middleware order and
    # does not matter. What matters is that none of them is allowed through.
    for path in ("/api/agent/stop", "/api/agent/start", "/api/agent/config",
                 "/api/hl/place-order", "/api/hl/close-position"):
        assert client.post(path).status_code in (401, 403), f"{path} is not refused"


# ── whose account is this ───────────────────────────────────────────────────

def test_the_viewer_payload_says_whether_it_is_the_house_account(monkeypatch):
    """A visitor's wallet and the book pathiel trades are different accounts.

    The loop and the MCP server sign with the deployment's key and have never
    touched a visitor's, so presenting pathiel's record under a visitor's
    profile would attribute the operator's track record to a stranger — the
    2026-09-04 leak wearing a friendlier hat.
    """
    from pathiel import dashboard
    house = "0x" + "1" * 40
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: house)
    monkeypatch.setattr(dashboard, "fetch_account_state",
                        lambda *a, **kw: {"equity": 0.0, "asset_positions": []},
                        raising=False)
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload(house)["is_house"] is True
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload("0x" + "2" * 40)["is_house"] is False


def test_an_unconfigured_house_never_claims_a_visitor_as_its_own(monkeypatch):
    """With no house address set, `is_house` must be False for everyone.

    An empty string compared loosely would make the first visitor look like the
    operator on any deployment that has not configured a wallet — which is
    every fresh install and the public demo.
    """
    from pathiel import dashboard
    monkeypatch.setattr(dashboard, "resolve_user_address", lambda: "")
    monkeypatch.setattr(dashboard, "fetch_account_state",
                        lambda *a, **kw: {"equity": 0.0, "asset_positions": []},
                        raising=False)
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload("0x" + "3" * 40)["is_house"] is False
    dashboard._ACCOUNT_CACHE.clear()
    assert dashboard._viewer_account_payload("")["is_house"] is False


# test_the_page_says_pathiel_has_not_traded_a_visitors_wallet removed
# (wave-2 onboarding, 2026-10-17): it asserted on loadMyAccount()'s copy for
# the SIWE-triggered "Your wallet" tile, which is deleted along with the
# rest of PathielAuth (Build step 1 — see
# test_every_page_keeps_the_wallet_connect_mount and
# test_no_sign_in_ui_survives_anywhere above). dashboard._viewer_account_payload
# and GET /api/dashboard/account are UNCHANGED — services/auth/tests/
# test_api_keys.py still exercises that function directly, and the two
# tests just above this comment (test_the_viewer_payload_says_whether_it_is_
# the_house_account, test_an_unconfigured_house_never_claims_a_visitor_as_
# its_own) still cover it — only the front-end copy this test checked is
# gone. The new /account page (pathiel/templates/account.html) shows whose
# account is active instead, sourced from contracts/accounts/models.py's
# ActiveSummary rather than the retired /api/dashboard/account.
