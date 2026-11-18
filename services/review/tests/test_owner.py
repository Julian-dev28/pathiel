"""services/review tags every trade owner=desk|operator from the same
ownership function the live enforcement uses (pathiel.agents.ownership.
is_desk_owned), so the operator's own hand trades stop polluting the desk's
leak detection -- the review-side half of the 2026-11-18 fix.

Gate tests: no network. `state_dir` (conftest.py, autouse) gives
pathiel.agents.ownership a fresh PATHIEL_STATE_DIR per test, so the journal
default in owner_of()/is_desk_owned() never touches another test's file.
"""
from __future__ import annotations

from pathiel.agents import ownership as O
from services.review import analysis as A
from services.review import ledger as LG

COIN = "xyz:BRENTOIL"


def _fill(side="B", sz=1.0, time=1_000, oid=1, cloid=None):
    f = {"coin": COIN, "side": side, "sz": sz, "time": time, "oid": oid}
    if cloid is not None:
        f["cloid"] = cloid
    return f


def _trade(fills_open):
    return {"coin": COIN, "desk": "oil", "fills_open": fills_open}


# ── owner_of ──────────────────────────────────────────────────────────────

def test_owner_of_desk_trade_from_cloid():
    tr = _trade([_fill(oid=1, cloid=O.make_cloid())])
    assert LG.owner_of(tr, journal=[]) == "desk"


def test_owner_of_operator_trade_no_tag():
    tr = _trade([_fill(oid=1, cloid=None)])
    assert LG.owner_of(tr, journal=[]) == "operator"


def test_owner_of_desk_trade_from_journal_backfill():
    tr = _trade([_fill(oid=42, cloid=None)])
    assert LG.owner_of(tr, journal=[{"oid": 42, "coin": COIN}]) == "desk"


def test_owner_of_a_trade_with_no_fills_open_is_operator():
    """A reconstructed trade whose fills_open key is missing or empty (a
    trade too old for loss_audit's SINCE_MS window, or the CSV regression
    fixture) never guesses desk."""
    assert LG.owner_of({"coin": COIN}, journal=[]) == "operator"
    assert LG.owner_of({"coin": COIN, "fills_open": []}, journal=[]) == "operator"


def test_owner_of_default_journal_reads_the_real_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path))
    O.record_desk_order(COIN, O.make_cloid(), "entry", oid=7)
    tr = _trade([_fill(oid=7, cloid=None)])
    assert LG.owner_of(tr) == "desk"           # journal=None -> the real journal


# ── to_record carries owner ──────────────────────────────────────────────

def test_to_record_sets_owner_desk(monkeypatch):
    tr = {"coin": COIN, "desk": "oil", "side": "long", "entry_time": 1_000, "exit_time": 2_000,
          "entry_px": 90.0, "exit_px": 91.0, "max_size": 1.0, "adds": 0, "notional_usd": 90.0,
          "closed_pnl": 1.0, "fees": 0.1, "net_usd": 0.9, "fills_open": [_fill(oid=1, cloid=O.make_cloid())]}
    rec = LG.to_record(tr, [], [], [], 10_000, journal=[])
    assert rec["owner"] == "desk" and rec["schema"] == LG.SCHEMA


def test_to_record_sets_owner_operator_when_untraceable():
    tr = {"coin": COIN, "desk": "oil", "side": "long", "entry_time": 1_000, "exit_time": 2_000,
          "entry_px": 90.0, "exit_px": 91.0, "max_size": 1.0, "adds": 0, "notional_usd": 90.0,
          "closed_pnl": 1.0, "fees": 0.1, "net_usd": 0.9, "fills_open": [_fill(oid=1, cloid=None)]}
    rec = LG.to_record(tr, [], [], [], 10_000, journal=[])
    assert rec["owner"] == "operator"


def test_the_csv_regression_fixture_has_no_owner_and_does_not_crash_analysis():
    """records_from_audit_csv builds records without fills -- no owner key,
    not "operator": the review never asserts it knows what it was never
    told. The owner dimension in analysis.py must handle that gracefully."""
    rows = LG.records_from_audit_csv(
        __file__.rsplit("/test_owner.py", 1)[0] + "/fixtures/audit_trades_2026-11-02.csv")
    assert rows and all("owner" not in r for r in rows)
    assert A.buckets(rows[0], "owner") == []


# ── analysis.py: the owner dimension sits beside "all" ──────────────────────

def _ledger_record(owner, net_usd, r_mult):
    return {"desk": "oil", "owner": owner, "side": "long", "net_usd": net_usd, "r_mult": r_mult,
            "weekend": False, "thesis_hit_7d": None, "thesis_hit_24h": None, "side_agrees": None,
            "planned_r": None, "hour_utc": 0, "range_pos": None, "risk_pct_equity": None,
            "trend_20d_pct": None, "other_desk_side": None, "tags": []}


def test_owner_dimension_separates_desk_from_operator_stats():
    records = [_ledger_record("desk", 10.0, 1.0), _ledger_record("desk", 5.0, 0.5),
              _ledger_record("operator", -13.34, -2.0), _ledger_record("operator", -11.36, -1.9)]
    rows = A.slice_table(records)
    by_key = {r["key"]: r for r in rows}
    assert by_key["owner=desk"]["n"] == 2 and by_key["owner=desk"]["net_usd"] == 15.0
    assert by_key["owner=operator"]["n"] == 2
    assert by_key["owner=operator"]["net_usd"] == -24.7
    # "all" still carries every trade, desk and operator alike -- the report
    # shows desk-only beside all-wallet, it never hides the operator's trades
    assert by_key["all"]["n"] == 4


def test_owner_dimension_is_left_out_when_every_record_has_the_same_owner():
    """slice_table's own rule: a dimension with one bucket covering every
    trade adds nothing over "all" and is dropped -- same as every other
    dimension, owner is not special-cased."""
    records = [_ledger_record("desk", 1.0, 0.1), _ledger_record("desk", 2.0, 0.2)]
    rows = A.slice_table(records)
    assert not any(r["dim"] == "owner" for r in rows)
