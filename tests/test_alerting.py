"""Alerts for the failures, not the happy path.

Every existing metric — equity, open positions, unrealized PnL — measures a
WORKING system. None of them move when it breaks, which is how a dead trading
loop, a blind market feed, and an unrotated disk each went unnoticed for weeks.

Each alert below corresponds to a silent failure this system has actually had,
and each has a metric behind it that is exported for real.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from pathia.server import app

ROOT = Path(__file__).resolve().parents[1]
RULE_FILE = ROOT / "k8s" / "prometheusrule.yaml"


@pytest.fixture(scope="module")
def rules():
    doc = yaml.safe_load(RULE_FILE.read_text())
    return {r["alert"]: r for g in doc["spec"]["groups"] for r in g["rules"]}


@pytest.fixture(scope="module")
def scrape():
    return TestClient(app).get("/metrics").text


def test_every_failure_mode_has_a_metric(scrape):
    """A dashboard number nobody scrapes cannot page anyone."""
    for metric in ("pathia_heartbeat_age_seconds", "pathia_feed_trustworthy",
                   "pathia_feed_gap_fraction", "pathia_can_trade",
                   "pathia_ai_brain_ready", "pathia_disk_free_bytes",
                   "pathia_log_dir_bytes", "pathia_max_drawdown_pct"):
        assert metric in scrape, f"{metric} is not exported"


def test_every_alert_references_a_metric_that_is_actually_exported(rules, scrape):
    """An alert on a metric nobody emits never fires, and reads as healthy."""
    exported = {line.split()[0] for line in scrape.splitlines()
                if line and not line.startswith("#")}
    for name, rule in rules.items():
        used = {tok for tok in rule["expr"].replace("(", " ").replace(")", " ").split()
                if tok.startswith("pathia_")}
        assert used, f"{name} references no pathia metric"
        for m in used:
            assert m in exported, f"{name} alerts on {m}, which is not exported"


def test_the_loop_death_alert_exists_and_is_critical(rules):
    """The zombie week: the machine slept, the loop stopped, nothing said so."""
    r = rules["PathiaLoopDead"]
    assert r["labels"]["severity"] == "critical"


def test_the_loop_alert_threshold_clears_the_p99_heartbeat_gap(rules):
    """p99 is ~420s on healthy days. A tighter threshold pages on ordinary slow
    cycles, and an alert that cries wolf gets muted — which is how you end up
    with no alerting at all."""
    expr = rules["PathiaLoopDead"]["expr"]
    threshold = float(expr.split(">")[1].strip())
    assert threshold >= 840, "threshold would fire on a normal slow cycle"


def test_alerts_have_actionable_annotations(rules):
    """A page that does not say what to do gets acknowledged and forgotten."""
    for name, r in rules.items():
        ann = r.get("annotations", {})
        assert ann.get("summary"), f"{name} has no summary"
        assert len(ann.get("description", "")) > 40, f"{name} says nothing useful"


def test_every_alert_waits_before_firing(rules):
    """`for:` on every rule — a single bad scrape must not page a human."""
    for name, r in rules.items():
        assert r.get("for"), f"{name} fires on a single scrape"


def test_the_rules_are_actually_deployable(rules):
    """A rule file nobody applies is a text file. It lives in the monitoring
    overlay rather than the base, because both it and the ServiceMonitor need
    the Prometheus Operator CRDs and putting them in the base would break
    `kubectl apply -k k8s/` on a cluster without them."""
    overlay = (ROOT / "k8s" / "monitoring" / "kustomization.yaml").read_text()
    assert "prometheusrule.yaml" in overlay
    assert "servicemonitor.yaml" in overlay, (
        "the scrape config must ship with the rules — alerts on metrics nobody "
        "collects never fire")
    base = (ROOT / "k8s" / "kustomization.yaml").read_text()
    assert "prometheusrule.yaml" not in base.split("resources:")[1], (
        "a CRD-dependent resource in the base breaks apply on a plain cluster")
    assert "k8s/monitoring/" in base, "the base must point at the overlay"


# ── the defect that made the heartbeat metric lie ────────────────────────────

def test_heartbeat_age_tracks_the_heartbeat_not_any_log_write(monkeypatch):
    """Found 2026-08-29: last_tick_age_s was computed from the last event of ANY
    kind, so a dashboard action or an operator audit entry reset the "loop is
    alive" signal. The loop had been dead 27 days and this reported 325s."""
    import time

    import pathia.dashboard as db
    now = int(time.time() * 1000)
    monkeypatch.setattr(db, "_read_log_lines", lambda: [
        {"ts": now - 9_000_000, "event": "loop_heartbeat", "equity": 100.0,
         "daily_pnl": 0.0, "open_positions": 0, "available": 100.0},
        {"ts": now - 1_000, "event": "operator_action", "action": "authorized"},
    ])
    s = db._summary_payload()
    assert s["last_tick_age_s"] > 8_000, (
        "a non-heartbeat log write made a dead loop look alive")
    assert s["last_event_age_s"] < 10, "last_event_age_s lost its own meaning"


