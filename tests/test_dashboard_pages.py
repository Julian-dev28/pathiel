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
import re
import shutil
import subprocess
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
    # demolition 2026-07-18: majors_swing / young_listings / news_catalyst
    # books deleted (never validated / refuted); +xs_xyz_equities (W-X2
    # ROBUST); +news_surge_short +mover_pass_short (mover_pass LONG
    # (reverse-refuted audit 2026-07-20) +news_surge_multi (worldmonitor
    # thesis, operator flip 2026-07-21) +news_ta_aligned (VALIDATED aligned
    # quadrant, 2026-07-22) — 13 books
    assert set(rows) == db._KNOWN_BOOK_NAMES and len(rows) == 13
    assert rows["engulf_short"]["status"] == "live"
    assert rows["rally_exhaustion"]["status"] == "live"   # no shadow_only key → live
    assert rows["xs_momentum"]["size"] == "4/leg basket"
    assert rows["xs_xyz_equities"]["status"] == "live"
    assert rows["xs_xyz_equities"]["size"] == "5/leg basket"
    assert rows["extreme_fade"]["size"] == "0.4x eq @ 1x"
    assert rows["unlock_short_runin"]["size"] == "$20 @ 1x"
    assert "news_ta_aligned" in rows and rows["news_ta_aligned"]["thesis"]
    assert all(r["thesis"] for r in rows.values())


def test_books_payload_missing_config_is_off(monkeypatch):
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    rows = db._books_payload()
    assert len(rows) == 13 and all(r["status"] == "off" for r in rows)


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
    assert ctx[0]["citations"] == [
        {"url": "https://example.com/sol-article", "title": "example.com/sol-article"}]
    assert ctx[0]["news_risk"] == "elevated"


def test_news_payload_empty_ledger(monkeypatch):
    _write_news_ledger([])
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    payload = db._news_payload()
    assert payload["items"] == [] and payload["research_context"] == []
    assert payload["stats"] == {"reads_today": 0, "breaking_today": 0,
                                "last_read_ts": None}


def test_news_payload_stats_fresh_and_title_ages(monkeypatch):
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


def test_news_payload_title_urls_passthrough(monkeypatch):
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
    assert 'data-nav="/analytics"' in r.text
    # nav is DASHBOARD · ACTIVITY · NEWS — config/operator tabs deleted
    assert 'data-nav="/config"' not in r.text
    assert 'data-nav="/operator"' not in r.text
    # token entry moved to the landing footer (localStorage only)
    assert "op-token-btn" in r.text
    # how-it-works sits at the BOTTOM: below the books dropdown, above the
    # footer (operator order 2026-07-12)
    assert r.text.index('id="books-wrap"') < r.text.index(HOW_IT_WORKS)
    assert r.text.index(HOW_IT_WORKS) < r.text.index("<footer")


def test_config_and_operator_pages_are_gone(client):
    """Operator order 2026-07-12: /config and /operator are deleted — 404 is
    the EXPECTED behavior, and no page links to them anymore."""
    assert client.get("/config").status_code == 404
    assert client.get("/operator").status_code == 404
    assert client.get("/api/dashboard/config").status_code == 404
    assert client.get("/api/dashboard/operator/trackers").status_code == 404
    assert client.post("/api/dashboard/operator/terminal",
                       json={"command": "status"}).status_code == 404
    for path in ("/", "/activity", "/news", "/analytics"):
        page = client.get(path).text
        assert 'data-nav="/config"' not in page, path
        assert 'data-nav="/operator"' not in page, path


def test_all_pages_render_and_are_self_contained(client):
    for path in ("/", "/activity", "/news", "/analytics"):
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


def test_landing_books_dropdown_wraps_flow(client):
    """Operator reversal 2026-07-12: live books is a dropdown again — round-7
    collapse behavior wrapped around the current flowing book-row styling."""
    r = client.get("/").text
    assert 'id="books-toggle"' in r and 'id="books-wrap"' in r
    assert "hermes-books-open" in r                  # state remembered in localStorage
    assert 'class="books-wrap"' in r                 # static HTML ships collapsed
    assert "books-open .books-wrap" in r             # CSS max-height/opacity transition
    assert ".books-head .chev" in r                  # chevron toggle affordance
    assert "live books" in r                         # header + counts always visible
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


