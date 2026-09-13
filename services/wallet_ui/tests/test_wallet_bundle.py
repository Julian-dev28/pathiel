"""Gate tests for the vendored wallet bundle.

`pathiel/static/wallet.js` is a compiled artifact committed to the repository,
which is normally a thing this project refuses to do. It is committed because
the deploy has no Node step — `api/index.py` is a Python function on Vercel, and
adding a second toolchain to that pipeline to produce one static file buys
nothing.

The cost of that decision is drift: someone edits services/wallet_ui/src, does
not rebuild, and ships a dashboard whose wallet button is a version nobody has
seen. These tests are what makes the decision safe. The freshness check
recomputes the hash of every source file and fails if it does not match the
manifest written at build time, so a stale bundle breaks the gate suite instead
of breaking sign-in in production.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WALLET_UI = ROOT / "services" / "wallet_ui"
STATIC = ROOT / "pathiel" / "static"
MANIFEST = WALLET_UI / "bundle.manifest.json"


def test_bundle_is_committed():
    assert (STATIC / "wallet.js").is_file(), (
        "pathiel/static/wallet.js is missing — run `npm run build` in "
        "services/wallet_ui and commit the result"
    )
    assert (STATIC / "wallet.css").is_file()


def test_manifest_is_committed():
    assert MANIFEST.is_file()
    recorded = json.loads(MANIFEST.read_text())
    assert recorded["sourceHash"]
    assert set(recorded["artifacts"]) == {"wallet.js", "wallet.css"}


def _toolchain_missing() -> str:
    """Why the staleness check cannot run here, or "" if it can.

    Two separate things are needed and only one of them used to be checked.
    node being on PATH says nothing about esbuild being installed, so on CI —
    which has node and had never run `npm ci` — the test ran, failed with
    ERR_MODULE_NOT_FOUND, and reported a stale bundle when the bundle was fine.
    """
    if shutil.which("node") is None:
        return "node is not installed"
    if not (WALLET_UI / "node_modules" / "esbuild").is_dir():
        return "esbuild is not installed (run `npm ci` in services/wallet_ui)"
    return ""


def test_bundle_is_not_stale():
    """The whole reason a build artifact is allowed in the tree.

    `build.mjs --check` hashes every file under src/ plus package.json and
    build.mjs, and compares that to the hash recorded when the bundle was
    written. Editing a source without rebuilding fails here.

    Skipped where the toolchain is absent — a fresh clone before `npm ci` —
    but NEVER on CI. A guard on a committed artifact that quietly skips itself
    is not a guard: a failed install would let a stale bundle through green.
    """
    missing = _toolchain_missing()
    if missing:
        if os.environ.get("CI"):
            pytest.fail(
                f"the wallet-bundle staleness check cannot run on CI: {missing}. "
                f"This guard is the only thing keeping a stale pathiel/static/"
                f"wallet.js out of a deploy, so it must not be skipped here — "
                f"fix the install step rather than the skip."
            )
        pytest.skip(missing)
    result = subprocess.run(
        ["node", "build.mjs", "--check"],
        cwd=WALLET_UI, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bundle_does_not_embed_a_walletconnect_project_id():
    """No relay, so no project ID — and nothing that looks like a leaked one.

    A WalletConnect project ID is a 32-character hex string. This deployment
    uses browser-extension wallets only (services/wallet_ui/src/App.tsx), so
    finding one here means a relay-backed connector came back in.
    """
    import re
    text = (STATIC / "wallet.js").read_text(errors="ignore")
    leaked = re.findall(r'projectId["\']?\s*[:=]\s*["\']([0-9a-f]{32})["\']', text)
    assert not leaked, f"a WalletConnect project ID is baked into the bundle: {leaked}"


def test_templates_carry_the_mount_point():
    """Every page with a masthead needs somewhere for the island to mount."""
    templates = sorted((ROOT / "pathiel" / "templates").glob("*.html"))
    assert templates, "no templates found"
    for path in templates:
        markup = path.read_text()
        if 'class="masthead-right"' not in markup:
            continue
        assert 'id="wallet-connect-root"' in markup, (
            f"{path.name} has a masthead but no wallet mount point"
        )


def test_bundle_is_not_loaded_on_page_load():
    """It is ~380 KB over the wire against ~30 KB for the dashboard's own JS.

    Loading it from a <script> tag in the templates would put that on every
    page view, including the signed-in operator who never opens the picker. It
    must only ever be injected by pathiel.js on demand.
    """
    for path in (ROOT / "pathiel" / "templates").glob("*.html"):
        markup = path.read_text()
        assert "/static/wallet.js" not in markup, (
            f"{path.name} loads wallet.js eagerly; it is injected on demand by "
            f"pathiel.js instead"
        )


def test_loader_resets_its_latch_on_failure():
    """A failed fetch must not wedge the button forever.

    The promise is cached so two clicks do not inject two script tags. If the
    cache is kept after an error, every later click resolves against the failed
    promise and the picker can never open again without a reload.
    """
    source = (STATIC / "pathiel.js").read_text()
    start = source.index("function loadWalletUI()")
    end = source.index("async function signIn()")
    loader = source[start:end]
    assert "onerror" in loader
    assert "walletLoading = null" in loader, (
        "loadWalletUI caches its promise but never clears it on error"
    )
