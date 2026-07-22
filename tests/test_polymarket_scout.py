"""Polymarket judgment-edge shadow scout — pure logic, ledger grading, and the
scan funnel. No network: a fake client + stub forecaster drive everything."""
from __future__ import annotations

import json
import time

import pytest

from services.polymarket_scout import ledger, scout
from services.polymarket_scout.forecaster import StubForecaster, _parse_forecast
from services.polymarket_scout.run import CFG, scan

DAY = scout._DAY_MS
NOW = int(time.time() * 1000)


def _iso(days_from_now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((NOW + int(days_from_now * DAY)) / 1000))


def _mkt(**over):
    m = {"id": "1", "question": "Will X happen by August?", "clobTokenIds": json.dumps(["yes_tok", "no_tok"]),
         "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0.40", "0.60"]),
         "endDate": _iso(10), "liquidity": "5000", "enableOrderBook": True, "active": True, "closed": False}
    m.update(over)
    return m


# ── filtering ────────────────────────────────────────────────────────────────
def test_latency_market_is_rejected():
    assert scout.is_latency_market({"question": "Bitcoin Up or Down - 5PM ET"}) is True
    assert scout.is_latency_market({"question": "Ethereum higher or lower today"}) is True
    assert scout.is_latency_market({"question": "Will Fed cut rates in September?"}) is False


def test_market_yes_prob_parses_string_and_list():
    assert scout.market_yes_prob(_mkt(outcomePrices=json.dumps(["0.73", "0.27"]))) == 0.73
    assert scout.market_yes_prob({"outcomePrices": ["0.10", "0.90"]}) == 0.10
    assert scout.market_yes_prob({"outcomePrices": None}) is None


def test_is_judgment_market_full_gate():
    assert scout.is_judgment_market(_mkt(), NOW, CFG) is True
    # order book off
    assert scout.is_judgment_market(_mkt(enableOrderBook=False), NOW, CFG) is False
    # closed
    assert scout.is_judgment_market(_mkt(closed=True), NOW, CFG) is False
    # too illiquid
    assert scout.is_judgment_market(_mkt(liquidity="100"), NOW, CFG) is False
    # resolves too soon (1d) and too far (30d)
    assert scout.is_judgment_market(_mkt(endDate=_iso(1)), NOW, CFG) is False
    assert scout.is_judgment_market(_mkt(endDate=_iso(30)), NOW, CFG) is False
    # near-settled extreme price -> no edge to find
    assert scout.is_judgment_market(_mkt(outcomePrices=json.dumps(["0.97", "0.03"])), NOW, CFG) is False
    # latency market excluded even if otherwise valid
    assert scout.is_judgment_market(_mkt(question="Solana Up or Down 3PM"), NOW, CFG) is False


# ── edge + fill + scoring ────────────────────────────────────────────────────
def test_signed_edge_and_side_band():
    assert scout.decide_side(scout.signed_edge(0.65, 0.40), 0.12) == "YES"   # LLM sees YES underpriced
    assert scout.decide_side(scout.signed_edge(0.20, 0.40), 0.12) == "NO"
    assert scout.decide_side(scout.signed_edge(0.45, 0.40), 0.12) is None     # inside the band


def test_paper_pnl_touch_fill_net_of_fees():
    # bought a side @ 0.40, it WON: gross +0.60, minus fill fee + redemption fee
    assert scout.paper_pnl(True, 0.40) == pytest.approx(0.60 - 2 * scout.FEE_PER_FILL)
    # bought @ 0.40, it LOST: gross -0.40, minus one fill fee
    assert scout.paper_pnl(False, 0.40) == pytest.approx(-0.40 - scout.FEE_PER_FILL)


def test_brier():
    assert scout.brier(0.9, True) == pytest.approx(0.01)
    assert scout.brier(0.9, False) == pytest.approx(0.81)


# ── scan funnel (fake client + stub forecaster, no network) ──────────────────
class _FakeClient:
    def __init__(self, markets, ask=(0.40, 500.0)):
        self._m = markets
        self._ask = ask

    def open_markets(self):
        return self._m

    def best_ask(self, token_id):
        return self._ask


