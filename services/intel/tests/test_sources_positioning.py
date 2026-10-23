"""Unit tests for services/intel/sources/positioning.py (CFTC COT) against
real captured Socrata rows for WTI, gold, and CME bitcoin."""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import positioning
from services.intel.tests.conftest import fixture_json


def _spec(coin: str, label: str) -> DeskSpec:
    return DeskSpec(id="xx", name="X", tagline="t", instruments=[Instrument(coin=coin, label=label)])


def test_wti_real_fixture_net_long_arithmetic(monkeypatch):
    fixture = fixture_json("cftc_cot_wti.json")
    row = fixture[0]
    expected_net_long = float(row["noncomm_positions_long_all"]) - float(row["noncomm_positions_short_all"])
    expected_change = float(row["change_in_noncomm_long_all"]) - float(row["change_in_noncomm_short_all"])

    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ([row], None))
    items, err = positioning.fetch_cot(_spec("xyz:CL", "WTI"))
    assert err is None
    assert len(items) == 1
    item = items[0]
    assert item.source == "cftc_cot"
    assert item.label == "WTI non-commercial net long"
    assert item.value == expected_net_long
    assert item.change == expected_change
    assert item.as_of.year == 2026


def test_gold_real_fixture(monkeypatch):
    fixture = fixture_json("cftc_cot_gold.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ([fixture[0]], None))
    items, err = positioning.fetch_cot(_spec("XAU", "Gold"))
    assert err is None and len(items) == 1
    assert items[0].label == "Gold non-commercial net long"


def test_btc_real_fixture(monkeypatch):
    fixture = fixture_json("cftc_cot_btc.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ([fixture[0]], None))
    items, err = positioning.fetch_cot(_spec("BTC", "Bitcoin"))
    assert err is None and len(items) == 1
    assert items[0].label == "CME Bitcoin non-commercial net long"


def test_no_matching_contract_for_desk_is_clean_noop():
    items, err = positioning.fetch_cot(_spec("xyz:AI", "SomeAIStock"))
    assert items == [] and err is None


def test_contract_code_match_is_case_insensitive_and_substring_safe():
    # "wti" must match an instrument labeled "WTI", but must not spuriously
    # match on an unrelated coin that happens to contain other letters.
    matched = positioning._matched_contracts(_spec("xyz:CL", "WTI"))
    assert matched == [("067651", "WTI non-commercial net long")]


def test_both_wti_and_gold_desk_gets_both_rows(monkeypatch):
    wti_row = fixture_json("cftc_cot_wti.json")[0]
    gold_row = fixture_json("cftc_cot_gold.json")[0]
    calls = {"n": 0}

    def fake(url, params=None, **kw):
        calls["n"] += 1
        code = (params or {}).get("$where", "")
        if "067651" in code:
            return [wti_row], None
        if "088691" in code:
            return [gold_row], None
        return [], None

    monkeypatch.setattr(http_client, "fetch_json", fake)
    spec = DeskSpec(id="xx", name="X", tagline="t", instruments=[
        Instrument(coin="xyz:CL", label="WTI"), Instrument(coin="XAU", label="Gold", role="secondary"),
    ])
    items, err = positioning.fetch_cot(spec)
    assert err is None
    assert {i.source for i in items} == {"cftc_cot"}
    assert {i.label for i in items} == {"WTI non-commercial net long", "Gold non-commercial net long"}


def test_no_rows_returned_is_reported_as_failure(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ([], None))
    items, err = positioning.fetch_cot(_spec("xyz:CL", "WTI"))
    assert items == []
    assert err is not None and "no_rows" in err


def test_http_failure_propagates_with_code(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    items, err = positioning.fetch_cot(_spec("xyz:CL", "WTI"))
    assert items == []
    assert err is not None and "067651" in err and "timeout" in err


def test_safe_float_never_raises_on_garbage():
    assert positioning._safe_float("not a number") == 0.0
    assert positioning._safe_float(None) == 0.0
    assert positioning._safe_float("123.5") == 123.5


# ── new contracts: silver, natgas, S&P 500 e-mini, Nasdaq-100 e-mini ───

def test_silver_real_fixture(monkeypatch):
    fixture = fixture_json("cftc_cot_silver.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    items, err = positioning.fetch_cot(_spec("xyz:SILVER", "Silver"))
    assert err is None and len(items) == 1
    assert items[0].label == "Silver non-commercial net long"


def test_natgas_real_fixture(monkeypatch):
    fixture = fixture_json("cftc_cot_natgas.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    items, err = positioning.fetch_cot(_spec("xyz:NATGAS", "Nat Gas"))
    assert err is None and len(items) == 1
    assert items[0].label == "Natural gas non-commercial net long"


def test_sp500_emini_real_fixture(monkeypatch):
    fixture = fixture_json("cftc_cot_sp500.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    items, err = positioning.fetch_cot(_spec("xyz:SP500", "S&P 500"))
    assert err is None and len(items) == 1
    assert items[0].label == "S&P 500 e-mini non-commercial net long"


def test_nasdaq_emini_real_fixture(monkeypatch):
    fixture = fixture_json("cftc_cot_ndx.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (fixture, None))
    items, err = positioning.fetch_cot(_spec("xyz:NDX", "Nasdaq-100"))
    assert err is None and len(items) == 1
    assert items[0].label == "Nasdaq-100 e-mini non-commercial net long"


def test_metals_desk_gets_both_gold_and_silver(monkeypatch):
    gold_row = fixture_json("cftc_cot_gold.json")[0]
    silver_row = fixture_json("cftc_cot_silver.json")[0]

    def fake(url, params=None, **kw):
        code = (params or {}).get("$where", "")
        if "088691" in code:
            return [gold_row], None
        if "084691" in code:
            return [silver_row], None
        return [], None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    spec = DeskSpec(id="metals", name="Metals", tagline="t", instruments=[
        Instrument(coin="xyz:GOLD", label="Gold"), Instrument(coin="xyz:SILVER", label="Silver",
                                                               role="secondary")])
    items, err = positioning.fetch_cot(spec)
    assert err is None
    assert {i.label for i in items} == {"Gold non-commercial net long", "Silver non-commercial net long"}


# ── 1-year percentile ──────────────────────────────────────────────────

def test_percentile_1y_is_one_hundred_percent_when_latest_is_the_highest(monkeypatch):
    rows = [{"noncomm_positions_long_all": str(100 + i), "noncomm_positions_short_all": "0",
            "report_date_as_yyyy_mm_dd": f"2026-{(i % 12) + 1:02d}-01",
            "change_in_noncomm_long_all": "0", "change_in_noncomm_short_all": "0"}
            for i in range(10)]
    rows[0], rows[-1] = rows[-1], rows[0]  # newest-first: put the biggest value first
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (rows, None))
    items, err = positioning.fetch_cot(_spec("xyz:CL", "WTI"))
    assert err is None
    assert items[0].percentile_1y == 1.0


def test_percentile_1y_none_with_a_single_row(monkeypatch):
    fixture = fixture_json("cftc_cot_wti.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: ([fixture[0]], None))
    items, err = positioning.fetch_cot(_spec("xyz:CL", "WTI"))
    assert err is None
    assert items[0].percentile_1y is None


def test_fetch_cot_requests_a_years_lookback(monkeypatch):
    seen = {}

    def fake(url, params=None, **kw):
        seen["limit"] = params.get("$limit")
        return fixture_json("cftc_cot_wti.json"), None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    positioning.fetch_cot(_spec("xyz:CL", "WTI"))
    assert seen["limit"] == positioning.COT_LOOKBACK_REPORTS
    assert positioning.COT_LOOKBACK_REPORTS >= 52
