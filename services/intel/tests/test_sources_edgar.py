"""Unit tests for services/intel/sources/edgar.py against real captured
SEC EDGAR responses for NVDA (CIK 0001045810, captured live 2026-10-23):
the company_tickers.json map, the Form 4 browse Atom feed, one filing's
index.json, and that filing's real ownership XML (a gift, transactionCode
"G" — exercised here for the "no open-market buy/sell" path; a synthetic
P/S XML below exercises the counting logic itself)."""
from __future__ import annotations

from datetime import datetime, timezone

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import edgar
from services.intel.tests.conftest import fixture_json, fixture_text

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)  # keeps the fixture's 2026-10-09
# Form 4 filing (the real, non-amendment one) comfortably inside the 14-day window


def _ai_spec() -> DeskSpec:
    return DeskSpec(id="ai", name="AI Equities", tagline="t", instruments=[
        Instrument(coin="xyz:NVDA", label="Nvidia", role="primary")])


def _oil_spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary")])


_SYNTHETIC_FORM4_BUY_SELL_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


# ── fetch_cik_map ──────────────────────────────────────────────────────

def test_fetch_cik_map_real_fixture(monkeypatch):
    tickers = fixture_json("sec_company_tickers_sample.json")

    def fake(url, **kw):
        assert kw.get("headers") == edgar.SEC_UA
        return tickers, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    cik_map, err = edgar.fetch_cik_map()
    assert err is None
    assert cik_map["NVDA"] == "0001045810"


# ── count_buys_and_sells ─────────────────────────────────────────────

def test_count_buys_and_sells_real_gift_fixture_is_zero_zero():
    xml = fixture_text("sec_nvda_form4_sample.xml")
    buys, sells = edgar.count_buys_and_sells(xml)
    assert (buys, sells) == (0, 0)  # the real fixture is a gift (code "G")


def test_count_buys_and_sells_synthetic_mixed():
    buys, sells = edgar.count_buys_and_sells(_SYNTHETIC_FORM4_BUY_SELL_XML)
    assert (buys, sells) == (1, 2)  # one P, two S; the "A" (grant) is excluded


# ── fetch_form4_entries (real Atom feed) ────────────────────────────────

def test_fetch_form4_entries_real_fixture_excludes_amendments(monkeypatch):
    atom = fixture_text("sec_nvda_form4_browse_atom.xml")
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (atom, None))
    entries, err = edgar.fetch_form4_entries("0001045810")
    assert err is None
    assert all(e["accession"] for e in entries)
    # the real fixture's first entry is a 4/A amendment — must be excluded
    assert not any(e["filing_date"] == "2026-10-13" for e in entries)
    assert any(e["accession"] == "0001197647-26-000011" for e in entries)


def test_fetch_form4_entries_http_failure(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (None, "timeout"))
    entries, err = edgar.fetch_form4_entries("0001045810")
    assert entries is None and "timeout" in err


# ── fetch_form4_xml_path (real index.json) ─────────────────────────────

