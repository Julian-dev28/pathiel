"""uw_flow_xs — cross-sectional options-flow book on xyz tokenized equities (W-UW1/2).

Edge (validated 2026-07-23, research/alpha_swarm/hypotheses/W-UW1,W-UW2): net options
flow on a US equity LEADS its spot. Ranking the xyz-equity universe by Unusual Whales net
call-minus-put VOLUME and going LONG the most-bullish-flow names / SHORT the most-bearish
graded net25 +1.9%/leg (fwd1) and +2.0%/leg (fwd5), BOTH OOS halves positive, matched
same-day random-null p=0.0005, n=41 rebalances over 14 names. net_premium confirmed the
same edge independently (+1.7%, p=0.0005). This is the strongest signal in the book.

BOUNDED go-live (operator authority 2026-07-23): $20/leg, 3x, k=2 per side, 5d hold, 20%
disaster stop. It rests on ONE ~2-month tape (90d UW lookback) — scale only after it holds
forward. KILL: cumulative forward EV25 < 0 over 12 rebalances -> shadow_only.

Data: Unusual Whales API via hermes_trader/client/uw_client (UW_API_KEY in .env.local).
Once per UTC day. Never raises into the loop.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file
from hermes_trader.client import uw_client as uw

logger = logging.getLogger(__name__)

_BOOK = "uw_flow_xs"
_STATE_FILE = state_file(".uw_flow_xs_state.json")


def _load_state() -> Dict[str, Any]:
    try:
        raw = json.load(open(_STATE_FILE))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(s: Dict[str, Any]) -> None:
    try:
        json.dump(s, open(_STATE_FILE, "w"))
    except Exception:
        pass


def _held(positions: Optional[List[Dict[str, Any]]]) -> set:
    out = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = str(pos.get("coin") or "")
        try:
            szi = float(pos.get("szi") or 0.0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            out.add(coin)
    return out


def _equity_coins(universe, min_vol: float) -> List[str]:
    """Liquid xyz-equity coins from the live universe (exclude index/basket tickers)."""
    skip = {"XYZ100", "SP500", "PURRDAT", "DRAM"}
    out = []
    for m in universe or []:
        coin = str(m.get("coin") or "")
        if not coin.startswith("xyz:"):
            continue
        tk = coin.split(":", 1)[1]
        if tk in skip:
            continue
        try:
            vol = float(m.get("dayNtlVlm") or m.get("dayNtlVolume") or 0.0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol and vol < min_vol:
            continue
        out.append(coin)
    return out


def _signal(coin: str) -> Optional[float]:
    """UW net call-minus-put volume for the underlying, normalised by gross volume."""
    tk = coin.split(":", 1)[1]
    np = uw.net_prem_daily(tk)
    if not np:
        return None
    gross = np.get("call_volume", 0.0) + np.get("put_volume", 0.0) + 1.0
    return np["net_volume"] / gross


def _analysis(coin: str, side: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    lev = max(1, int(cfg.get("leverage", 3)))
    stop = float(cfg.get("stop_pct", 20.0))
    hold = float(cfg.get("hold_days", 5.0))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": side.upper(), "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": f"[{_BOOK}] {side} — UW net-flow rank (W-UW1/2, +2%/leg p=0.0005)",
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK,
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": lev,
        "backup_sl_pct_override": stop,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("min_volume_usd", 250_000.0)),
        "dsl_exit_override": {
            "max_loss_pct": stop, "max_loss_roe_pct": stop * lev,
            "protect_pct": 9999.0, "retrace_threshold": 1.0,
            "hard_timeout_minutes": hold * 1440.0,
            "breakeven_trigger_pct": 0.0, "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0, "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False}, "noise_band": {"enabled": False},
        },
    }


def maybe_run(config: Dict[str, Any], universe: Optional[List[Dict[str, Any]]],
              positions: Optional[List[Dict[str, Any]]] = None,
              execute_fn: Optional[Callable] = None) -> int:
    """Once per UTC day: rank xyz equities by UW net-flow, record + (if live) open the
    top-k long / bottom-k short. Returns legs recorded."""
    cfg = (config.get(_BOOK) or {})
    if not bool(cfg.get("enabled", True)) or not uw.has_key():
        return 0
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    state = _load_state()
    if state.get("last_day") == today:
        return 0

    k = int(cfg.get("k_per_leg", 2))
    coins = _equity_coins(universe, float(cfg.get("min_volume_usd", 250_000.0)))
    scores: Dict[str, float] = {}
    for c in coins[:int(cfg.get("max_scan", 20))]:
        s = _signal(c)
        if s is not None:
            scores[c] = s
        time.sleep(0.15)
    if len(scores) < 2 * k:
        logger.info(f"[uw-flow-xs] only {len(scores)} flow reads — need {2*k}; skip")
        return 0

    ranked = sorted(scores, key=lambda c: -scores[c])
    longs, shorts = ranked[:k], ranked[-k:]
    shadow_only = bool(cfg.get("shadow_only", False))
    now_ms = int(now.timestamp() * 1000)

    # 1) record every leg (zero-capital forward proof)
    for side, names in (("long", longs), ("short", shorts)):
        for c in names:
            shadow_ledger.record(_BOOK, coin=c, side=side, signal_bar_t=now_ms, ts=now_ms,
                                 horizon_days=float(cfg.get("hold_days", 5.0)),
                                 stop_pct=float(cfg.get("stop_pct", 20.0)),
                                 meta={"signal": round(scores[c], 5), "shadow": shadow_only,
                                       "rank": "top" if side == "long" else "bottom"})
    state["last_day"] = today
    _save_state(state)
    logger.info(f"[uw-flow-xs] {'SHADOW' if shadow_only else 'LIVE'} — "
                f"L {longs} / S {shorts} (of {len(scores)} flow reads)")

    if shadow_only or execute_fn is None:
        return 2 * k

    # 2) LIVE: claim + open each leg (bounded)
    held = _held(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK)
    blocked = claims.claimed_by_others(_BOOK)
    for side, names in (("long", longs), ("short", shorts)):
        for c in names:
            if c in held or c in blocked:
                continue
            if not claims.claim(c, _BOOK):
                continue
            try:
                res = execute_fn(_analysis(c, side, cfg))
                if isinstance(res, dict) and res.get("executed"):
                    logger.info(f"[uw-flow-xs] LIVE opened {side} {c} (flow {scores[c]:+.3f})")
                else:
                    claims.release(c, _BOOK)
            except Exception as exc:  # noqa: BLE001
                claims.release(c, _BOOK)
                logger.warning(f"[uw-flow-xs] open {side} {c} failed: {exc}")
    claims.save()
    return 2 * k
