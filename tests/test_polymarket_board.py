"""Board payload + cache: the forecast join, the live-edge recompute, the gate
arithmetic, and the staleness contract the dashboard route depends on."""
from __future__ import annotations

import json
import time

import pytest

from services.polymarket_scout import board, ledger

NOW = int(time.time() * 1000)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))


def _row(**over):
    r = {"market_id": "1", "question": "Q?", "yes": 0.40, "volume_24h": 50_000.0,
         "change_24h": 0.02, "end_date": "", "breaking": False, "tags": []}
    r.update(over)
    return r


def _ledger_row(**over):
    r = {"market_id": "1", "question": "Q?", "side": "YES", "llm_yes": 0.70,
         "mkt_yes": 0.40, "fill_px": 0.42, "edge": 0.30, "ts": NOW, "lane": "trending"}
    r.update(over)
    return r


# ── join ─────────────────────────────────────────────────────────────────────
def test_forecasts_by_market_keeps_the_newest_row_per_market():
    fc = board.forecasts_by_market([_ledger_row(llm_yes=0.60, ts=1),
                                    _ledger_row(llm_yes=0.75, ts=2)])
    assert fc["1"]["llm_yes"] == 0.75


def test_attach_forecasts_recomputes_edge_against_the_current_price():
    fc = board.forecasts_by_market([_ledger_row(llm_yes=0.70, mkt_yes=0.40)])
    # price has since moved 0.40 -> 0.65, so the live edge is 5pp, not the
    # frozen 30pp the ledger recorded at signal time
    out = board.attach_forecasts([_row(yes=0.65)], fc)
    assert out[0]["live_edge"] == pytest.approx(0.05)
    assert out[0]["forecast"]["edge"] == 0.30


def test_attach_forecasts_leaves_unjudged_rows_null():
    out = board.attach_forecasts([_row(market_id="nope")], {})
    assert out[0]["forecast"] is None and out[0]["live_edge"] is None


def test_attach_forecasts_survives_a_missing_price():
    fc = board.forecasts_by_market([_ledger_row()])
    assert board.attach_forecasts([_row(yes=None)], fc)[0]["live_edge"] is None


# ── gate ─────────────────────────────────────────────────────────────────────
def test_gate_needs_n_pnl_and_brier_together():
    passing = board.scoreboard({"n": 200, "mean_pnl_per_$": 0.05, "llm_beats_market": True})
    assert passing["gate"]["passed"] is True
    for bad in ({"n": 10, "mean_pnl_per_$": 0.05, "llm_beats_market": True},
                {"n": 200, "mean_pnl_per_$": 0.01, "llm_beats_market": True},
                {"n": 200, "mean_pnl_per_$": 0.05, "llm_beats_market": False}):
        assert board.scoreboard(bad)["gate"]["passed"] is False


def test_scoreboard_of_an_empty_grade_is_renderable():
    s = board.scoreboard({})
    assert s["n"] == 0 and s["gate"]["passed"] is False and s["mean_pnl_per_$"] is None


# ── cache ────────────────────────────────────────────────────────────────────
def test_load_without_a_cache_returns_an_empty_renderable_board():
    b = board.load()
    assert b["status"] == "empty" and b["stale"] is True
    assert b["trending"] == [] and b["counts"]["breaking"] == 0
    assert b["scoreboard"]["n"] == 0


def test_load_marks_a_fresh_cache_ok_and_an_old_one_stale():
    board.save({"generated_at": NOW, "trending": [_row()], "counts": {}, "universe": 1})
    fresh = board.load(now_ms=NOW + 60_000)
    assert fresh["status"] == "ok" and fresh["stale"] is False and fresh["age_s"] == 60
    old = board.load(now_ms=NOW + (board.STALE_AFTER_S + 60) * 1000)
    assert old["status"] == "stale" and old["stale"] is True


def test_load_of_a_corrupt_cache_does_not_raise():
    with open(board._path(), "w") as fh:
        fh.write("{not json")
    assert board.load()["status"] == "empty"


def test_save_is_atomic_and_leaves_no_tmp_file():
    import os
    p = board.save({"generated_at": NOW})
    assert os.path.isfile(p) and not os.path.exists(p + ".tmp")


