"""Catalyst / observation flags on top of a trend read.

Every flag is a pure predicate over numbers already computed upstream, so the
same coin state always produces the same flags. Each flag carries a `kind`
(structure / positioning / event / risk) and a one-line `note` the tab renders
verbatim — no LLM in this path.

Event sources are read-only reuses of state the bot already maintains:
  unlock calendar : .state/.unlock_recorder_state.json (DefiLlama emissions)
  news catalysts  : .state/shadow_ledger/news_catalyst.jsonl
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from services.trend_engine import env

DAY_MS = 86_400_000
_UNLOCK_STATE = os.path.join(env.state_dir(), ".unlock_recorder_state.json")
_NEWS_LEDGER = os.path.join(env.state_dir(), "shadow_ledger", "news_catalyst.jsonl")

# kind -> render priority (lower sorts first on the card)
KINDS = {"event": 0, "structure": 1, "positioning": 2, "risk": 3}


def _flag(code: str, kind: str, note: str, weight: float = 0.0) -> Dict[str, Any]:
    return {"code": code, "kind": kind, "note": note, "weight": round(weight, 3)}


# ── event sources ────────────────────────────────────────────────────────────


def load_unlocks(path: str = _UNLOCK_STATE,
                 now_ms: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
    """coin -> upcoming unlock events inside the next 8 days (nearest first).

    Reads the recorder's own cache; never fetches. A stale or missing file
    means no unlock flags, which is the correct degradation — a wrong unlock
    date is worse than none.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        with open(path) as fh:
            state = json.load(fh)
    except Exception:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for e in state.get("upcoming") or []:
        try:
            t_ms = int(e["t_ms"])
            coin = str(e["coin"])
            pct = float(e["pct"])
        except Exception:
            continue
        if not (now_ms <= t_ms <= now_ms + 8 * DAY_MS):
            continue
        out.setdefault(coin, []).append(
            {"t_ms": t_ms, "pct": pct, "hours": round((t_ms - now_ms) / 3_600_000, 1)})
    for rows in out.values():
        rows.sort(key=lambda r: r["t_ms"])
    return out


