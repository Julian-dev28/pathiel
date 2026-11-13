"""Gate tests for the trade attribution (services/strategies/evals/
trade_attribution.py, 2026-11-13): the pure features, the slices, the
operator's facts recomputed, and a regression on the saved bundle of both
wallets' public fills (fixtures/attribution_2026-11-13: 23 closed round
trips, 2026-10-15 to 2026-11-13). No network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pathiel.models.types import Candle
from services.review import stats as S
from services.strategies.evals import trade_attribution as A

HOUR_MS = 3_600_000
FIXTURE = Path(__file__).parent / "fixtures" / "attribution_2026-11-13"


def _bars(closes, t0=0, spread=1.0):
    return [Candle(t=t0 + i * HOUR_MS, o=c, h=c + spread, l=c - spread, c=c, v=1.0) for i, c in enumerate(closes)]


# ── pure features ──────────────────────────────────────────────────────────

def test_closed_before_keeps_only_bars_closed_by_the_tick():
    bars = _bars([1.0, 2.0, 3.0])
    assert [b.c for b in A.closed_before(bars, 2 * HOUR_MS)] == [1.0, 2.0]
    assert [b.c for b in A.closed_before(bars, 2 * HOUR_MS - 1)] == [1.0]


def test_atr14_is_the_simple_mean_of_true_ranges_and_needs_15_bars():
    flat = _bars([100.0] * 15, spread=0.5)          # every true range is 1.0
    assert A.atr14(flat) == pytest.approx(1.0)
    assert A.atr14(flat[:14]) is None
    gap = _bars([100.0] * 14 + [103.0], spread=0.5)  # the last bar gaps: TR = 103.5 - 100 = 3.5
    assert A.atr14(gap) == pytest.approx((13 * 1.0 + 3.5) / 14)


def test_ret_pct_reads_the_last_close_against_n_bars_earlier():
    bars = _bars([100.0, 50.0, 110.0])
    assert A.ret_pct(bars, 2) == pytest.approx(10.0)
    assert A.ret_pct(bars, 1) == pytest.approx(120.0)
    assert A.ret_pct(bars, 3) is None


def test_entry_type_calls_an_ioc_limit_a_market_entry():
    """place_bracket sends "take the mid now" as a Limit/Ioc; a Gtc limit
    rested at a price."""
    assert A.entry_type("Limit/Ioc") == "market"
    assert A.entry_type("Market/Ioc") == "market"
    assert A.entry_type("Limit/Gtc") == "resting"
    assert A.entry_type("Limit/Alo") == "resting"
    assert A.entry_type(None) == "unknown"


def test_order_type_finds_the_opening_order_by_oid_and_coin():
    orders = [{"order": {"coin": "xyz:BRENTOIL", "oid": 7, "orderType": "Limit", "tif": "Ioc"}},
              {"order": {"coin": "xyz:SILVER", "oid": 7, "orderType": "Limit", "tif": "Gtc"}}]
    assert A.order_type(7, orders, "xyz:BRENTOIL") == "Limit/Ioc"
    assert A.order_type(7, orders, "xyz:SILVER") == "Limit/Gtc"
    assert A.order_type(8, orders, "xyz:BRENTOIL") is None


def test_funding_at_takes_the_latest_row_at_or_before_the_entry():
    rows = [{"time": 1000, "fundingRate": "0.001"}, {"time": 2000, "fundingRate": "-0.002"},
            {"time": 3000, "fundingRate": "0.003"}]
    assert A.funding_at(2000, rows) == pytest.approx(-0.002)
    assert A.funding_at(2999, rows) == pytest.approx(-0.002)
    assert A.funding_at(999, rows) is None


def test_funding_side_says_who_pays():
    assert A.funding_side(0.001, "long") == "pays"
    assert A.funding_side(0.001, "short") == "receives"
    assert A.funding_side(-0.001, "long") == "receives"
    assert A.funding_side(0.0, "long") == "zero"
    assert A.funding_side(None, "long") is None


def test_event_proximity_signs_the_hours_and_ignores_impact_1():
    t = 100 * HOUR_MS
    events = [{"title": "noise", "impact": 1, "coins": ["xyz:BRENTOIL"], "at_ms": t + HOUR_MS, "window": None},
              {"title": "FOMC", "impact": 3, "coins": ["xyz:BRENTOIL"], "at_ms": t + 5 * HOUR_MS,
               "window": {"start_ms": t + 3 * HOUR_MS, "end_ms": t + 7 * HOUR_MS}},
              {"title": "gold only", "impact": 3, "coins": ["xyz:GOLD"], "at_ms": t + 2 * HOUR_MS, "window": None}]
    out = A.event_proximity("xyz:BRENTOIL", t, events, [])
    assert out == {"event_h": 5.0, "event_name": "FOMC", "in_blackout": False, "blackout_event": None}
    inside = A.event_proximity("xyz:BRENTOIL", t + 4 * HOUR_MS, events, [])
    assert inside["in_blackout"] is True and inside["blackout_event"] == "FOMC" and inside["event_h"] == 1.0


def test_event_proximity_reads_the_eia_two_hour_window():
    t = 100 * HOUR_MS
    eia = [t + int(1.5 * HOUR_MS)]
    out = A.event_proximity("xyz:BRENTOIL", t, [], eia)
    assert out["event_name"].startswith("EIA") and out["event_h"] == 1.5 and out["in_blackout"] is True
    after = A.event_proximity("xyz:BRENTOIL", t + 4 * HOUR_MS, [], eia)
    assert after["in_blackout"] is False and after["event_h"] == -2.5


def test_exit_type_calls_a_manual_close_inside_the_oil_weekend_a_weekend_flat():
    # 2026-11-06 21:00Z is Friday 16:00 New York: the first weekend tick
    from datetime import datetime, timezone
    fri = int(datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc).timestamp() * 1000)
    orders = [{"order": {"coin": "xyz:BRENTOIL", "oid": 1, "orderType": "Limit", "timestamp": fri}}]
    tr = {"coin": "xyz:BRENTOIL", "exit_time": fri, "close_oids": [1]}
    assert A.exit_type(tr, orders) == "weekend"
    tr_mid = {"coin": "xyz:BRENTOIL", "exit_time": fri - 10 * HOUR_MS, "close_oids": [1]}
    assert A.exit_type(tr_mid, orders) == "manual"
    stop = [{"order": {"coin": "xyz:BRENTOIL", "oid": 1, "orderType": "Stop Market", "timestamp": fri}}]
    assert A.exit_type(tr, stop) == "stop"


def test_range_bucket_separates_through_the_edge_from_at_the_edge():
    assert A._range({"range_pos": -0.05}) == "through own edge"
    assert A._range({"range_pos": 0.0}) == "bottom third (at edge)"
    assert A._range({"range_pos": 0.33}) == "bottom third (at edge)"
    assert A._range({"range_pos": 0.5}) == "middle third"
    assert A._range({"range_pos": 0.9}) == "top third"
    assert A._range({"range_pos": 1.2}) == "past far edge"
    assert A._range({}) is None


def test_other_buckets():
    assert A._stop_atr({"stop_atr": 0.6}) == "<1.5 ATR" and A._stop_atr({"stop_atr": 3.0}) == "2.5+ ATR"
    assert A._ret5d({"ret_5d_pct": -5.0}) == "5d down >2%" and A._ret5d({"ret_5d_pct": 1.0}) == "5d flat"
    assert A._hour({"hour_utc": 13}) == "12-18Z" and A._hour({"hour_utc": 0}) == "00-06Z"
    assert A._event({"in_blackout": True, "event_h": 30.0}) == "inside blackout"
    assert A._event({"in_blackout": False, "event_h": -30.0}) == "event 12-48h away"
    assert A._hold({"hold_h": 1.0}) == "held <2h" and A._hold({"hold_h": 30.0}) == "held 12h+"
    assert A._rewrite({"rewrite_2h": True}) == "rewrite <2h before" and A._rewrite({}) is None
    assert A._month({"entry": "2026-11-04T17:32Z"}) == "2026-11"


def test_slice_row_uses_the_reviews_labels():
    recs = [{"side": "long", "net_usd": 10.0, "r_mult": 2.0}] * 4 + [{"side": "short", "net_usd": -5.0, "r_mult": -1.0}] * 4
    row = A.slice_row(recs, "side", "long")
    assert row["n"] == 4 and row["wins"] == 4 and row["net_usd"] == 40.0
    assert row["label"] == S.LEAN_POS            # 4 trades, all one sign, under 10: LEANING+, never EDGE
    assert A.slice_row(recs, "side", "short")["label"] == S.LEAN_NEG
    assert A.slice_row(recs[:2], "all", "all")["label"] == S.NOISE   # n < 3


def test_slice_table_skips_a_dimension_with_one_bucket_covering_everything():
    recs = [{"wallet": "pathiel", "side": "long", "net_usd": 1.0, "r_mult": 1.0}] * 3
    keys = {r["key"] for r in A.slice_table(recs)}
    assert "all" in keys and "wallet=pathiel" not in keys and "side=long" not in keys


def test_shorts_check_counts_per_wallet_and_in_all():
    recs = [{"wallet": "pathiel", "side": "short", "coin": "xyz:SILVER", "net_usd": -2.0},
            {"wallet": "pathiel", "side": "long", "coin": "xyz:BRENTOIL", "net_usd": 9.0},
            {"wallet": "watch", "side": "long", "coin": "xyz:BRENTOIL", "net_usd": 1.0}]
    out = A.shorts_check(recs)
    assert out["all"] == {"n": 1, "wins": 0, "net_usd": -2.0, "coins": ["xyz:SILVER"], "made_money": False}
    assert out["watch"]["n"] == 0 and out["watch"]["made_money"] is False


def test_winners_vs_losers_counts_each_bucket():
    recs = [{"side": "long", "net_usd": 5.0}, {"side": "long", "net_usd": -1.0}, {"side": "short", "net_usd": -1.0}]
    rows = {(r["dim"], r["value"]): r for r in A.winners_vs_losers(recs)}
    assert rows[("side", "long")] == {"dim": "side", "value": "long", "n": 2, "wins": 1, "losses": 1,
                                      "win_rate": 0.5, "net_usd": 4.0}


# ── the saved bundle: both wallets, 2026-10-15 to 2026-11-13 ───────────────

@pytest.fixture(scope="module")
def result():
    return A.run(A.load_bundle(str(FIXTURE)))


def test_bundle_pairs_23_round_trips(result):
    t = result["totals"]
    assert t["all"] == {"n": 23, "wins": 9, "net_usd": -37.84}
    assert t["pathiel"] == {"n": 19, "wins": 7, "net_usd": 20.56}      # the review ledger's 19 trades, +$20.56
    assert t["watch"] == {"n": 4, "wins": 2, "net_usd": -58.4}
    assert all(r["entry_type"] in ("market", "resting") for r in result["records"])


def test_no_short_made_money_in_either_wallet(result):
    s = result["shorts"]
    assert s["pathiel"] == {"n": 5, "wins": 0, "net_usd": -14.41, "coins": ["xyz:SILVER"], "made_money": False}
    assert s["watch"]["n"] == 0 and s["all"]["n"] == 5
    assert not any(v["made_money"] for v in s.values())


def test_the_operators_facts_recomputed(result):
    f = result["facts"]
    assert f["october_oil_longs"]["computed"] == {"n": 7, "wins": 5, "net_usd": 69.74} and f["october_oil_longs"]["matches"]
    assert f["silver_shorts"]["computed"] == {"n": 5, "wins": 0, "net_usd": -14.41} and f["silver_shorts"]["matches"]
    last = f["last_trade"]["computed"]
    assert last["entry"] == "2026-11-12T03:58Z" and last["closed_pnl"] == 30.2 and last["net_usd"] == 30.01
    assert f["last_trade"]["matches"]
    # the brief said 3 longs through the low lost $74.95; the fills show four, -$110.68
    nov = f["november_through_low_longs_11_02_03"]["computed"]
    assert nov["n"] == 4 and nov["net_usd"] == -110.68 and not f["november_through_low_longs_11_02_03"]["matches"]


def test_the_winner_and_loser_slices(result):
    rows = {r["key"]: r for r in result["slices"]}
    edge = rows["range=bottom third (at edge)"]
    assert (edge["n"], edge["wins"], edge["net_usd"], edge["label"]) == (5, 3, 80.63, S.LEAN_POS)
    through = rows["range=through own edge"]
    assert (through["n"], through["wins"], through["net_usd"], through["label"]) == (11, 2, -58.9, S.LEAN_NEG)
    assert (rows["entry=resting"]["n"], rows["entry=resting"]["wins"], rows["entry=resting"]["label"]) == (5, 3, S.LEAN_POS)
    assert (rows["entry=market"]["n"], rows["entry=market"]["wins"], rows["entry=market"]["label"]) == (18, 6, S.LEAN_NEG)
    old = rows["rewrite=thesis older than 2h"]
    assert (old["n"], old["wins"], old["net_usd"], old["label"]) == (4, 3, 70.68, S.LEAN_POS)
    fresh = rows["rewrite=rewrite <2h before"]
    assert (fresh["n"], fresh["wins"], fresh["net_usd"], fresh["label"]) == (10, 4, -35.7, S.NOISE)
    assert rows["exit=stop"]["label"] == S.LEAK and rows["exit=stop"]["n"] == 12   # by definition, not a trait
    assert rows["side=short"]["n"] == 5 and rows["side=short"]["wins"] == 0
    assert rows["month=2026-10"]["label"] == S.LEAN_POS and rows["month=2026-11"]["label"] == S.LEAN_NEG
    assert rows["hour=12-18Z"]["label"] == S.NOISE          # 5 of 8, +$73: a lead, not a rule


def test_every_desk_trade_has_its_entry_context(result):
    for r in result["records"]:
        assert r["range_pos"] is not None and r["ret_5d_pct"] is not None and r["atr14_1h"] is not None
        assert r["hour_utc"] == int(r["entry"][11:13]) and r["weekday"] in A.WEEKDAYS
        assert r["funding_vs_side"] in ("pays", "receives", "zero")
        assert r["exit_type"] in ("stop", "target", "manual", "weekend")
    by_entry = {r["entry"]: r for r in result["records"]}
    r = by_entry["2026-11-04T17:32Z"]                      # the +$58 bounce long, inside the midterms window
    assert r["in_blackout"] is True and "midterm" in r["blackout_event"] and r["rewrite_2h"] is True
    assert by_entry["2026-10-31T16:18Z"]["exit_type"] == "weekend"   # closed by hand inside the oil weekend
    assert by_entry["2026-10-27T14:33Z"]["entry_type"] == "resting" and by_entry["2026-10-27T14:33Z"]["range_pos"] == 0.0
    watch = [r for r in result["records"] if r["wallet"] == "watch"]
    assert all(r["r_mult"] is None and r["exit_type"] == "manual" for r in watch)   # no bracket on the watch wallet


def test_cli_offline_writes_the_three_files(tmp_path):
    rc = A.main(["--offline", str(FIXTURE), "--out-dir", str(tmp_path)])
    assert rc == 0
    for name in ("trades.csv", "report.json", "attribution.md"):
        assert (tmp_path / name).stat().st_size > 0
    rep = json.loads((tmp_path / "report.json").read_text())
    assert rep["totals"]["all"]["n"] == 23
    md = (tmp_path / "attribution.md").read_text()
    assert "| pathiel | 5 | 0 | -14.41 | xyz:SILVER | no |" in md


def test_markdown_renders_every_record_once(result):
    md = A.markdown(result)
    assert md.count("| pathiel | BRENTOIL |") == 14 and md.count("| pathiel | SILVER |") == 5
    assert md.count("| watch | BRENTOIL |") == 4


def test_a_bundle_round_trip_is_byte_stable(tmp_path):
    """save_bundle(load_bundle(x)) replays to the same records."""
    b = A.load_bundle(str(FIXTURE))
    out = tmp_path / "b"
    A.save_bundle(b, str(out), coins=b["coins"])
    again = A.run(A.load_bundle(str(out)))
    first = A.run(b)
    assert [(r["entry"], r["net_usd"], r["r_mult"]) for r in again["records"]] == \
        [(r["entry"], r["net_usd"], r["r_mult"]) for r in first["records"]]
    assert os.path.exists(out / "meta.json")
