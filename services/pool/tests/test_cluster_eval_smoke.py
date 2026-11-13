"""Gate-lane smoke of the cluster eval (services/pool/evals/cluster_eval.py).
Owned by the architect's commit, like the contract it tests.

The rates need 200 seeds and half a minute, so they run in the periodic lane.
Here: the eval runs, is deterministic, and the gates that are exact at any
seed count hold."""
from __future__ import annotations

import random

import contracts.pool as pool
from services.pool.evals import cluster_eval as E


def test_the_scorecard_runs_and_the_exact_gates_hold():
    rows = E.run(seeds=2, n_boot=100, n_perm=100)
    by = {r["check"]: r for r in rows}
    assert list(by) == ["same_call", "chain_bound.span", "chain_bound.clusters", "chain_bound.chained_60m",
                        "reference", "null", "teeth", "window_60m (reported)", "null_long", "power"]
    for check in ("same_call", "chain_bound.span", "chain_bound.clusters", "chain_bound.chained_60m",
                  "reference"):
        assert by[check]["ok"] is True, by[check]
    assert by["chain_bound.chained_60m"]["got"] == 1          # the rule this replaced: one cluster for ever
    assert by["chain_bound.clusters"]["got"] >= E.CHAIN_MIN_CLUSTERS
    assert by["window_60m (reported)"]["ok"] is None
    assert rows == E.run(seeds=2, n_boot=100, n_perm=100)     # seeded: the same rows twice


def test_the_world_is_fair_when_the_coin_is_even():
    """A bracket on a unit-step walk is worth exactly -1 or +2, and wins one
    time in three: the null of the eval is a real null."""
    rng = random.Random(7)
    trades = E.bracket_trades(rng, E.walk(rng, 60), members=12, days=60, p_enter=0.05, stop_steps=4)
    rs = [r for _, _, r in trades]
    assert set(rs) == {-1.0, 2.0} and len(rs) > 1000
    assert abs(sum(rs) / len(rs)) < 0.15
    assert all(exit_ms > entry_ms for entry_ms, exit_ms, _ in trades)
    assert trades == sorted(trades)


def test_the_evals_reference_rule_is_the_contracts():
    rng = random.Random(11)
    entries = sorted(rng.randrange(0, 40 * pool.DAY_MS) for _ in range(500))
    assert E.anchored(entries, pool.CLUSTER_SPAN_MS) == pool.cluster_ids([(E.COIN, E.SIDE, e) for e in entries])
    assert len(set(E.chained(entries, pool.DAY_MS))) <= len(set(E.anchored(entries, pool.DAY_MS)))


def test_main_prints_the_summary_line_and_fails_closed(capsys, monkeypatch):
    monkeypatch.setattr(E, "run", lambda seeds: [E._row("null", 0.5, "<= 0.1", False),
                                                 E._row("teeth", 0.5, ">= 0.25", True),
                                                 E._row("window_60m (reported)", 0.3, "no gate", None)])
    assert E.main(["--seeds", "3"]) == 1
    out = capsys.readouterr().out
    assert "CLUSTER-EVAL span_hours=24 seeds=3 passed=1/2" in out and "FAIL" in out and "info" in out