def test_no_heartbeat_at_all_is_not_reported_as_age_zero(monkeypatch):
    """Age 0 would read as perfectly healthy, which is the opposite of true."""
    import pathia.metrics as M
    import pathia.dashboard as db
    monkeypatch.setattr(db, "_summary_payload", lambda: {"last_tick_age_s": None})
    M._refresh()
    assert M.HEARTBEAT_AGE._value.get() > 1e6


# ── a job that fails daily still looks like it ran daily ────────────────────

def test_success_is_tracked_separately_from_dispatch(tmp_path):
    """`last_run` moves on every dispatch, success or not. autonomous-cycle hit
    its 1500s deadline on every run from 2026-08-23 to 08-31 — eight days with
    no book graded, no demotion possible, and every surface reporting it as
    having "run today"."""
    import importlib.util
    import json as _json
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "sched_ok", _os.path.join(root, "scripts", "scheduler.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    state_path = str(tmp_path / "sched.json")
    m._stamp("job", 1000.0, {"rc": 1, "elapsed_s": 1500.0}, state_path)
    st = _json.loads(open(state_path).read())["job"]
    assert st["last_run"] == 1000.0
    assert "last_ok" not in st, "a failed run must not count as a success"

    m._stamp("job", 2000.0, {"rc": 0, "elapsed_s": 12.0}, state_path)
    st = _json.loads(open(state_path).read())["job"]
    assert st["last_ok"] == 2000.0

    m._stamp("job", 3000.0, {"rc": 1, "elapsed_s": 1500.0}, state_path)
    st = _json.loads(open(state_path).read())["job"]
    assert st["last_run"] == 3000.0
    assert st["last_ok"] == 2000.0, "a later failure erased the last success"


def test_grading_staleness_is_exported_and_alerted(monkeypatch, tmp_path):
    import importlib as il
    import json as _json
    import os as _os
    import time as _time

    monkeypatch.setenv("PATHIA_STATE_DIR", str(tmp_path))
    (tmp_path / "scheduler.json").write_text(
        _json.dumps({"autonomous-cycle": {"last_run": _time.time(),
                                          "last_ok": _time.time() - 9 * 86400}}))
    import pathia.agents.rebalancer_owned as ro
    from pathia import metrics
    il.reload(ro)
    try:
        metrics._refresh()
        assert metrics.GRADING_AGE._value.get() > 172800, (
            "eight days of failed runs read as fresh")
    finally:
        il.reload(ro)

    import yaml
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    doc = yaml.safe_load(open(_os.path.join(root, "k8s", "prometheusrule.yaml")))
    names = {r["alert"] for g in doc["spec"]["groups"] for r in g["rules"]}
    assert "PathiaGradingStale" in names


def test_a_job_that_has_never_succeeded_reads_as_maximally_stale(monkeypatch, tmp_path):
    import importlib as il
    import json as _json

    monkeypatch.setenv("PATHIA_STATE_DIR", str(tmp_path))
    (tmp_path / "scheduler.json").write_text(
        _json.dumps({"autonomous-cycle": {"last_run": 1.0, "last_rc": 1}}))
    import pathia.agents.rebalancer_owned as ro
    from pathia import metrics
    il.reload(ro)
    try:
        metrics._refresh()
        assert metrics.GRADING_AGE._value.get() > 172800
    finally:
        il.reload(ro)


def test_the_cycle_grades_only_what_the_config_can_act_on(monkeypatch):
    """28 books and 11,536 rows were graded daily when 4 books and 4,181 rows
    are all that can be promoted or demoted. The run hit its 1500s deadline
    every time from 2026-08-23 to 08-31 and exited having changed nothing."""
    import importlib.util
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "ac", _os.path.join(root, "scripts", "autonomous_cycle.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    # `_SWITCHES` maps a book to its config switch. A book absent from it has
    # no switch to flip, so nothing the cycle decides could ever be applied.
    live = "news_surge_short"
    assert live in m._SWITCHES
    sw = m._SWITCHES[live]
    cfg = ({sw[1]: {"enabled": True, "shadow_only": False}} if sw[0] in ("top", "entries")
           else {sw[1]: {sw[2]: {"enabled": True, "shadow_only": False}}})
    assert m._is_live(cfg, live) is True
    assert m._is_live(cfg, "a_book_that_was_deleted") is None, (
        "a book with no config switch must be identifiable as an orphan")

    # and main() must actually use that to skip them
    import ast
    src = open(_os.path.join(root, "scripts", "autonomous_cycle.py")).read()
    main = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(src, main) or ""
    assert "_is_live(cfg, b) is not None" in body, (
        "main still grades every ledger on disk")
    assert "--all-books" in src, "the historical re-grade is no longer possible"


def test_the_fetch_cache_is_shared_across_books():
    """Built per book, every book refetched coins the previous one pulled — and
    the two news books trade the same signal, so their coin sets overlap almost
    entirely. 392 fetches, 229 unique."""
    import ast
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = open(_os.path.join(root, "scripts", "autonomous_cycle.py")).read()
    tree = ast.parse(src)
    assert any(isinstance(n, ast.FunctionDef) and n.name == "shared_fetchers"
               for n in tree.body)
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(src, main) or ""
    assert "shared_fetchers(" in body, "main builds no shared cache"
    assert "grade_book(book, now_ms, fetchers)" in body, (
        "grade_book is not given the shared cache")


def test_a_deleted_jobs_state_entry_is_pruned():
    """A deleted job's last_run stays forever, so `scheduler.py status` lists
    poly-board with a timestamp — a job that will never run again looking like
    one that just did."""
    import importlib.util
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "sched_prune", _os.path.join(root, "scripts", "scheduler.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    state = {"alerts": {"last_run": 1.0}, "poly-board": {"last_run": 2.0}}
    kept = m.prune_ghosts(state, {"alerts": {}})
    assert kept == {"alerts": {"last_run": 1.0}}


def test_pruning_keeps_every_real_job():
    """Over-pruning would silently reset every job's clock and re-run
    everything at once."""
    import importlib.util
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "sched_prune2", _os.path.join(root, "scripts", "scheduler.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    state = {name: {"last_run": 1.0} for name in m.JOBS}
    assert m.prune_ghosts(state) == state


def test_the_cycle_records_its_own_completion(tmp_path, monkeypatch):
    """The scheduler's last_ok only knows about runs the SCHEDULER started, so
    a hand-run grade would still read as eight days stale. What matters is
    whether the books were graded, not who ran it."""
    import importlib as il
    import json as _json
    import time as _time

    monkeypatch.setenv("PATHIA_STATE_DIR", str(tmp_path))
    (tmp_path / "grading.json").write_text(
        _json.dumps({"ts": _time.time(), "books_graded": 4}))
    import pathia.agents.rebalancer_owned as ro
    from pathia import metrics
    il.reload(ro)
    try:
        metrics._refresh()
        assert metrics.GRADING_AGE._value.get() < 60
    finally:
        il.reload(ro)


def test_the_scheduler_stamp_is_the_fallback(tmp_path, monkeypatch):
    """No receipt yet (an older install) must still read the scheduler's
    record rather than jumping straight to 'never graded'."""
    import importlib as il
    import json as _json
    import time as _time

    monkeypatch.setenv("PATHIA_STATE_DIR", str(tmp_path))
    (tmp_path / "scheduler.json").write_text(
        _json.dumps({"autonomous-cycle": {"last_ok": _time.time() - 120}}))
    import pathia.agents.rebalancer_owned as ro
    from pathia import metrics
    il.reload(ro)
    try:
        metrics._refresh()
        age = metrics.GRADING_AGE._value.get()
        assert 60 < age < 600
    finally:
        il.reload(ro)


def test_the_completion_receipt_is_written_last():
    """A run that aborts on the 1500s deadline must leave the OLD timestamp
    standing. Recording completion before the work would make an abort look
    exactly like a success — the bug this whole metric exists to catch."""
    import ast
    import os as _os

    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    src = open(_os.path.join(root, "scripts", "autonomous_cycle.py")).read()
    main = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(src, main) or ""
    lines = [i for i, ln in enumerate(body.splitlines())
             if "_record_completion(" in ln]
    assert lines, "main never records completion"
    grade = [i for i, ln in enumerate(body.splitlines()) if "grade_book(" in ln]
    assert grade and min(lines) > max(grade), (
        "completion is recorded before the grading finishes")
