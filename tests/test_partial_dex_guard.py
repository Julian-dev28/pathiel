"""Idle-capital partial-dex read protection (2026-07-17).

The incident: one degraded /info?perpDexs response (empty list) got cached
for 24h, so equity aggregation dropped every HIP-3 dex. xyz held $8.47 of
idle USDC with NO open position, so the held-dex guard never fired — the
dashboard read main-only equity and faked a -$6.40 day. Three layers fixed:
the cache never persists an empty dex list (and serves stale over empty),
missing_material_dexes flags any funded dex absent from a read, and the
dashboard equity KPI includes spot so it matches HL's own Account Equity.
"""
import json

from hermes_trader.client.hl_client import missing_material_dexes
from hermes_trader.client import universe as uni


# ── missing_material_dexes ───────────────────────────────────────────────────

def test_funded_flat_dex_missing_is_flagged():
    last = {"": 11.95, "xyz": 8.47}
    assert missing_material_dexes(last, {""}) == {"xyz"}


def test_dust_dex_missing_is_ignored():
    last = {"": 11.95, "km": 0.03}          # dust below the $0.50 floor
    assert missing_material_dexes(last, {""}) == set()


def test_full_read_flags_nothing():
    last = {"": 11.95, "xyz": 8.47}
    assert missing_material_dexes(last, {"", "xyz"}) == set()


def test_emptied_dex_is_not_flagged_after_it_reads_zero():
    # operator moves all USDC off xyz; the NEXT successful read stores 0.0,
    # after which its absence must not block anything
    assert missing_material_dexes({"": 12.0, "xyz": 0.0}, {""}) == set()


def test_no_history_flags_nothing():
    assert missing_material_dexes(None, {""}) == set()
    assert missing_material_dexes({}, set()) == set()


# ── list_hip3_dexes cache poisoning ──────────────────────────────────────────

def _prime(monkeypatch, tmp_path, cached, response):
    monkeypatch.setattr(uni, "_PERP_DEXS_CACHE_PATH", tmp_path / "perp_dexs.json")
    if cached is not None:
        (tmp_path / "perp_dexs.json").write_text(json.dumps(cached))
    monkeypatch.setattr(uni, "_http_post", lambda *a, **kw: response)


def test_empty_perpdexs_response_is_never_cached(monkeypatch, tmp_path):
    _prime(monkeypatch, tmp_path, None, [])          # degraded: empty response
    assert uni.list_hip3_dexes(force_refresh=True) == []
    assert not (tmp_path / "perp_dexs.json").exists(), \
        "degraded empty response must not be written to the cache"


def test_empty_response_serves_stale_snapshot(monkeypatch, tmp_path):
    _prime(monkeypatch, tmp_path, ["xyz", "km"], [])  # good cache, bad response
    # cache mtime is fresh, but force_refresh skips it — the stale-serve path
    # must still return the snapshot rather than an empty list
    assert uni.list_hip3_dexes(force_refresh=True) == ["xyz", "km"]


def test_empty_cached_list_is_not_trusted(monkeypatch, tmp_path):
    # the exact poisoned state found live on 2026-07-17: cache file == []
    _prime(monkeypatch, tmp_path, [],
           [None, {"name": "xyz"}, {"name": "km"}])
    assert uni.list_hip3_dexes() == ["xyz", "km"]     # refetches, ignores []
    assert json.loads((tmp_path / "perp_dexs.json").read_text()) == ["xyz", "km"]


def test_good_response_still_caches(monkeypatch, tmp_path):
    _prime(monkeypatch, tmp_path, None, [None, {"name": "xyz"}])
    assert uni.list_hip3_dexes(force_refresh=True) == ["xyz"]
    assert json.loads((tmp_path / "perp_dexs.json").read_text()) == ["xyz"]
