"""Gate for scripts/supply_chain_check.py (security review 2026-10-25,
docs/security/SUPPLY_CHAIN_2026-10-25.md).

Two halves. The real repo passes every offline check. And each check fails
on a planted copy of the exact mistake it exists for, so a check that
silently stopped looking (a renamed key, a regex that matches nothing) turns
this file red instead of staying green forever.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("supply_chain_check", ROOT / "scripts" / "supply_chain_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


@pytest.mark.parametrize("check", sc.OFFLINE_CHECKS, ids=lambda c: c.__name__)
def test_the_repo_passes(check):
    assert check() == []


# ── each check catches its own failure ──────────────────────────────────────

@pytest.fixture
def lock_copy(tmp_path, monkeypatch):
    dst = tmp_path / "package-lock.json"
    shutil.copy(sc.LOCK, dst)
    monkeypatch.setattr(sc, "LOCK", dst)
    return dst


def _edit_lock(path: Path, fn):
    data = json.loads(path.read_text())
    fn(data["packages"])
    path.write_text(json.dumps(data))


def test_a_lock_entry_without_integrity_is_caught(lock_copy):
    _edit_lock(lock_copy, lambda p: p["node_modules/viem"].pop("integrity"))
    assert any("viem: no sha512 integrity" in x for x in sc.check_lockfile_integrity())


def test_a_lock_entry_from_another_registry_is_caught(lock_copy):
    def bad(p):
        p["node_modules/react"]["resolved"] = "https://evil.example/react-18.3.1.tgz"
    _edit_lock(lock_copy, bad)
    assert any("resolved outside" in x for x in sc.check_lockfile_integrity())


def test_a_new_install_script_is_caught(lock_copy):
    _edit_lock(lock_copy, lambda p: p["node_modules/react"].update(hasInstallScript=True))
    assert any("node_modules/react" in x for x in sc.check_install_scripts())


def test_npmrc_without_ignore_scripts_is_caught(tmp_path, monkeypatch):
    rc = tmp_path / ".npmrc"
    rc.write_text("save-exact=true\n# ignore-scripts=true\n")
    monkeypatch.setattr(sc, "NPMRC", rc)
    assert any("ignore-scripts" in x for x in sc.check_install_scripts())


def test_a_caret_range_is_caught(tmp_path, monkeypatch):
    pj = json.loads(sc.PACKAGE_JSON.read_text())
    pj["dependencies"]["viem"] = "^2.56.3"
    dst = tmp_path / "package.json"
    dst.write_text(json.dumps(pj))
    monkeypatch.setattr(sc, "PACKAGE_JSON", dst)
    assert any("viem" in x and "range" in x for x in sc.check_package_json_exact())


_BAD_WORKFLOW = """name: x
on: [push]
jobs:
  t:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: pip install requests
"""


def test_workflow_mistakes_are_caught(tmp_path, monkeypatch):
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "bad.yml").write_text(_BAD_WORKFLOW)
    monkeypatch.setattr(sc, "WORKFLOWS", wf_dir)
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    found = "\n".join(sc.check_workflows())
    assert "not pinned to a commit SHA: actions/checkout@v4" in found
    assert "npm install without --ignore-scripts" in found
    assert "pip install outside the hashed lock" in found
    assert "no top-level permissions block" in found
    assert "grants a write permission" in found


def test_a_hand_edited_bundle_is_caught(tmp_path, monkeypatch):
    static = tmp_path / "static"
    shutil.copytree(sc.STATIC, static, ignore=shutil.ignore_patterns("*.png", "*.ico"))
    with open(static / "wallet.js", "a") as f:
        f.write(";window.x=1;")
    monkeypatch.setattr(sc, "STATIC", static)
    assert any("wallet.js: sha256" in x for x in sc.check_bundle_manifest())


def test_a_bundled_package_the_lock_does_not_pin_is_caught(tmp_path, monkeypatch):
    m = json.loads(sc.MANIFEST.read_text())
    m["bundledPackages"].append("axios@1.16.0")
    dst = tmp_path / "bundle.manifest.json"
    dst.write_text(json.dumps(m))
    monkeypatch.setattr(sc, "MANIFEST", dst)
    # axios@1.16.0 IS in the lock (a non-shipping transitive dep); a version it
    # does not pin must fail.
    m["bundledPackages"][-1] = "axios@9.9.9"
    dst.write_text(json.dumps(m))
    assert any("axios@9.9.9" in x for x in sc.check_bundle_manifest())


def test_a_drainer_indicator_in_the_bundle_is_caught(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "wallet.js").write_text("var a=1;window.stealthProxyControl={};")
    (static / "wallet.css").write_text("a{}")
    monkeypatch.setattr(sc, "STATIC", static)
    assert any("stealthProxyControl" in x for x in sc.check_bundle_iocs())


def test_a_registry_token_in_npmrc_is_caught(tmp_path, monkeypatch):
    rc = tmp_path / ".npmrc"
    rc.write_text("ignore-scripts=true\n//registry.npmjs.org/:_authToken=npm_x\n")
    monkeypatch.setattr(sc, "NPMRC", rc)
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    assert sc.check_no_registry_tokens()


def test_a_python_pin_without_hashes_is_caught(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("fastapi==0.141.1\n    # via pathiel\n")
    monkeypatch.setattr(sc, "PY_LOCK", lock)
    assert any("fastapi" in x for x in sc.check_python_lock_hashes())


def test_known_worm_file_names_match():
    for name in ("setup_bun.js", "pkg/bun_environment.js", ".github/workflows/shai-hulud-workflow.yml",
                 ".github/workflows/formatter_123.yml", "truffleSecrets.json"):
        assert sc.WORM_FILENAMES.search(name), name
    assert not sc.WORM_FILENAMES.search("services/wallet_ui/build.mjs")


def test_python_pins_parse_the_hashed_lock():
    pins = sc.python_pins()
    assert pins["fastapi"] and pins["urllib3"] and pins["certifi"]
    # The two upgrades this review made, held so a regeneration cannot
    # quietly walk them back.
    assert tuple(int(x) for x in pins["urllib3"].split(".")[:2]) >= (2, 8)
    assert pins["certifi"] >= "2026.10.19"
