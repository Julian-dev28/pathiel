"""Gate for scripts/supply_chain_check.py (security review 2026-10-25,
docs/security/SUPPLY_CHAIN_2026-10-25.md).

Two halves. The real repo passes every offline check. And each check fails
on a planted copy of the exact mistake it exists for, so a check that
silently stopped looking (a renamed key, a regex that matches nothing) turns
this file red instead of staying green forever.

2026-10-25, later: the Node half (package-lock integrity, install scripts,
exact pins, the wallet bundle's manifest and drainer indicators) left with
services/wallet_ui; see docs/WALLET_UI_RESTORE.md. What is left guards the
Python lock, the workflows and the repo itself.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("supply_chain_check", ROOT / "scripts" / "supply_chain_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


@pytest.mark.parametrize("check", sc.OFFLINE_CHECKS, ids=lambda c: c.__name__)
def test_the_repo_passes(check):
    assert check() == []


def test_there_is_no_node_left_to_check():
    assert not (ROOT / "services" / "wallet_ui").exists()
    assert not (ROOT / "pathiel" / "static" / "wallet.js").exists()
    assert not hasattr(sc, "LOCK") and not hasattr(sc, "MANIFEST")


# ── each check catches its own failure ──────────────────────────────────────

def test_workflow_mistakes_are_caught(tmp_path, monkeypatch):
    wf = tmp_path / "bad.yml"
    wf.write_text(
        "on: pull_request_target\n"
        "jobs:\n  t:\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: npm ci\n"
        "      - run: pip install requests\n"
    )
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    monkeypatch.setattr(sc, "WORKFLOWS", tmp_path)
    found = "\n".join(sc.check_workflows())
    assert "not pinned to a commit SHA" in found
    assert "npm install without --ignore-scripts" in found
    assert "pip install outside the hashed lock" in found
    assert "no top-level permissions block" in found
    assert "pull_request_target" in found


def test_a_registry_token_in_npmrc_is_caught(tmp_path, monkeypatch):
    rc = tmp_path / ".npmrc"
    rc.write_text("//registry.npmjs.org/:_authToken=npm_x\n")
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    assert any("registry credential" in p for p in sc.check_no_registry_tokens())


def test_a_python_pin_without_hashes_is_caught(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("requests==2.32.3\nhttpx==0.28.1\n    --hash=sha256:abc\n")
    monkeypatch.setattr(sc, "PY_LOCK", lock)
    found = sc.check_python_lock_hashes()
    assert any("requests==2.32.3 has no --hash" in p for p in found)
    assert not any("httpx" in p for p in found)


def test_known_worm_file_names_match():
    for name in ("setup_bun.js", "bun_environment.js", "shai-hulud-workflow.yml",
                 "x/truffleSecrets.json", ".github/workflows/formatter_123.yaml"):
        assert sc.WORM_FILENAMES.search(name), name
    assert not sc.WORM_FILENAMES.search("scripts/build_plugin_skill.py")


def test_python_pins_parse_the_hashed_lock():
    pins = sc.python_pins()
    assert pins, "requirements.lock parsed to no pins"
    assert "fastapi" in pins and "certifi" in pins


# ── release.yml: the one workflow allowed to write (2026-11-12) ─────────────

_RELEASE_OK = (
    "on:\n  release:\n    types: [published]\n  workflow_dispatch:\n"
    "permissions:\n  contents: read\n"
    "jobs:\n  image:\n    runs-on: ubuntu-latest\n    permissions:\n"
    "      contents: read\n      packages: write\n"
    "  registry:\n    runs-on: ubuntu-latest\n    permissions:\n"
    "      contents: read\n      id-token: write\n"
)


def _check_one(tmp_path, monkeypatch, name, text):
    (tmp_path / name).write_text(text)
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    monkeypatch.setattr(sc, "WORKFLOWS", tmp_path)
    return sc.check_workflows()


def test_release_workflow_may_write_packages_and_id_token(tmp_path, monkeypatch):
    assert _check_one(tmp_path, monkeypatch, "release.yml", _RELEASE_OK) == []


def test_the_same_writes_in_any_other_workflow_are_caught(tmp_path, monkeypatch):
    found = "\n".join(_check_one(tmp_path, monkeypatch, "ci.yml", _RELEASE_OK))
    assert "grants a write permission (packages)" in found
    assert "grants a write permission (id-token)" in found


@pytest.mark.parametrize("mutation,needle", [
    (("      packages: write\n", "      contents: write\n"), "grants a write permission (contents)"),
    (("permissions:\n  contents: read\n", "permissions:\n  packages: write\n"), "packages outside a job"),
    (("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n"), "runs on ['push']"),
    (("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n"), "runs on ['pull_request']"),
])
def test_release_workflow_cannot_widen_its_writes(tmp_path, monkeypatch, mutation, needle):
    text = _RELEASE_OK.replace(*mutation)
    assert text != _RELEASE_OK
    found = "\n".join(_check_one(tmp_path, monkeypatch, "release.yml", text))
    assert needle in found, found


def test_the_shipped_release_workflow_passes_and_pins_its_tools():
    wf = sc.WORKFLOWS / "release.yml"
    text = wf.read_text()
    assert [p for p in sc.check_workflows() if "release.yml" in p] == []
    assert re.search(r"MCP_PUBLISHER_SHA256: [0-9a-f]{64}\n", text)
    assert "sha256sum -c" in text
    assert "mcp-publisher login github-oidc" in text
    # 0.4.0 shipped amd64 + arm64; a release must not shrink :latest to one.
    assert "runner: ubuntu-24.04-arm" in text and "runner: ubuntu-24.04\n" in text
    assert "linux/amd64 linux/arm64" in text
