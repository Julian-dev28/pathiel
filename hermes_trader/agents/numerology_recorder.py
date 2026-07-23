"""The numerology 'money formula' as a real book — SHADOW by default, armable by flag.

day_root_odd: reduce the day-of-month to a single digit; ODD -> LONG ETH, EVEN -> SHORT.
Top of 168 numerology formulas in-sample (W-FUN1) at +11.35%/trade — and PROVEN NOISE:
  - no transfer: +210% ETH vs +6% BTC vs mid on SOL, same formula (real edges transfer);
  - 1000-coin-flip null median -86..-89%; the formula is the top-4% luck tail; inverse -> $6;
  - all 40 coins -> $0 all-in 25x; ETH/BTC/SOL all liquidated the SAME day (a correlated dump);
  - p=0.009 fails the multiple-comparisons bar (0.05/168 = 0.0003).

DEFAULT shadow_only=true -> records the daily call to the ledger, ZERO capital, and returns.
It is wired as a full book so that setting `numerology_eth.shadow_only=false` (a deliberate
operator action) ARMS it: from then on it claims ETH and routes a LONG/SHORT through the
SAME executor + safety gates as every other book, at the configured leverage (40x) and
equity_frac (0.5). ⚠️ Armed, this bets ~50% of equity at 40x (liq at 2.5%) on a coin flip
with a proven -86% expected outcome. The author will not flip it. Promotion via shadow_status
like any book — it will not promote.
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

logger = logging.getLogger(__name__)

_BOOK = "numerology_eth"
_COIN = "ETH"
_STATE_FILE = state_file(".numerology_recorder_state.json")


def _reduce(n: int) -> int:
    while n > 9:
        n = sum(int(d) for d in str(n))
    return n


def day_root_odd_dir(dt: datetime) -> int:
    """+1 LONG if the day-of-month digit-root is odd, else -1 SHORT."""
    return 1 if _reduce(dt.day) % 2 == 1 else -1


def _load_state() -> Dict[str, Any]:
    try:
        raw = json.load(open(_STATE_FILE))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _eth_mid(universe: Optional[List[Dict[str, Any]]]) -> float:
    for m in universe or []:
        if str(m.get("coin")) == _COIN:
            try:
                return float(m.get("midPx") or m.get("markPx") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


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


def _analysis(direction: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
    leverage = max(1, int(cfg.get("leverage", 40)))
    equity_frac = float(cfg.get("equity_frac", 0.5))
    hold_days = float(cfg.get("horizon_days", 1.0))
    liq_pct = 100.0 / leverage
    # disaster stop strictly INSIDE liquidation so the book stops instead of liquidating
    stop_pct = min(float(cfg.get("stop_pct", 0.9 * liq_pct)), 0.95 * liq_pct)
    side = "long" if direction > 0 else "short"
    return {
        "id": str(uuid.uuid4()), "coin": _COIN,
        "verdict": side.upper(), "side": side,
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK}] day_root_odd {side} ETH — KNOWN NOISE paper/degen book "
                      f"(root {_reduce(datetime.now(timezone.utc).day)})"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK,
        # equity-fraction sizing: notional = dex_equity * equity_frac * leverage
        "strategy_book_equity_frac_override": equity_frac,
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "dsl_exit_override": {
            "max_loss_pct": stop_pct,
            "max_loss_roe_pct": stop_pct * leverage,
            "protect_pct": 9999.0,
            "retrace_threshold": 0.5,
            "hard_timeout_minutes": hold_days * 1440.0,
            "breakeven_trigger_pct": 0.0,
            "breakeven_lock_pct": 0.0,
            "stale_flat_timeout_minutes": 0.0,
            "consecutive_breaches_required": 1,
            "atr_stop": {"enabled": False},
            "noise_band": {"enabled": False},
        },
    }


def maybe_record(universe: Optional[List[Dict[str, Any]]],
                 config: Dict[str, Any],
                 positions: Optional[List[Dict[str, Any]]] = None,
                 execute_fn: Optional[Callable] = None) -> int:
    """Once per UTC day at/after `hour`: record the day_root_odd ETH call to the ledger
    (always, zero capital). If shadow_only=false AND an execute_fn is provided, ALSO claim
    ETH and route the trade live through the executor. Returns 1 if a new day fired."""
    cfg = (config.get(_BOOK) or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now = datetime.now(timezone.utc)
    if now.hour < int(cfg.get("hour", 14)):
        return 0
    today = now.date().isoformat()
    state = _load_state()
    if state.get("last_day") == today:
        return 0

    direction = day_root_odd_dir(now)
    side = "long" if direction > 0 else "short"
    leverage = int(cfg.get("leverage", 40))
    equity_frac = float(cfg.get("equity_frac", 0.5))
    eth_mid = _eth_mid(universe)
    shadow_only = bool(cfg.get("shadow_only", True))

    # 1) ALWAYS record (zero capital) — the forward proof-of-noise ledger
    shadow_ledger.record(
        _BOOK, coin=_COIN, side=side,
        signal_bar_t=int(now.timestamp() * 1000), ts=int(now.timestamp() * 1000),
        entry_ref_px=eth_mid, horizon_days=float(cfg.get("horizon_days", 1.0)),
        meta={"scheme": "day_root_odd", "day_root": _reduce(now.day),
              "hour": int(cfg.get("hour", 14)), "shadow": shadow_only,
              "leverage": leverage, "equity_frac": equity_frac,
              "liq_pct": round(100.0 / leverage, 3),
              "note": "known-noise (W-FUN1); will not promote"},
    )
    state["last_day"] = today
    _save_state(state)

    # 2) SHADOW default: record only, never trade
    if shadow_only or execute_fn is None:
        logger.info(f"[numerology-eth] PAPER {side.upper()} ETH @ {leverage}x, "
                    f"{equity_frac:.0%} equity (root {_reduce(now.day)}) — SHADOW, zero capital")
        return 1

    # 3) ARMED (operator flipped shadow_only=false): claim ETH, route live
    logger.warning(f"[numerology-eth] ARMED — routing LIVE {side.upper()} ETH @ {leverage}x "
                   f"on KNOWN-NOISE formula (root {_reduce(now.day)})")
    held = _held(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK)
    if _COIN in claims.claimed_by_others(_BOOK):
        logger.info(f"[numerology-eth] {_COIN} claimed by another book — skip")
        return 1
    if not claims.claim(_COIN, _BOOK):
        return 1
    try:
        result = execute_fn(_analysis(direction, cfg))
        executed = bool(result.get("executed")) if isinstance(result, dict) else result is None
        if not executed:
            claims.release(_COIN, _BOOK)
            detail = result.get("reason") if isinstance(result, dict) else result
            logger.info(f"[numerology-eth] not executed ({detail}) — claim released")
    except Exception as exc:  # noqa: BLE001
        claims.release(_COIN, _BOOK)
        logger.warning(f"[numerology-eth] execute failed: {exc}")
    finally:
        claims.save()
    return 1


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    d = day_root_odd_dir(now)
    print(f"{now.date()} day-root {_reduce(now.day)} -> {'LONG' if d > 0 else 'SHORT'} ETH (paper)")