def test_pixel_cat_on_every_tab(client):
    """The cat is on every tab (operator order 2026-07-15), not just the
    dashboard — crafted 16x16 SVG pixel art with status-driven states, not
    an emoji, on all four pages, each independently polling for its state."""
    for path in ("/", "/activity", "/news", "/analytics"):
        r = client.get(path).text
        assert 'id="pixel-cat"' in r, f"{path}: missing the cat"
        assert "<rect" in r and "crispEdges" in r, f"{path}: not real pixel art"
        for state in ("cat-sleep", "cat-sad", "cat-alert", "cat-bounce"):
            assert state in r, f"{path}: missing cat state {state}"
        assert "c-tail-a" in r and "c-tail-b" in r, f"{path}: no tail flick"
        assert "c-zzz" in r, f"{path}: no sleep pixels"
        assert "c-ears-p" in r and "c-ears-f" in r, f"{path}: no ear states"
        assert "updateCat" in r, f"{path}: cat not driven by summary data"
        assert "prefers-reduced-motion" in r, f"{path}: no static-cat fallback"
        # every non-landing tab fetches its own /api/dashboard/summary poll
        # to drive the cat rather than sharing state across pages
        if path != "/":
            assert "refreshCat" in r and "/api/dashboard/summary" in r, path


def test_skeleton_shimmer_replaces_loading_text(client):
    """Modern loading state (operator: 'best design principles, modernity') —
    analytics' initial panel placeholders are shimmer bars, not bare
    'loading…' text, matching how Robinhood/Stripe/Linear-class apps signal
    an in-flight fetch."""
    r = client.get("/analytics").text
    assert "skeleton" in r and "shimmer" in r
    assert r.count('class="skeleton"') >= 5


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


def _extract_js_block(html: str, kind: str, name: str) -> str:
    """Pull one pure-logic const one-liner or multi-line function out of a
    served page's <script> block by name, so it can be executed in
    isolation under node — no DOM/fetch dependency, safe outside a browser."""
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


def test_eight_bit_texture_everywhere(client):
    for path in ("/", "/activity", "/news", "/analytics"):
        page = client.get(path).text
        assert "4px 4px 0" in page, f"{path}: missing hard pixel offset shadow"
        assert "repeating-linear-gradient" in page, f"{path}: missing scanline texture"


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
    assert "open positions" in r
    assert "refreshPositions" in r
    # placed between the KPI row and the equity curve
    assert r.index('id="positions-body"') < r.index('id="equity-chart"')
    assert r.index("last tick") < r.index('id="positions-body"')
    # liq proximity danger treatment + origin badges + PnL tick animation
    assert "liq-danger" in r
    assert "MANUAL" in r and "originBadge" in r
    assert "tick-up" in r and "tick-dn" in r


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
    assert "nothing actionable since" in act      # empty-tape copy (trade pane)
    assert "entry refused" in act                 # gate groups read as flight-log
    assert "quiet stream" in news                 # sparse-ledger empty state copy
    assert "breaking-pulse" in news               # stronger pulse on breaking items
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
    assert len(rows) == 13
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
    # unknown reasons pass through untouched
    assert h("some_new_gate (whatever)") == "some_new_gate (whatever)"
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
    assert stages == {"scans": 2, "candidates": 5, "researched": 1, "executed": 1}
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
    assert stages["executed"] == 2                 # book_open + execute(True)
    assert set(d["coins"]) == {"BTC", "ETH", "SOL"}


def test_funnel_payload_empty_log(monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    d = db._funnel_payload(window_s=86400)
    assert all(s["n"] == 0 for s in d["funnel"])
    assert d["top_reasons"] == [] and d["coins"] == []


def test_book_league_merges_summary_with_config(monkeypatch, tmp_path):
    from hermes_trader.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    with open(tmp_path / "extreme_fade.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 1000, "coin": "BTC", "signal_bar_t": 1000,
                             "entry_ref_px": 100.0, "horizon_days": 3.0}) + "\n")
    with open(tmp_path / "whale_flow.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": 2000, "coin": "ETH", "signal_bar_t": 2000,
                             "entry_ref_px": 50.0, "horizon_days": 1.0}) + "\n")
    monkeypatch.setattr(db, "read_agent_config", lambda: dict(FIXTURE_CONFIG))
    rows = {r["book"]: r for r in db._book_league_payload(now_ms=2_000_000_000)}
    assert rows["extreme_fade"]["status"] == "live"
    assert rows["extreme_fade"]["size"] == "0.4x eq @ 1x"
    assert rows["extreme_fade"]["resolved"] == 1        # far past its 3d horizon
    # whale_flow REFUTED + removed 2026-07-22 -> in _REMOVED_BOOKS, never renders
    assert "whale_flow" not in rows


