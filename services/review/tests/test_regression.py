"""Regression on the real record: the 11 trades of the 2026-11-02 loss audit
(/tmp/loss-audit/trades.csv, copied to fixtures/audit_trades_2026-11-02.csv:
10 closed, 1 open) and the saved inputs bundle the review rebuilds them
from (fixtures/bundle_2026-11-02, public fills and candles, data to
2026-11-02 09:40Z).

The 11-02 Brent long (-$33.87, a wrong-way call at full size) is the trade
the loop most wants to "learn" from. At n=1 its slice must not be called a
LEAK: one trade is an anecdote, and the review has to say so."""
from __future__ import annotations

import pytest

from services.review import analysis as A
from services.review import inputs as I
from services.review import ledger as LG
from services.review import stats as S
from services.review.tests.conftest import AUDIT_CSV, BUNDLE

NOV2_LONG = "2026-11-02T03:50Z"


@pytest.fixture(scope="module")
def csv_records():
    return LG.records_from_audit_csv(AUDIT_CSV)


@pytest.fixture(scope="module")
def bundle_records():
    return LG.build_records(I.load_bundle(BUNDLE))


def test_the_csv_holds_the_ten_closed_trades(csv_records):
    assert len(csv_records) == 10
    # 31.4886: the CSV's own net column (LOSS_AUDIT_2026-11-02.md prints +31.47 from an earlier fetch)
    assert round(sum(r["net_usd"] for r in csv_records), 2) == 31.49


def test_the_nov2_brent_long_is_wrong_direction_and_size(csv_records):
    r = next(x for x in csv_records if x["entry"] == NOV2_LONG)
    assert r["coin"] == "xyz:BRENTOIL" and r["side"] == "long"
    assert r["tags"] == ["wrong_direction", "size"]
    assert r["net_usd"] == pytest.approx(-33.8717)
    assert r["r_mult"] == pytest.approx(-33.8717 / (0.76 * 42.36), rel=1e-6)


def test_its_slices_are_not_called_a_leak_at_this_n(csv_records):
    rows = {r["key"]: r for r in A.slice_table(csv_records)}
    wd = rows["cause=wrong_direction"]
    assert wd["n"] == 1 and wd["label"] == S.NOISE
    assert rows["cause=size"]["n"] == 2 and rows["cause=size"]["label"] == S.NOISE
    assert not [k for k, r in rows.items() if r["label"] in (S.EDGE, S.LEAK)]   # n is small everywhere


def test_the_bundle_rebuilds_the_same_trades_as_the_audit(csv_records, bundle_records):
    """The ledger is built by the loss audit's own code: same trades, same
    net, same tags as the audit's CSV (both from the same fills)."""
    assert [r["entry"] for r in bundle_records] == [r["entry"] for r in csv_records]
    for a, b in zip(bundle_records, csv_records):
        assert a["net_usd"] == pytest.approx(b["net_usd"], abs=0.01), a["entry"]
        assert a["tags"] == b["tags"], a["entry"]


def test_the_bundle_record_carries_thesis_and_rule_context(bundle_records):
    r = next(x for x in bundle_records if x["entry"] == NOV2_LONG)
    assert r["thesis_id"] == "oil-20261102T0348-m" and r["thesis_side"] == "long"
    assert r["thesis_age_min"] == pytest.approx(1.6, abs=0.1)        # opened 2 minutes after the rewrite
    assert r["rewrite_reason"] == "same_side"
    assert r["weekend"] is False and r["hour_utc"] == 3
    assert r["range_pos"] < 0                                          # bought under the 24h low
    assert r["eff_leverage"] == pytest.approx(17.9)
    assert r["risk_pct_equity"] == pytest.approx(12.3)
    assert r["settled"] is False                                       # stopped 08:35Z, data to 09:40Z
    assert 0 <= r["luck_pct"] <= 100 and r["luck_n"] > 400


def test_settled_and_provisional_split_at_twelve_hours(bundle_records):
    prov = [r["entry"] for r in bundle_records if not r["settled"]]
    assert prov == ["2026-11-02T00:51Z", "2026-11-02T01:18Z", NOV2_LONG]


def test_the_luck_check_is_deterministic(bundle_records):
    again = LG.build_records(I.load_bundle(BUNDLE))
    assert [r["luck_pct"] for r in again] == [r["luck_pct"] for r in bundle_records]