# ── build ────────────────────────────────────────────────────────────────────
class FakeClient:
    def open_events(self, limit=100, pages=3, exclude_tag_ids=(), tag_ids=()):
        if tag_ids:                      # the sports fetch
            return [{"id": "s", "title": "Game", "slug": "g", "live": True,
                     "score": "2-1", "sport": {"sport": "tennis"},
                     "tags": [{"slug": "tennis"}],
                     "markets": [{"id": "9", "question": "Set 2 winner?",
                                  "clobTokenIds": json.dumps(["y9", "n9"]),
                                  "outcomePrices": json.dumps(["0.20", "0.80"]),
                                  "endDate": time.strftime(
                                      "%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(NOW / 1000 + 2 * 3600)),
                                  "liquidity": "9000", "volume24hr": 700_000.0,
                                  "oneDayPriceChange": 0.0, "bestBid": 0.19,
                                  "bestAsk": 0.21, "spread": 0.02,
                                  "enableOrderBook": True, "active": True,
                                  "closed": False}]}]

        def mkt(mid, vol, chg):
            return {"id": mid, "question": f"Q{mid}?",
                    "clobTokenIds": json.dumps([f"y{mid}", f"n{mid}"]),
                    "outcomePrices": json.dumps(["0.40", "0.60"]),
                    "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime(NOW / 1000 + 72 * 3600)),
                    "liquidity": "9000", "volume24hr": vol, "oneDayPriceChange": chg,
                    "bestBid": 0.39, "bestAsk": 0.41, "spread": 0.02,
                    "enableOrderBook": True, "active": True, "closed": False}
        return [{"id": "e", "title": "T", "slug": "s", "tags": [{"slug": "world"}],
                 "markets": [mkt("1", 500_000.0, 0.30), mkt("2", 90_000.0, 0.0)]}]

    def market_by_id(self, market_id):
        return None


def test_build_assembles_all_three_feeds_and_grades():
    ledger.record(**{k: v for k, v in _ledger_row().items()
                     if k not in ("ts",)}, token_id="y1", end_date="", category="")
    p = board.build(FakeClient(), now_ms=NOW, provider="claude_cli")
    assert p["universe"] == 3                      # 2 trending + 1 sports
    assert [r["market_id"] for r in p["breaking"]] == ["1"]
    assert p["counts"]["trending"] == 2
    assert [r["market_id"] for r in p["edges"]] == ["1"]     # only the judged market
    assert p["edges"][0]["live_edge"] == pytest.approx(0.30)
    assert p["provider"] == "claude_cli"
    assert p["scoreboard"]["n"] == 0                         # nothing resolved yet


def test_build_survives_a_grading_failure():
    class Boom(FakeClient):
        def market_by_id(self, market_id):
            raise RuntimeError("gamma down")
    ledger.record(**{k: v for k, v in _ledger_row().items() if k != "ts"},
                  token_id="y1", end_date="", category="")
    p = board.build(Boom(), now_ms=NOW)
    assert p["scoreboard"]["n"] == 0 and p["counts"]["trending"] == 2


def test_refresh_writes_the_cache_the_dashboard_reads():
    board.refresh(FakeClient(), provider="claude_cli")
    b = board.load()
    assert b["status"] == "ok" and b["counts"]["trending"] == 2


# ── sports + crazy odds ──────────────────────────────────────────────────────
def test_build_adds_the_sports_board_from_a_second_fetch():
    p = board.build(FakeClient(), now_ms=NOW)
    assert p["counts"]["sports"] == 1
    s = p["sports"][0]
    assert s["live"] is True and s["sport"] == "tennis" and s["score"] == "2-1"
    assert p["universe"] == 3            # 2 trending + 1 sports


def test_longshot_board_spans_both_universes():
    """A 20c game line is as much a >=3x market as a 20c ceasefire — the crazy
    odds tab must not be limited to the judgment feed."""
    p = board.build(FakeClient(), now_ms=NOW)
    shots = {r["market_id"] for r in p["longshots"]}
    assert "9" in shots                  # the sports row at 0.20
    assert all(r["yes"] <= 0.33 and r["payout_x"] for r in p["longshots"])


def test_build_survives_a_sports_fetch_failure():
    class NoSports(FakeClient):
        def open_events(self, limit=100, pages=3, exclude_tag_ids=(), tag_ids=()):
            if tag_ids:
                raise RuntimeError("gamma down")
            return FakeClient.open_events(self, limit, pages, exclude_tag_ids)

    p = board.build(NoSports(), now_ms=NOW)
    assert p["counts"]["sports"] == 0 and p["counts"]["trending"] == 2


def test_empty_board_declares_every_feed_the_page_reads():
    b = board.load()
    for feed in ("trending", "breaking", "edges", "sports", "longshots"):
        assert b[feed] == [] and b["counts"][feed] == 0
