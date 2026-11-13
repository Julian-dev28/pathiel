"""Match desk theses to trades and build full ledger records from a bundle.

Split out of services/review/ledger.py (2026-11-13): this is the one place
in services/review that imports services.strategies.evals.stop_cap_test
(for `same_market`), which itself reaches the rest of the backtest/eval
chain -- desk_tick_backtest, live_params, profit_lock_test, market_scout,
strategy_sweep, scenario_sweep, cross_market_backtest. services/pool/grade.py
imports services.review.ledger for to_record/annotate_pairs/update_ledger/
ledger_records, none of which need any of that; keeping the split here means
grade.py's import never drags that chain into the plugin bundle
(scripts/build_plugin_bundle.py walks every import reachable from the MCP
server, lazy ones inside functions included).

services/review/review.py (the operator's own review run, never reached from
the MCP server's entry scripts) is the only caller.
"""
from __future__ import annotations

from typing import Any, Dict, List

from services.review import inputs as I
from services.review.ledger import annotate_pairs, to_record
from services.strategies.evals import loss_audit as LA
from services.strategies.evals.stop_cap_test import same_market


def theses_by_desk(bundle: Dict[str, Any]) -> Dict[str, List[Any]]:
    dirs = bundle.get("theses_dirs") or {}
    oil = I.thesis_directions(dirs.get("oil", ""))
    metals = I.thesis_directions(dirs.get("metals", ""))
    # The metals desk has traded silver (2026-10-31 to 11-05) and gold (one
    # thesis before that, the primary since 11-05): keep every thesis whose
    # entry sits at one of its coins' prices, so a gold call is never judged
    # as a silver one or dropped.
    bars = [(bundle.get("bars1h") or {}).get(c) or [] for c in ("xyz:SILVER", "xyz:GOLD")]
    bars = [b for b in bars if b]
    if metals and bars:
        keep = {th.thesis_id for b in bars for th in same_market(metals, b)}
        metals = [th for th in metals if th.thesis_id in keep]
    return {"oil": oil, "metals": metals}


def build_records(bundle: Dict[str, Any], since_ms: int = LA.SINCE_MS) -> List[Dict[str, Any]]:
    """Every closed trade in `bundle` as a ledger record, oldest first. Pure
    on the bundle."""
    theses = theses_by_desk(bundle)
    raw = {k: bundle[k] for k in I.RAW_KEYS}
    rep = LA.run_audit(raw, bundle["bars5"], theses, since_ms)
    out = []
    for tr in rep["trades"]:
        if tr["exit_time"] is None:
            continue
        out.append(to_record(tr, theses.get(tr["desk"], []), bundle["bars1h"].get(tr["coin"], []),
                             bundle["bars5"].get(tr["coin"], []), bundle["as_of_ms"]))
    return annotate_pairs(out)
