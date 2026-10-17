"""Shared fixtures for the tests/browser/ e2e suite.

One demo-data server per test session, reused by every page's tests, rather
than each file booting (and ~1s-waiting-on) its own copy. The boot sequence
mirrors services/wallet_ui/tests/test_wallet_flow_browser.py's `server`
fixture exactly: materialize synthetic data, open the read APIs, close every
write, and scrub every live-looking env var out of the child rather than
trust that none is set (this worktree carries no .env.local, but the check
costs one dict comprehension and the next worktree that runs this suite might
not be as clean).

Deliberately NOT api/index.py: that entrypoint refuses to start while a
.env.local exists on disk anywhere under the repo root, which is correct for
a real deployment and simply wrong for a test running in a developer's own
checkout that happens to have one in a sibling worktree.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")
from playwright.sync_api import Browser, sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN_ENV = (
    "HYPERLIQUID_PRIVATE_KEY", "PRIVATE_KEY_HEX",
    "HYPERLIQUID_ACCOUNT_ADDRESS", "HL_ACCOUNT_ADDRESS",
    "PATHIEL_OPERATOR_TOKEN",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def demo_server() -> Iterator[str]:
    """Base URL of a pathiel.server:app instance on synthetic data.

    PATHIEL_PUBLIC_DASHBOARD=1: every page under test is a logged-out,
    anonymous visit (the common case, and the one this sweep found broken
    the most), so the house-account panels render instead of 401ing the
    tests out of checking them. PATHIEL_DASHBOARD_READONLY=1: no test here
    may mutate anything, and this is cheap insurance that one accidentally
    doesn't.
    """
    sys.path.insert(0, str(ROOT))
    from services.demo.generator import materialize

    data_dir = tempfile.mkdtemp(prefix="pathiel-browser-demo-")
    home = tempfile.mkdtemp(prefix="pathiel-browser-home-")

    env = {k: v for k, v in os.environ.items() if k not in _FORBIDDEN_ENV}
    env.update(materialize(data_dir))
    env["HOME"] = home
    env["PATHIEL_PUBLIC_DASHBOARD"] = "1"
    env["PATHIEL_DASHBOARD_READONLY"] = "1"

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import uvicorn; from pathiel.server import app;"
         f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.fail("dashboard did not come up")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    """One Chromium instance per tests/browser/ test module.

    Playwright's sync API supports exactly one `sync_playwright()` context per
    process/thread — a second one raises "using Playwright Sync API inside
    the asyncio loop" the moment it is entered, which is what happened both
    when each file in THIS directory tried to open its own, and — the reason
    this is module-scoped rather than session-scoped — when a session-wide
    instance here was still open by the time pytest moved on to
    services/wallet_ui/tests/test_wallet_flow_browser.py (that file owns an
    identical fixture, independently, for the same reason: it predates this
    directory and runs standalone too). Module scope tears this one down
    before collection leaves `tests/browser/`, so the two never overlap in
    the same process — the cost is one extra Chromium launch for this
    directory's second test module, versus a hard collision whenever both
    directories' browser suites run together in one pytest invocation.
    Every test file in this directory takes `browser` as a fixture rather
    than managing its own instance.
    """
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()
