"""Unit tests for services/intel/sources/fred.py. No live FRED_API_KEY was
available in this sandbox (confirmed live 2026-10-23: FRED returns the
documented API_KEY_MISSING-equivalent error body with no key) — the
success-path fixtures are built from FRED's documented
`/fred/series/observations` schema, same caveat as eia.py/policy.py."""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import fred


def _metals_spec() -> DeskSpec:
    return DeskSpec(id="metals", name="Metals", tagline="t", instruments=[
        Instrument(coin="xyz:GOLD", label="Gold", role="primary")])


def _indices_spec() -> DeskSpec:
    return DeskSpec(id="indices", name="Indices", tagline="t", instruments=[
        Instrument(coin="xyz:SP500", label="S&P 500", role="primary")])


def _oil_spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary")])


def _obs(values_newest_first):
    dates = [f"2026-10-{22 - i}" for i in range(len(values_newest_first))]
    return [{"date": d, "value": v} for d, v in zip(dates, values_newest_first)]


def test_missing_key_is_reported_not_silent(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    items, err = fred.fetch_fred(_metals_spec())
    assert items == []
    assert err == "fred:FRED_API_KEY_missing"


def test_skips_desks_that_are_neither_metals_nor_indices(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    items, err = fred.fetch_fred(_oil_spec())
    assert items == [] and err is None


def test_indices_desk_also_gets_fred(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: ({"observations": _obs([4.01] * 10)}, None))
    items, err = fred.fetch_fred(_indices_spec())
    assert err is None and len(items) == len(fred.SERIES)


def test_computes_5d_change_from_the_6th_observation_back(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    # [10] is 5 observations back from [0] (indices 0..5 -> 6 values).
    values = [4.10, 4.09, 4.08, 4.07, 4.06, 4.00, 3.90]

    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: ({"observations": _obs(values)}, None))
    items, err = fred.fetch_fred(_metals_spec())
    assert err is None
    dgs10 = next(dp for dp in items if dp.label == "10y Treasury yield")
    assert dgs10.value == 4.10
    assert dgs10.prior == 4.00  # values[5], the 5th-back observation
    assert abs(dgs10.change - 0.10) < 1e-9
    assert "5d change" in dgs10.note


def test_skips_missing_observations_marked_with_a_dot(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    raw = [{"date": "2026-10-22", "value": "."}, {"date": "2026-10-21", "value": "4.02"}]
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: ({"observations": raw}, None))
    items, err = fred.fetch_fred(_metals_spec())
    assert err is None
    dgs10 = next(dp for dp in items if dp.label == "10y Treasury yield")
    assert dgs10.value == 4.02  # the "." row is skipped, not treated as 0


def test_per_series_http_failure_is_isolated(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    calls = {"n": 0}

    def fake(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "timeout"
        return {"observations": _obs([1.0] * 10)}, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    items, err = fred.fetch_fred(_metals_spec())
    assert err is not None and "timeout" in err
    assert calls["n"] == len(fred.SERIES), "one series failing must not stop the others"
    assert len(items) == len(fred.SERIES) - 1


def test_no_valid_observations_is_reported(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: ({"observations": [{"date": "x", "value": "."}]}, None))
    items, err = fred.fetch_fred(_metals_spec())
    assert items == []
    assert err is not None and "no_valid_observations" in err
