"""Gate tests for the listing metadata: the official MCP registry (server.json),
Glama (glama.json) and the Claude Code marketplace
(.claude-plugin/).

Nothing here submits anything. Each check is a rule a registry or a user
relies on: one version everywhere, mainnet as the default network with
testnet as the opt-in (0.4.0), the fee, the default and the opt-in in the
manifest descriptions, discovery keywords, and no key or secret declared in
any listing (keys live only in the user's own env file).

There is no smithery.yaml: Smithery shut down its hosted build and connection
services on 2026-10-31 (https://smithery.ai/future) and is a directory only.
test_no_smithery_yaml keeps a dead build file from coming back.

Offline, no network.

    .venv/bin/python -m pytest -q tests/test_listing_metadata.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVER_JSON = ROOT / "server.json"
SMITHERY_YAML = ROOT / "smithery.yaml"   # must not exist
GLAMA_JSON = ROOT / "glama.json"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKET_JSON = ROOT / ".claude-plugin" / "marketplace.json"

SERVER_SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
GLAMA_SCHEMA_URL = "https://glama.ai/mcp/schemas/server.json"
# The registry schema's name pattern: reverse-DNS namespace, one slash, server name.
SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
REPO_URL = "https://github.com/Julian-dev28/pathiel"
PUBLIC_DEFAULT_NETWORK = "mainnet"
NETWORKS = ["mainnet", "testnet"]
# Discovery: the terms a user types into a plugin or MCP directory search.
DISCOVERY_KEYWORDS = {"hyperliquid", "trading", "perps", "mcp", "claude-code", "risk-management", "ai-agent", "trading-agent", "ai-trading",
                      "oil", "gold"}
MARKET_CATEGORY = "finance"
IMAGE = "ghcr.io/julian-dev28/pathiel-mcp"

# Anything named like a credential. Listing files declare none: a registry or
# a hosted runner would then hold the user's key, which is what this project
# promises never happens.
SECRET_NAME_RE = re.compile(r"(key|secret|token|password|passphrase|private|mnemonic|seed)",
                            re.IGNORECASE)

# The key sets at 2816df7 (0.3.0). Claude Code validates these manifests; a new
# key is a format change, not a description edit.
PLUGIN_KEYS = {"name", "displayName", "version", "description", "author", "homepage",
               "repository", "license", "keywords", "mcpServers"}
MARKET_KEYS = {"name", "description", "owner", "plugins"}
MARKET_ENTRY_KEYS = {"name", "source", "description", "category", "keywords"}


def _json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def package_version() -> str:
    """pathiel.__version__ read from the source, without importing the package
    (its import patches ssl defaults for the whole process)."""
    src = (ROOT / "pathiel" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', src, re.MULTILINE)
    assert m, "pathiel/__init__.py has no __version__ line"
    return m.group(1)


def version_sources() -> List[Tuple[str, str]]:
    return [("pathiel/__init__.py", package_version()),
            (".claude-plugin/plugin.json", _json(PLUGIN_JSON).get("version", "")),
            ("server.json", _json(SERVER_JSON).get("version", ""))]


def _walk(node: Any, path: str = "") -> Iterator[Tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def declared_secrets(server: Optional[Dict[str, Any]] = None,
                     glama: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every place a listing file marks something secret or names a key-shaped
    input: isSecret true anywhere, an environment variable, argument, variable
    or config property whose name looks like a credential, Defaults to the files on disk."""
    hits: List[str] = []
    server = _json(SERVER_JSON) if server is None else server
    glama = _json(GLAMA_JSON) if glama is None else glama
    for path, node in _walk(server):
        if not isinstance(node, dict):
            continue
        if node.get("isSecret") is True:
            hits.append(f"server.json{path}: isSecret true")
        name = node.get("name")
        if isinstance(name, str) and SECRET_NAME_RE.search(name):
            hits.append(f"server.json{path}: name {name!r}")
        for var in (node.get("variables") or {}):
            if SECRET_NAME_RE.search(var):
                hits.append(f"server.json{path}.variables: {var!r}")
    for path, node in _walk(glama):
        if isinstance(node, dict) and node.get("isSecret") is True:
            hits.append(f"glama.json{path}: isSecret true")
    return hits


def oci_package() -> Dict[str, Any]:
    packages = _json(SERVER_JSON).get("packages") or []
    assert len(packages) == 1, f"server.json should list exactly one package, has {len(packages)}"
    return packages[0]


