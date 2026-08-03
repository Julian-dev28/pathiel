"""Gate tests for scripts/scheduler.py — the only scheduler that runs on this
box (cron and launchd are TCC-dead, see the module docstring).

Offline and deterministic: every job is an injected `runner`, no subprocess and
no clock beyond an explicit `now`. What is covered:

  schedule math   catch-up after the lid was shut, fixed-grid intervals
  concurrency     a slow job cannot starve a cheap one, and cannot stack
  bookkeeping     `last_run` is claimed at START, outcomes never clobber
                  each other, and a failing job never ends the pass
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hermes_scheduler",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "scheduler.py"))
sched = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sched)


@pytest.fixture(autouse=True)
def _clean_runtable():
    with sched._RUN_LOCK:
        sched._RUNNING.clear()
    yield
    with sched._RUN_LOCK:
        sched._RUNNING.clear()


def _jobs(**over):
    base = {
        "fast": {"args": ["true"], "interval_min": 30, "log": "logs/x.log",
                 "why": "cheap cache refresh"},
        "slow": {"args": ["true"], "interval_min": 240, "log": "logs/y.log",
                 "why": "hour-long LLM lane"},
    }
    for k, v in over.items():
        base[k].update(v)
    return base


def _state_path(tmp_path):
    return str(tmp_path / "scheduler.json")


# ── the bug this was written for ─────────────────────────────────────────────


def test_a_slow_job_does_not_starve_a_cheap_one(tmp_path):
    """Measured 2026-08-03: `poly-board` ran 3612s and `trends-price` — 55s of
    work on a 30-minute cadence — could not fire for 11 HOURS, so /trends served
    an 11h-old read with nothing on the page saying so."""
    release = threading.Event()
    ran = []

    def runner(args, log):
        ran.append(args)
        if args == ["slow"]:
            release.wait(5.0)                 # the hour-long job
        return 0

    jobs = _jobs(slow={"args": ["slow"]}, fast={"args": ["fast"]})
    sched.tick(now=1000.0, jobs=jobs, runner=runner, state_path=_state_path(tmp_path),
               printer=lambda s: None)
    for _ in range(100):                      # the cheap job finishes regardless
        if ["fast"] in ran:
            break
        time.sleep(0.02)
    assert ["fast"] in ran, "the cheap job waited on the slow one"
    release.set()


def test_a_job_still_running_is_skipped_not_stacked(tmp_path):
    release = threading.Event()
    starts = []

    def runner(args, log):
        starts.append(time.time())
        release.wait(5.0)
        return 0

    jobs = {"slow": _jobs()["slow"]}
    jobs["slow"]["interval_min"] = 1
    p = _state_path(tmp_path)
    sched.tick(now=1000.0, jobs=jobs, runner=runner, state_path=p, printer=lambda s: None)
    time.sleep(0.05)
    msgs = []
    sched.tick(now=1000.0 + 3600, jobs=jobs, runner=runner, state_path=p,
               printer=msgs.append)
    assert len(starts) == 1, "a slow job was started twice"
    assert any("still running" in m for m in msgs)
    release.set()


def test_concurrency_is_capped_so_a_catch_up_burst_cannot_flood_the_api(tmp_path):
    """This Mac sleeps. On wake every job is due at once, and three parallel HL
    scans is how the rate budget gets burned in one tick."""
    release = threading.Event()
    live = []
    jobs = {f"j{i}": {"args": [f"j{i}"], "interval_min": 30, "log": "logs/x.log",
                      "why": "scan"} for i in range(6)}

    def runner(args, log):
        live.append(args[0])
        release.wait(5.0)
        return 0

    msgs = []
    sched.tick(now=1000.0, jobs=jobs, runner=runner, state_path=_state_path(tmp_path),
               printer=msgs.append)
    time.sleep(0.1)
    assert len(live) <= sched.MAX_CONCURRENT_JOBS
    assert any("already" in m and "running" in m for m in msgs)
    release.set()


def test_last_run_is_claimed_at_start_so_cadence_does_not_drift(tmp_path):
    """Stamping on completion makes a 70-minute job on a 60-minute cadence fire
    the instant it lands, forever. The slot is claimed when it starts."""
    p = _state_path(tmp_path)
    release = threading.Event()
    jobs = {"slow": {"args": ["slow"], "interval_min": 60, "log": "logs/y.log",
                     "why": "long"}}
    sched.tick(now=1000.0, jobs=jobs, runner=lambda a, l: release.wait(5.0) or 0,
               state_path=p, printer=lambda s: None)
    time.sleep(0.05)
    assert json.load(open(p))["slow"]["last_run"] == 1000.0
    release.set()


def test_an_outcome_never_clobbers_another_jobs_entry(tmp_path):
    p = _state_path(tmp_path)
    jobs = _jobs(fast={"args": ["fast"]}, slow={"args": ["slow"], "interval_min": 30})
    sched.tick(now=1000.0, jobs=jobs, runner=lambda a, l: 0, state_path=p,
               printer=lambda s: None, join=True)
    st = json.load(open(p))
    assert set(st) == {"fast", "slow"}
    assert all(st[n]["last_rc"] == 0 and st[n]["last_run"] == 1000.0 for n in st)


def test_a_failing_job_is_recorded_and_the_pass_continues(tmp_path):
    p = _state_path(tmp_path)
    jobs = _jobs()
    msgs = []

    def runner(args, log):
        return 1 if args == jobs["slow"]["args"] and log.endswith("y.log") else 0

    sched.tick(now=1000.0, jobs=jobs, runner=runner, state_path=p,
               printer=msgs.append, join=True)
    st = json.load(open(p))
    assert st["slow"]["last_rc"] == 1 and st["fast"]["last_rc"] == 0
    assert any("FAILED" in m for m in msgs)


# ── catch-up after the lid was shut ──────────────────────────────────────────


def test_a_job_due_while_the_machine_slept_fires_on_wake():
    job = {"interval_min": 60, "why": "x"}
    slept_through = time.time() - 5 * 3600
    assert sched.is_due(job, slept_through, time.time()) is True


def test_a_job_that_just_ran_is_not_due():
    job = {"interval_min": 60, "why": "x"}
    now = time.time()
    assert sched.is_due(job, now - 60, now) is False


def test_the_running_table_reports_what_is_in_flight(tmp_path):
    release = threading.Event()
    sched.tick(now=1000.0, jobs={"slow": {"args": ["s"], "interval_min": 30,
                                          "log": "logs/y.log", "why": "long"}},
               runner=lambda a, l: release.wait(5.0) or 0,
               state_path=_state_path(tmp_path), printer=lambda s: None)
    time.sleep(0.05)
    assert sched.running_jobs() == ["slow"]
    release.set()
    time.sleep(0.1)
    assert sched.running_jobs() == []
