"""Periodic eval: scripts/site_latency_eval.py against the running dashboard
server (PATHIEL_EVAL_BASE_URL, default http://localhost:8000). Every page and
API request answers in under a second with a dashboard tab polling, and the
Desks click shows content in under 1.5 s. Read-only. Skips when no server
answers.

    .venv/bin/python -m pytest -q -m online tests/test_site_latency_online.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _eval():
    spec = importlib.util.spec_from_file_location("site_latency_eval", ROOT / "scripts" / "site_latency_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.online
def test_the_site_answers_fast_with_a_dashboard_tab_open(tmp_path):
    ev = _eval()
    base = os.environ.get("PATHIEL_EVAL_BASE_URL", "http://localhost:8000").rstrip("/")
    if ev._req(base, "/api/health", timeout=5)[0] != 200:
        pytest.skip(f"no dashboard server at {base}")
    report = tmp_path / "report.json"
    assert ev.run(base, rounds=4, browser=True, json_path=str(report)) == 0, report.read_text()


# ── the eval's own arithmetic: offline, in the gate ──────────────────────────

def test_p95_and_budget_reading():
    ev = _eval()
    assert ev._p95([]) == 0.0
    assert ev._p95([0.1] * 9 + [9.0]) == 9.0            # 10 samples: the 10th is the 95th pct
    assert ev._p95([0.1] * 18 + [9.0] * 2) == 9.0       # 20 samples: 2 slow ones reach it
    assert ev._p95([0.1] * 19 + [9.0]) == 0.1           # 20 samples: 1 slow one does not
    assert ev._refused_foreground({"refused_requests": {"foreground": {"a": 2, "b": 1},
                                                        "background": {"a": 50}}}) == 3
    assert ev._refused_foreground({}) == 0


def _fake_site(threaded: bool):
    """A stand-in server: order books take 0.1 s, health answers at once.
    Threaded, it serves health while books wait (the fixed server.py); not
    threaded, health queues behind every book (one event loop, blocked)."""
    import http.server
    import threading
    import time

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/hl/orderbook"):
                time.sleep(0.1)
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    cls = http.server.ThreadingHTTPServer if threaded else http.server.HTTPServer
    srv = cls(("127.0.0.1", 0), Handler)
    srv.request_queue_size = 32
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_the_venue_read_check_tells_a_blocked_loop_from_a_free_one():
    ev = _eval()
    free, free_url = _fake_site(threaded=True)
    blocked, blocked_url = _fake_site(threaded=False)
    try:
        assert ev._loop_check(free_url)[0] < ev.LOOP_MAX_S
        assert ev._loop_check(blocked_url)[0] > 0.4          # behind ~8 x 0.1 s of books
    finally:
        free.shutdown()
        blocked.shutdown()


def test_a_dead_server_is_reported_not_crashed_on():
    assert _eval().run("http://127.0.0.1:9", rounds=1, browser=False) == 2
