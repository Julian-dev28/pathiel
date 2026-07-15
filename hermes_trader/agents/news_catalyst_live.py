"""W-N3: zero-capital news-catalyst recorder (Lane N spec, 2026-07-11).

W-N replay failed every wire gate (GDELT blind on 8/10 small-cap pairs, n=1
tradeable replay), so per the lane verdict the news edge gets a forward SHADOW
RECORDER, not capital. Every 30 minutes this lane reads the live Google News
coverage-surge signal (coin_catalyst — 2 cached keyless RSS queries per coin,
zero GDELT, zero HL rate-budget impact) for the current scan candidates and
records a hypothetical LONG per read to the unified shadow ledger.

Both breaking and non-breaking reads are recorded on the same coins — the
non-breaking reads ARE the matched null, so the grader's breaking-vs-baseline
split is built into the data.

LIVE FLIP 2026-07-12 (operator order, ahead of the original >=60d gate):
breaking reads (n>=3 recent articles AND surge >= 3x own baseline) open a
bounded $20/1x LONG, 15% stop, 1d hold — the exact recorded geometry, so the
ledger keeps grading the same shape capital trades. Evidence basis was THIN
(n=1 historical replay) at flip time; the pre-committed kill was
news_catalyst.shadow_only=true, mandatory review at 10 episodes if EV25 < 0.

FLIPPED BACK TO SHADOW 2026-07-16 (mandatory review triggered): shadow_status
graded REFUTED at 34 resolved signals, -8.65%/sig @12bps, negative in BOTH
OOS halves (-7.35% / -9.94%) — see `python scripts/shadow_status.py`. Do not
re-flip live without fresh forward evidence overturning that verdict; the
live geometry above (leverage/stop/hold) is kept as-is for the shadow arm so
the ledger keeps grading the exact same trade shape.
Non-breaking reads keep recording as the matched null in both modes.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.dsl_exit import active_position_coins
from hermes_trader.agents.news_catalyst import coin_catalyst
from hermes_trader.agents.rebalancer_owned import get_claims_registry, state_file

logger = logging.getLogger(__name__)

_BOOK_NAME = "news_catalyst"
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_TS_FILE = state_file(".news_catalyst_live_ts.json")
_SEEN_FILE = state_file(".news_catalyst_live_seen.json")
_MAX_COINS_PER_PASS = 8  # keep the RSS pass bounded even on wild scan days


def _last_pass_ms() -> int:
    try:
        raw = json.load(open(_TS_FILE))
        return int(raw.get("ts", 0)) if isinstance(raw, dict) else 0
    except Exception:
        return 0


def _mark_pass(now_ms: int) -> None:
    try:
        with open(_TS_FILE, "w") as fh:
            json.dump({"ts": now_ms}, fh)
    except Exception:
        pass


def _load_seen() -> Dict[str, int]:
    try:
        raw = json.load(open(_SEEN_FILE))
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_seen(seen: Dict[str, int]) -> None:
    try:
        with open(_SEEN_FILE, "w") as fh:
            json.dump(seen, fh, sort_keys=True)
    except Exception:
        pass


def _held_coins(positions) -> set:
    held = set()
    for p in positions or []:
        pos = p.get("position", p) if isinstance(p, dict) else {}
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            szi = 0.0
        if coin and szi != 0:
            held.add(coin)
    try:
        held.update(active_position_coins().keys())
    except Exception:
        pass
    return held


def _analysis(coin: str, rep, cfg: Dict[str, Any]) -> Dict[str, Any]:
    stop_pct = float(cfg.get("stop_pct", 15.0))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_days = float(cfg.get("hold_days", 1.0))
    top = rep.headlines[0].title if rep.headlines else ""
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "LONG", "side": "long",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] BREAKING coverage surge {rep.surge_x:.1f}x "
                      f"(n={rep.n_recent}): {top[:120]}"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 20.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "dsl_exit_override": {
            # Recorded geometry: 15% stop or 1d horizon close, no trail.
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


def _execute_opened(result: Any) -> bool:
    if isinstance(result, dict):
        nested = result.get("result")
        if isinstance(nested, dict):
            return bool(nested.get("executed"))
        if "executed" in result:
            return bool(result.get("executed"))
        if "ok" in result:
            return bool(result.get("ok"))
    return result is None


def maybe_run(config: Dict[str, Any],
              perceptions: Optional[List[Dict[str, Any]]],
              positions: Optional[List[Dict[str, Any]]] = None,
              execute_fn: Optional[Callable] = None) -> int:
    """Call once per scan with the cycle's perceptions. Throttled to one pass
    per scan_interval_min. Records every read; opens bounded LONGs on
    breaking reads when shadow_only=false and an execute_fn is provided.
    Returns rows recorded."""
    cfg = (config.get("news_catalyst") or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    interval_min = float(cfg.get("scan_interval_min", 30.0))
    if now_ms - _last_pass_ms() < interval_min * 60_000:
        return 0
    _mark_pass(now_ms)  # mark first: a failing RSS pass must not retry-storm

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for p in perceptions or []:
        coin = str(p.get("coin") or "")
        if not coin or coin in seen:
            continue
        seen.add(coin)
        if len(seen) > _MAX_COINS_PER_PASS:
            break
        try:
            mid = float(p.get("mid") or 0.0)
        except (TypeError, ValueError):
            mid = 0.0
        if mid <= 0:
            continue
        try:
            rep = coin_catalyst(coin)
        except Exception as exc:
            logger.debug(f"[news-catalyst-live] {coin}: read failed ({exc})")
            continue
        shadow_only = bool(cfg.get("shadow_only", True))
        rows.append({
            "coin": coin, "side": "long",
            "signal_bar_t": (now_ms // _HOUR_MS) * _HOUR_MS,
            "entry_ref_px": mid, "horizon_days": float(cfg.get("hold_days", 1.0)),
            "stop_pct": float(cfg.get("stop_pct", 15.0)),
            "meta": {
                "n_recent": rep.n_recent,
                "surge_x": rep.surge_x,
                "breaking": bool(rep.breaking),
                "top3_titles": [a.title for a in (rep.headlines or [])[:3]],
                "top3_urls": [a.url for a in (rep.headlines or [])[:3]],
                "top3_ages_h": [
                    (round((time.time() - a.seen.timestamp()) / 3600, 1)
                     if a.seen else None)
                    for a in (rep.headlines or [])[:3]
                ],
                "shadow": shadow_only,
                "_rep": rep,   # stripped before recording; live-open handle
            },
        })
    # Recording never carries the live handle.
    n = shadow_ledger.record_many(_BOOK_NAME, [
        {**r, "meta": {k: v for k, v in r["meta"].items() if k != "_rep"}}
        for r in rows
    ])
    if n:
        n_breaking = sum(1 for r in rows if r["meta"]["breaking"])
        logger.info(f"[news-catalyst-live] recorded {n} read(s), {n_breaking} breaking")

    # LIVE arm (2026-07-12 operator flip): breaking reads open bounded longs.
    if bool(cfg.get("shadow_only", True)) or execute_fn is None:
        return n
    breaking_rows = [r for r in rows if r["meta"]["breaking"]]
    if not breaking_rows:
        return n
    opened_seen = _load_seen()
    held = _held_coins(positions)
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked_by_claim = claims.claimed_by_others(_BOOK_NAME)
    max_new = int(cfg.get("max_new_per_cycle", 1))
    opened = 0
    for r in sorted(breaking_rows, key=lambda x: -x["meta"]["surge_x"]):
        if opened >= max_new:
            break
        coin = r["coin"]
        day_key = f"{coin}:{now_ms // _DAY_MS}"
        if day_key in opened_seen or coin in held or coin in blocked_by_claim:
            continue
        if not claims.claim(coin, _BOOK_NAME):
            continue
        try:
            result = execute_fn(_analysis(coin, r["meta"]["_rep"], cfg))
            if _execute_opened(result):
                opened += 1
                opened_seen[day_key] = now_ms
                logger.info(f"[news-catalyst-live] LIVE opened long {coin} "
                            f"(surge {r['meta']['surge_x']:.1f}x)")
            else:
                claims.release(coin, _BOOK_NAME)
                why = (result.get("reason") or result.get("blocked_by")) if isinstance(result, dict) else result
                logger.warning(f"[news-catalyst-live] {coin} not opened: {why}")
        except Exception as exc:
            claims.release(coin, _BOOK_NAME)
            logger.warning(f"[news-catalyst-live] open {coin} failed: {exc}")
    if opened:
        # prune day keys older than 60d
        cutoff = (now_ms - 60 * _DAY_MS) // _DAY_MS
        _save_seen({k: v for k, v in opened_seen.items()
                    if int(k.rsplit(":", 1)[1]) >= cutoff})
        claims.save()
    return n
