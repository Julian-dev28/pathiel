"""services/pool/grade.py: the grading recipe, the per-member ledger, member
rows and the pool's independent-trade clusters and slices. Offline.

The house regression (10 closed trades, 5 wins, mean R 0.9933) reuses
services/review/tests/fixtures/bundle_2026-11-02 (read only), round-tripped
through fetch.save_bundle/load_bundle so the pool's own bundle format is
exercised on real data. Every other test builds small synthetic fills,
orders and records (services/pool/tests/pool_fakes.py, or hand-built dicts
for cluster_records / pool_rows, which only need already-graded shapes)."""
from __future__ import annotations

import json
import os

import pytest

import contracts.pool as pool
from services.pool import fetch as F
from services.pool import grade as G
from services.pool import paths
from services.pool.tests import pool_fakes as PF
from services.review import inputs as I
from services.review import stats as S

REVIEW_FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "review", "tests", "fixtures")
REVIEW_BUNDLE = os.path.join(REVIEW_FIXTURES, "bundle_2026-11-02")
HOUSE_ADDR = "0x2c2e4eff574b3fd9879adef9c5cac6fd7c916985"


def _member(address, count_from_ms, kind="member", signer_role="master"):
    return {"address": address, "kind": kind, "signer_role": signer_role,
            "count_from": pool.iso_from_ms(count_from_ms), "count_from_ms": count_from_ms}


def _simple_record(coin, side, entry_ms, exit_ms, r_mult, net_usd=None, luck_pct=None, **extra):
    rec = {"coin": coin, "side": side, "entry_ms": entry_ms, "exit_ms": exit_ms, "r_mult": r_mult,
          "net_usd": net_usd if net_usd is not None else r_mult, "luck_pct": luck_pct,
          "desk": pool.POOL_DESK_OF[coin]}
    rec.update(extra)
    return rec


def _crec(coin, side, entry_ms, r_mult, **extra):
    base = {"coin": coin, "side": side, "entry_ms": entry_ms, "r_mult": r_mult, "net_usd": r_mult,
           "desk": pool.POOL_DESK_OF[coin], "weekend": False, "hour_utc": 12, "planned_r": 2.0,
           "range_pos": 0.5, "trend_20d_pct": 0.0}
    base.update(extra)
    return base


def _cluster(desk, side, weekend, hour_utc, planned_r, range_pos, trend_20d_pct, r_mult, n_trades=1,
            addresses=None):
    return {"desk": desk, "side": side, "weekend": weekend, "hour_utc": hour_utc, "planned_r": planned_r,
           "range_pos": range_pos, "trend_20d_pct": trend_20d_pct, "r_mult": r_mult, "net_usd": r_mult,
           "n_trades": n_trades, "addresses": addresses or {"0x" + "aa" * 20}}


# ── the house regression ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def house_bundle(tmp_path_factory):
    """services/review/tests/fixtures/bundle_2026-11-02, read and
    round-tripped through fetch.save_bundle / load_bundle as the house's own
    pool bundle: one member (the house address), its fills and orders (on
    the pool coins already) and its candles."""
    review_bundle = I.load_bundle(REVIEW_BUNDLE)
    pool_bundle = {
        "as_of_ms": review_bundle["as_of_ms"],
        "inputs": {HOUSE_ADDR: {"fills": review_bundle["fills"], "orders": review_bundle["orders"]}},
        "bars5": review_bundle["bars5"], "bars1h": review_bundle["bars1h"],
    }
    out = tmp_path_factory.mktemp("house-bundle")
    F.save_bundle(str(out), pool_bundle)
    return F.load_bundle(str(out))


def test_the_house_regression_ten_closed_five_wins_mean_r(house_bundle):
    from services.strategies.evals import loss_audit as LA
    one = house_bundle["inputs"][HOUSE_ADDR]
    recs = G.member_records(one, house_bundle["bars5"], house_bundle["bars1h"], LA.SINCE_MS,
                            house_bundle["as_of_ms"])
    assert len(recs) == 10
    graded = [r for r in recs if r.get("r_mult") is not None]
    assert len(graded) == 10
    assert sum(1 for r in recs if r["net_usd"] > 0) == 5
    mean_r = sum(r["r_mult"] for r in graded) / len(graded)
    assert mean_r == pytest.approx(0.9933, abs=1e-4)


# ── member_records edge cases ───────────────────────────────────────────────

