"""The gate lane is offline: conftest.py's guard refuses any host that is not
loopback and fails the test that asked.

2026-10-19: 19 gate tests reached api.hyperliquid.xyz or news.google.com
through calls they had not stubbed (one took 24.7 s in retry backoff, one 18 s,
and some passed or failed on what Google or the live market said that day),
while scripts/hooks/pre-commit promised a suite that "makes no network calls".
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_a_real_host_is_refused_at_once_and_recorded(offline_attempts):
    with pytest.raises(socket.gaierror, match="gate tests are offline"):
        socket.getaddrinfo("api.hyperliquid.xyz", 443)
    host, thread, frames = offline_attempts[-1]
    assert host == "api.hyperliquid.xyz" and thread == "MainThread"
    assert any("test_offline_gate.py" in f for f in frames)
    offline_attempts.clear()                      # reached out on purpose


def test_a_client_library_gets_the_refusal_not_a_timeout(offline_attempts):
    with pytest.raises(urllib.error.URLError, match="offline"):
        urllib.request.urlopen("https://news.google.com/rss", timeout=5)
    assert [a[0] for a in offline_attempts] == ["news.google.com"]
    offline_attempts.clear()


def test_a_raw_connect_to_an_address_is_refused(offline_attempts):
    with socket.socket() as s, pytest.raises(socket.gaierror):
        s.settimeout(2)                           # unguarded, this would hang, not pass
        s.connect(("192.0.2.1", 443))             # TEST-NET-1: never routed
    assert offline_attempts[-1][0] == "192.0.2.1"
    offline_attempts.clear()


def test_loopback_stays_open(offline_attempts):
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)
    assert offline_attempts == []


def test_a_test_that_reaches_out_fails_and_a_network_lane_does_not(tmp_path):
    """The whole mechanism, end to end: the root conftest in a fresh pytest
    process, one test leaking from the main thread, one from a background
    thread, one in the online lane, one on loopback, and a regime one test
    leaves behind that the next must not see."""
    shutil.copy(ROOT / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    online: network lane\n")
    (tmp_path / "test_leaky.py").write_text('''
import socket
import threading

import pytest


def _reach():
    try:
        socket.getaddrinfo("api.hyperliquid.xyz", 443)
    except OSError:
        pass                    # the code under test swallows it, as fetchers do


def test_reaches_out():
    _reach()


def test_reaches_out_from_a_thread():
    t = threading.Thread(target=_reach, name="bg-feed")
    t.start()
    t.join()


@pytest.mark.online
def test_the_online_lane_keeps_the_network():
    import conftest
    assert conftest._net_open.is_set()


def test_loopback():
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_a_regime_left_behind():
    import time
    from pathiel.agents import market_regime
    market_regime._regime_cache["BTC"] = ("up", time.time())


def test_the_next_test_starts_with_no_regime_and_no_market():
    from pathiel.agents import market_regime
    assert market_regime._regime_cache == {}
    assert market_regime.detect_regime("BTC") == "neutral"
''')
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "--rootdir", str(tmp_path), str(tmp_path)],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    text = out.stdout + out.stderr
    assert out.returncode == 1, text
    assert "6 passed, 2 errors" in text, text
    assert text.count("ERROR at teardown of test_reaches_out") == 2, text
    assert "api.hyperliquid.xyz (thread MainThread) via test_leaky.py" in text, text
    assert "api.hyperliquid.xyz (thread bg-feed)" in text, text
