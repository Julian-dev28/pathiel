"""How long each journaled thesis stayed in force after a closed 1h candle
broke its own stop. Deterministic, no LLM calls; reads the desk journal and
public 1h candles.

2026-11-02: the oil long of 01:19Z (stop 110.83) was broken by the 03:00Z
close (110.17) and stayed the live call until a 1.5% move forced a rewrite at
03:48Z. scripts/thesis_due.py now makes a broken stop a rewrite reason; this
eval measures, over the whole journal, the hours a thesis was "dead but live"
under the old reasons (what the new reason removes).

    python -m services.strategies.evals.invalidated_thesis_hours --desk oil
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location("thesis_due", os.path.join(ROOT, "scripts", "thesis_due.py"))
TD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TD)


def dead_hours(theses: List[Dict[str, Any]], bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure. `theses` sorted by created_at; each one is live until the next.
    For each, the first invalidating close and the hours it stayed live after."""
    out = []
    for i, t in enumerate(theses):
        end = TD._ts(theses[i + 1]["created_at"]) if i + 1 < len(theses) else max(
            (datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc) for b in bars), default=TD._ts(t["created_at"]))
        start = TD._ts(t["created_at"])
        window = [b for b in bars if start <= datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc) < end]
        reason = TD.invalidating_close(t, window, end)
        hours: Optional[float] = None
        if reason:
            broke = next(datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc)
                         for b in sorted(window, key=lambda b: b["t"])
                         if TD.invalidating_close(t, [b], end))
            hours = round((end - broke).total_seconds() / 3600 - 1, 2)
        out.append({"id": t.get("id"), "side": (t.get("call") or {}).get("side"),
                    "stop_px": (t.get("call") or {}).get("stop_px"), "invalidated": reason,
                    "live_hours_after_break": hours})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desk", default="oil")
    ap.add_argument("--last", type=int, default=30)
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import _state_env
    _state_env.load_env_local(ROOT)
    from services.strategies import journal
    rows = [json.loads(t.model_dump_json()) for t in journal.list_theses(a.desk)]
    rows = sorted(rows, key=lambda r: r["created_at"])[-a.last:]
    if not rows:
        print("no theses")
        return 0
    coin = rows[-1]["call"].get("instrument") or "xyz:BRENTOIL"
    bars = TD._hour_closes(coin, TD._ts(rows[0]["created_at"]))
    res = dead_hours(rows, bars)
    broken = [r for r in res if r["invalidated"]]
    for r in res:
        print(json.dumps(r))
    total = sum(r["live_hours_after_break"] or 0 for r in broken)
    print(f"\n{a.desk}: {len(res)} theses, {len(broken)} broke their stop on a closed 1h candle while live, "
          f"{total:.1f} hours traded as live after the break (the new rewrite reason removes these)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