def test_fills_before_count_from_never_count():
    coin = "xyz:BRENTOIL"
    t_before = 1_000_000_000_000
    t_after = t_before + 10 * pool.DAY_MS
    count_from = t_after - pool.DAY_MS
    fb, ob = PF.bracket_trade(coin, "long", 70.0, 69.0, 5.0, t_before, target_px=72.0)
    fa, oa = PF.bracket_trade(coin, "long", 70.0, 69.0, 5.0, t_after, target_px=72.0)
    recs = G.member_records({"fills": fb + fa, "orders": ob + oa}, {}, {}, count_from, t_after + 10 ** 7)
    assert len(recs) == 1
    assert recs[0]["entry_ms"] == t_after


def test_a_position_open_at_the_join_is_skipped_the_next_trade_counts():
    coin = "xyz:BRENTOIL"
    count_from = 1_700_000_000_000
    pre_existing_close = PF.fill(coin, "A", 5.0, 68.0, count_from + 1_000, 5.0)   # closes a position we never opened
    fills2, orders2 = PF.bracket_trade(coin, "long", 70.0, 69.0, 10.0, count_from + 2_000_000, target_px=72.0)
    recs = G.member_records({"fills": [pre_existing_close] + fills2, "orders": orders2}, {}, {}, count_from,
                            count_from + 10 ** 8)
    assert len(recs) == 1
    assert recs[0]["entry_ms"] == count_from + 2_000_000


def test_a_fill_on_another_coin_never_appears():
    coin, other = "xyz:BRENTOIL", "BTC"
    t0 = 1_700_000_000_000
    f1, o1 = PF.bracket_trade(coin, "long", 70.0, 69.0, 5.0, t0, target_px=72.0)
    f2, o2 = PF.bracket_trade(other, "long", 60_000.0, 59_000.0, 0.1, t0 + 10_000, target_px=62_000.0)
    recs = G.member_records({"fills": f1 + f2, "orders": o1 + o2}, {}, {}, t0 - 1_000, t0 + 10 ** 7)
    assert len(recs) == 1 and recs[0]["coin"] == coin


def test_a_trade_with_no_stop_is_closed_not_graded():
    coin = "xyz:BRENTOIL"
    t0 = 1_700_000_000_000
    open_fill = PF.fill(coin, "B", 5.0, 70.0, t0, 0.0)
    close_fill = PF.fill(coin, "A", 5.0, 71.0, t0 + 3_600_000, 5.0, closed_pnl=5.0)
    recs = G.member_records({"fills": [open_fill, close_fill], "orders": []}, {}, {}, t0 - 1_000, t0 + 10 ** 7)
    assert len(recs) == 1
    assert recs[0]["r_mult"] is None
    assert recs[0]["exit_ms"] == t0 + 3_600_000


def test_a_flip_through_zero_is_closed_not_graded():
    coin = "xyz:BRENTOIL"
    t0 = 1_700_000_000_000
    open_fill = PF.fill(coin, "B", 5.0, 70.0, t0, 0.0)                               # 0 -> +5
    flip_fill = PF.fill(coin, "A", 12.0, 71.0, t0 + 60_000, 5.0)                      # +5 -> -7 (through zero)
    close_fill = PF.fill(coin, "B", 7.0, 72.0, t0 + 3_600_000, -7.0, closed_pnl=-20.0)  # -7 -> 0
    stop_order = PF.order(coin, "A", 65.0, 5.0, t0 + 1_000, trigger_px=65.0, order_type="Stop Market",
                         reduce_only=True)
    recs = G.member_records({"fills": [open_fill, flip_fill, close_fill], "orders": [stop_order]}, {}, {},
                            t0 - 1_000, t0 + 10 ** 7)
    assert len(recs) == 1
    assert recs[0]["r_mult"] is None
    assert recs[0]["pool_note"] == "flip"
    assert recs[0]["exit_ms"] == t0 + 3_600_000


# ── the ledger ────────────────────────────────────────────────────────────

def test_update_ledger_is_idempotent():
    coin = "xyz:BRENTOIL"
    t0 = 1_700_000_000_000
    fills, orders = PF.bracket_trade(coin, "long", 70.0, 69.0, 10.0, t0, target_px=72.0)
    as_of = t0 + 3_600_000 + 1_000
    recs = G.member_records({"fills": fills, "orders": orders}, {}, {}, t0 - 1_000, as_of)
    addr = "0x" + "33" * 20
    first = G.update_ledger(addr, recs, as_of)
    assert first["added"] == [recs[0]["id"]]
    second = G.update_ledger(addr, recs, as_of)
    assert second["added"] == [] and second["revised"] == []


