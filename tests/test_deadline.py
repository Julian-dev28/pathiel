"""No network recorder may wedge the trading loop.

2026-09-07: the loop stopped for 8 minutes with three live positions. Cause was
social_trending_recorder's CoinGecko fetch, which already passed timeout=15.0 to
urllib. That is a per-socket-operation timeout, not a total deadline — every
recv resets it, so a dribbling server holds the call open forever. It neither
returned nor raised.

Exits run EARLY in the cycle and recorders run LATE, so a recorder that never
returns does not break the exit that already happened. It prevents every exit
after it. Stops become unreachable on live positions because a social API is
slow. These tests pin the fix at both layers.
"""
from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from pathia.agents.deadline import with_deadline

LOOP = Path(__file__).resolve().parents[1] / "scripts" / "trading_loop.py"
RECORDERS = ("_data_logger_maybe_log", "_unlock_maybe_record",
             "_social_trending_maybe_record")


def test_a_hanging_call_returns_the_default_not_the_hang():
    started = threading.Event()

    def hang():
        started.set()
        time.sleep(30)
        return "never"

    t0 = time.time()
    out = with_deadline(hang, 0.3, "fallback", "test-hang")
    assert out == "fallback"
    assert time.time() - t0 < 5, "the deadline did not actually bound the wait"
    assert started.is_set()


def test_an_exception_also_yields_the_default():
    """The caller must not have to distinguish 'timed out' from 'raised' —
    either way the answer is 'no data this pass'."""
    def boom():
        raise RuntimeError("upstream is down")
    assert with_deadline(boom, 5, [], "test-raise") == []


def test_a_fast_call_is_untouched():
    assert with_deadline(lambda: 42, 5, 0, "test-fast") == 42


def test_the_worker_is_a_daemon_so_a_stall_cannot_hold_the_process_open():
    """A blocked syscall cannot be interrupted from outside, so the thread is
    abandoned. It MUST be a daemon or the abandoned thread keeps the process
    alive at shutdown."""
    names = []

    def slow():
        names.append(threading.current_thread().daemon)
        time.sleep(2)

    with_deadline(slow, 0.2, None, "test-daemon")
    time.sleep(0.1)
    assert names and names[0] is True


@pytest.mark.parametrize("recorder", RECORDERS)
def test_every_network_recorder_is_deadline_bounded_in_the_loop(recorder):
    """Structural: a future recorder added without a deadline reintroduces the
    exact wedge. Checked over the AST, not by grep, so a call nested inside the
    lambda still counts as bounded."""
    tree = ast.parse(LOOP.read_text())
    bounded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "_with_deadline":
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and getattr(inner.func, "id", None) == recorder):
                bounded = True
    assert bounded, (
        f"{recorder}() is not wrapped in _with_deadline. A stall in it stops "
        f"every subsequent exit on live positions.")
