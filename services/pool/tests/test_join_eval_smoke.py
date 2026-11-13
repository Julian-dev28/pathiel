"""Gate-lane smoke of services/pool/evals/join_eval.py: the whole scorecard
runs and every row passes. The eval itself is the periodic-lane proof; this
is just "it still runs and is still green", in the commit gate."""
from __future__ import annotations

from services.pool.evals import join_eval as E


def test_join_eval_passes_every_row():
    assert E.main([]) == 0


def test_join_eval_has_at_least_30_rows_across_every_group():
    rows, no_leak, _transcript = E.run()
    assert len(rows) >= 30
    assert no_leak is True
    assert all(r["ok"] for r in rows)
    groups = {r["check"].split(".")[0] for r in rows}
    assert {"valid", "offline", "online", "pr", "replay", "no_key_leak"} <= groups