def load_news(path: str = _NEWS_LEDGER, now_ms: Optional[int] = None,
              window_h: float = 72.0, cap: int = 4000) -> Dict[str, Dict[str, Any]]:
    """coin -> freshest news-catalyst row inside `window_h`.

    The news recorder writes one row per coverage read; only the newest per
    coin matters for a weekly trend card.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    cutoff = now_ms - int(window_h * 3_600_000)
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path) as fh:
            lines = fh.readlines()[-cap:]
    except Exception:
        return {}
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        coin = row.get("coin")
        ts = int(row.get("ts") or row.get("opened_ms") or 0)
        if not coin or ts < cutoff:
            continue
        prev = out.get(coin)
        if prev is None or ts >= prev.get("ts", 0):
            out[coin] = {"ts": ts, "breaking": bool(row.get("breaking")),
                         "count": row.get("article_count") or row.get("count"),
                         "title": (row.get("titles") or [None])[0] if row.get("titles") else row.get("title")}
    return out


# ── the flag pass ────────────────────────────────────────────────────────────


def flags_for(read: Dict[str, Any], *, unlocks: Optional[List[Dict[str, Any]]] = None,
              news: Optional[Dict[str, Any]] = None,
              funding_z: Optional[float] = None) -> List[Dict[str, Any]]:
    """All flags that fire for one coin read.

    `weight` is a directional hint in [-1, 1] (positive = supports up) used
    only to explain the card; it never feeds the forecast, which stays purely
    price-driven so the two reads can disagree visibly.
    """
    out: List[Dict[str, Any]] = []
    rp = read.get("range_pos_7d")
    rp30 = read.get("range_pos_30d")
    eff = float(read.get("efficiency") or 0.0)
    r7 = float(read.get("ret_7d") or 0.0)
    stack = read.get("ema_stack")
    stk = int(read.get("streak_days") or 0)
    atr = float(read.get("atr_pct") or 0.0)
    atr30 = float(read.get("atr_pct_30d") or 0.0)
    volr = read.get("volume_ratio")
    resid = read.get("resid_7d")
    corr = read.get("corr_btc")

    # structure
    if rp is not None and rp >= 0.97 and r7 > 0:
        out.append(_flag("BREAKOUT_7D", "structure", "closing at the top of its 7d range", 0.5))
    if rp is not None and rp <= 0.03 and r7 < 0:
        out.append(_flag("BREAKDOWN_7D", "structure", "closing at the bottom of its 7d range", -0.5))
    if rp30 is not None and rp30 >= 0.98:
        out.append(_flag("HIGH_30D", "structure", "at a 30-day high", 0.6))
    if rp30 is not None and rp30 <= 0.02:
        out.append(_flag("LOW_30D", "structure", "at a 30-day low", -0.6))
    if stack == "bull":
        out.append(_flag("EMA_STACK_BULL", "structure", "price > 7d EMA > 21d EMA", 0.35))
    if stack == "bear":
        out.append(_flag("EMA_STACK_BEAR", "structure", "price < 7d EMA < 21d EMA", -0.35))
    if abs(stk) >= 4:
        out.append(_flag(f"STREAK_{abs(stk)}D", "structure",
                         f"{abs(stk)} straight {'up' if stk > 0 else 'down'} days",
                         0.2 if stk > 0 else -0.2))
    if eff < 0.15 and abs(r7) >= 8.0:
        out.append(_flag("CHOP_TRAP", "risk",
                         f"{r7:+.1f}% on the week but efficiency {eff:.2f} — round trip, not trend", 0.0))

    # positioning
    if funding_z is not None and funding_z >= 2.0:
        out.append(_flag("FUNDING_CROWDED_LONG", "positioning",
                         f"funding z={funding_z:+.1f} — longs paying up", -0.4))
    if funding_z is not None and funding_z <= -2.0:
        out.append(_flag("FUNDING_CROWDED_SHORT", "positioning",
                         f"funding z={funding_z:+.1f} — shorts paying up", 0.4))
    if volr is not None and volr >= 2.0:
        out.append(_flag("VOLUME_SURGE", "positioning",
                         f"24h volume {volr:.1f}x its 7d average", 0.2))
    if volr is not None and volr <= 0.4:
        out.append(_flag("VOLUME_DRY", "positioning",
                         f"24h volume {volr:.1f}x its 7d average — no participation", 0.0))

    # risk
    if atr30 > 0 and atr >= atr30 * 1.6:
        out.append(_flag("VOL_EXPANSION", "risk",
                         f"ATR {atr:.1f}% vs {atr30:.1f}% baseline — ranges widening", 0.0))
    if r7 >= 25.0:
        out.append(_flag("OVEREXTENDED_UP", "risk", f"{r7:+.0f}% in 7 days — late to chase", -0.3))
    if r7 <= -25.0:
        out.append(_flag("OVEREXTENDED_DOWN", "risk", f"{r7:+.0f}% in 7 days — capitulation range", 0.3))

    # relative
    if resid is not None and resid >= 10.0:
        out.append(_flag("LEADER", "structure", f"{resid:+.1f}% vs BTC after beta — leading the tape", 0.4))
    if resid is not None and resid <= -10.0:
        out.append(_flag("LAGGARD", "structure", f"{resid:+.1f}% vs BTC after beta — bleeding relative", -0.4))
    if corr is not None and abs(corr) < 0.25:
        out.append(_flag("DECOUPLED", "structure", f"corr to BTC {corr:+.2f} — own story", 0.0))

    # events
    for ev in (unlocks or [])[:2]:
        out.append(_flag("UNLOCK", "event",
                         f"{ev['pct']:.1f}% of circ unlocks in {ev['hours']:.0f}h", -0.5))
    if news:
        age_h = max(0.0, (time.time() * 1000 - news.get("ts", 0)) / 3_600_000)
        tag = "breaking" if news.get("breaking") else "coverage"
        out.append(_flag("NEWS", "event",
                         f"{tag} read {age_h:.0f}h ago"
                         + (f": {str(news.get('title'))[:80]}" if news.get("title") else ""),
                         0.0))

    out.sort(key=lambda f: (KINDS.get(f["kind"], 9), -abs(f["weight"])))
    return out


def flag_bias(flags: List[Dict[str, Any]]) -> float:
    """Net directional hint of a flag set, clipped to [-1, 1].

    Reported next to the price forecast so a disagreement between "what the
    chart says" and "what is happening around the chart" is visible instead of
    averaged away.
    """
    s = sum(float(f.get("weight") or 0.0) for f in flags)
    return max(-1.0, min(1.0, s / 2.0))
