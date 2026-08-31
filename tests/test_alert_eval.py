"""The alert evaluator: does a rule that should fire actually reach a human?

k8s/prometheusrule.yaml was written for the Prometheus Operator. Nothing here
runs Kubernetes, so for as long as those rules existed they fired nowhere. The
evaluator closes that, and these tests exist because the ways it could be
quietly useless are the same ways the system was quietly broken before it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


A = _load("alert_eval", "scripts/alert_eval.py")



def _samples_satisfying_every_rule() -> dict:
    """Metric values that make every shipped rule fire.

    Derived from the rules rather than hand-written, so adding a rule cannot
    silently stop being covered here — which is exactly what happened when
    HermesSupervisionStale and HermesAlertingStale were added.
    """
    import re
    satisfy = {">": lambda n: n + 1, ">=": lambda n: n, "<": lambda n: n - 1,
               "<=": lambda n: n, "==": lambda n: n, "!=": lambda n: n + 1}
    out = {}
    for rule in A.load_rules():
        for term in rule["expr"].split(" and "):
            m = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*"
                         r"(-?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s*$", term)
            assert m, f"cannot satisfy {term!r} — extend this helper with the rule"
            out[m.group(1)] = satisfy[m.group(2)](float(m.group(3)))
    return out


# ── the rules the operator would evaluate are the rules we evaluate ──────────

def test_every_shipped_rule_is_evaluable():
    """An expression this cannot parse is an alert that never fires. If a rule
    is added in a syntax the evaluator does not handle, this test is the only
    thing standing between that and a silently dead alert."""
    samples = {m: 0.0 for r in A.load_rules() for m in A.metrics_referenced(r["expr"])}
    for rule in A.load_rules():
        A.evaluate(rule["expr"], samples)          # must not raise
        A.parse_duration(rule.get("for", "0s"))


def test_every_rule_metric_is_actually_exported():
    """Same check the k8s test makes, made against the live evaluator's parser
    so the two cannot drift."""
    import hermes_trader.dashboard  # noqa: F401  (metrics registered on import)
    from fastapi.testclient import TestClient

    from hermes_trader.server import app
    body = TestClient(app).get("/metrics").text
    exported = set(A.parse_metrics(body))
    for rule in A.load_rules():
        for m in A.metrics_referenced(rule["expr"]):
            assert m in exported, f"{rule['alert']} alerts on unexported {m}"


# ── an unparseable rule must be loud, never "not firing" ─────────────────────

def test_an_unsupported_expression_raises_rather_than_reading_as_healthy():
    with pytest.raises(A.UnsupportedExpr):
        A.evaluate("rate(hermes_trades_total[5m]) > 0", {"hermes_trades_total": 1})


def test_a_missing_metric_raises_rather_than_reading_as_healthy():
    """The single most important behaviour here. `metric == 0` against an
    absent metric must not evaluate false and report all-clear."""
    with pytest.raises(A.UnsupportedExpr):
        A.evaluate("hermes_typo_metric == 0", {"hermes_feed_trustworthy": 1.0})


def test_an_unreachable_metrics_endpoint_is_reported_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE", str(tmp_path / "a.json"))
    monkeypatch.setattr(A, "ALERT_LOG", str(tmp_path / "a.log"))
    monkeypatch.setattr(A, "METRICS_URL", "http://127.0.0.1:1/metrics")
    monkeypatch.setattr(A, "notify", lambda *a, **k: None)
    monkeypatch.setattr(A, "scrape", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    assert A.main([]) == 2, "a dead server must not report 'no alerts firing'"
    assert "ERROR" in (tmp_path / "a.log").read_text()


# ── expression semantics ─────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,samples,expected", [
    ("hermes_heartbeat_age_seconds > 900", {"hermes_heartbeat_age_seconds": 901}, True),
    ("hermes_heartbeat_age_seconds > 900", {"hermes_heartbeat_age_seconds": 900}, False),
    ("hermes_feed_trustworthy == 0", {"hermes_feed_trustworthy": 0.0}, True),
    ("hermes_feed_trustworthy == 0", {"hermes_feed_trustworthy": 1.0}, False),
    ("hermes_disk_free_bytes < 2e9", {"hermes_disk_free_bytes": 1.9e9}, True),
    ("hermes_disk_free_bytes < 2e9", {"hermes_disk_free_bytes": 2.1e9}, False),
    ("hermes_max_drawdown_pct < -25", {"hermes_max_drawdown_pct": -94.78}, True),
    ("hermes_max_drawdown_pct < -25", {"hermes_max_drawdown_pct": -10.0}, False),
])
def test_comparison_semantics(expr, samples, expected):
    assert A.evaluate(expr, samples) is expected


def test_and_requires_both_sides():
    expr = "hermes_live_mode == 1 and hermes_can_trade == 0"
    assert A.evaluate(expr, {"hermes_live_mode": 1.0, "hermes_can_trade": 0.0}) is True
    assert A.evaluate(expr, {"hermes_live_mode": 1.0, "hermes_can_trade": 1.0}) is False
    assert A.evaluate(expr, {"hermes_live_mode": 0.0, "hermes_can_trade": 0.0}) is False


@pytest.mark.parametrize("text,secs", [("5m", 300), ("1h", 3600), ("30s", 30), ("2d", 172800)])
def test_duration_parsing(text, secs):
    assert A.parse_duration(text) == secs


# ── `for:` semantics ─────────────────────────────────────────────────────────

def test_a_condition_must_hold_for_the_full_window_before_firing():
    """`for: 5m` exists so one slow cycle does not page. Firing immediately
    would make the alerts noisy, and noisy alerts get muted."""
    t0 = 1000.0
    st, since = A.transition("X", True, 300, None, False, t0)
    assert st == "pending" and since == t0
    st, since = A.transition("X", True, 300, since, False, t0 + 299)
    assert st == "pending"
    st, since = A.transition("X", True, 300, since, False, t0 + 300)
    assert st == "started_firing"


def test_a_firing_alert_does_not_re_notify_every_tick():
    st, _ = A.transition("X", True, 300, 1000.0, True, 9999.0)
    assert st == "firing", "started_firing again would re-notify forever"


def test_recovery_resolves_and_clears_the_pending_clock():
    st, since = A.transition("X", False, 300, 1000.0, True, 2000.0)
    assert st == "resolved" and since is None
    # a condition that flaps must restart its window, not resume it
    st, since = A.transition("X", True, 300, None, False, 2001.0)
    assert st == "pending" and since == 2001.0


def test_a_never_true_condition_is_inactive_not_resolved():
    st, _ = A.transition("X", False, 300, None, False, 1000.0)
    assert st == "inactive"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_scheduler_runs_the_evaluator():
    m = _load("sched_alerts", "scripts/scheduler.py")
    assert "alerts" in m.JOBS
    assert m.JOBS["alerts"]["args"][-1].endswith("alert_eval.py")
    assert m.JOBS["alerts"]["interval_min"] <= 5


def test_end_to_end_against_live_rules_writes_state(tmp_path, monkeypatch):
    """The whole path with delivery stubbed: real rules, injected samples."""
    monkeypatch.setattr(A, "STATE", str(tmp_path / "a.json"))
    monkeypatch.setattr(A, "ALERT_LOG", str(tmp_path / "a.log"))
    sent = []
    monkeypatch.setattr(A, "notify", lambda t, m: sent.append((t, m)))
    monkeypatch.setattr(A, "scrape", lambda *a, **k: _samples_satisfying_every_rule())

    assert A.main(["--quiet"]) == 0, "first pass: everything pending, nothing fired yet"
    assert sent == []

    # ...and once every `for:` window has elapsed, all seven fire exactly once.
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 86400)
    assert A.main(["--quiet"]) == 1
    saved = json.loads((tmp_path / "a.json").read_text())
    assert len(saved["firing"]) == len(A.load_rules()) == len(sent)
    assert not saved["errors"]

    # a third pass must not re-notify
    before = len(sent)
    A.main(["--quiet"])
    assert len(sent) == before, "a standing alert re-notified"


# ── annotation rendering ─────────────────────────────────────────────────────

def test_every_shipped_annotation_renders_completely():
    """The rules are Go templates. Prometheus substitutes them; nothing did
    here, so the first alert this evaluator ever delivered read "Max drawdown
    {{ $value }}% over the risk window" — delivered, logged, notified, and
    useless. An alert a human cannot read is barely better than one that never
    fires. This fails if a rule is written with a template the evaluator does
    not handle."""
    samples = _samples_satisfying_every_rule()
    unrendered = []
    for rule in A.load_rules():
        text, ok = A.render(rule["annotations"]["summary"], rule["expr"], samples)
        if not ok:
            unrendered.append(f"{rule['alert']}: {text}")
    assert not unrendered, "\n".join(unrendered)


@pytest.mark.parametrize("seconds,expected", [
    (5400, "1h 30m"), (900, "15m"), (86400 * 2 + 3600, "2d 1h"), (45, "45s"),
])
def test_duration_humanising(seconds, expected):
    assert A.humanize_duration(seconds) == expected


def test_percentage_humanising():
    assert A.humanize_percentage(0.75) == "75%"
    assert A.humanize_percentage(0.0) == "0%"


def test_the_raw_value_is_substituted():
    text, ok = A.render("Max drawdown {{ $value }}% over the risk window",
                        "hermes_max_drawdown_pct < -25",
                        {"hermes_max_drawdown_pct": -94.78})
    assert ok and "-94.78%" in text and "{{" not in text


def test_a_with_query_block_pulls_a_different_metric():
    text, ok = A.render(
        '{{ with query "hermes_feed_gap_fraction" }}'
        '{{ . | first | value | humanizePercentage }}{{ end }} unreadable',
        "hermes_feed_trustworthy == 0",
        {"hermes_feed_trustworthy": 0.0, "hermes_feed_gap_fraction": 0.75})
    assert ok and text == "75% unreadable"


def test_an_unrenderable_template_is_left_intact_and_reported():
    """Blanking it would produce a complete-looking sentence that quietly lost
    its number — the omission would be invisible."""
    text, ok = A.render("saw {{ $labels.instance }} misbehaving",
                        "hermes_can_trade == 0", {"hermes_can_trade": 0.0})
    assert ok is False
    assert "{{ $labels.instance }}" in text


def test_an_unrenderable_summary_makes_the_run_report_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE", str(tmp_path / "a.json"))
    monkeypatch.setattr(A, "ALERT_LOG", str(tmp_path / "a.log"))
    monkeypatch.setattr(A, "notify", lambda *a, **k: None)
    monkeypatch.setattr(A, "scrape", lambda *a, **k: {"hermes_can_trade": 0.0})
    monkeypatch.setattr(A, "load_rules", lambda *a, **k: [{
        "alert": "Bogus", "expr": "hermes_can_trade == 0", "for": "0s",
        "labels": {"severity": "warning"},
        "annotations": {"summary": "broken {{ $labels.pod }}"}}])
    assert A.main(["--quiet"]) == 2, "an unreadable alert must not report clean"