def server_json_rules() -> List[Tuple[str, bool]]:
    """Each registry rule server.json must meet, as (rule, ok). The eval counts them."""
    d = _json(SERVER_JSON)
    desc = d.get("description") or ""
    pkgs = d.get("packages") or []
    pkg = pkgs[0] if len(pkgs) == 1 else {}
    args = pkg.get("runtimeArguments") or []
    named = {a.get("name"): a for a in args if a.get("type") == "named"}
    vol = named.get("-v") or {}
    home_var = (vol.get("variables") or {}).get("pathiel_home") or {}
    envs = {e.get("name"): e for e in pkg.get("environmentVariables") or []}
    net = envs.get("PATHIEL_NETWORK") or {}
    return [
        ("$schema is the 2025-12-11 server schema", d.get("$schema") == SERVER_SCHEMA_URL),
        ("name matches the registry pattern", bool(SERVER_NAME_RE.match(d.get("name") or ""))),
        ("name is io.github.Julian-dev28/pathiel", d.get("name") == "io.github.Julian-dev28/pathiel"),
        ("title is pathiel", d.get("title") == "pathiel"),
        ("description is 1 to 100 chars", 1 <= len(desc) <= 100),
        ("description names mainnet and the testnet opt-in",
         "mainnet" in desc.lower() and "testnet opt-in" in desc.lower()),
        ("description names the exact stop", "exact stop" in desc.lower()),
        ("version is the package version", d.get("version") == package_version()),
        ("repository is the GitHub repo", d.get("repository") == {"url": REPO_URL, "source": "github"}),
        ("websiteUrl is the README public-install anchor",
         d.get("websiteUrl") == f"{REPO_URL}#before-you-trade"),
        ("exactly one package", len(pkgs) == 1),
        ("package is oci", pkg.get("registryType") == "oci"),
        ("package identifier is the image tagged with the version",
         pkg.get("identifier") == f"{IMAGE}:{package_version()}"),
        ("transport is stdio", pkg.get("transport") == {"type": "stdio"}),
        ("runtimeHint is docker", pkg.get("runtimeHint") == "docker"),
        ("runtime arguments carry -i and --rm", "-i" in named and "--rm" in named),
        ("volume argument mounts {pathiel_home} at /data", vol.get("value") == "{pathiel_home}:/data"),
        ("pathiel_home is a required filepath",
         home_var.get("format") == "filepath" and home_var.get("isRequired") is True),
        ("PATHIEL_NETWORK defaults to mainnet", net.get("default") == PUBLIC_DEFAULT_NETWORK),
        ("PATHIEL_NETWORK choices are mainnet and testnet", net.get("choices") == NETWORKS),
        ("PATHIEL_NETWORK is not secret", net.get("isSecret") is False),
        ("PATHIEL_NETWORK is the only environment variable", set(envs) == {"PATHIEL_NETWORK"}),
    ]


def network_defaults() -> List[Tuple[str, bool]]:
    """Each listing's network rule, as (rule, ok): the files this unit owns
    say mainnet by default and testnet opt-in."""
    pkg = oci_package()
    net = {e.get("name"): e for e in pkg.get("environmentVariables") or []}.get("PATHIEL_NETWORK") or {}
    plugin = _json(PLUGIN_JSON)["description"].lower()
    market = _json(MARKET_JSON)["plugins"][0]["description"].lower()
    return [
        ("server.json PATHIEL_NETWORK default is mainnet", net.get("default") == PUBLIC_DEFAULT_NETWORK),
        ("plugin.json description: mainnet by default, testnet opt-in",
         "mainnet by default" in plugin and "testnet opt-in" in plugin),
        ("marketplace.json description: mainnet by default, testnet opt-in",
         "mainnet by default" in market and "testnet opt-in" in market),
    ]


# ── tests ────────────────────────────────────────────────────────────────────

def test_versions_agree_everywhere():
    sources = version_sources()
    assert {v for _, v in sources} == {"0.4.1"}, sources
    assert oci_package()["identifier"].endswith(":" + package_version())


def test_server_json_required_fields_and_limits():
    d = _json(SERVER_JSON)
    for field in ("name", "description", "version"):
        assert d.get(field), f"server.json lacks required field {field!r}"
    failed = [rule for rule, ok in server_json_rules() if not ok]
    assert not failed, f"server.json breaks: {failed}"


