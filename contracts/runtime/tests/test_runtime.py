"""contracts/runtime: the resolution table every other unit relies on."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import contracts.runtime as rt

ADDR = "0x" + "ab" * 20


@pytest.mark.parametrize("value,kind", [
    (None, "operator"), ("", "operator"), ("public", "public"), ("PUBLIC", "public"),
    ("  Public \n", "public"), ("operator", "operator"), ("publicx", "operator"),
    ("1", "operator"), ("true", "operator"),
])
def test_install_kind_table(value, kind):
    env = {} if value is None else {rt.ENV_INSTALL: value}
    assert rt.install_kind(env) == kind
    assert rt.is_public(env) is (kind == "public")


@pytest.mark.parametrize("install,net,want", [
    (None, None, "mainnet"), (None, "testnet", "testnet"), (None, "mainnet", "mainnet"),
    (None, "bogus", "mainnet"), (None, " TestNet ", "testnet"), (None, "", "mainnet"),
    ("public", None, "testnet"), ("public", "mainnet", "mainnet"), ("public", "MAINNET", "mainnet"),
    ("public", "testnet", "testnet"), ("public", "bogus", "testnet"), ("public", "", "testnet"),
])
def test_network_table(install, net, want):
    env = {}
    if install is not None:
        env[rt.ENV_INSTALL] = install
    if net is not None:
        env[rt.ENV_NETWORK] = net
    assert rt.network(env) == want
    assert rt.is_mainnet(env) is (want == "mainnet")


@pytest.mark.parametrize("net,explicit", [
    (None, False), ("", False), ("bogus", False), ("testnet", True), ("Mainnet", True),
])
def test_network_is_explicit(net, explicit):
    env = {} if net is None else {rt.ENV_NETWORK: net}
    assert rt.network_is_explicit(env) is explicit
    assert rt.network_is_explicit({rt.ENV_INSTALL: "public", **env}) is explicit


def test_api_and_info_url():
    assert rt.MAINNET_API_URL == "https://api.hyperliquid.xyz"
    assert rt.TESTNET_API_URL == "https://api.hyperliquid-testnet.xyz"
    assert rt.api_url({}) == rt.MAINNET_API_URL
    assert rt.info_url({}) == "https://api.hyperliquid.xyz/info"
    pub = {rt.ENV_INSTALL: "public"}
    assert rt.api_url(pub) == rt.TESTNET_API_URL
    assert rt.info_url(pub) == "https://api.hyperliquid-testnet.xyz/info"
    assert rt.api_url({**pub, rt.ENV_NETWORK: "mainnet"}) == rt.MAINNET_API_URL


def test_defaults_read_os_environ_at_call_time(monkeypatch):
    monkeypatch.delenv(rt.ENV_INSTALL, raising=False)
    monkeypatch.delenv(rt.ENV_NETWORK, raising=False)
    assert rt.install_kind() == "operator" and rt.network() == "mainnet"
    monkeypatch.setenv(rt.ENV_INSTALL, "public")
    assert rt.install_kind() == "public" and rt.network() == "testnet"


def test_builder_address_validation(monkeypatch):
    assert rt.DEFAULT_BUILDER_ADDRESS == ""          # Julien fills it in; empty ships
    assert rt.builder_address({}) == ""
    assert rt.builder_address({rt.ENV_BUILDER_ADDRESS: ADDR}) == ADDR
    assert rt.builder_address({rt.ENV_BUILDER_ADDRESS: ADDR.upper().replace("0X", "0x")}) == ADDR
    assert rt.builder_address({rt.ENV_BUILDER_ADDRESS: f"  {ADDR}\n"}) == ADDR
    for bad in ("0x123", ADDR[2:], "0x" + "zz" * 20, ADDR + "00", "nope"):
        assert rt.builder_address({rt.ENV_BUILDER_ADDRESS: bad}) == "", bad
    monkeypatch.setattr(rt, "DEFAULT_BUILDER_ADDRESS", ADDR.upper().replace("0X", "0x"))
    assert rt.builder_address({}) == ADDR                    # default is lowercased too
    assert rt.builder_address({rt.ENV_BUILDER_ADDRESS: ""}) == ADDR   # empty override = default
    monkeypatch.setattr(rt, "DEFAULT_BUILDER_ADDRESS", "not an address")
    assert rt.builder_address({}) == ""


@pytest.mark.parametrize("raw,want", [
    (None, 10), ("", 10), ("0", 0), ("101", 0), ("abc", 0), ("25", 25), ("100", 100),
    ("-5", 0), ("2.5", 0), ("1_0", 0), (" 25 ", 25), ("+5", 0),
])
def test_builder_fee_bounds(raw, want):
    env = {} if raw is None else {rt.ENV_BUILDER_FEE: raw}
    got = rt.builder_fee_tenths_bp(env)
    assert got == want and type(got) is int


def test_default_fee_is_one_bp_well_under_the_cap():
    assert rt.DEFAULT_BUILDER_FEE_TENTHS_BP == 10
    assert rt.MAX_PERP_BUILDER_FEE_TENTHS_BP == 100
    assert rt.DEFAULT_BUILDER_FEE_TENTHS_BP * 10 <= rt.MAX_PERP_BUILDER_FEE_TENTHS_BP


@pytest.mark.parametrize("fee,s", [
    (0, "0%"), (1, "0.001%"), (5, "0.005%"), (10, "0.01%"), (25, "0.025%"), (50, "0.05%"),
    (100, "0.1%"), (12, "0.012%"),
])
def test_fee_rate_str_table(fee, s):
    assert rt.fee_rate_str(fee) == s


def test_describe_shape():
    d = rt.describe({rt.ENV_INSTALL: "public", rt.ENV_BUILDER_ADDRESS: ADDR})
    assert d == {"install": "public", "network": "testnet", "network_explicit": False,
                 "api_url": rt.TESTNET_API_URL,
                 "builder": {"address": ADDR, "fee_tenths_bp": 10, "fee_rate": "0.01%"}}
    assert rt.describe({})["install"] == "operator"
    assert rt.CONTRACT_VERSION == "1.0.0"


def test_config_point_banner_is_in_place():
    src = Path(rt.__file__).read_text()
    assert src.count("BUILDER CONFIG POINT") == 1
    assert src.count("DEFAULT_BUILDER_ADDRESS =") == 1
    banner = src.split("BUILDER CONFIG POINT", 1)[1].split('DEFAULT_BUILDER_ADDRESS = ""', 1)
    assert len(banner) == 2, "the empty default sits right under the banner"
    text = banner[0]
    for must in ("Julien fills this in", "publicly", "100 USDC", "Empty means no builder"):
        assert must in text, must


def test_module_imports_only_stdlib():
    tree = ast.parse(Path(rt.__file__).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    names.discard("__future__")
    stdlib = set(sys.stdlib_module_names)
    assert names and names <= stdlib, names - stdlib
