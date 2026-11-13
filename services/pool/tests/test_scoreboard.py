"""services/pool/scoreboard.py: build, write, summary_line, new_rows.
Offline, pure dicts: the grading pipeline is covered in test_grade.py."""
from __future__ import annotations

import contracts.pool as pool
from services.pool import scoreboard as SB

COIN = "xyz:BRENTOIL"
AS_OF = pool.ms_from_iso("2026-11-14T12:00:00Z")
CUTOFF = pool.cutoff_ms(AS_OF)   # 2026-11-13T00:00:00Z


def _member(address, count_from_ms, kind="member", signer_role="master"):
    return {"address": address, "kind": kind, "signer_role": signer_role,
           "count_from": pool.iso_from_ms(count_from_ms), "count_from_ms": count_from_ms}


def _rec(entry_ms, exit_ms, r_mult, coin=COIN, side="long", luck_pct=None, **extra):
    rec = {"coin": coin, "side": side, "entry_ms": entry_ms, "exit_ms": exit_ms, "r_mult": r_mult,
          "net_usd": r_mult if r_mult is not None else 0.0, "desk": pool.POOL_DESK_OF[coin],
          "weekend": False, "hour_utc": 12, "planned_r": 2.0, "range_pos": 0.5, "trend_20d_pct": 0.0,
          "luck_pct": luck_pct}
    rec.update(extra)
    return rec


def test_build_validates_for_empty_house_and_several_members():
    assert pool.validate_scoreboard(SB.build([], {}, AS_OF)) == []

    house = _member("0x" + "2c" * 20, AS_OF - 30 * pool.DAY_MS, kind="house", signer_role=None)
    assert pool.validate_scoreboard(SB.build([house], {house["address"]: []}, AS_OF)) == []

    members = [house] + [_member(f"0x{i:040x}", AS_OF - 30 * pool.DAY_MS) for i in range(1, 4)]
    records = {m["address"]: [_rec(m["count_from_ms"] + j * 2 * pool.DAY_MS,
                                   m["count_from_ms"] + j * 2 * pool.DAY_MS + 3_600_000,
                                   1.0 if j % 2 == 0 else -0.6) for j in range(4)]
              for m in members}
    doc = SB.build(members, records, AS_OF)
    assert pool.validate_scoreboard(doc) == []
    assert doc["counts"]["members"] == 3 and doc["counts"]["house"] == 1


def test_determinism_same_bytes_an_hour_later_and_write_returns_false_second_time(tmp_path):
    addr = "0x" + "11" * 20
    members = [_member(addr, AS_OF - 10 * pool.DAY_MS)]
    records = {addr: [_rec(AS_OF - 5 * pool.DAY_MS, AS_OF - 5 * pool.DAY_MS + 3_600_000, 1.2)]}
    doc1 = SB.build(members, records, AS_OF)
    doc2 = SB.build(members, records, AS_OF)
    assert pool.dump_scoreboard(doc1) == pool.dump_scoreboard(doc2)
    later = AS_OF + 3_600_000
    assert pool.day_of(later) == pool.day_of(AS_OF)
    doc3 = SB.build(members, records, later)
    assert pool.dump_scoreboard(doc1) == pool.dump_scoreboard(doc3)
    out = str(tmp_path)
    assert SB.write(doc1, out) is True
    assert SB.write(doc1, out) is False


def test_delay_boundary_only_before_cutoff_is_counted_anywhere():
    addr = "0x" + "22" * 20
    member = _member(addr, AS_OF - 10 * pool.DAY_MS)
    records = [
        _rec(CUTOFF - 2 * pool.HOUR_MS, CUTOFF - 1, 1.0),      # closes just before cutoff: counted
        _rec(CUTOFF - 1 * pool.HOUR_MS, CUTOFF, -1.0),          # closes exactly at cutoff: not counted
        _rec(CUTOFF, CUTOFF + 1, 2.0),                          # closes just after cutoff: not counted
    ]
    doc = SB.build([member], {addr: records}, AS_OF)
    row = doc["members"][0]
    assert row["closed_trades"] == 1 and row["n"] == 1
    assert doc["counts"]["closed_trades"] == 1 and doc["counts"]["graded_trades"] == 1
    assert doc["pool"][0]["n"] == 1


def test_an_open_trade_never_counts():
    addr = "0x" + "23" * 20
    member = _member(addr, AS_OF - 10 * pool.DAY_MS)
    rec = _rec(AS_OF - 5 * pool.DAY_MS, None, None)
    doc = SB.build([member], {addr: [rec]}, AS_OF)
    assert doc["members"][0]["closed_trades"] == 0 and doc["members"][0]["n"] == 0
    assert doc["pool"][0]["n"] == 0