def test_a_settled_record_is_frozen():
    coin = "xyz:BRENTOIL"
    t0 = 1_700_000_000_000
    fills, orders = PF.bracket_trade(coin, "long", 70.0, 69.0, 10.0, t0, target_px=72.0)
    as_of = t0 + 3_600_000 + 13 * 3_600_000   # 13h after exit: settled (SETTLE_MS = 12h)
    recs = G.member_records({"fills": fills, "orders": orders}, {}, {}, t0 - 1_000, as_of)
    assert len(recs) == 1 and recs[0]["settled"] is True
    addr = "0x" + "22" * 20
    G.update_ledger(addr, recs, as_of)
    drifted = [dict(recs[0], net_usd=999.0, r_mult=999.0)]
    G.update_ledger(addr, drifted, as_of)
    back = G.ledger_records(addr, t0 - 1_000)
    assert len(back) == 1
    assert back[0]["net_usd"] == recs[0]["net_usd"] != 999.0


def test_purge_ledgers_deletes_only_inactive_addresses():
    a, b = "0x" + "aa" * 20, "0x" + "bb" * 20
    for addr in (a, b):
        p = paths.ledger_path(addr)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(json.dumps({"id": "x"}) + "\n")
    removed = G.purge_ledgers([a])
    assert removed == [b]
    assert os.path.exists(paths.ledger_path(a))
    assert not os.path.exists(paths.ledger_path(b))


# ── member_row ───────────────────────────────────────────────────────────

def test_member_row_counts_rounds_and_luck_median():
    addr = "0x" + "44" * 20
    cfm = 1_700_000_000_000
    cutoff = cfm + 100 * pool.DAY_MS
    records = [
        _simple_record("xyz:BRENTOIL", "long", cfm + 1_000, cfm + 2_000, 1.0, luck_pct=20.0),
        _simple_record("xyz:BRENTOIL", "long", cfm + 3_000, cfm + 4_000, -0.5, luck_pct=80.0),
        _simple_record("xyz:BRENTOIL", "long", cfm + 5_000, cfm + 6_000, None, luck_pct=None),
    ]
    row = G.member_row(_member(addr, cfm), records, cutoff)
    assert row["closed_trades"] == 3 and row["n"] == 2
    assert row["luck_n"] == 2 and row["luck_median"] == 50.0
    assert row["since"] == pool.day_of(cfm)
    assert row["address"] == addr and row["short"] == pool.short_address(addr)
    assert row["explorer"] == pool.explorer_url(addr)
    for k in ("win_rate", "win_lo", "win_hi", "mean_r", "r_lo", "r_hi", "p"):
        assert row[k] is None or isinstance(row[k], float)
    assert G.member_row(_member(addr, cfm), records, cutoff) == row   # deterministic


def test_member_row_at_n_zero_is_all_nulls_and_noise():
    addr = "0x" + "55" * 20
    member = _member(addr, 1_700_000_000_000)
    row = G.member_row(member, [], member["count_from_ms"] + pool.DAY_MS)
    assert row["n"] == 0 and row["closed_trades"] == 0 and row["label"] == "NOISE"
    for k in ("win_rate", "win_lo", "win_hi", "mean_r", "r_lo", "r_hi", "p"):
        assert row[k] is None
    assert row["luck_median"] is None and row["luck_n"] == 0


def test_member_row_ranges_are_seeded_per_address():
    """The same address always gets the same ranges: calling member_row
    twice on identical inputs reproduces every field, including the
    bootstrap CIs and the permutation p (both seeded off the address)."""
    cfm = 1_700_000_000_000
    cutoff = cfm + 100 * pool.DAY_MS
    records = [_simple_record("xyz:BRENTOIL", "long", cfm + i * 10_000, cfm + i * 10_000 + 5_000,
                              1.0 if i % 3 == 0 else -0.8 if i % 3 == 1 else 0.3) for i in range(12)]
    addr, other = "0x" + "66" * 20, "0x" + "77" * 20
    assert G.member_row(_member(addr, cfm), records, cutoff) == G.member_row(_member(addr, cfm), records, cutoff)
    assert S.sub_seed(S.SEED, addr) != S.sub_seed(S.SEED, other)   # two addresses never share a seed


# ── cluster_records ──────────────────────────────────────────────────────

def test_cluster_records_ten_members_one_call_is_one_cluster():
    coin = "xyz:BRENTOIL"
    t0 = 1_700_000_000_000
    graded_by_address = {f"0x{i:040x}": [_crec(coin, "long", t0 + i * 60_000, 1.0 + 0.1 * i)] for i in range(10)}
    clusters = G.cluster_records(graded_by_address)
    assert len(clusters) == 1
    assert clusters[0]["n_trades"] == 10
    assert clusters[0]["r_mult"] == pytest.approx(sum(1.0 + 0.1 * i for i in range(10)) / 10)
    assert len(clusters[0]["addresses"]) == 10


