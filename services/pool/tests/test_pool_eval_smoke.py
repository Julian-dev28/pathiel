"""Gate-lane smoke of services/pool/evals/pool_eval.py: the eval runs, is
deterministic, and the exact gates hold at a small seed count and small
inputs. The full run (200 seeds, 40 clusters, 12 members over 30 days,
about 90 seconds) is the periodic lane, python -m services.pool.evals.pool_eval."""
from __future__ import annotations

from services.pool.evals import pool_eval as E

SMALL = {"quiet_members": 2, "quiet_days": 3, "independent_n": 6}


def test_the_scorecard_runs_and_every_gate_holds_at_a_small_seed_count():
    E._VALID_DOCS.clear()
    rows = E.run(seeds=2, quiet_seeds=1, **SMALL)
    by = {r["check"]: r for r in rows}
    assert list(by) == ["copied_calls", "planted_edge", "pure_noise", "delay", "leave", "no_quiet_hour",
                        "no_quiet_hour.seconds", "every_doc_valid"]
    for check in ("copied_calls", "delay", "leave", "no_quiet_hour", "every_doc_valid"):
        assert by[check]["ok"] is True, by[check]
    assert by["no_quiet_hour.seconds"]["ok"] is None


def test_the_scorecard_is_deterministic():
    E._VALID_DOCS.clear()
    first = E.run(seeds=1, quiet_seeds=1, **SMALL)
    E._VALID_DOCS.clear()
    second = E.run(seeds=1, quiet_seeds=1, **SMALL)
    assert first == second


def test_main_prints_the_summary_line_and_exits_1_on_a_failed_gate(capsys, monkeypatch):
    monkeypatch.setattr(E, "copied_calls", lambda seeds: (seeds, seeds))   # every seed "fails" on purpose
    rc = E.main(["--seeds", "1", "--quiet-seeds", "1", "--quiet-members", "2", "--quiet-days", "3",
                "--independent-n", "6"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "POOL-EVAL seeds=1 quiet_seeds=1 passed=" in out
    assert "FAIL" in out


def test_main_exits_0_when_every_gate_passes(monkeypatch):
    """planted_edge / pure_noise need real statistical power (hence 40
    clusters and 200 seeds in the periodic run) and are not pinned to pass
    at a tiny seed count (same convention as cluster_eval's own smoke test,
    which excludes its power-sensitive gates too); this checks only that
    main() wires an all-passing scorecard to exit 0."""
    monkeypatch.setattr(E, "run", lambda **kw: [
        {"check": "a", "got": 1, "want": "1", "ok": True},
        {"check": "b.seconds", "got": 0.1, "want": "reported, not gated", "ok": None},
    ])
    assert E.main(["--seeds", "1"]) == 0
