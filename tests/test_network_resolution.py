"""Every venue call follows the runtime network (contracts/runtime).

hl_client.HL_API is resolved once at import, so the import-time cases run in
a subprocess with a pinned, offline environment: a temp HOME, an existing
empty env file, empty key values and dead proxies. In-process cases
monkeypatch hl_client.HL_API and PATHIEL_NETWORK together, the way a launcher
that exported PATHIEL_NETWORK before Python started would leave them.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from eth_account import Account as EthAccount

import contracts.runtime as rt
from pathiel.client import exchange as ex
from pathiel.client import hl_client
from pathiel.client import universe as uni

ROOT = Path(__file__).resolve().parents[1]
DEAD_PROXY = "http://127.0.0.1:9"


def _pinned_env(tmp_path: Path, **extra: str) -> dict:
    """A subprocess env that cannot read the checkout's .env.local, sign, or
    reach the venue."""
    env_file = tmp_path / "env.local"
    env_file.write_text("")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATHIEL_ENV_FILE": str(env_file),
        "PATHIEL_UNIVERSE_CACHE_DIR": str(tmp_path / "universe"),
        "HYPERLIQUID_PRIVATE_KEY": "",
        "HYPERLIQUID_MASTER_PRIVATE_KEY": "",
        "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY,
        "http_proxy": DEAD_PROXY, "https_proxy": DEAD_PROXY,
        "NO_PROXY": "", "no_proxy": "",
    }
    env.update(extra)
    return env


_PROBE = ("import pathiel.client.hl_client as h, pathiel.client.universe as u; "
          "print(h.HL_API); print(u._CACHE_DIR)")


def _probe(tmp_path, **extra):
    return subprocess.run([sys.executable, "-c", _PROBE], cwd=str(tmp_path),
                          env=_pinned_env(tmp_path, **extra), capture_output=True, text=True,
                          timeout=60)


@pytest.mark.parametrize("install,net,want,state", [
    (None, None, rt.MAINNET_API_URL, None),
    (None, "mainnet", rt.MAINNET_API_URL, None),
    (None, "testnet", rt.TESTNET_API_URL, "state-testnet"),
    ("public", None, rt.MAINNET_API_URL, None),         # 2026-11-12: mainnet by default
    ("public", "mainnet", rt.MAINNET_API_URL, None),
    ("public", "testnet", rt.TESTNET_API_URL, None),
    ("public", "bogus", rt.MAINNET_API_URL, None),      # Python: invalid = unset; the launcher
                                                        # never passes one (it exports testnet)
])
def test_hl_api_follows_env_at_import(install, net, want, state, tmp_path):
    extra = {}
    if install:
        extra["PATHIEL_INSTALL"] = install
    if net:
        extra["PATHIEL_NETWORK"] = net
    if state:
        extra["PATHIEL_STATE_DIR"] = str(tmp_path / state)
    r = _probe(tmp_path, **extra)
    assert r.returncode == 0, r.stderr[-2000:]
    api, cache = r.stdout.split()
    assert api == want
    base = tmp_path / "universe"
    assert Path(cache) == (base / "testnet" if want == rt.TESTNET_API_URL else base)


def test_operator_testnet_refuses_unset_state_dir(tmp_path):
    r = _probe(tmp_path, PATHIEL_NETWORK="testnet")
    assert r.returncode != 0
    assert "testnet from a maintainer checkout needs its own state dir" in r.stderr


def test_operator_testnet_refuses_a_non_testnet_state_dir(tmp_path):
    # Not under tmp_path: pytest names that folder after this test, which says
    # "testnet". The guard only reads the string, so the path need not exist.
    live = "/nonexistent/pathiel/live-state"
    r = _probe(tmp_path, PATHIEL_NETWORK="testnet", PATHIEL_STATE_DIR=live)
    assert r.returncode != 0
    assert "PATHIEL_STATE_DIR" in r.stderr


def test_operator_testnet_accepts_a_testnet_state_dir(tmp_path):
    r = _probe(tmp_path, PATHIEL_NETWORK="TESTNET",
               PATHIEL_STATE_DIR=str(tmp_path / "pathiel-TestNet-state"))
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.split()[0] == rt.TESTNET_API_URL


def test_public_testnet_needs_no_state_dir_name(tmp_path):
    # a public install keeps state under ~/.pathiel already split by the launcher
    r = _probe(tmp_path, PATHIEL_INSTALL="public", PATHIEL_STATE_DIR=str(tmp_path / "s"))
    assert r.returncode == 0, r.stderr[-2000:]


# ── in process ────────────────────────────────────────────────────────────────

@pytest.fixture
def testnet(monkeypatch):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    monkeypatch.setattr(hl_client, "HL_API", rt.TESTNET_API_URL)


class _Capture:
    def __init__(self):
        self.kw = {}

    def factory(self):
        cap = self

        class Fake:
            def __init__(self, *a, **kw):
                cap.kw = dict(kw)
                if a:
                    cap.kw["_args"] = a
                self.session = None
        return Fake


def _build_clients(monkeypatch):
    info, exch, init = _Capture(), _Capture(), _Capture()
    monkeypatch.setattr(ex, "Info", info.factory())
    monkeypatch.setattr(ex, "Exchange", exch.factory())
    monkeypatch.setattr(ex, "_info_instance", None)
    monkeypatch.setattr(ex, "_exchange_instance", None)
    monkeypatch.setattr(ex, "_exchange_identity_key", None)
    monkeypatch.setattr(ex, "_resolve_perp_dexs", lambda: None)
    key = "0x" + EthAccount.create().key.hex()
    monkeypatch.setattr(ex, "_resolve_identity", lambda: (key, "0x" + "a1" * 20, None, False))
    import hyperliquid.info
    monkeypatch.setattr(hyperliquid.info, "Info", init.factory())
    monkeypatch.setattr(hl_client, "_info_instance", None)
    monkeypatch.setattr(hl_client, "_fetch_meta_sync", lambda: ({}, {}))
    ex._get_info()
    ex._make_exchange()
    hl_client.init_info()
    return info.kw, exch.kw, init.kw


def test_info_and_exchange_get_the_resolved_base_url(monkeypatch, testnet):
    info, exch, init = _build_clients(monkeypatch)
    assert info["base_url"] == rt.TESTNET_API_URL
    assert exch["base_url"] == rt.TESTNET_API_URL
    assert init["base_url"] == rt.TESTNET_API_URL


def test_operator_clients_stay_on_mainnet(monkeypatch):
    assert hl_client.HL_API == rt.MAINNET_API_URL
    info, exch, init = _build_clients(monkeypatch)
    assert info["base_url"] == exch["base_url"] == init["base_url"] == rt.MAINNET_API_URL


def _registered(n=278):
    names = [f"t{i}" for i in range(n - 2)] + ["xyz", "km"]
    return names


@pytest.fixture
def cfg(monkeypatch):
    import copy

    from pathiel.agents import config_store
    state = {"cfg": copy.deepcopy(config_store.DEFAULT_CONFIG)}
    monkeypatch.setattr(config_store, "read_agent_config", lambda: copy.deepcopy(state["cfg"]))
    monkeypatch.setattr(uni, "registered_hip3_dexes", lambda force_refresh=False: _registered())
    return state


def test_testnet_dex_filter_and_mainnet_passthrough(cfg, monkeypatch):
    assert len(uni.list_hip3_dexes()) == 278, "mainnet: every registered dex, unchanged"
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")
    assert cfg["cfg"]["hip3_dex_allowlist"] == ["xyz"]
    assert uni.list_hip3_dexes() == ["xyz"]
    cfg["cfg"]["hip3_dex_allowlist"] = []
    assert uni.list_hip3_dexes() == [], "empty allowlist on testnet: no dex"
    cfg["cfg"]["hip3_dex_allowlist"] = ["xyz", "km", "nope", "xyz"]
    cfg["cfg"]["hip3_dex_blocklist"] = ["km"]
    assert uni.list_hip3_dexes() == ["xyz"]
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_agent_config", lambda: 1 / 0)
    assert uni.list_hip3_dexes() == [], "unreadable config never widens testnet"


def test_registered_list_is_todays_list_hip3_dexes(monkeypatch, tmp_path):
    monkeypatch.setattr(uni, "_PERP_DEXS_CACHE_PATH", tmp_path / "perp_dexs.json")
    monkeypatch.setattr(uni, "_http_post", lambda path, payload: [None, {"name": "xyz"},
                                                                   {"name": "km"}])
    assert uni.registered_hip3_dexes(force_refresh=True) == ["xyz", "km"]
    assert uni.list_hip3_dexes() == ["xyz", "km"]


@pytest.mark.parametrize("reply,registry,registered", [
    ([None], [], []),                                   # read OK, no HIP-3 dex here
    (None, None, []),                                   # read failed, nothing cached
    ([None, {"name": "xyz"}], ["xyz"], ["xyz"]),
])
def test_hip3_dex_registry_tells_empty_from_failed(monkeypatch, tmp_path, reply, registry,
                                                   registered):
    """Final review: perpDexs=[null] (a network with no HIP-3 dex) and a
    failed read both gave [], so coin_listing said 'unknown' and the public
    gate let xyz:CL through without the network note."""
    monkeypatch.setattr(uni, "_PERP_DEXS_CACHE_PATH", tmp_path / "perp_dexs.json")
    monkeypatch.setattr(uni, "_http_post", lambda path, payload: reply)
    assert uni.hip3_dex_registry(force_refresh=True) == registry
    assert uni.registered_hip3_dexes(force_refresh=True) == registered, "operator answer unchanged"


def test_coin_listing_on_a_network_without_hip3_dexes(monkeypatch, tmp_path):
    monkeypatch.setattr(uni, "_PERP_DEXS_CACHE_PATH", tmp_path / "perp_dexs.json")
    monkeypatch.setattr(uni, "_http_post", lambda path, payload: [None])
    monkeypatch.setattr(ex, "_cached_universe", lambda dex=None, force_refresh=False: [{"name": "BTC"}])
    assert ex.coin_listing("xyz:CL") == "not_listed"
    assert ex.coin_listing("BTC") == "listed"
    monkeypatch.setattr(uni, "_http_post", lambda path, payload: None)
    assert ex.coin_listing("xyz:CL") == "unknown"


def test_universe_cache_dir_scoped_by_network(tmp_path):
    assert uni._cache_dir_for(tmp_path, "mainnet") == tmp_path
    assert uni._cache_dir_for(tmp_path, "testnet") == tmp_path / "testnet"
    # the gate's own cache dir is the mainnet one, exactly the configured path
    assert uni._CACHE_DIR == Path(os.environ["PATHIEL_UNIVERSE_CACHE_DIR"])


def test_public_exchange_refuses_after_network_drift(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")      # HL_API froze to mainnet
    assert hl_client.HL_API == rt.MAINNET_API_URL
    called = []
    monkeypatch.setattr(ex, "_resolve_identity", lambda: called.append(1) or ("k", "a", None, False))
    with pytest.raises(RuntimeError, match="network changed after start"):
        ex._make_exchange()
    assert called == [], "refused before any key is touched"
    # the order functions turn it into a plain error, no SDK call
    monkeypatch.setattr(ex, "_has_usable_credential", lambda: True)
    monkeypatch.setattr(ex, "get_coin_index", lambda c: (0, 2, 0))
    monkeypatch.setattr(ex, "_ioc_cross_price", lambda coin, is_buy, mid: mid)
    out = ex.place_hl_order(True, 1.0, 100.0, "BTC")
    assert out["ok"] is False and "network changed" in out["error"]
    # consistent public mainnet: no refusal from the guard
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    _info, exch, _init = _build_clients(monkeypatch)
    assert exch["base_url"] == rt.MAINNET_API_URL


def test_operator_has_no_drift_guard(monkeypatch):
    monkeypatch.setenv("PATHIEL_NETWORK", "testnet")       # operator: never refused by it
    _info, exch, _init = _build_clients(monkeypatch)
    assert exch["base_url"] == rt.MAINNET_API_URL


@pytest.fixture
def listing(monkeypatch):
    state = {"registered": ["xyz", "km"], "main": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}],
             "xyz": [{"name": "xyz:CL"}, {"name": "xyz:BRENTOIL", "isDelisted": True}],
             "fail": False}

    def _uni(dex=None, force_refresh=False):
        if state["fail"]:
            raise RuntimeError("meta down")
        return state["main"] if dex is None else state.get(dex, [])

    monkeypatch.setattr(ex, "_cached_universe", _uni)
    monkeypatch.setattr(uni, "hip3_dex_registry", lambda force_refresh=False: state["registered"])
    return state


def test_coin_listing_states(listing):
    assert ex.coin_listing("BTC") == "listed"
    assert ex.coin_listing("OLD") == "delisted"
    assert ex.coin_listing("NOPE") == "not_listed"
    assert ex.coin_listing("xyz:CL") == "listed"
    assert ex.coin_listing("xyz:BRENTOIL") == "delisted"
    assert ex.coin_listing("xyz:GOLD") == "not_listed"
    assert ex.coin_listing("zzz:ANY") == "not_listed", "dex not registered on this network"
    listing["registered"] = None
    assert ex.coin_listing("xyz:CL") == "unknown", "dex list unreadable"
    listing["registered"] = []
    assert ex.coin_listing("xyz:CL") == "not_listed", "read fine, no HIP-3 dex on this network"
    listing["registered"] = ["xyz"]
    listing["fail"] = True
    assert ex.coin_listing("BTC") == "unknown"
    assert ex.coin_listing("xyz:CL") == "unknown"
    listing["fail"] = False
    listing["main"] = []
    assert ex.coin_listing("BTC") == "unknown"