def test_cluster_records_span_boundary_23h_one_cluster_25h_two():
    coin, addr = "xyz:BRENTOIL", "0x" + "11" * 20
    t0 = 1_700_000_000_000
    close = {addr: [_crec(coin, "long", t0, 1.0), _crec(coin, "long", t0 + 23 * pool.HOUR_MS, 2.0)]}
    far = {addr: [_crec(coin, "long", t0, 1.0), _crec(coin, "long", t0 + 25 * pool.HOUR_MS, 2.0)]}
    assert len(G.cluster_records(close)) == 1
    assert len(G.cluster_records(far)) == 2


def test_cluster_records_no_quiet_hour_three_days_gives_three_clusters():
    coin, addr = "xyz:BRENTOIL", "0x" + "22" * 20
    t0 = 1_700_000_000_000
    entries = [t0 + i * 15 * 60_000 for i in range(3 * 24 * 4)]   # every 15 minutes for 3 days
    clusters = G.cluster_records({addr: [_crec(coin, "long", e, 0.0) for e in entries]})
    assert len(clusters) == 3
    assert sum(c["n_trades"] for c in clusters) == len(entries)


def test_cluster_records_other_coin_or_side_is_a_separate_cluster():
    addr = "0x" + "33" * 20
    t0 = 1_700_000_000_000
    recs = [_crec("xyz:BRENTOIL", "long", t0, 1.0), _crec("xyz:CL", "long", t0, 1.0),
           _crec("xyz:BRENTOIL", "short", t0, 1.0)]
    clusters = G.cluster_records({addr: recs})
    assert len(clusters) == 3


def test_cluster_records_ties_on_entry_pick_the_lowest_address():
    coin = "xyz:BRENTOIL"
    t0 = 1_700_000_000_000
    graded_by_address = {
        "0x" + "bb" * 20: [_crec(coin, "long", t0, 1.0, desk="metals")],
        "0x" + "aa" * 20: [_crec(coin, "long", t0, 1.0, desk="oil")],
    }
    clusters = G.cluster_records(graded_by_address)
    assert len(clusters) == 1 and clusters[0]["desk"] == "oil"   # "0xaa..." < "0xbb..."


# ── pool_rows ────────────────────────────────────────────────────────────

def test_pool_rows_all_first_and_skips_a_uniform_dimension():
    clusters = [
        _cluster("oil", "long", False, 10, 2.0, 0.5, 1.0, 1.0, n_trades=3, addresses={"a", "b", "c"}),
        _cluster("metals", "long", False, 10, 2.0, 0.5, 1.0, -0.5, n_trades=2, addresses={"d", "e"}),
        _cluster("oil", "long", False, 10, 2.0, 0.5, 1.0, 0.5, n_trades=1, addresses={"f"}),
    ]
    rows = G.pool_rows(clusters)
    assert rows[0]["dim"] == "all" and rows[0]["key"] == "all"
    assert {r["dim"] for r in rows} == {"all", "desk"}   # side/week/hour/planned_r/range/vs_20d_trend are uniform
    desk_rows = [r for r in rows if r["dim"] == "desk"]
    assert [r["value"] for r in desk_rows] == ["oil", "metals"]
    oil_row = next(r for r in rows if r["key"] == "desk=oil")
    assert oil_row["n"] == 2 and oil_row["n_trades"] == 4 and oil_row["members"] == 4


def test_pool_rows_present_even_when_empty():
    rows = G.pool_rows([])
    assert len(rows) == 1
    assert rows[0] == {"key": "all", "dim": "all", "value": "all", "n": 0, "n_trades": 0, "members": 0,
                       "wins": 0, "win_rate": None, "win_lo": None, "win_hi": None, "mean_r": None,
                       "r_lo": None, "r_hi": None, "p": None, "label": "NOISE"}


def test_pool_rows_order_is_n_descending_then_value():
    clusters = [
        _cluster("oil", "long", False, 1, 2.0, 0.5, 1.0, 1.0),
        _cluster("oil", "long", False, 7, 2.0, 0.5, 1.0, 1.0),
        _cluster("oil", "long", False, 8, 2.0, 0.5, 1.0, 1.0),
        _cluster("oil", "long", False, 13, 2.0, 0.5, 1.0, 1.0),
        _cluster("oil", "long", False, 14, 2.0, 0.5, 1.0, 1.0),
    ]
    rows = [r for r in G.pool_rows(clusters) if r["dim"] == "hour"]
    assert [r["value"] for r in rows] == ["06-12Z", "12-18Z", "00-06Z"]
