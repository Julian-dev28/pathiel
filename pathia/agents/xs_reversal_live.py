"""xs_reversal — short the 3-day cross-sectional winners, where funding is awake.

LIVE FROM THE FIRST BAR, by operator instruction 2026-09-04. There is no shadow
arm and no promotion ladder: the evidence was gathered before the book existed
rather than after, which is the order the doctrine asks for anyway.

THE EDGE
--------
Measured on the data_logger panel (280 coins, funding + OI + price, ~7.7
snapshots a day since 2026-06-26), quoted from the short's side and net of
funding paid and 25bps of round trip. See research/alpha_swarm/findings/
W-XSR1_cross_sectional_reversal.md for the full working.

  short top decile of 3d return          +1.373%/trade  n=5106  p 0.0000
  ... and funding awake >= 67% of 7d      +2.474%/trade  n=1995  p 0.0000

The awake filter is the part that matters and it is not a liquidity proxy:
inside the THIN half alone, awake +2.776% against dead -0.754%.

WHY IT SHOULD WORK, STATED SO IT CAN BE WRONG
----------------------------------------------
The trade is that an extended move was driven by positioning that has to
unwind. Funding only moves when longs and shorts disagree enough to pay each
other, so a coin pinned at Hyperliquid's 1.25e-05 baseline has no crowded side
to unwind and nothing to give back — which is exactly what the dead bucket
shows at -0.443%.

If that mechanism is right, the book dies when funding stops discriminating:
either the venue changes how the baseline works, or a regime arrives where the
biggest movers are driven by spot flow rather than perp positioning. The
nightly grader in autonomous_cycle.py is what catches that, and it demotes on
forward evidence without asking anyone.

WHAT IS DELIBERATELY CONSERVATIVE
----------------------------------
The study ranked across the whole panel. This ranks across the coins actually
visible in the live universe this cycle, which is a smaller and more liquid set.
That makes the live book's decile a subset of the tested one rather than a
superset — a narrower trade than the one measured, never a wider one.

The honest discount: the awake filter was FOUND, not predicted. It fell out of
a test of something else (H4, funding direction, which failed). Found results
earn a forward record before they earn confidence, which is what the ledger
below is for — not a shadow arm, a receipt.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from pathia.agents import shadow_ledger
from pathia.agents.book_helpers import bounded_exit_override
from pathia.agents.rebalancer_owned import get_claims_registry
from pathia.models.types import BookAnalysis

logger = logging.getLogger(__name__)

_BOOK_NAME = "xs_reversal"
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_BASELINE_F = 1.25e-05          # Hyperliquid's resting funding rate

# Defaults mirror the tested specification. Every one is overridable from
# config so the book can be tuned without a deploy, and every one that changes
# the trade is named in the finding.
_D = {
    "lookback_d": 3.0,          # momentum window
    "awake_lookback_d": 7.0,    # window for judging the funding market awake
    "awake_min_frac": 0.67,     # fraction of that window spent off baseline
    "top_pct": 90.0,            # cross-sectional percentile to short above
    "min_volume_usd": 1_000_000.0,
    "hold_hours": 24.0,
    "stop_pct": 15.0,
    "min_universe": 20,         # a decile of 8 coins is not a decile
}


def _panel_path() -> str:
    """The data_logger's panel, read through the module that owns it.

    Deliberately NOT a second state-file declaration for the same name: two
    modules naming one path independently is how a rename in one silently
    orphans the other, and it reads as a second writer to anyone auditing state
    ownership. test_no_two_modules_share_a_state_file_path enforces that, and
    it is right to. data_logger writes this file; this book only ever reads it.
    """
    from pathia.agents.data_logger import _LOG_FILE
    return _LOG_FILE


def _load_panel(now_ms: int, days: float) -> Dict[str, List[Dict[str, Any]]]:
    """coin -> [{ts, px, f, v}] over the trailing `days`, oldest first.

    Reads the data_logger's own file. Best-effort: a missing or half-written
    panel means this book takes no trade this cycle, which is the correct
    failure — never an entry on partial data.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    path = _panel_path()
    if not os.path.exists(path):
        return out
    cutoff = now_ms - int(days * _DAY_MS)
    try:
        with open(path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue          # a torn last line while the logger writes
                ts = int(snap.get("ts") or 0)
                if ts < cutoff:
                    continue
                for row in snap.get("rows") or []:
                    c = row.get("c")
                    if not c:
                        continue
                    try:
                        out.setdefault(c, []).append({
                            "ts": ts,
                            "px": float(row.get("px") or 0.0),
                            "f": float(row.get("f")) if row.get("f") is not None else None,
                            "v": float(row.get("v") or 0.0),
                        })
                    except (TypeError, ValueError):
                        continue
    except OSError:
        return {}
    for rows in out.values():
        rows.sort(key=lambda r: r["ts"])
    return out


def _awake_fraction(rows: List[Dict[str, Any]], now_ms: int, days: float) -> Optional[float]:
    """Share of the trailing window this coin spent off the venue baseline.

    Trailing only. Judging it on the latest tick alone would let one print
    decide, and a coin that woke up an hour ago has not demonstrated the
    two-sided positioning the trade depends on.
    """
    cutoff = now_ms - int(days * _DAY_MS)
    fs = [r["f"] for r in rows if r["ts"] >= cutoff and r["f"] is not None]
    if len(fs) < 8:
        return None
    return sum(1 for f in fs if abs(f - _BASELINE_F) > 1e-9) / len(fs)


def _momentum(rows: List[Dict[str, Any]], now_ms: int, days: float) -> Optional[float]:
    cutoff = now_ms - int(days * _DAY_MS)
    past = [r for r in rows if r["ts"] <= cutoff and r["px"] > 0]
    latest = [r for r in rows if r["px"] > 0]
    if not past or not latest:
        return None
    p0, p1 = past[-1]["px"], latest[-1]["px"]
    return (p1 / p0 - 1.0) * 100.0 if p0 > 0 else None


def _analysis(coin: str, mom: float, awake: float, cfg: Dict[str, Any]) -> BookAnalysis:
    stop_pct = float(cfg.get("stop_pct", _D["stop_pct"]))
    leverage = max(1, int(cfg.get("leverage", 1)))
    hold_h = float(cfg.get("hold_hours", _D["hold_hours"]))
    return {
        "id": str(uuid.uuid4()), "coin": coin,
        "verdict": "SHORT", "side": "short",
        "confidence": 0.99, "entry_px": 0.0, "stop_px": 0.0, "tp_px": 0.0,
        "reasoning": (f"[{_BOOK_NAME}] +{mom:.1f}% over 3d puts this in the top "
                      f"decile of the live universe, and funding has been off "
                      f"baseline {awake*100:.0f}% of the last 7d — W-XSR1 "
                      f"cross-sectional reversal, {hold_h:.0f}h hold"),
        "news_risk": "none", "ai_down": False, "created_at": int(time.time() * 1000),
        "composite_score": 0.0, "strategy_book": _BOOK_NAME,
        "strategy_book_notional": float(cfg.get("notional_usd", 11.0)),
        "leverage_override": leverage,
        "backup_sl_pct_override": stop_pct,
        "tp_scale_fraction_override": 0.0,
        "min_short_volume_usd_override": float(cfg.get("min_volume_usd",
                                                       _D["min_volume_usd"])),
        # Flat hold, no trail. W-X1 measured exit overlays across four validated
        # families and found they buy win rate by paying EV; the one exception
        # was a book whose edge decayed intraday, which this is not.
        "dsl_exit_override": bounded_exit_override(stop_pct, leverage, hold_h * 60.0),
    }


def maybe_run(config: Dict[str, Any],
              universe: Optional[List[Dict[str, Any]]],
              positions: Optional[List[Dict[str, Any]]],
              execute_fn: Callable[[BookAnalysis], Any]) -> Optional[Dict[str, Any]]:
    """Rank the live universe by 3d return and short the top decile.

    Records every candidate it considered to the ledger whether or not capital
    was available, so the forward record is the strategy's and not the account
    balance's. A book that only logs the trades it could afford grades itself on
    a sample selected by margin.
    """
    cfg = config.get(_BOOK_NAME) or {}
    if not bool(cfg.get("enabled", False)) or bool(cfg.get("shadow_only", False)):
        return None

    now_ms = int(time.time() * 1000)
    look_d = float(cfg.get("lookback_d", _D["lookback_d"]))
    awake_d = float(cfg.get("awake_lookback_d", _D["awake_lookback_d"]))
    awake_min = float(cfg.get("awake_min_frac", _D["awake_min_frac"]))
    top_pct = float(cfg.get("top_pct", _D["top_pct"]))
    min_vol = float(cfg.get("min_volume_usd", _D["min_volume_usd"]))
    max_new = int(cfg.get("max_new_per_cycle", 1))
    min_uni = int(cfg.get("min_universe", _D["min_universe"]))

    tradable = {str(m.get("coin") or "") for m in (universe or []) if m.get("coin")}
    if not tradable:
        return None

    panel = _load_panel(now_ms, max(look_d, awake_d) + 1.0)
    if not panel:
        return None

    scored: List[Dict[str, Any]] = []
    for coin, rows in panel.items():
        if coin not in tradable:
            continue
        if not rows or rows[-1]["v"] < min_vol:
            continue
        mom = _momentum(rows, now_ms, look_d)
        awake = _awake_fraction(rows, now_ms, awake_d)
        if mom is None or awake is None:
            continue
        scored.append({"coin": coin, "mom": mom, "awake": awake,
                       "px": rows[-1]["px"]})

    # A decile is only a decile against a real cross-section. Ranking eight
    # coins and shorting the top one is not this strategy, it is a coin flip
    # wearing its name.
    if len(scored) < min_uni:
        logger.info("[%s] universe %d < %d — no rank this cycle",
                    _BOOK_NAME, len(scored), min_uni)
        return None

    scored.sort(key=lambda r: r["mom"])
    n = len(scored)
    for rank, r in enumerate(scored):
        r["mom_pct"] = rank / (n - 1) * 100.0

    candidates = [r for r in scored
                  if r["mom_pct"] >= top_pct and r["awake"] >= awake_min]
    if not candidates:
        return None
    candidates.sort(key=lambda r: -r["mom"])       # most extended first

    # The ledger takes every candidate. This is the evidence record, not a
    # shadow arm: it exists so the nightly grader can demote this book on its
    # own forward numbers.
    for c in candidates:
        shadow_ledger.record(
            _BOOK_NAME, coin=c["coin"], side="short",
            entry_ref_px=c["px"],
            horizon_days=float(cfg.get("hold_hours", _D["hold_hours"])) / 24.0,
            stop_pct=float(cfg.get("stop_pct", _D["stop_pct"])),
            meta={"mom_3d": round(c["mom"], 2),
                  "mom_pct": round(c["mom_pct"], 1),
                  "awake_frac": round(c["awake"], 3),
                  "universe": n},
        )

    held = {str((p.get("position") or {}).get("coin") or "") for p in (positions or [])}
    claims = get_claims_registry()
    claims.prune_to(held, _BOOK_NAME)
    blocked = claims.claimed_by_others(_BOOK_NAME)

    opened = 0
    for c in candidates:
        if opened >= max_new:
            break
        coin = c["coin"]
        if coin in held or coin in blocked or not claims.claim(coin, _BOOK_NAME):
            continue
        try:
            result = execute_fn(_analysis(coin, c["mom"], c["awake"], cfg))
        except Exception as exc:
            claims.release(coin, _BOOK_NAME)
            logger.warning("[%s] %s execute raised: %s", _BOOK_NAME, coin, exc)
            continue
        if isinstance(result, dict) and result.get("executed"):
            opened += 1
            logger.info("[%s] SHORT %s (+%.1f%% 3d, awake %.0f%%)",
                        _BOOK_NAME, coin, c["mom"], c["awake"] * 100)
        else:
            claims.release(coin, _BOOK_NAME)
            reason = (result or {}).get("blocked_by") if isinstance(result, dict) else None
            logger.info("[%s] %s not opened: %s", _BOOK_NAME, coin, reason)
    claims.save()
    return {"book": _BOOK_NAME, "candidates": len(candidates),
            "universe": n, "opened": opened}