def test_left_sets_counts_left_with_no_member_row():
    doc = SB.build([], {}, AS_OF, left=3)
    assert doc["counts"]["left"] == 3 and doc["members"] == []


def test_privacy_no_forbidden_keys_and_no_raw_fill_values_leak():
    addr = "0x" + "44" * 20
    member = _member(addr, AS_OF - 10 * pool.DAY_MS)
    distinctive_px, distinctive_sz = "73.9182736451", "4.5591827364"
    records = [_rec(AS_OF - 5 * pool.DAY_MS, AS_OF - 5 * pool.DAY_MS + 3_600_000, 1.0,
                    entry_px=float(distinctive_px), max_size=float(distinctive_sz),
                    closed_pnl=12.3456789, fees=0.0123456)]
    doc = SB.build([member], {addr: records}, AS_OF)
    assert pool.validate_scoreboard(doc) == []
    assert pool._forbidden_keys(doc) == []
    dumped = pool.dump_scoreboard(doc)
    for leaked in (distinctive_px, distinctive_sz, "12.3456789", "0.0123456"):
        assert leaked not in dumped


def test_members_ordered_by_n_then_address_even_when_a_loser_comes_first():
    addr_more, addr_fewer = "0x" + "99" * 20, "0x" + "11" * 20   # "0x99.." > "0x11.." alphabetically
    m_more, m_fewer = _member(addr_more, AS_OF - 20 * pool.DAY_MS), _member(addr_fewer, AS_OF - 20 * pool.DAY_MS)
    recs_more = [_rec(AS_OF - (10 + j) * pool.DAY_MS, AS_OF - (10 + j) * pool.DAY_MS + 3_600_000, -1.0)
                for j in range(3)]
    recs_fewer = [_rec(AS_OF - 10 * pool.DAY_MS, AS_OF - 10 * pool.DAY_MS + 3_600_000, 2.0)]
    doc = SB.build([m_more, m_fewer], {addr_more: recs_more, addr_fewer: recs_fewer}, AS_OF)
    assert [r["address"] for r in doc["members"]] == [addr_more, addr_fewer]
    assert doc["members"][0]["mean_r"] < 0 and doc["members"][1]["mean_r"] > 0


def test_no_row_under_min_n_is_edge_or_leak():
    addr = "0x" + "55" * 20
    member = _member(addr, AS_OF - 20 * pool.DAY_MS)
    recs = [_rec(AS_OF - (19 - j) * pool.DAY_MS, AS_OF - (19 - j) * pool.DAY_MS + 3_600_000, 3.0) for j in range(8)]
    doc = SB.build([member], {addr: recs}, AS_OF)
    assert all(r["label"] not in ("EDGE", "LEAK") for r in doc["pool"])
    assert all(r["label"] not in ("EDGE", "LEAK") for r in doc["members"])


def test_independent_trades_is_at_most_graded_and_equals_cluster_count():
    addr1, addr2 = "0x" + "aa" * 20, "0x" + "bb" * 20
    m1, m2 = _member(addr1, AS_OF - 20 * pool.DAY_MS), _member(addr2, AS_OF - 20 * pool.DAY_MS)
    t0 = AS_OF - 15 * pool.DAY_MS
    recs1 = [_rec(t0, t0 + 3_600_000, 1.0), _rec(t0 + 60_000, t0 + 3_600_000 + 60_000, 1.5)]
    recs2 = [_rec(t0 + 120_000, t0 + 3_600_000 + 120_000, -0.5)]
    doc = SB.build([m1, m2], {addr1: recs1, addr2: recs2}, AS_OF)
    assert doc["counts"]["independent_trades"] <= doc["counts"]["graded_trades"]
    assert doc["counts"]["independent_trades"] == 1
    assert doc["counts"]["graded_trades"] == 3


def test_new_rows_none_first_build_one_per_gain_none_for_kept_empty_on_invalid():
    addr = "0x" + "cc" * 20
    member = _member(addr, AS_OF - 20 * pool.DAY_MS)
    doc = SB.build([member], {addr: [_rec(AS_OF - 10 * pool.DAY_MS, AS_OF - 10 * pool.DAY_MS + 3_600_000, 1.0)]},
                  AS_OF)
    assert SB.new_rows(None, doc) == []
    assert SB.new_rows(doc, doc) == []
    assert SB.new_rows({"not": "valid"}, doc) == []
    empty = SB.build([], {}, AS_OF)
    assert SB.new_rows(empty, doc) == [f"POOL-NEW {pool.short_address(addr)} since={doc['members'][0]['since']}"]
