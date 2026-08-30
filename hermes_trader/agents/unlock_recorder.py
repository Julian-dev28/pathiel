"""Unlock calendar + the run-in signal token-unlock recorder (W-U lane, 2026-07-11).

Scheduled supply unlocks are the one news type knowable IN ADVANCE: DefiLlama
publishes the cliff calendar, so "unlock in <=24h" is a point-in-time-safe
signal with no scraping race. This module records hypothetical SHORTs to the
unified shadow ledger ahead of unlocks >= min_pct_circ of circulating supply.
Nothing here trades; promotion runs through scripts/shadow_status.py
(>=30 episodes, EV25>0 both halves) exactly like every other recorder.

W-U1 backtest evidence (research/alpha_swarm/hypotheses/W-U1_results.json,
915 events, 90 HL coins, 2026-07-11): the registered T-1d->T+2d short earned
+0.76%/trade net but p_mc=0.10 with a SIGN FLIP across halves (-0.24 / +1.76)
— NOT validated. The robust exploratory fact: price falls -2.1% close-to-close
over T-3d->T (n=408, win 39.5%) and the drop is DONE by the event (post
T->T+3d ~= 0). Hence TWO forward arms, both zero-capital, forward data
adjudicates: ``unlock_short_runin`` enters in the [72h,48h) pre-unlock window
(rides the run-in), ``unlock_short`` enters inside the last 24h (the
registered cell).

Calendar source: the free DefiLlama open-datasets bucket (the api.llama.fi
emissions endpoints went paid). The 21MB index is refreshed at most every
``refresh_hours`` (default 12) and reduced immediately to a tiny upcoming-
events state file; a failed refresh keeps the stale calendar (unlock dates
don't move on that timescale).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.book_helpers import load_state, save_state
from hermes_trader.agents.book_helpers import safe_float as _num
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_INDEX_URL = "https://defillama-datasets.llama.fi/emissionsIndex"
_STATE_FILE = state_file(".unlock_recorder_state.json")
_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unlock_map.json")


def _load_state() -> Dict[str, Any]:
    return load_state(_STATE_FILE)


def _save_state(state: Dict[str, Any]) -> None:
    save_state(_STATE_FILE, state)


def _load_map() -> Dict[str, str]:
    try:
        raw = json.load(open(_MAP_FILE))
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning(f"[unlock-recorder] map load failed: {exc}")
        return {}


def _extract_upcoming(index: Dict[str, Any], name_to_sym: Dict[str, str],
                      min_pct: float, now_ms: int) -> List[Dict[str, Any]]:
    """Reduce the 21MB index to [{coin, t_ms, pct}] within the next 45 days."""
    horizon = now_ms + 45 * _DAY_MS
    merged: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for r in index.get("data") or []:
        sym = name_to_sym.get(str(r.get("name") or ""))
        circ = _num(r.get("circSupply"))
        if not sym or circ <= 0:
            continue
        for e in r.get("events") or []:
            ts_ms = int(_num(e.get("timestamp")) * 1000)
            toks = e.get("noOfTokens")
            amt = sum(_num(x) for x in toks) if isinstance(toks, list) else 0.0
            if ts_ms <= now_ms or ts_ms > horizon or amt <= 0:
                continue
            pct = amt / circ * 100.0
            key = (sym, ts_ms // _DAY_MS)
            if key in merged:
                merged[key]["pct"] += pct
            else:
                merged[key] = {"coin": sym, "t_ms": ts_ms, "pct": pct}
    return sorted((e for e in merged.values() if e["pct"] >= min_pct),
                  key=lambda e: e["t_ms"])


def _refresh_calendar(state: Dict[str, Any], min_pct: float,
                      refresh_hours: float, now_ms: int) -> Dict[str, Any]:
    last = int(_num(state.get("fetched_at_ms")))
    if now_ms - last < refresh_hours * _HOUR_MS and state.get("upcoming") is not None:
        return state
    try:
        with urllib.request.urlopen(_INDEX_URL, timeout=90) as resp:
            index = json.load(resp)
        upcoming = _extract_upcoming(index, _load_map(), min_pct, now_ms)
        state["upcoming"] = upcoming
        state["fetched_at_ms"] = now_ms
        logger.info(f"[unlock-recorder] calendar refreshed: {len(upcoming)} upcoming "
                    f"unlocks >= {min_pct}% circ on HL coins (next 45d)")
    except Exception as exc:
        logger.warning(f"[unlock-recorder] calendar refresh failed (keeping stale): {exc}")
        state.setdefault("upcoming", [])
        # back off a full cycle either way so a dead endpoint can't hammer
        state["fetched_at_ms"] = now_ms
    return state


# (book_name, window lower/upper bound in hours before the unlock, horizon)
# 2026-08-30 — the T-1d `unlock_short` arm is gone. Operator directive:
# "nothing should be a recorder". It graded VALIDATED (n=14, +3.41%, mc_p=0.034)
# and the grader's own note on it was "validated but has no bounded capital
# path (recorder//counterfactual)" — a book that can prove itself and still
# never trade is dead weight, and keeping it meant paying to maintain evidence
# nothing could act on.
#
# The run-in arm survives because it HAS a capital path: unlock_short_live.py
# trades exactly this signal and graded VALIDATED at n=14, +3.94% net25,
# mc_p=0.0375. The calendar this module maintains is what feeds it, which is why
# the module stays even though its recorder half does not.
_ARMS = (
    ("unlock_short_runin", 48.0, 72.0, 3.0),  # W-U1 run-in drift: -2.1% T-3d->T
)


def maybe_record(universe: Optional[List[Dict[str, Any]]],
                 config: Dict[str, Any]) -> int:
    """Call once per scan. Records a hypothetical SHORT (stop 15%) the first
    time each unlock event enters each arm's pre-unlock window. Returns number
    of episodes recorded."""
    cfg = (config.get("unlock_recorder") or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    state = _refresh_calendar(
        _load_state(),
        min_pct=float(cfg.get("min_pct_circ", 1.0)),
        refresh_hours=float(cfg.get("refresh_hours", 12.0)),
        now_ms=now_ms,
    )
    mids: Dict[str, float] = {}
    for m in universe or []:
        c = m.get("coin") or ""
        px = _num(m.get("midPx") or m.get("markPx"))
        if c and px > 0:
            mids[c] = px
    seen = state.get("seen") or {}
    n = 0
    for ev in state.get("upcoming") or []:
        t_ms = int(_num(ev.get("t_ms")))
        coin = str(ev.get("coin") or "")
        if not coin:
            continue
        hours_out = (t_ms - now_ms) / _HOUR_MS
        for book, lo_h, hi_h, horizon in _ARMS:
            if not (lo_h <= hours_out < hi_h):
                continue
            key = f"{book}:{coin}:{t_ms // _DAY_MS}"
            if key in seen:
                continue
            px = mids.get(coin)
            if not px:
                continue  # not tradable right now — skip, retry next scan
            shadow_ledger.record(
                book, coin=coin, side="short",
                signal_bar_t=(now_ms // _HOUR_MS) * _HOUR_MS,
                entry_ref_px=px, horizon_days=horizon, stop_pct=15.0,
                meta={"unlock_pct_circ": round(_num(ev.get("pct")), 2),
                      "unlock_t_ms": t_ms, "hours_to_unlock": round(hours_out, 1),
                      "shadow": True},
            )
            seen[key] = now_ms
            n += 1
            logger.info(f"[unlock-recorder] {coin}: unlock {_num(ev.get('pct')):.1f}% circ "
                        f"in {hours_out:.0f}h — {book} shadow SHORT recorded @ {px}")
    if n or seen != (state.get("seen") or {}):
        # prune dedup keys older than 60d so the file can't grow unbounded
        cutoff_day = (now_ms - 60 * _DAY_MS) // _DAY_MS
        state["seen"] = {k: v for k, v in seen.items()
                         if int(k.rsplit(":", 1)[1]) >= cutoff_day}
        _save_state(state)
    return n