def test_book_league_removed_books_never_render(monkeypatch, tmp_path):
    """premium_fade_short / neg_funding_fade: module deleted, ledger fully
    graded and REFUTED, and — operator order 2026-07-17 — removed from the
    UI entirely. Their ledger files stay on disk as evidence, but the league
    payload must skip them; a genuinely still-accruing lane like whale_flow
    keeps its 'recorder' status."""
    from hermes_trader.agents import shadow_ledger
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
    assert rows["news_ta_quadrant"]["status"] == "recorder"
    # no row can ever carry the retired 'dead' status again
    assert all(r["status"] != "dead" for r in rows.values())


def test_book_league_empty_ledger_dir(monkeypatch, tmp_path):
    from hermes_trader.agents import shadow_ledger
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
    now = 200 * db._HOUR_MS if hasattr(db, "_HOUR_MS") else 200 * 3_600_000
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
    assert by_coin["BTC"]["funding_pctile"] == 100.0
    assert by_coin["BTC"]["oi_change_24h_pct"] is not None
    # BTC (extreme) ranks ahead of ETH (flat, ~mid percentile) in the top list
    assert d["rows"][0]["coin"] == "BTC"


def test_funding_heat_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_FUNDING_OI_LOG", str(tmp_path / "nope.jsonl"))
    d = db._funding_heat_payload()
    assert d["status"] == "accruing" and d["count"] == 0 and d["since"] is None


def test_tapes_payload_whale_and_news(monkeypatch, tmp_path):
    from hermes_trader.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    now = 10_000_000_000
    with open(tmp_path / "whale_flow.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": now - 1000, "coin": "BTC", "side": "long",
                             "meta": {"buy_usd": 500000, "sell_usd": 100000, "net_usd": 400000}}) + "\n")
        fh.write(json.dumps({"ts": now - 30 * 3_600_000, "coin": "OLD", "side": "long",
                             "meta": {"buy_usd": 1, "sell_usd": 1, "net_usd": 0}}) + "\n")  # >24h, excluded
    with open(tmp_path / "news_catalyst.jsonl", "w") as fh:
        fh.write(json.dumps({"ts": now - 500, "coin": "ARB", "side": "long",
                             "meta": {"surge_x": 4.2, "breaking": True}}) + "\n")
    d = db._tapes_payload(now_ms=now)
    # whale_flow REFUTED + removed 2026-07-22 -> whale tape is always empty now
    assert d["whale"]["status"] == "removed" and d["whale"]["rows"] == []
    assert d["news"]["rows"][0]["coin"] == "ARB" and d["news"]["rows"][0]["breaking"] is True


def test_tapes_payload_empty_is_accruing(monkeypatch, tmp_path):
    from hermes_trader.agents import shadow_ledger
    monkeypatch.setattr(shadow_ledger, "_ledger_dir", lambda: str(tmp_path))
    d = db._tapes_payload()
    assert d["whale"]["status"] == "removed" and d["whale"]["rows"] == []
    assert d["news"]["status"] == "accruing" and d["news"]["rows"] == []


def test_coin_chart_payload_no_coin():
    assert db._coin_chart_payload("") == {
        "coin": "", "interval": "1h", "candles": [], "markers": [], "status": "no_coin"}


def test_coin_chart_payload_markers_and_candles(monkeypatch):
    class FakeCandle:
        def __init__(self, t, o, h, l, c, v):
            self.t, self.o, self.h, self.l, self.c, self.v = t, o, h, l, c, v

    candles = [FakeCandle(1000 + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100.5 + i, 10)
              for i in range(5)]
    import hermes_trader.client.hl_client as hl_client
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
    import hermes_trader.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles", lambda *a, **kw: [])
    d = db._coin_chart_payload("NOPE")
    assert d["status"] == "no_data" and d["candles"] == []


def test_analytics_endpoints_route(client, monkeypatch):
    monkeypatch.setattr(db, "_read_log_lines", lambda: [])
    monkeypatch.setattr(db, "read_agent_config", lambda: {})
    for ep in ("/api/dashboard/funnel", "/api/dashboard/book_league",
               "/api/dashboard/funding_heat", "/api/dashboard/tapes"):
        r = client.get(ep)
        assert r.status_code == 200, ep

    import hermes_trader.client.hl_client as hl_client
    monkeypatch.setattr(hl_client, "fetch_hl_candles", lambda *a, **kw: [])
    r = client.get("/api/dashboard/coin_chart?coin=BTC")
    assert r.status_code == 200 and r.json()["status"] == "no_data"
    assert client.get("/api/dashboard/coin_chart").status_code == 422  # coin required