def test_scan_records_divergences_and_skips_in_band(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    markets = [_mkt(id="A", outcomePrices=json.dumps(["0.40", "0.60"])),   # LLM 0.70 -> YES edge +0.30
               _mkt(id="B", outcomePrices=json.dumps(["0.55", "0.45"]))]   # LLM 0.52 -> in band, skip
    # both markets share the question, so make the forecaster prob depend on id via desc
    def stub(q, d):
        return (0.70, "strong yes") if d == "A" else (0.52, "coin flip")
    for m in markets:
        m["description"] = m["id"]
    rec = scan(_FakeClient(markets), StubForecaster(stub), CFG, record_fn=ledger.record)
    assert len(rec) == 1 and rec[0]["side"] == "YES" and rec[0]["market_id"] == "A"
    assert rec[0]["fill_px"] == 0.40                       # filled at the touch, not the 0.40 mid coincidence
    # persisted to the ledger
    rows = ledger.load()
    assert len(rows) == 1 and rows[0]["market_id"] == "A"


def test_scan_skips_when_no_ask_to_fill(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    m = _mkt(id="C", description="C", outcomePrices=json.dumps(["0.30", "0.70"]))
    client = _FakeClient([m]); client._ask = None          # no book -> can't paper-fill
    rec = scan(client, StubForecaster(lambda q, d: (0.75, "yes")), CFG, record_fn=ledger.record)
    assert rec == []


# ── ledger grading + Brier comparison ────────────────────────────────────────
def test_grade_scores_pnl_and_llm_vs_market_brier(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    # 2 YES paper trades filled @0.40; LLM said 0.70, market said 0.40
    for i in (1, 2):
        ledger.record(market_id=str(i), question=f"q{i}", side="YES", token_id="t",
                      llm_yes=0.70, mkt_yes=0.40, fill_px=0.40, edge=0.30, end_date=_iso(5))
    # market 1 resolves YES (LLM right, we win), market 2 resolves NO (we lose)
    outcomes = {"1": True, "2": False, "3": None}
    g = ledger.grade(lambda mid: outcomes.get(mid))
    assert g["n"] == 2
    assert g["mean_pnl_per_$"] == pytest.approx(((0.60 - 0.02) + (-0.40 - 0.01)) / 2, abs=1e-6)
    # LLM (0.70) is better calibrated than the market (0.40) on the one that hit YES,
    # worse on the NO — net Brier decides. Both computed, comparison exposed.
    assert "brier_llm" in g and "brier_mkt" in g and "llm_beats_market" in g


def test_grade_ignores_unresolved():
    g = ledger.grade(lambda mid: None, rows=[{"market_id": "x", "side": "YES", "fill_px": 0.4,
                                              "llm_yes": 0.6, "mkt_yes": 0.5}])
    assert g["n"] == 0 and g["pending"] == 1


# ── forecaster parse tolerance ───────────────────────────────────────────────
def test_parse_forecast_tolerates_prose_and_clamps():
    p, why = _parse_forecast('Sure. {"yes_prob": 0.62, "reasoning": "solid"} done')
    assert p == 0.62 and why == "solid"
    assert _parse_forecast('{"yes_prob": 1.5}') is None      # out of [0,1] -> rejected
    p3, _ = _parse_forecast('{"yes_prob": 0.995, "reasoning": ""}')
    assert p3 == 0.99                                         # in-range extreme -> clamped
    assert _parse_forecast("no json here") is None


# ── live resolver (resolution grading) ───────────────────────────────────────
def test_resolve_yes_won_reads_settled_prices():
    assert scout.resolve_yes_won({"closed": True, "outcomePrices": json.dumps(["1", "0"])}) is True
    assert scout.resolve_yes_won({"closed": True, "outcomePrices": json.dumps(["0", "1"])}) is False
    assert scout.resolve_yes_won({"closed": False, "outcomePrices": json.dumps(["0.6", "0.4"])}) is None
    assert scout.resolve_yes_won({"closed": True, "outcomePrices": json.dumps(["0.5", "0.5"])}) is None  # void/ambiguous
    assert scout.resolve_yes_won(None) is None


def test_gamma_resolver_caches_per_market():
    calls = {"n": 0}

    class _C:
        def market_by_id(self, mid):
            calls["n"] += 1
            return {"closed": True, "outcomePrices": json.dumps(["1", "0"])} if mid == "win" else \
                   {"closed": False, "outcomePrices": json.dumps(["0.3", "0.7"])}

    r = scout.make_gamma_resolver(_C())
    assert r("win") is True and r("win") is True     # second call cached
    assert r("open") is None
    assert calls["n"] == 2                            # "win" fetched once, "open" once


def test_scan_dedups_already_recorded_markets(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path))
    m = _mkt(id="DUP", description="DUP", outcomePrices=json.dumps(["0.40", "0.60"]))
    fc = StubForecaster(lambda q, d: (0.75, "yes"))
    first = scan(_FakeClient([m]), fc, CFG, record_fn=ledger.record)
    assert len(first) == 1                               # recorded once
    second = scan(_FakeClient([m]), fc, CFG, record_fn=ledger.record)
    assert second == []                                  # same market skipped next run
    assert len(ledger.load()) == 1                       # no duplicate in the ledger
