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


# ── fetch_observations: the research MCP's fred_series tool ─────────────────

def test_fetch_observations_missing_key_is_reported_not_silent(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    data, err = fred.fetch_observations("DGS10")
    assert data is None
    assert err == "FRED_API_KEY_missing"


def test_fetch_observations_passes_an_explicit_key_without_reading_the_env(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    seen = {}

    def fake(url, **kw):
        seen.update(kw.get("params") or {})
        return {"observations": _obs([1.0])}, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    data, err = fred.fetch_observations("DGS10", api_key="explicit-key")
    assert err is None and data is not None
    assert seen["api_key"] == "explicit-key"


def test_fetch_observations_no_range_is_newest_first_capped_at_obs_limit(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    seen = {}

    def fake(url, **kw):
        seen.update(kw.get("params") or {})
        return {"observations": _obs([1.0, 2.0])}, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    data, err = fred.fetch_observations("DGS10")
    assert err is None
    assert seen["sort_order"] == "desc"
    assert seen["limit"] == fred.OBS_LIMIT
    assert "observation_start" not in seen and "observation_end" not in seen


def test_fetch_observations_with_a_range_is_chronological_and_uncapped(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    seen = {}

    def fake(url, **kw):
        seen.update(kw.get("params") or {})
        return {"observations": _obs([1.0, 2.0])}, None
    monkeypatch.setattr(http_client, "fetch_json", fake)
    data, err = fred.fetch_observations("DGS10", start="2026-01-01", end="2026-06-01")
    assert err is None
    assert seen["sort_order"] == "asc"
    assert seen["observation_start"] == "2026-01-01"
    assert seen["observation_end"] == "2026-06-01"
    assert "limit" not in seen


def test_fetch_observations_bad_shape_and_http_error_pass_through(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: ({"nope": True}, None))
    data, err = fred.fetch_observations("DGS10")
    assert data is None and err == "bad_shape"

    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: (None, "timeout"))
    data, err = fred.fetch_observations("DGS10")
    assert data is None and err == "timeout"