def test_server_json_package_is_oci_stdio_and_tagged_with_the_version():
    pkg = oci_package()
    assert pkg["registryType"] == "oci"
    assert pkg["transport"] == {"type": "stdio"}
    assert pkg["identifier"] == f"{IMAGE}:{package_version()}"
    names = [a.get("name") for a in pkg["runtimeArguments"]]
    assert names[:2] == ["-i", "--rm"], names


def test_server_json_declares_no_secret():
    assert declared_secrets() == []


def test_secret_check_catches_planted_keys():
    """An empty list proves nothing if the check never looked. Plant a
    key-shaped env var with isSecret; it must be named."""
    server = _json(SERVER_JSON)
    server["packages"][0]["environmentVariables"].append(
        {"name": "HYPERLIQUID_PRIVATE_KEY", "isSecret": True})
    hits = declared_secrets(server=server)
    joined = "\n".join(hits)
    assert "isSecret true" in joined, hits
    assert "name 'HYPERLIQUID_PRIVATE_KEY'" in joined, hits


def test_network_defaults_in_every_listing_file():
    failed = [f for f, ok in network_defaults() if not ok]
    assert not failed, f"network rules broken: {failed}"


def test_no_smithery_yaml():
    """Smithery's hosted builds ended 2026-10-31; a smithery.yaml would
    describe a deploy flow that no longer exists."""
    assert not SMITHERY_YAML.exists()


def test_glama_json_shape():
    d = _json(GLAMA_JSON)
    assert d == {"$schema": GLAMA_SCHEMA_URL, "maintainers": ["Julian-dev28"]}


def test_plugin_and_marketplace_descriptions_disclose_fee_and_network():
    from contracts import runtime

    fee = runtime.fee_rate_str(runtime.DEFAULT_BUILDER_FEE_TENTHS_BP)
    for label, desc in (("plugin.json", _json(PLUGIN_JSON)["description"]),
                        ("marketplace.json", _json(MARKET_JSON)["plugins"][0]["description"])):
        low = desc.lower()
        assert "60 MCP tools" in desc, label
        assert "exact stop and target" in low, label
        assert "mainnet by default" in low and "testnet opt-in" in low, label
        assert "read-only without a key" in low, label
        if runtime.BUILDER_FEE_WAIVED:
            assert "free: no builder fee" in low, f"{label} does not say there is no fee"
            assert "%" not in desc.replace("1% per trade", ""), f"{label} names a fee rate"
        else:
            assert f"{fee} builder fee" in desc, f"{label} does not name the {fee} builder fee"
            assert "approve" in low, f"{label} does not say the fee needs approval"


def test_manifests_carry_discovery_keywords_and_category():
    plugin, market = _json(PLUGIN_JSON), _json(MARKET_JSON)
    entry = market["plugins"][0]
    assert DISCOVERY_KEYWORDS <= set(plugin["keywords"]), plugin["keywords"]
    assert plugin["keywords"] == entry["keywords"], "plugin.json and marketplace.json keywords drift"
    assert all(k == k.lower() and " " not in k for k in plugin["keywords"])
    assert entry["category"] == MARKET_CATEGORY
    assert plugin["description"] == entry["description"], "one description in both manifests"


def test_manifest_key_set_unchanged():
    plugin, market = _json(PLUGIN_JSON), _json(MARKET_JSON)
    assert set(plugin) == PLUGIN_KEYS
    assert set(market) == MARKET_KEYS
    assert len(market["plugins"]) == 1 and set(market["plugins"][0]) == MARKET_ENTRY_KEYS
    assert plugin["mcpServers"] == {"pathiel": {"command": "bash",
                                                "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp"]}}
    assert sorted(p.name for p in (ROOT / ".claude-plugin").iterdir()) == \
        ["icon.png", "marketplace.json", "plugin.json"]


def test_plugin_icon_meets_the_directory_rules():
    """The plugin directory takes a square PNG, 512 to 2048 px a side, under
    2 MB, and keeps only the first one it sees, so a bad file here sticks."""
    import struct

    data = (ROOT / ".claude-plugin" / "icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    assert w == h and 512 <= w <= 2048, (w, h)
    assert len(data) < 2 * 1024 * 1024


@pytest.mark.parametrize("path", [SERVER_JSON, GLAMA_JSON, PLUGIN_JSON, MARKET_JSON])
def test_listing_json_files_parse(path):
    assert isinstance(_json(path), dict)
