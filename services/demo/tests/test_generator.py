"""Gate tests for the demo data generator.

Two classes of failure these exist to catch.

The first is the demo silently looking broken. The dashboard is time-aware —
`_summary_payload` calls the bot "offline" past a 300s heartbeat gap and
`read_position_snapshot` drops a snapshot older than 120s — so an off-by-one in
the timeline produces a deploy that renders perfectly and says the system is
dead. That is exactly what happened once already; `test_newest_heartbeat_is_fresh`
is the regression.

The second is the demo making a claim. These numbers go on a public URL. If the
equity walk escapes its envelope or the win rate drifts toward fantasy, the
demo stops being a screenshot of a product and starts being a performance
record that nothing backs. Those bounds are asserted, not hoped for.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from services.demo.generator import (
    BOOKS,
    START_EQUITY,
    build_agent_memory,
    build_position_snapshot,
    build_session_log,
    build_shadow_ledgers,
    materialize,
)

NOW = 1_789_000_000_000  # fixed clock, so every assertion below is exact


@pytest.fixture
def log_and_open():
    return build_session_log(NOW)


class TestSessionLog:
    def test_is_deterministic(self):
        a, _ = build_session_log(NOW, seed=7)
        b, _ = build_session_log(NOW, seed=7)
        assert a == b

    def test_different_seeds_diverge(self):
        a, _ = build_session_log(NOW, seed=1)
        b, _ = build_session_log(NOW, seed=2)
        assert a != b

    def test_events_are_ordered_oldest_first(self, log_and_open):
        events, _ = log_and_open
        stamps = [e["ts"] for e in events]
        assert stamps == sorted(stamps)

    def test_newest_heartbeat_is_fresh(self, log_and_open):
        """The dashboard's staleness cutoff is 300s. Stay well inside it.

        Regression: the timeline was built with `steps` intervals where it
        needed `steps - 1`, which put the newest heartbeat 340s back and made
        every boot report "stale".
        """
        events, _ = log_and_open
        beats = [e for e in events if e["event"] == "loop_heartbeat"]
        age_s = (NOW - beats[-1]["ts"]) / 1000
        assert 0 < age_s < 120, f"newest heartbeat is {age_s:.0f}s old"

    def test_history_spans_the_requested_window(self):
        events, _ = build_session_log(NOW, days=7)
        span_days = (events[-1]["ts"] - events[0]["ts"]) / 86_400_000
        assert 6.9 < span_days < 7.1

    def test_every_event_is_a_type_the_dashboard_classifies(self, log_and_open):
        events, _ = log_and_open
        known = {"loop_start", "loop_heartbeat", "scan", "execute",
                 "dsl_exit", "slots_full_skip", "error"}
        assert {e["event"] for e in events} <= known

    def test_heartbeats_carry_every_field_summary_reads(self, log_and_open):
        events, _ = log_and_open
        needed = {"equity", "spot_usdc", "available", "daily_pnl",
                  "open_positions", "dex_equity", "dex_available", "ts"}
        for beat in (e for e in events if e["event"] == "loop_heartbeat"):
            assert needed <= set(beat), f"missing {needed - set(beat)}"

    def test_equity_stays_inside_its_envelope(self, log_and_open):
        """A demo is not allowed to print a month the system never had."""
        events, _ = log_and_open
        totals = [e["equity"] + e["spot_usdc"]
                  for e in events if e["event"] == "loop_heartbeat"]
        assert min(totals) > START_EQUITY * 0.93
        assert max(totals) < START_EQUITY * 1.12

    def test_open_position_count_never_exceeds_the_slot_limit(self, log_and_open):
        events, _ = log_and_open
        assert max(e["open_positions"]
                   for e in events if e["event"] == "loop_heartbeat") <= 3

    def test_every_close_has_a_matching_open(self, log_and_open):
        events, _ = log_and_open
        opened: list[str] = []
        for e in events:
            if e["event"] == "execute":
                opened.append(e["coin"])
            elif e["event"] == "dsl_exit":
                assert e["coin"] in opened, f"{e['coin']} closed without an open"
                opened.remove(e["coin"])

    def test_closes_are_consistent_with_their_leverage(self, log_and_open):
        events, _ = log_and_open
        for e in (x for x in events if x["event"] == "dsl_exit"):
            assert e["leveraged_pct"] == pytest.approx(
                e["unrealized_pct"] * e["leverage"], rel=1e-6)

    def test_win_rate_is_plausible(self, log_and_open):
        """Positive expectancy, not a fantasy. Anything above ~65% on a
        momentum book is a number to be suspicious of."""
        events, _ = log_and_open
        closes = [e for e in events if e["event"] == "dsl_exit"]
        assert len(closes) >= 10, "not enough closes to demo a track record"
        wins = sum(1 for e in closes if e["unrealized_pct"] > 0)
        assert 0.35 < wins / len(closes) < 0.65

    def test_books_named_are_books_the_dashboard_knows(self, log_and_open):
        events, _ = log_and_open
        assert {e["book"] for e in events if e["event"] == "execute"} <= set(BOOKS)

    def test_never_holds_two_positions_in_one_coin(self, log_and_open):
        """Hyperliquid's oneWay mode nets a coin into a single line, so a long
        and a short of the same ticker at the same time is a state the exchange
        cannot be in — and the demo was rendering exactly that."""
        events, _ = log_and_open
        held: set[str] = set()
        for e in events:
            if e["event"] == "execute":
                assert e["coin"] not in held, f"opened {e['coin']} while already open"
                held.add(e["coin"])
            elif e["event"] == "dsl_exit":
                held.discard(e["coin"])


class TestPositionSnapshot:
    def test_agrees_with_the_last_heartbeat(self, log_and_open):
        """The panels have to tell the same story: "3 open" on the hero row and
        three rows in the positions table are the same three trades."""
        events, open_positions = log_and_open
        snap = build_position_snapshot(NOW, open_positions)
        beats = [e for e in events if e["event"] == "loop_heartbeat"]
        assert len(snap["asset_positions"]) == beats[-1]["open_positions"]

    def test_is_fresh_enough_to_be_read(self, log_and_open):
        _, open_positions = log_and_open
        snap = build_position_snapshot(NOW, open_positions)
        assert (NOW - snap["saved_at"]) / 1000 < 120

    def test_rows_match_hyperliquid_shape(self, log_and_open):
        _, open_positions = log_and_open
        snap = build_position_snapshot(NOW, open_positions)
        needed = {"coin", "szi", "leverage", "entryPx", "positionValue",
                  "unrealizedPnl", "returnOnEquity", "liquidationPx",
                  "marginUsed", "maxLeverage", "cumFunding"}
        for row in snap["asset_positions"]:
            assert row["type"] == "oneWay"
            assert needed <= set(row["position"])
            assert set(row["position"]["leverage"]) == {"type", "value", "rawUsd"}

    def test_side_matches_the_sign_of_the_size(self, log_and_open):
        _, open_positions = log_and_open
        snap = build_position_snapshot(NOW, open_positions)
        for row, pos in zip(snap["asset_positions"], open_positions):
            szi = float(row["position"]["szi"])
            assert (szi > 0) == (pos["side"] == "long")

    def test_position_sizes_are_within_configured_notional(self, log_and_open):
        _, open_positions = log_and_open
        snap = build_position_snapshot(NOW, open_positions)
        for row in snap["asset_positions"]:
            assert 50 < float(row["position"]["positionValue"]) < 200


class TestAgentMemory:
    def test_every_open_position_has_a_reason(self, log_and_open):
        """A blank "why this opened" line strips the positions table of the one
        thing that makes it more than a list of tickers."""
        _, open_positions = log_and_open
        mem = build_agent_memory(open_positions)
        assert len(mem["entryCtx"]) == len(open_positions)
        for pos in open_positions:
            ctx = mem["entryCtx"][f"{pos['coin']}_{pos['side']}"]
            assert ctx["book"] in BOOKS
            assert len(ctx["reason"]) > 10

    def test_keys_match_what_memory_peeks_for(self, log_and_open):
        _, open_positions = log_and_open
        for key in build_agent_memory(open_positions)["entryCtx"]:
            coin, _, side = key.rpartition("_")
            assert coin and side in {"long", "short"}


class TestShadowLedgers:
    def test_covers_every_book(self):
        assert set(build_shadow_ledgers(NOW)) == set(BOOKS)

    def test_records_carry_what_summary_reads(self):
        for recs in build_shadow_ledgers(NOW).values():
            for r in recs:
                assert {"ts", "coin", "signal_bar_t", "entry_ref_px",
                        "horizon_days"} <= set(r)
                assert r["entry_ref_px"] > 0
                assert r["signal_bar_t"] <= r["ts"]

    def test_inventories_are_large_enough_to_mean_something(self):
        """A book with four signals is not evidence, and a league table that
        says so is the honest version of this panel."""
        for recs in build_shadow_ledgers(NOW).values():
            assert len(recs) >= 38

    def test_signals_fall_inside_the_history_window(self):
        for recs in build_shadow_ledgers(NOW, days=7).values():
            for r in recs:
                assert NOW - 7 * 86_400_000 <= r["ts"] <= NOW


class TestMaterialize:
    def test_writes_every_file_and_exports_every_path(self, tmp_path):
        env = materialize(str(tmp_path), now_ms=NOW)
        assert set(env) == {"SESSION_LOG_PATH", "PATHIA_POSITIONS_SNAPSHOT_FILE",
                            "PATHIA_AGENT_CONFIG_FILE", "PATHIA_AGENT_MEMORY_FILE",
                            "PATHIA_STATE_DIR"}
        assert os.path.isfile(env["SESSION_LOG_PATH"])
        assert os.path.isfile(env["PATHIA_POSITIONS_SNAPSHOT_FILE"])
        assert os.path.isfile(env["PATHIA_AGENT_CONFIG_FILE"])
        assert os.path.isfile(env["PATHIA_AGENT_MEMORY_FILE"])
        ledgers = os.path.join(env["PATHIA_STATE_DIR"], "shadow_ledger")
        assert sorted(os.listdir(ledgers)) == sorted(f"{b}.jsonl" for b in BOOKS)

    def test_session_log_is_valid_jsonl(self, tmp_path):
        env = materialize(str(tmp_path), now_ms=NOW)
        with open(env["SESSION_LOG_PATH"]) as f:
            for line in f:
                assert "event" in json.loads(line)

    def test_agent_config_turns_the_books_on(self, tmp_path):
        """All five 'off' is a demo that shows nothing running."""
        env = materialize(str(tmp_path), now_ms=NOW)
        with open(env["PATHIA_AGENT_CONFIG_FILE"]) as f:
            cfg = json.load(f)
        keys = ["unlock_short", "news_surge_short", "news_surge_multi",
                "social_trending", "xs_reversal"]
        assert all(cfg[k]["enabled"] for k in keys)

    def test_defaults_to_the_wall_clock(self, tmp_path):
        env = materialize(str(tmp_path))
        with open(env["PATHIA_POSITIONS_SNAPSHOT_FILE"]) as f:
            saved_at = json.load(f)["saved_at"]
        assert abs(saved_at - time.time() * 1000) < 10_000