def test_analytics_page_markers(client):
    r = client.get("/analytics").text
    for marker in ("panel-funnel", "panel-league", "panel-chart", "panel-heat",
                  "panel-tapes", "funnel-bars", "league-body", "coin-canvas",
                  "heat-body", "whale-body", "news-body", "hermes-an-"):
        assert marker in r, f"missing {marker}"
    assert 'data-nav="/analytics"' in r
    # whale $ runs into the millions — must render abbreviated ($1.31M), not
    # the raw fixed-2dp form ($1310471.00) (operator screenshot 2026-07-15)
    assert "fmtMoneyCompact" in r
    assert "tape-net" in r and "fmtMoneyCompact(r.net_usd)" in r
    # the dead-book branch is GONE (operator order 2026-07-17: refuted books
    # are removed from the UI, not rendered with a special badge)
    assert "b-dead" not in r and "row-dead" not in r


# ── landing v3 (2026-07-17): living ambient layer + informational one-pager ──

def test_landing_v3_webgl_ambient_layer(client):
    """The background is a hand-written WebGL2 fragment shader driven by the
    live summary payload (mood/energy/positions uniforms) — not a library,
    not a video. Must carry every budget-discipline marker: low-power GPU
    hint, hidden-tab pause, reduced-motion static frame, and a clean removal
    path (CSS gradient fallback) when webgl2 is unavailable."""
    r = client.get("/").text
    assert 'id="gl-bg"' in r
    assert "webgl2" in r and "#version 300 es" in r
    assert "u_mood" in r and "u_energy" in r and "u_pos" in r
    assert "low-power" in r                          # powerPreference set
    assert "visibilitychange" in r                   # pauses when tab hidden
    assert "cv.remove()" in r                        # no-WebGL2 fallback path
    assert "__setGlState" in r                       # summary payload drives uniforms
    assert "reduceMotion" in r and "prefers-reduced-motion" in r


def test_landing_v3_modern_css_stack(client):
    """July-2026 CSS, each feature gated so older engines degrade cleanly:
    scroll-driven animations behind @supports, registered @property for the
    animatable border beam, :has() status theming, OKLCH + color-mix accents,
    container-query KPI sizing, text-wrap, tabular numerals."""
    r = client.get("/").text
    assert "animation-timeline" in r and "@supports" in r
    assert "@property" in r and "--beam" in r
    assert "body:has(" in r                          # page reacts to its own pill
    assert "oklch(" in r and "color-mix(" in r
    assert "container-type" in r and "cqi" in r
    assert "text-wrap" in r
    assert "tabular-nums" in r
    assert "@starting-style" in r                    # popover entry animation
    assert "interpolate-size" in r                   # books dropdown height:auto


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
    assert "::backdrop" in r
    assert "prompt(" not in r and "confirm(" not in r
    assert "hermes-op-token" in r
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


def test_v3_ambient_layer_on_every_tab(client):
    """The living shader background ships on all four tabs (operator order
    2026-07-17: 'update all pages'), each with the full budget-discipline
    marker set. Non-landing tabs have no status pill, so the CAT is the
    status source — body:has() watches its sleep state to shift the OKLCH
    accent pair, and refreshCat feeds the same summary payload to the shader
    uniforms via __setGlState."""
    for path in ("/", "/activity", "/news", "/analytics"):
        r = client.get(path).text
        assert 'id="gl-bg"' in r and "#version 300 es" in r, path
        assert "u_mood" in r and "u_energy" in r and "u_pos" in r, path
        assert "low-power" in r and "visibilitychange" in r, path
        assert "cv.remove()" in r, path                   # no-WebGL2 fallback
        assert "__setGlState" in r, path
        assert 'id="scroll-progress"' in r and "animation-timeline" in r, path
        assert "oklch(" in r and "color-mix(" in r and "body:has(" in r, path
        if path != "/":
            assert "body:has(#pixel-cat.cat-sleep)" in r, path
            assert "window.__setGlState(s)" in r, path    # wired into refreshCat


# ── v4 app-shell (2026-07-17): compiled CSS, MPA transitions, the wire ──────

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


def test_v4_mpa_view_transitions_cat_morphs(client):
    """Cross-document view transitions: all four tabs opt in, and the cat
    carries the same view-transition-name so it MORPHS between pages when
    you navigate — the mascot persists across the whole app."""
    for path in ("/", "/activity", "/news", "/analytics"):
        r = client.get(path).text
        assert "@view-transition{navigation:auto}" in r, path
        assert "view-transition-name:cat" in r, path


def test_v4_speculation_rules_and_hotkeys(client):
    """Hover-eagerness prerendering (speculation rules) makes tab hops
    instant; g-then-key hotkeys (g d/a/n/y) give app-style navigation,
    inert while typing."""
    for path in ("/", "/activity", "/news", "/analytics"):
        r = client.get(path).text
        assert 'type="speculationrules"' in r, path
        assert '"eagerness":"moderate"' in r, path
        assert "keydown" in r and "e.target.closest('input,textarea,select')" in r, path


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
