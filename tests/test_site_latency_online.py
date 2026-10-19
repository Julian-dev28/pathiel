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


def test_a_dead_server_is_reported_not_crashed_on():
    assert _eval().run("http://127.0.0.1:9", rounds=1, browser=False) == 2
