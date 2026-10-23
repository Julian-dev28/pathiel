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

from pathiel.server import app

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
    """A dashboard number nobody scrapes cannot page anyone.

    2026-10-23 (wave 2): pathiel_heartbeat_age_seconds/pathiel_feed_trustworthy/
    pathiel_feed_gap_fraction dropped — the trading loop and its scanner that
    wrote them are retired, and a metric with no writer left reads as a
    permanent, false failure rather than a real one (the same reasoning that
    dropped pathiel_grading_age_seconds in wave 1)."""
    for metric in ("pathiel_can_trade", "pathiel_ai_brain_ready",
                   "pathiel_disk_free_bytes", "pathiel_log_dir_bytes",
                   "pathiel_max_drawdown_pct", "pathiel_backup_age_seconds"):
        assert metric in scrape, f"{metric} is not exported"


def test_every_alert_references_a_metric_that_is_actually_exported(rules, scrape):
    """An alert on a metric nobody emits never fires, and reads as healthy."""
    exported = {line.split()[0] for line in scrape.splitlines()
                if line and not line.startswith("#")}
    for name, rule in rules.items():
        used = {tok for tok in rule["expr"].replace("(", " ").replace(")", " ").split()
                if tok.startswith("pathiel_")}
        assert used, f"{name} references no pathiel metric"
        for m in used:
            assert m in exported, f"{name} alerts on {m}, which is not exported"


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

    import pathiel.dashboard as db
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


# 2026-10-23 (wave 2): pathiel.metrics.HEARTBEAT_AGE and scripts/scheduler.py
# are both retired with the rest of the autonomous engine (the trading loop
# that wrote the heartbeat, and the cron-replacement scheduler whose
# dispatch/success/pruning bookkeeping the tests here used to cover). Claude
# trades through the MCP server now; there is no scheduled job, no heartbeat,
# and nothing left for those tests to exercise.


