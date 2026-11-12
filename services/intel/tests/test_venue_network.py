"""services/intel/sources/venue.py reads the resolved network's /info URL
(contracts/runtime), not a hardcoded mainnet one.

HL_INFO_URL is read once at import, so each case reloads the venue module
under its env and reloads it again afterwards with the env cleared, which
leaves the mainnet constant in place for every later test. Only
services.intel.sources.venue is reloaded: pathiel.client.hl_client freezes
HL_API at import for the whole process and is never touched here.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

from services.intel import http_client
from services.intel.sources import venue

MAINNET = "https://api.hyperliquid.xyz/info"
TESTNET = "https://api.hyperliquid-testnet.xyz/info"
_RUNTIME_KEYS = ("PATHIEL_INSTALL", "PATHIEL_NETWORK")


@pytest.fixture
def reload_venue(monkeypatch):
    """reload_venue(**env) -> the venue module re-imported under env."""
    def _reload(**env):
        for key in _RUNTIME_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(venue)

    yield _reload
    for key in _RUNTIME_KEYS:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(venue)
    assert venue.HL_INFO_URL == MAINNET


@pytest.fixture
def posted(monkeypatch):
    """Every URL handed to http_client.fetch_json_post; nothing leaves the box."""
    urls = []

    def fake_post(url, body, **kwargs):
        urls.append(url)
        if body.get("type") == "metaAndAssetCtxs":
            return [{"universe": [{"name": "BRENTOIL"}]}, [{"funding": "0.0001"}]], None
        return [], None

    monkeypatch.setattr(http_client, "fetch_json_post", fake_post)
    return urls


def test_default_is_mainnet_info_url(reload_venue, posted):
    mod = reload_venue()
    assert mod.HL_INFO_URL == MAINNET
    ctxs, err = mod.fetch_meta_and_ctxs("xyz")
    assert err is None and ctxs == {"BRENTOIL": {"funding": "0.0001"}}
    assert posted == [MAINNET]


def test_testnet_env_selects_the_testnet_info_url(reload_venue, posted):
    mod = reload_venue(PATHIEL_NETWORK="testnet")
    assert mod.HL_INFO_URL == TESTNET
    mod.fetch_meta_and_ctxs(None)
    assert posted == [TESTNET]


def test_public_install_without_a_network_uses_testnet(reload_venue, posted):
    mod = reload_venue(PATHIEL_INSTALL="public")
    assert mod.HL_INFO_URL == TESTNET


def test_public_install_opted_into_mainnet_uses_mainnet(reload_venue, posted):
    mod = reload_venue(PATHIEL_INSTALL="public", PATHIEL_NETWORK="mainnet")
    assert mod.HL_INFO_URL == MAINNET


@pytest.mark.parametrize("network, want", [("mainnet", MAINNET), ("testnet", TESTNET)])
def test_both_venue_calls_use_the_same_constant(reload_venue, posted, network, want):
    mod = reload_venue(PATHIEL_NETWORK=network)
    mod.fetch_meta_and_ctxs("xyz")
    mod.fetch_funding_history("xyz:BRENTOIL", datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert posted == [want, want]
