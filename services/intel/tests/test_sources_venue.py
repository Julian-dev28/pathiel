"""Unit tests for services/intel/sources/venue.py against a real captured
Hyperliquid metaAndAssetCtxs(dex="xyz") response and a real fundingHistory
sample for xyz:BRENTOIL (both captured live 2026-10-23)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.strategies.models import DeskSpec, Instrument
from services.intel import http_client
from services.intel.sources import venue
from services.intel.tests.conftest import fixture_json

NOW = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)


def _oil_like_spec() -> DeskSpec:
    return DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary"),
        Instrument(coin="xyz:CL", label="WTI", role="secondary"),
    ])


def _fake_meta_and_ctxs(dex_filter=None):
    fixture = fixture_json("hl_meta_and_ctxs_xyz.json")

    def fake(url, json_body, **kw):
        assert json_body["type"] == "metaAndAssetCtxs"
        assert json_body.get("dex") == "xyz"
        return fixture, None
    return fake


# ── fetch_meta_and_ctxs ────────────────────────────────────────────────

def test_fetch_meta_and_ctxs_real_fixture_builds_coin_map(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json_post", _fake_meta_and_ctxs())
    ctxs, err = venue.fetch_meta_and_ctxs("xyz")
    assert err is None
    assert set(ctxs) == {"xyz:GOLD", "xyz:SILVER", "xyz:CL", "xyz:NATGAS", "xyz:BRENTOIL"}
    assert ctxs["xyz:BRENTOIL"]["markPx"] == "109.68"


def test_fetch_meta_and_ctxs_http_failure(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json_post", lambda *a, **k: (None, "timeout"))
    ctxs, err = venue.fetch_meta_and_ctxs("xyz")
    assert ctxs is None and "timeout" in err


def test_fetch_meta_and_ctxs_bad_shape(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json_post", lambda *a, **k: ({"not": "a list"}, None))
    ctxs, err = venue.fetch_meta_and_ctxs(None)
    assert ctxs is None and "bad_shape" in err


# ── fetch_funding_history ──────────────────────────────────────────────

def test_fetch_funding_history_real_fixture(monkeypatch):
    fixture = fixture_json("hl_funding_history_brentoil.json")

    def fake(url, json_body, **kw):
        assert json_body["type"] == "fundingHistory"
        assert json_body["coin"] == "xyz:BRENTOIL"
        return fixture, None
    monkeypatch.setattr(http_client, "fetch_json_post", fake)
    history, err = venue.fetch_funding_history("xyz:BRENTOIL", NOW)
    assert err is None
    assert len(history) == len(fixture)
    assert history[0]["coin"] == "xyz:BRENTOIL"


def test_fetch_funding_history_failure(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_json_post", lambda *a, **k: (None, "http_500"))
    history, err = venue.fetch_funding_history("xyz:BRENTOIL", NOW)
    assert history is None and "http_500" in err


# ── percentile / 24h-ago helpers (pure) ─────────────────────────────────

def test_funding_pctl_insufficient_history_is_honest_not_a_guess():
    note, crowded = venue._funding_pctl_note(0.0001, [{"fundingRate": "0.0001", "time": 1}])
    assert "insufficient" in note
    assert crowded is False


def test_funding_pctl_crowded_at_90th_percentile():
    # 150 samples (above MIN_FUNDING_SAMPLES_FOR_PCTL), funding_now strictly
    # above every one of them -> 100th percentile, definitely crowded.
    history = [{"fundingRate": str(i / 1_000_000.0), "time": i} for i in range(150)]
    note, crowded = venue._funding_pctl_note(1.0, history)
    assert crowded is True
    assert "CROWDED" in note
    assert "pct30d=1.00" in note


def test_funding_pctl_not_crowded_mid_distribution():
    history = [{"fundingRate": str(i / 1_000_000.0), "time": i} for i in range(150)]
    note, crowded = venue._funding_pctl_note(0.000075, history)  # ~50th percentile
    assert crowded is False
    assert "CROWDED" not in note


def test_funding_24h_ago_finds_closest_sample_within_window():
    now = NOW
    target_ms = (now - timedelta(hours=24)).timestamp() * 1000
    history = [
        {"fundingRate": "0.00001", "time": target_ms - 10 * 60 * 1000},  # 10 min off target
        {"fundingRate": "0.00009", "time": target_ms - 10 * 3600 * 1000},  # way off (34h ago)
    ]
    assert venue._funding_24h_ago(now, history) == 0.00001


def test_funding_24h_ago_none_when_nothing_in_window():
    history = [{"fundingRate": "0.00001", "time": NOW.timestamp() * 1000}]  # "now", not 24h ago
    assert venue._funding_24h_ago(NOW, history) is None


def test_oi_24h_ago_usd_within_window():
    now_s = NOW.timestamp()
    history = [
        {"ts": now_s - 25 * 3600, "oi_usd": 100.0},   # in [20h,30h) window
        {"ts": now_s - 1 * 3600, "oi_usd": 999.0},    # too recent
    ]
    assert venue._oi_24h_ago_usd(history, now_s) == 100.0


def test_oi_24h_ago_usd_none_outside_window():
    now_s = NOW.timestamp()
    history = [{"ts": now_s - 1 * 3600, "oi_usd": 100.0}]
    assert venue._oi_24h_ago_usd(history, now_s) is None


# ── fetch_venue_positioning end to end ──────────────────────────────────

def test_fetch_venue_positioning_first_run_has_no_oi_change_yet(tmp_path, monkeypatch):
    fixture = fixture_json("hl_meta_and_ctxs_xyz.json")
    funding_fixture = fixture_json("hl_funding_history_brentoil.json")

    def fake_post(url, json_body, **kw):
        if json_body["type"] == "metaAndAssetCtxs":
            return fixture, None
        return funding_fixture, None
    monkeypatch.setattr(http_client, "fetch_json_post", fake_post)

    items, errors = venue.fetch_venue_positioning(_oil_like_spec(), NOW, str(tmp_path))
    assert errors == []
    # 3 datapoints (funding, oi, volume) per instrument x 2 instruments
    assert len(items) == 6
    oi_points = [i for i in items if "open interest" in i.label]
    assert len(oi_points) == 2
    for dp in oi_points:
        assert dp.prior is None
        assert "insufficient" in dp.note


def test_fetch_venue_positioning_second_run_24h_later_computes_oi_change(tmp_path, monkeypatch):
    fixture = fixture_json("hl_meta_and_ctxs_xyz.json")
    funding_fixture = fixture_json("hl_funding_history_brentoil.json")

    def fake_post(url, json_body, **kw):
        if json_body["type"] == "metaAndAssetCtxs":
            return fixture, None
        return funding_fixture, None
    monkeypatch.setattr(http_client, "fetch_json_post", fake_post)

    spec = _oil_like_spec()
    venue.fetch_venue_positioning(spec, NOW, str(tmp_path))
    later = NOW + timedelta(hours=25)
    items, errors = venue.fetch_venue_positioning(spec, later, str(tmp_path))
    assert errors == []
    oi_points = [i for i in items if "open interest" in i.label]
    for dp in oi_points:
        assert dp.prior is not None
        assert dp.change == dp.value - dp.prior
        assert "chg_24h=" in dp.note


def test_fetch_venue_positioning_oi_crowding_flag(tmp_path, monkeypatch):
    fixture = fixture_json("hl_meta_and_ctxs_xyz.json")
    funding_fixture = fixture_json("hl_funding_history_brentoil.json")
    calls = {"n": 0}

    def fake_post(url, json_body, **kw):
        if json_body["type"] == "fundingHistory":
            return funding_fixture, None
        calls["n"] += 1
        fx = [dict(fixture[0]), [dict(c) for c in fixture[1]]]
        if calls["n"] == 2:  # second call: bump BRENTOIL OI by 50%
            idx = fx[0]["universe"].index(
                next(u for u in fx[0]["universe"] if u["name"] == "xyz:BRENTOIL"))
            fx[1][idx] = dict(fx[1][idx])
            fx[1][idx]["openInterest"] = str(float(fixture[1][idx]["openInterest"]) * 1.5)
        return fx, None
    monkeypatch.setattr(http_client, "fetch_json_post", fake_post)

    spec = DeskSpec(id="oil", name="Oil", tagline="t",
                     instruments=[Instrument(coin="xyz:BRENTOIL", label="Brent", role="primary")])
    venue.fetch_venue_positioning(spec, NOW, str(tmp_path))
    later = NOW + timedelta(hours=25)
    items, _errors = venue.fetch_venue_positioning(spec, later, str(tmp_path))
    oi_dp = next(i for i in items if "open interest" in i.label)
    assert oi_dp.change > 0
    assert "CROWDED" in oi_dp.note
    assert oi_dp.relevance == 0.8


def test_fetch_venue_positioning_instrument_missing_from_universe_is_reported(tmp_path, monkeypatch):
    fixture = fixture_json("hl_meta_and_ctxs_xyz.json")
    monkeypatch.setattr(http_client, "fetch_json_post",
                        lambda url, json_body, **kw: (fixture, None) if json_body["type"] == "metaAndAssetCtxs"
                        else ([], None))
    spec = DeskSpec(id="oil", name="Oil", tagline="t", instruments=[
        Instrument(coin="xyz:NOPE", label="Nonexistent", role="primary")])
    items, errors = venue.fetch_venue_positioning(spec, NOW, str(tmp_path))
    assert items == []
    assert any("not_in_universe" in e for e in errors)


def test_fetch_venue_positioning_main_dex_sends_no_dex_field(tmp_path, monkeypatch):
    seen = {}

    def fake_post(url, json_body, **kw):
        if json_body["type"] == "metaAndAssetCtxs":
            seen["dex_in_body"] = "dex" in json_body
            return [{"universe": [{"name": "BTC"}]},
                    [{"funding": "0.00001", "openInterest": "100", "markPx": "50000",
                      "dayNtlVlm": "1000000"}]], None
        return [], None
    monkeypatch.setattr(http_client, "fetch_json_post", fake_post)
    spec = DeskSpec(id="btc", name="Bitcoin", tagline="t",
                     instruments=[Instrument(coin="BTC", label="BTC", role="primary")])
    venue.fetch_venue_positioning(spec, NOW, str(tmp_path))
    assert seen["dex_in_body"] is False
