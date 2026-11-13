"""One review run: ledger update, stats, proposals, report. No network here
(the caller hands in a bundle), no LLM, no orders, no live config."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.review import analysis as A
from services.review import env
from services.review import ledger as LG
from services.review import proposals as P
from services.review import report as R
from services.review import stats as S
from services.review import thesis_match as TM
from services.strategies.evals import loss_audit as LA

# Each desk's primary (metals: gold since 2026-11-05). A thesis's 24h check
# reads its own instrument's bars first (analysis.thesis_outcomes), so the
# desk's silver-era calls are still judged on silver.
DESK_COIN = {"oil": "xyz:BRENTOIL", "metals": "xyz:GOLD"}


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path) as fh:
            body = json.load(fh)
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return {}


def run(bundle: Dict[str, Any], since_ms: int = LA.SINCE_MS, ledger_path: Optional[str] = None,
        last_run_path: Optional[str] = None, report_dir: Optional[str] = None, seed: int = S.SEED,
        write_report: bool = True) -> Dict[str, Any]:
    ledger_path = ledger_path or env.ledger_path()
    last_run_path = last_run_path or env.last_run_path()
    report_dir = report_dir or env.report_dir()
    as_of = LG.iso(bundle["as_of_ms"])

    built = TM.build_records(bundle)
    upd = LG.update_ledger(ledger_path, built, as_of)
    recs = LG.ledger_records(ledger_path, since_ms)

    theses = TM.theses_by_desk(bundle)
    theses = {d: [t for t in ts if t.created_at_ms >= since_ms] for d, ts in theses.items()}
    raw_by_id = {str(t.get("id")): t for ts in (bundle.get("theses_raw") or {}).values() for t in ts}
    bars1h = {d: bundle["bars1h"].get(c, []) for d, c in DESK_COIN.items()}
    outcomes = A.thesis_outcomes(theses, raw_by_id, bars1h, bundle["as_of_ms"], bars1h_by_coin=bundle["bars1h"])
    A.join_thesis_outcomes(recs, outcomes)

    rows = A.slice_table(recs, seed)
    desks_by_key = {row["key"]: sorted({r["desk"] for r in recs if row["value"] in A.buckets(r, row["dim"])})
                    for row in rows}
    props = P.build(rows, desks_by_key)

    last = _load_json(last_run_path)
    was = last.get("labels") or {}
    new_leaks = [r["key"] for r in rows if r["label"] == S.LEAK and was.get(r["key"]) != S.LEAK]
    seen = set(last.get("proposals") or [])
    new_props = [p["id"] for p in props["proposals"] if p["id"] not in seen]

    date = as_of[:10]
    report_path = os.path.join(report_dir, f"REVIEW_{date}.md")
    try:
        rel = os.path.relpath(report_path, env.REPO_ROOT)
    except ValueError:
        rel = report_path
    res: Dict[str, Any] = {
        "as_of": as_of, "since": LG.iso(since_ms)[:10], "records": recs, "rows": rows,
        "calls": A.thesis_calls(outcomes), "proposals": props, "new_leaks": new_leaks,
        "new_proposals": new_props, "report_path": report_path,
        "report_rel": rel if not rel.startswith("..") else report_path,
        "provisional": ", ".join(r["entry"] for r in recs if not r.get("settled")),
        "ledger": {"total": len(recs), **upd},
    }
    if write_report:
        os.makedirs(report_dir, exist_ok=True)
        with open(report_path, "w") as fh:
            fh.write(R.render(res))
    os.makedirs(os.path.dirname(last_run_path) or ".", exist_ok=True)
    with open(last_run_path, "w") as fh:
        json.dump({"as_of": as_of, "labels": {r["key"]: r["label"] for r in rows},
                   "proposals": sorted(seen | {p["id"] for p in props["proposals"]}),
                   "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}, fh, indent=1)
    res["summary"] = R.summary_line(res)
    return res