def test_fetch_form4_xml_path_real_fixture(monkeypatch):
    index_json = fixture_json("sec_nvda_form4_index.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (index_json, None))
    name, err = edgar.fetch_form4_xml_path("0001045810", "0001197647-26-000011")
    assert err is None
    assert name == "wk-form4_1791579847.xml"


def test_fetch_form4_xml_path_no_xml_found(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda *a, **k: ({"directory": {"item": [{"name": "foo.txt"}]}}, None))
    name, err = edgar.fetch_form4_xml_path("0001045810", "0001-26-000011")
    assert name is None and "no_xml_found" in err


# ── fetch_form4_activity_for_ticker end to end (real fixtures) ─────────

def test_fetch_form4_activity_real_fixtures_end_to_end(monkeypatch):
    atom = fixture_text("sec_nvda_form4_browse_atom.xml")
    index_json = fixture_json("sec_nvda_form4_index.json")
    xml_text = fixture_text("sec_nvda_form4_sample.xml")

    def fake_text(url, **kw):
        if "browse-edgar" in url:
            return atom, None
        return xml_text, None

    def fake_json(url, **kw):
        return index_json, None

    monkeypatch.setattr(http_client, "fetch_text", fake_text)
    monkeypatch.setattr(http_client, "fetch_json", fake_json)
    dp, errors = edgar.fetch_form4_activity_for_ticker("NVDA", "0001045810", NOW)
    assert errors == []
    assert dp is not None
    assert dp.source == "sec_edgar"
    assert dp.value == 0.0  # the one in-window real filing is a gift, no P/S
    assert "0 buys, 0 sells" in dp.note


def test_fetch_form4_activity_no_filings_in_window_is_clean_noop(monkeypatch):
    atom = fixture_text("sec_nvda_form4_browse_atom.xml")
    monkeypatch.setattr(http_client, "fetch_text", lambda *a, **k: (atom, None))
    far_future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    dp, errors = edgar.fetch_form4_activity_for_ticker("NVDA", "0001045810", far_future)
    assert dp is None and errors == []


# ── estimated_earnings_date / fetch_next_earnings_for_ticker ───────────

def test_estimated_earnings_date_real_fixture():
    submissions = fixture_json("sec_nvda_submissions_sample.json")
    est = edgar.estimated_earnings_date(submissions)
    assert est is not None
    est_at, last_filing_date = est
    assert last_filing_date == "2026-08-26"
    assert est_at.date().isoformat() == "2026-11-25"  # 2026-08-26 + 91 days


def test_estimated_earnings_date_no_10q_10k_returns_none():
    assert edgar.estimated_earnings_date({"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-01-01"]}}}) is None
    assert edgar.estimated_earnings_date({}) is None


def test_fetch_next_earnings_for_ticker_real_fixture(monkeypatch):
    submissions = fixture_json("sec_nvda_submissions_sample.json")
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (submissions, None))
    dp, err = edgar.fetch_next_earnings_for_ticker("NVDA", "0001045810", NOW)
    assert err is None and dp is not None
    assert dp.unit == "days"
    assert abs(dp.value - 33.5) < 1.0  # 2026-11-25T00:00 minus 2026-10-22T12:00
    assert "2026-11-25" in dp.note
    assert "not confirmed" in dp.note


# ── fetch_edgar (desk gating + end to end) ─────────────────────────────

def test_fetch_edgar_skips_non_equity_desk(monkeypatch):
    items, err = edgar.fetch_edgar(_oil_spec(), NOW)
    assert items == [] and err is None


def test_fetch_edgar_end_to_end_real_fixtures(monkeypatch):
    tickers_fixture = fixture_json("sec_company_tickers_sample.json")
    atom = fixture_text("sec_nvda_form4_browse_atom.xml")
    index_json = fixture_json("sec_nvda_form4_index.json")
    xml_text = fixture_text("sec_nvda_form4_sample.xml")
    submissions = fixture_json("sec_nvda_submissions_sample.json")

    def fake_text(url, **kw):
        if "browse-edgar" in url:
            return atom, None
        return xml_text, None

    def fake_json(url, **kw):
        if "company_tickers" in url:
            return tickers_fixture, None
        if "submissions" in url:
            return submissions, None
        return index_json, None

    monkeypatch.setattr(http_client, "fetch_text", fake_text)
    monkeypatch.setattr(http_client, "fetch_json", fake_json)
    items, err = edgar.fetch_edgar(_ai_spec(), NOW)
    assert err is None
    labels = {dp.label for dp in items}
    assert "NVDA insider Form 4 net (buys-sells)" in labels
    assert "NVDA next earnings (estimated)" in labels


def test_fetch_edgar_cik_map_failure_propagates(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json", lambda *a, **k: (None, "timeout"))
    items, err = edgar.fetch_edgar(_ai_spec(), NOW)
    assert items == []
    assert err is not None and "timeout" in err
