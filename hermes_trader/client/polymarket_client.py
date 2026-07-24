"""Polymarket read-only client — prediction-market data (2026-07-24, curiosity/research).

Public APIs, NO auth: Gamma (markets/metadata), CLOB (prices/history). Prediction markets
are binary YES/NO contracts priced 0..1 that resolve to 0 or 1. Our candle-momentum engines
do NOT directly transfer (these aren't trending assets — they're probability estimates
converging to a binary). What MIGHT transfer / is worth testing:
  - favorite-longshot bias: longshots (low p) historically OVERpriced -> systematically fade
    them (buy NO); favorites underpriced. The most documented prediction-market edge.
  - news overreaction fade (our extreme_fade logic, on probability jumps).
Read-only. Best-effort. This is research scaffolding, not a live book.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def _get(base: str, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    for attempt in range(3):
        try:
            r = requests.get(base + path, params=params or {}, timeout=30)
            if r.status_code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            if attempt < 2:
                time.sleep(1); continue
            return None
    return None


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def markets(limit: int = 100, active: bool = True, closed: bool = False,
            order: str = "volume", ascending: bool = False) -> List[Dict[str, Any]]:
    raw = _get(GAMMA, "/markets", {"limit": limit, "active": str(active).lower(),
                                   "closed": str(closed).lower(), "order": order,
                                   "ascending": str(ascending).lower()})
    return raw if isinstance(raw, list) else (raw or {}).get("data", [])


def resolved_markets(limit: int = 500) -> List[Dict[str, Any]]:
    """Closed/resolved markets — for calibration/backtest (did the favorite win?)."""
    raw = _get(GAMMA, "/markets", {"limit": limit, "closed": "true", "order": "volume",
                                   "ascending": "false"})
    return raw if isinstance(raw, list) else (raw or {}).get("data", [])


def parse_market(m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise a gamma market -> {question, yes_price, volume, liquidity, end, resolved,
    winner (1=YES won, 0=NO won, None=open)}."""
    try:
        outs = json.loads(m.get("outcomes") or "[]")
        prices = [_f(p) for p in json.loads(m.get("outcomePrices") or "[]")]
    except Exception:
        return None
    if len(prices) < 2:
        return None
    yes_i = 0
    for i, o in enumerate(outs):
        if str(o).lower() == "yes":
            yes_i = i; break
    yes_p = prices[yes_i]
    winner = None
    if m.get("closed"):
        # resolved price ~1.0 marks the winning outcome
        winner = 1 if prices[yes_i] > 0.5 else 0
    return {"question": m.get("question"), "yes_price": yes_p,
            "volume": _f(m.get("volume")), "liquidity": _f(m.get("liquidity")),
            "end": m.get("endDate"), "resolved": bool(m.get("closed")), "winner": winner,
            "token_ids": m.get("clobTokenIds")}


def price_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> List[Dict[str, float]]:
    raw = _get(CLOB, "/prices-history", {"market": token_id, "interval": interval, "fidelity": fidelity})
    if isinstance(raw, dict):
        h = raw.get("history")
        return h if isinstance(h, list) else []
    return raw if isinstance(raw, list) else []


if __name__ == "__main__":
    ms = markets(limit=5)
    print(f"top markets: {len(ms)}")
    for m in ms[:3]:
        p = parse_market(m)
        if p:
            print(f"  YES {p['yes_price']:.2f}  ${p['volume']/1e3:.0f}k vol — {p['question'][:60]}")
    rs = resolved_markets(limit=5)
    print(f"resolved sample: {len(rs)}")
