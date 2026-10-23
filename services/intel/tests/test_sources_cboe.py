"""Unit tests for services/intel/sources/cboe.py against real captured
VIX/VIX9D/VIX3M daily-close CSV tails (cdn.cboe.com, 2026-10-23)."""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import cboe
from services.intel.tests.conftest import fixture_text


def _indices_spec() -> DeskSpec:
    return DeskSpec(id="indices", name="Indices", tagline="t", instruments=[
        Instrument(coin="xyz:SP500", label="S&P 500", role="primary")])


def _ai_spec() -> DeskSpec:
    return DeskSpec(id="ai", name="AI", tagline="t", instruments=[
        Instrument(coin="xyz:NVDA", label="Nvidia", role="primary")])


def _oil_spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary")])


def _csv_for(symbol: str) -> str:
    return fixture_text(f"cboe_{symbol}_history.csv")


def _fake_fetch_text(url, **kw):
    for symbol in cboe.SYMBOLS:
        if url == cboe.CBOE_CSV_URL_FMT.format(sym=symbol):
            return _csv_for(symbol), None
    return None, "unexpected_url"


# ── parse_cboe_csv ───────────────────────────────────────────────────

def test_parse_real_vix_csv_tail():
    rows = cboe.parse_cboe_csv(_csv_for("VIX"))
    assert len(rows) == 8
    last_dt, last_close = rows[-1]
    assert last_dt.isoformat().startswith("2026-10-22")
    assert last_close == 19.03


def test_parse_skips_malformed_rows():
    text = "DATE,OPEN,HIGH,LOW,CLOSE\n10/22/2026,1,2,3,19.03\nnot,a,valid,row\n"
    rows = cboe.parse_cboe_csv(text)
    assert len(rows) == 1


# ── desk gating ──────────────────────────────────────────────────────

def test_skips_oil_desk_cleanly(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", _fake_fetch_text)
    items, err = cboe.fetch_cboe(_oil_spec())
    assert items == [] and err is None


def test_fires_for_ai_equity_desk(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", _fake_fetch_text)
    items, err = cboe.fetch_cboe(_ai_spec())
    assert err is None and len(items) == 4  # VIX, VIX9D, VIX3M + slope


# ── real-fixture arithmetic ──────────────────────────────────────────

def test_real_fixture_vix_day_change_and_slope(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", _fake_fetch_text)
    items, err = cboe.fetch_cboe(_indices_spec())
    assert err is None
    by_label = {dp.label: dp for dp in items}

    vix = by_label["VIX"]
    assert vix.value == 19.03 and vix.prior == 16.68
    assert abs(vix.change - (19.03 - 16.68)) < 1e-9

    slope = by_label["VIX term structure slope (9D/3M)"]
    expected_slope = 18.86 / 20.12
    assert abs(slope.value - expected_slope) < 1e-6
    assert "contango" in slope.note  # 0.937 < 1.0 -> calm, not fear


def test_slope_flags_backwardation_as_fear(monkeypatch):
    def fake(url, **kw):
        if "VIX9D" in url:
            return "DATE,OPEN,HIGH,LOW,CLOSE\n10/21/2026,1,1,1,30.0\n10/22/2026,1,1,1,32.0\n", None
        if "VIX3M" in url:
            return "DATE,OPEN,HIGH,LOW,CLOSE\n10/21/2026,1,1,1,19.0\n10/22/2026,1,1,1,20.0\n", None
        return "DATE,OPEN,HIGH,LOW,CLOSE\n10/21/2026,1,1,1,19.0\n10/22/2026,1,1,1,20.0\n", None
    monkeypatch.setattr(http_client, "fetch_text", fake)
    items, err = cboe.fetch_cboe(_indices_spec())
    assert err is None
    slope = next(dp for dp in items if "slope" in dp.label)
    assert slope.value == 32.0 / 20.0
    assert "backwardation" in slope.note
    assert slope.relevance == 0.75


# ── failure isolation ───────────────────────────────────────────────

def test_one_symbol_failing_does_not_block_the_others(monkeypatch):
    def fake(url, **kw):
        if "VIX9D" in url:
            return None, "timeout"
        return _fake_fetch_text(url)
    monkeypatch.setattr(http_client, "fetch_text", fake)
    items, err = cboe.fetch_cboe(_indices_spec())
    assert err is not None and "VIX9D" in err and "timeout" in err
    labels = {dp.label for dp in items}
    assert "VIX" in labels and "VIX3M" in labels
    assert not any("slope" in l for l in labels)  # needs both VIX9D and VIX3M


def test_insufficient_rows_is_reported(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text",
                        lambda url, **kw: ("DATE,OPEN,HIGH,LOW,CLOSE\n10/22/2026,1,1,1,19.0\n", None))
    items, err = cboe.fetch_cboe(_indices_spec())
    assert items == []
    assert err is not None and "insufficient_rows" in err
