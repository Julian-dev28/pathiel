"""Unit tests for services/intel/sources/eia.py. No live EIA_API_KEY was
available in this sandbox (confirmed live 2026-10-23: both endpoints
return the documented API_KEY_MISSING error with no key) — the
success-path fixtures here are built from EIA v2's documented
`/data/` response schema, same caveat as policy.py's congress.gov
fixture and this module's own docstring."""
from __future__ import annotations

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import eia


def _oil_spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary")])


def _metals_spec() -> DeskSpec:
    return DeskSpec(id="metals", name="Metals", tagline="t", instruments=[
        Instrument(coin="xyz:GOLD", label="Gold", role="primary")])


def _gas_spec() -> DeskSpec:
    """Mirrors services/strategies/desks/gas.py — the dedicated nat-gas
    desk added on overhaul-2026-10-17 alongside this branch."""
    return DeskSpec(id="gas", name="Nat Gas", tagline="t", instruments=[
        Instrument(coin="xyz:NATGAS", label="Nat Gas", role="primary")])


def _levels_response(values):
    """values newest-first -> the documented EIA v2 {"response": {"data": [...]}}
    shape, one row per week."""
    periods = [f"2026-{10 - i // 4:02d}-{(10 - (i % 4) * 7) % 28 + 1:02d}" for i in range(len(values))]
    return {"response": {"data": [{"period": p, "value": str(v)} for p, v in zip(periods, values)]}}


# ── missing key ──────────────────────────────────────────────────────

def test_crude_stocks_missing_key_is_reported_not_silent(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    items, err = eia.fetch_crude_stocks(_oil_spec())
    assert items == []
    assert err == "eia:crude_stocks:EIA_API_KEY_missing"


def test_natgas_storage_missing_key_is_reported_not_silent(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    items, err = eia.fetch_natgas_storage(_oil_spec())
    assert items == []
    assert err == "eia:natgas_storage:EIA_API_KEY_missing"


# ── desk gating ──────────────────────────────────────────────────────

def test_crude_stocks_skips_non_oil_desk_cleanly(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    items, err = eia.fetch_crude_stocks(_metals_spec())
    assert items == [] and err is None


def test_crude_stocks_skips_the_gas_desk_too(monkeypatch):
    """Crude stocks are not this desk's edge — only the natural-gas
    storage number is (see fetch_natgas_storage below)."""
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    items, err = eia.fetch_crude_stocks(_gas_spec())
    assert items == [] and err is None


def test_natgas_storage_fires_for_the_dedicated_gas_desk(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    levels = [3000, 3050, 3090, 3120, 3140, 3155]
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: (_levels_response(levels), None))
    items, err = eia.fetch_natgas_storage(_gas_spec())
    assert err is None and len(items) == 1
    assert items[0].unit == "bcf"


# ── success path (documented schema, not a live capture — see docstring) ──

def test_crude_stocks_computes_weekly_change_and_surprise(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    # Levels (thousand bbl), newest-first: latest draw of 10,000 vs a
    # trailing 4-week average draw of 2,500.
    levels = [420_000, 430_000, 431_000, 432_500, 434_000, 435_500]
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: (_levels_response(levels), None))
    items, err = eia.fetch_crude_stocks(_oil_spec())
    assert err is None and len(items) == 1
    dp = items[0]
    assert dp.source == "eia"
    assert dp.label == "EIA crude stocks weekly change"
    assert dp.value == -10.0          # (420000-430000)/1000
    assert dp.prior == -1.0           # (430000-431000)/1000
    avg4 = (-1.0 + -1.5 + -1.5 + -1.5) / 4
    assert abs(dp.change - (-10.0 - avg4)) < 1e-9
    assert "surprise" in dp.note


def test_natgas_storage_unit_is_bcf_not_scaled(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    levels = [3000, 3050, 3090, 3120, 3140, 3155]
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: (_levels_response(levels), None))
    items, err = eia.fetch_natgas_storage(_oil_spec())
    assert err is None and len(items) == 1
    assert items[0].unit == "bcf"
    assert items[0].value == -50.0  # 3000 - 3050, no scaling


def test_crude_stocks_http_failure_propagates(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: (None, "timeout"))
    items, err = eia.fetch_crude_stocks(_oil_spec())
    assert items == [] and err == "eia:crude_stocks:timeout"


def test_crude_stocks_insufficient_history_is_reported(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json",
                        lambda url, **kw: (_levels_response([420_000]), None))
    items, err = eia.fetch_crude_stocks(_oil_spec())
    assert items == [] and err == "eia:crude_stocks:insufficient_history"


def test_crude_stocks_bad_shape_is_reported(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "fake-key")
    monkeypatch.setattr(http_client, "fetch_json", lambda url, **kw: ({"nope": True}, None))
    items, err = eia.fetch_crude_stocks(_oil_spec())
    assert items == [] and err == "eia:crude_stocks:bad_shape"
