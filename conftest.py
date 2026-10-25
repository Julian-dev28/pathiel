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
# Local mode now also requires the Host header to name this machine (the
# DNS-rebinding check in services/auth/integration.py). Starlette's TestClient
# sends Host: testserver, so the test session lists it as a local name; the
# rebinding tests clear this to exercise the production default.
os.environ.setdefault("PATHIEL_LOCAL_HOSTNAMES", "testserver")
os.environ.setdefault("PATHIEL_DASHBOARD_LIVE_EQUITY", "0")
os.environ.setdefault("PATHIEL_DESK_BACKGROUND_REFRESH", "0")
os.environ.setdefault("PATHIEL_DESK_WARMUP", "0")
os.environ.setdefault("PATHIEL_META_PREWARM", "0")
os.environ.setdefault("PATHIEL_GRADE_LOOP", "0")


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


# ── Gate tests are offline ──────────────────────────────────────────────────
#
# scripts/hooks/pre-commit says the gate suite makes no network calls. Until
# 2026-10-19 nothing enforced it: 19 tests reached api.hyperliquid.xyz or
# news.google.com through calls they had not stubbed. One spent 24.7 s in retry
# backoff, one 18 s, and some passed or failed on what Google or the live
# market said that day. Now a lookup of any host that is not loopback is
# refused at once and recorded, and the running test errors at teardown naming
# the host and the code that asked. Tests marked online, live or browser are
# the lanes that talk to the world; they keep the network.

import socket      # noqa: E402
import threading   # noqa: E402
import traceback   # noqa: E402

_ROOT = str(pathlib.Path(__file__).parent)
_NETWORK_LANES = ("online", "live", "browser")
_LOOPBACK = {"localhost", "::1", "0.0.0.0", "testserver"}
_net_open = threading.Event()      # set while a test in a network lane runs
_net_attempts: list = []
_net_lock = threading.Lock()
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


def _is_loopback(host: str) -> bool:
    return host in _LOOPBACK or host.startswith("127.") or host.endswith(".localhost")


def _refuse(host: str) -> None:
    frames = [f"{os.path.relpath(f.filename, _ROOT)}:{f.lineno} {f.name}"
              for f in traceback.extract_stack()
              if f.filename.startswith(_ROOT) and "/.venv/" not in f.filename
              and f.name not in ("_refuse", "_offline_getaddrinfo", "_offline_connect")]
    with _net_lock:
        _net_attempts.append((host, threading.current_thread().name, frames[-6:]))
    raise socket.gaierror(socket.EAI_NONAME, f"gate tests are offline (conftest.py): {host}")


def _offline_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else str(host or "")
    if name and not _is_loopback(name) and not _net_open.is_set():
        _refuse(name)
    return _real_getaddrinfo(host, *args, **kwargs)


def _offline_connect(self, address, *args, **kwargs):
    if (isinstance(address, tuple) and address and not _is_loopback(str(address[0]))
            and not _net_open.is_set()):
        _refuse(str(address[0]))
    return _real_connect(self, address, *args, **kwargs)


# Process-wide, so a daemon thread a test started is covered too.
socket.getaddrinfo = _offline_getaddrinfo
socket.socket.connect = _offline_connect


def _leak_report(attempts) -> str:
    lines = ["this gate test reached for the network; stub the call, or mark the "
             "test online if talking to the venue is the point:"]
    for host, thread, frames in attempts[:5]:
        lines.append(f"  {host} (thread {thread}) via {' <- '.join(reversed(frames)) or '?'}")
    if len(attempts) > 5:
        lines.append(f"  ... {len(attempts) - 5} more")
    return "\n".join(lines)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # Before this test's fixtures, module-scoped ones included, are set up.
    if any(item.get_closest_marker(m) for m in _NETWORK_LANES):
        _net_open.set()
    else:
        _net_open.clear()
    with _net_lock:
        _net_attempts.clear()


@pytest.fixture(autouse=True)
def _offline():
    yield
    with _net_lock:
        attempts = list(_net_attempts)
        _net_attempts.clear()
    if attempts:
        pytest.fail(_leak_report(attempts), pytrace=False)


@pytest.fixture
def offline_attempts():
    """The refused lookups so far in this test, for tests of the guard itself.
    Clear it before returning if the test reached out on purpose."""
    return _net_attempts
