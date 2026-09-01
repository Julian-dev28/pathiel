"""Disk cache for the three lanes.

The dashboard NEVER computes a lane inside a request. Every lane here does
live network work (HL candles, Binance klines, Gamma + CLOB), and this repo's
rule is that a slow upstream must not be able to hang a dashboard poll. So:

    refresher (scheduler / CLI)  ->  writes .state/trend_engine/<lane>.json
    dashboard GET                ->  pure file read, marks its own staleness
    dashboard POST refresh       ->  operator-gated background job, same writer

`stale_after` per lane is set to roughly one refresh interval, so a tab that
says "fresh" really is.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Optional, Sequence

from services.trend_engine import env

# Resolved through the SAME env as the bot's own state, or a cron run
# would write its cache next to a different shadow ledger than the one the
# dashboard reads (see services/trend_engine/env.py).
DIR = os.path.join(env.state_dir(), "trend_engine")
LANES = ("hl",)
# Roughly one refresh interval, so a lane that missed its slot reads STALE
# rather than quietly serving an old scan.
STALE_AFTER = {"hl": 1800.0}


def path(lane: str) -> str:
    return os.path.join(DIR, f"{lane}.json")


def save(lane: str, payload: Dict[str, Any]) -> str:
    """Atomic write — a half-written cache read by a poll is a broken tab."""
    os.makedirs(DIR, exist_ok=True)
    p = path(lane)
    fd, tmp = tempfile.mkstemp(dir=DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, default=str)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p


def load(lane: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Cached lane payload plus `age_s` / `stale`. Never raises, never fetches."""
    now = time.time() if now is None else now
    try:
        with open(path(lane)) as fh:
            payload = json.load(fh)
    except Exception:
        return {"status": "empty", "lane": lane, "stale": True, "age_s": None,
                "hint": f"run: python -m services.trend_engine.run --lane {lane} --save"}
    gen = float(payload.get("generated_at") or 0)
    age = max(0.0, now - gen) if gen else None
    payload["lane"] = lane
    payload["age_s"] = round(age, 1) if age is not None else None
    payload["stale"] = bool(age is None or age > STALE_AFTER.get(lane, 1800.0))
    payload["stale_after_s"] = STALE_AFTER.get(lane, 1800.0)
    return payload


def compute(lane: str, **kw: Any) -> Dict[str, Any]:
    """Run one lane fresh (network). Not called from request handlers."""
    if lane == "hl":
        from services.trend_engine.hl_trends import scan
        return scan(**kw)
    raise ValueError(f"unknown lane: {lane}")


def refresh(lane: str, keep_ai: bool = True, **kw: Any) -> Dict[str, Any]:
    """Recompute a lane and write it, carrying the previous AI block forward.

    The AI pass costs a model call and is slower than the numbers it reads, so
    a data refresh must not silently wipe it — it is carried over and stamped
    with the age of the read it was written against.
    """
    prev = load(lane) if keep_ai else {}
    payload = compute(lane, **kw)
    # The action layer is derived, cheap and pure — compute it here so the tab
    # never has to, and so a stale cache carries the actions that matched its
    # own numbers rather than newer ones.
    try:
        from services.trend_engine.playbook import build as build_playbook
        payload["playbook"] = build_playbook(lane, payload)
    except Exception as exc:
        payload["playbook"] = {"status": "error", "error": str(exc)[:200], "actions": []}
    old_ai = prev.get("ai")
    if keep_ai and isinstance(old_ai, dict) and old_ai.get("status") == "ok":
        old_ai = dict(old_ai)
        old_ai["stale_for_this_read"] = True
        payload["ai"] = old_ai
    save(lane, payload)
    return payload


def attach_ai(lane: str, ai: Dict[str, Any]) -> Dict[str, Any]:
    """Write an AI block onto the cached lane payload (no recompute)."""
    payload = load(lane)
    if payload.get("status") == "empty":
        return payload
    for k in ("age_s", "stale", "stale_after_s", "lane"):
        payload.pop(k, None)
    payload["ai"] = ai
    save(lane, payload)
    return payload


def refresh_eval(top_n: int = 25, days: int = 400, force: bool = False) -> Dict[str, Any]:
    """Re-run the HL walk-forward if the saved one is over a day old.

    Kept on its own cadence because it pulls 400 daily bars per coin — far more
    than a scan — and its answer moves slowly. `scan()` attaches whatever is
    on disk, so this is what keeps the honesty panel current.
    """
    from services.trend_engine.hl_trends import backtest, eval_is_stale, save_eval
    if not force and not eval_is_stale():
        return {"status": "fresh", "skipped": True}
    ev = backtest(top_n=top_n, days=days)
    save_eval(ev)
    return ev


def refresh_all(only: Optional[Sequence[str]] = None,
                **per_lane: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh lanes, isolating failures so one dead API can't stop the rest.

    `only` restricts the pass — the scheduler runs the price lane every 30
    minutes.

    The HL walk-forward runs FIRST when stale, so the scan that follows picks
    the fresh numbers up in the same pass.
    """
    lanes = [l for l in LANES if (only is None or l in only)]
    out: Dict[str, Any] = {}
    if "hl" in lanes:
        try:
            ev = refresh_eval()
            out["hl_eval"] = {"status": ev.get("status", "ok"),
                              "dir_hit": ev.get("dir_hit"), "n": ev.get("n")}
        except Exception as exc:
            out["hl_eval"] = {"status": "error", "error": str(exc)[:200]}
    for lane in lanes:
        t0 = time.time()
        try:
            p = refresh(lane, **(per_lane.get(lane) or {}))
            out[lane] = {"status": p.get("status"), "elapsed_s": round(time.time() - t0, 2)}
        except Exception as exc:
            out[lane] = {"status": "error", "error": str(exc)[:200],
                         "elapsed_s": round(time.time() - t0, 2)}
    return out
