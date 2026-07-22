"""Shadow ledger for the Polymarket scout — resolution-graded, not candle-graded.

One jsonl under <state>/polymarket_scout/signals.jsonl. Each row is a paper trade
recorded at signal time (fill at the touch). Grading rejoins the row to the
market's ACTUAL resolution and scores paper PnL + Brier(LLM) vs Brier(market).
No auto-flip; the go-live gate in README.md is the only path to capital.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from services.polymarket_scout.scout import brier, paper_pnl

SCHEMA = 1


def _state_dir() -> str:
    base = os.environ.get("HERMES_STATE_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".state")
    d = os.path.join(base, "polymarket_scout")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _path() -> str:
    return os.path.join(_state_dir(), "signals.jsonl")


def record(*, market_id: str, question: str, side: str, token_id: str,
           llm_yes: float, mkt_yes: float, fill_px: float, edge: float,
           end_date: str, category: str = "", reasoning: str = "",
           ts: Optional[int] = None) -> Dict[str, Any]:
    """Append one paper trade. side in {YES,NO}; fill_px is what we PAID at the
    touch on that side. Best-effort — never raises into the scan loop."""
    row = {
        "v": SCHEMA, "ts": int(ts if ts is not None else time.time() * 1000),
        "market_id": str(market_id), "question": question, "side": side,
        "token_id": str(token_id), "llm_yes": round(float(llm_yes), 4),
        "mkt_yes": round(float(mkt_yes), 4), "fill_px": round(float(fill_px), 4),
        "edge": round(float(edge), 4), "end_date": end_date, "category": category,
        "reasoning": reasoning[:500], "resolved": False, "outcome_yes": None,
    }
    try:
        with open(_path(), "a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass
    return row


def load() -> List[Dict[str, Any]]:
    p = _path()
    if not os.path.isfile(p):
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in open(p):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def grade(resolver: Callable[[str], Optional[bool]],
          rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Grade every row whose market has resolved. `resolver(market_id)` returns
    True if YES won, False if NO won, None if not yet resolved. Returns aggregate
    paper EV + the Brier comparison that decides whether the LLM actually beats
    the market's own price."""
    rows = rows if rows is not None else load()
    pnls: List[float] = []
    b_llm: List[float] = []
    b_mkt: List[float] = []
    detail: List[Dict[str, Any]] = []
    for r in rows:
        yes_won = resolver(str(r.get("market_id")))
        if yes_won is None:
            continue
        side_won = (yes_won if r.get("side") == "YES" else (not yes_won))
        pnl = paper_pnl(bool(side_won), float(r.get("fill_px") or 0.0))
        pnls.append(pnl)
        b_llm.append(brier(float(r.get("llm_yes") or 0.5), bool(yes_won)))
        b_mkt.append(brier(float(r.get("mkt_yes") or 0.5), bool(yes_won)))
        detail.append({"q": (r.get("question") or "")[:60], "side": r.get("side"),
                       "fill": r.get("fill_px"), "yes_won": yes_won,
                       "pnl": round(pnl, 4)})
    n = len(pnls)
    out: Dict[str, Any] = {"n": n, "pending": len(rows) - n}
    if n == 0:
        return out
    out["mean_pnl_per_$"] = round(sum(pnls) / n, 4)
    out["total_pnl_per_$"] = round(sum(pnls), 4)
    out["win_rate"] = round(sum(1 for x in pnls if x > 0) / n, 3)
    out["brier_llm"] = round(sum(b_llm) / n, 4)
    out["brier_mkt"] = round(sum(b_mkt) / n, 4)
    out["llm_beats_market"] = out["brier_llm"] < out["brier_mkt"]
    out["detail"] = detail
    return out
