"""Gate tests for the ledger: append-only, idempotent by trade id, frozen once
settled, and the context fields (rewrite reason, 24h range, planned R)."""
from __future__ import annotations

import json

from pathiel.models.types import Candle
from services.review import env
from services.review import ledger as LG
from services.strategies.backtest.data import ThesisDirection

H = 3_600_000


def rec(i, **kw):
    base = {"schema": LG.SCHEMA, "id": f"xyz:BRENTOIL|{i}|1", "coin": "xyz:BRENTOIL", "desk": "oil", "side": "long",
            "entry_ms": i, "exit_ms": i + H, "entry": LG.iso(i), "net_usd": 1.0, "r_mult": 0.5, "tags": [],
            "settled": True, "as_of": "x"}
    base.update(kw)
    return base


def test_ledger_path_follows_the_state_dir(state_dir):
    assert env.ledger_path() == str(state_dir / "review" / "ledger.jsonl")


def test_second_run_on_the_same_inputs_appends_nothing(tmp_path):
    p = str(tmp_path / "l.jsonl")
    recs = [rec(1), rec(2)]
    a = LG.update_ledger(p, recs, "t1")
    b = LG.update_ledger(p, recs, "t2")
    assert a["added"] == [r["id"] for r in recs] and not a["revised"]
    assert not b["added"] and not b["revised"] and len(b["unchanged"]) == 2
    assert len(open(p).read().splitlines()) == 2


def test_as_of_and_recorded_at_do_not_make_a_record_new(tmp_path):
    p = str(tmp_path / "l.jsonl")
    LG.update_ledger(p, [rec(1, settled=False, as_of="a")], "t1")
    b = LG.update_ledger(p, [rec(1, settled=False, as_of="b")], "t2")
    assert not b["revised"]


def test_a_provisional_trade_is_revised_then_frozen_once_settled(tmp_path):
    p = str(tmp_path / "l.jsonl")
    LG.update_ledger(p, [rec(1, settled=False, tags=[])], "t1")
    b = LG.update_ledger(p, [rec(1, settled=False, tags=["wrong_direction"])], "t2")
    assert b["revised"] == [rec(1)["id"]]
    c = LG.update_ledger(p, [rec(1, settled=True, tags=["wrong_direction"])], "t3")
    assert c["revised"] == [rec(1)["id"]]
    # settled: a later rebuild that drifted (equity samples rolled) changes nothing
    d = LG.update_ledger(p, [rec(1, settled=True, tags=["wrong_direction"], net_usd=9.9)], "t4")
    assert not d["revised"] and not d["added"]
    cur = LG.latest(LG.read_ledger(p))[rec(1)["id"]]
    assert cur["tags"] == ["wrong_direction"] and cur["net_usd"] == 1.0
    assert len(open(p).read().splitlines()) == 3      # append only: history kept


def test_a_settled_record_from_an_older_schema_is_rebuilt_once(tmp_path):
    p = str(tmp_path / "l.jsonl")
    LG.update_ledger(p, [rec(1, schema=LG.SCHEMA - 1, net_usd=2.0)], "t1")
    b = LG.update_ledger(p, [rec(1)], "t2")
    assert b["revised"] == [rec(1)["id"]]
    assert not LG.update_ledger(p, [rec(1, net_usd=5.0)], "t3")["revised"]


def test_a_torn_last_line_is_skipped(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps(rec(1)) + "\n" + '{"id": "half')
    assert [r["id"] for r in LG.read_ledger(str(p))] == [rec(1)["id"]]
    LG.update_ledger(str(p), [rec(1), rec(2)], "t")
    # the append starts on a fresh line, so the torn half never swallows a record
    assert len(LG.ledger_records(str(p))) == 2
    assert p.read_text().splitlines()[1] == '{"id": "half'


def test_ledger_records_filters_by_since_and_sorts(tmp_path):
    p = str(tmp_path / "l.jsonl")
    LG.update_ledger(p, [rec(30), rec(10), rec(20)], "t")
    assert [r["entry_ms"] for r in LG.ledger_records(p)] == [10, 20, 30]
    assert [r["entry_ms"] for r in LG.ledger_records(p, since_ms=20)] == [20, 30]


# ── context fields ─────────────────────────────────────────────────────────

def th(t, side, tid="x"):
    return ThesisDirection(created_at_ms=t, side=side, entry_px=None, stop_px=None, invalidation_px=None,
                           targets=[], thesis_id=tid)


def test_rewrite_reason_from_the_journal_order():
    a, b, c, d = th(1, "long"), th(2, "long"), th(3, "short"), th(4, "flat")
    e = th(5, "long")
    seq = [e, d, c, b, a]                          # order on disk does not matter
    assert LG.rewrite_reason(a, seq) == "first"
    assert LG.rewrite_reason(b, seq) == "same_side"
    assert LG.rewrite_reason(c, seq) == "flip"
    assert LG.rewrite_reason(e, seq) == "after_flat"
    assert LG.rewrite_reason(None, seq) is None


def bar(t, lo, hi):
    return Candle(t=t, o=lo, h=hi, l=lo, c=hi, v=0.0)


def test_range_position_is_side_adjusted_and_sees_only_closed_bars():
    bars = [bar(i * H, 100.0, 110.0) for i in range(24)] + [bar(24 * H, 50.0, 200.0)]  # last bar still open
    entry = 24 * H + 60_000
    assert LG.range_position(entry, 100.0, "long", bars)["range_pos"] == 0.0
    assert LG.range_position(entry, 99.0, "long", bars)["range_pos"] == -0.1     # bought under the low
    assert LG.range_position(entry, 110.0, "short", bars)["range_pos"] == 0.0    # sold the high
    assert LG.range_position(entry, 111.0, "short", bars)["range_pos"] == -0.1
    assert LG.range_position(entry, 105.0, "long", bars[:5])["range_pos"] is None   # too little history


def test_planned_r():
    assert LG.planned_r(110.01, 109.25, 111.83) == 2.39
    assert LG.planned_r(110.0, None, 111.0) is None
