"""v2 gate tests — ledger v2_ namespace + recorder funding/OI accrual."""
from __future__ import annotations

import json
import os

import pytest

import hermes_trader.agents.shadow_ledger as sl
import hermes_trader.v2.ledger as ledger
import hermes_trader.v2.recorder as recorder


class TestLedgerNamespace:
    def test_book_name_prefixing(self):
        assert ledger.book_name("extreme_fade") == "v2_extreme_fade"
        assert ledger.book_name("v2_extreme_fade") == "v2_extreme_fade"   # no double prefix

    def test_record_lands_in_v2_file_with_v1_schema(self):
        rec = ledger.record("extreme_fade", coin="AAA", side="long",
                            signal_bar_t=86_400_000, entry_ref_px=100.0,
                            horizon_days=3.0, stop_pct=20.0, ts=1,
                            meta={"prior_ret_pct": -13.0})
        assert rec["book"] == "v2_extreme_fade"
        # File exists under the redirected state dir, named by the v2 book.
        path = os.path.join(os.environ["HERMES_STATE_DIR"], "shadow_ledger",
                            "v2_extreme_fade.jsonl")
        assert os.path.isfile(path)
        rows = ledger.load("extreme_fade")
        assert rows[-1]["coin"] == "AAA" and rows[-1]["v"] == sl.SCHEMA_VERSION

    def test_v2_books_grade_through_the_same_survey(self):
        """shadow_status inventories every *.jsonl — v2 books appear automatically."""
        ledger.record("funding_spike_short", coin="BBB", side="short",
                      signal_bar_t=86_400_000, entry_ref_px=50.0,
                      horizon_days=5.0, stop_pct=15.0, ts=1)
        assert "v2_funding_spike_short" in sl.list_books()

    def test_record_many_counts(self):
        n = ledger.record_many("xs_momentum", [
            {"coin": "A", "side": "long", "signal_bar_t": 1, "entry_ref_px": 1.0,
             "horizon_days": 5.0, "stop_pct": 25.0, "ts": 1},
            {"coin": "B", "side": "short", "signal_bar_t": 1, "entry_ref_px": 1.0,
             "horizon_days": 5.0, "stop_pct": 25.0, "ts": 1},
        ])
        assert n == 2


_UNI = [
    {"coin": "BTC", "type": "perp", "dex": "", "funding": 0.0000125,
     "openInterest": 1234.5, "markPx": 100000.0, "dayNtlVlm": 1e9},
    {"coin": "@107", "type": "spot", "funding": None, "openInterest": None},  # skipped
    {"coin": "DEAD", "type": "perp", "funding": 0, "openInterest": 0},        # skipped
]


class TestRecorder:
    def test_snapshot_appends_expected_fields(self, tmp_path):
        log, ts = str(tmp_path / "d.jsonl"), str(tmp_path / "ts")
        n = recorder.maybe_record({"enabled": True, "interval_hours": 1.0}, _UNI,
                                  now_s=1_000_000.0, log_path=log, ts_path=ts)
        assert n == 1
        snap = json.loads(open(log).read().strip())
        assert snap["n"] == 1
        row = snap["rows"][0]
        assert row["c"] == "BTC" and row["f"] == 0.0000125
        assert row["oi"] == 1234.5 and row["v"] == 1e9

    def test_throttled_within_interval(self, tmp_path):
        log, ts = str(tmp_path / "d.jsonl"), str(tmp_path / "ts")
        cfg = {"enabled": True, "interval_hours": 1.0}
        assert recorder.maybe_record(cfg, _UNI, now_s=1_000_000.0, log_path=log, ts_path=ts) == 1
        assert recorder.maybe_record(cfg, _UNI, now_s=1_000_000.0 + 3599, log_path=log, ts_path=ts) == 0
        assert recorder.maybe_record(cfg, _UNI, now_s=1_000_000.0 + 3601, log_path=log, ts_path=ts) == 1
        assert len(open(log).read().strip().splitlines()) == 2

    def test_disabled_is_a_noop(self, tmp_path):
        log, ts = str(tmp_path / "d.jsonl"), str(tmp_path / "ts")
        assert recorder.maybe_record({"enabled": False}, _UNI, now_s=1_000_000.0,
                                     log_path=log, ts_path=ts) == 0
        assert not os.path.exists(log)

    def test_same_files_as_v1_data_logger(self):
        """Continuity contract: v2 keeps accruing into v1's frontier dataset."""
        import hermes_trader.agents.data_logger as v1
        assert recorder._LOG_FILE == v1._LOG_FILE
        assert recorder._TS_FILE == v1._TS_FILE
