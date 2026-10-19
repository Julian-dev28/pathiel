"""Pytest bootstrap — load .env.local before the test session starts.

pathiel.client.exchange reads Hyperliquid credentials at import time, so
the environment must be populated before any test imports the package. This is
a no-op when .env.local is absent (e.g. CI without secrets).
"""
import os
import pathlib

_ENV_FILE = pathlib.Path(__file__).parent / ".env.local"
# Wallet private keys are loaded ONLY for the real-money e2e run
# (PATHIEL_E2E=1, `pytest -m live`). Gate and online tests are offline or
# read-only and never need them; with them loaded, one failing assertion that
# printed a credentials object leaked a real agent key into test output
# (2026-10-17). Addresses and other settings still load.
_LOAD_PRIVATE_KEYS = os.environ.get("PATHIEL_E2E") == "1"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            if "PRIVATE_KEY" in _k.upper() and not _LOAD_PRIVATE_KEYS:
                # Pin it EMPTY rather than skipping it: server.py, the MCP
                # server, services/*/env.py and scripts/_state_env.py all load
                # .env.local with os.environ.setdefault at import time, and an
                # existing empty value is what keeps them from re-adding it.
                os.environ[_k] = ""
                continue
            os.environ.setdefault(_k, _v.strip())

# The dashboard reads live equity when the loop heartbeat is stale; gate tests
# must not reach Hyperliquid. Tests that cover the live path enable it with a mock.
os.environ.setdefault("PATHIEL_DASHBOARD_LIVE_EQUITY", "0")
os.environ.setdefault("PATHIEL_DESK_BACKGROUND_REFRESH", "0")
os.environ.setdefault("PATHIEL_DESK_WARMUP", "0")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_account_snapshot():
    """pathiel.client.account_snapshot shares one account read per address
    across the whole process (that is its job in the server); one test's
    fake account must never be served to the next test."""
    from pathiel.client import account_snapshot
    account_snapshot.reset()
    yield
    account_snapshot.reset()
