"""Forward-shadow of the VOL 2.0x ATR stop (the one vol-stop variant that survived the
15m backtest, but was NOT OOS-robust + worse-DD). Paper-tracks each live entry with the
WIDER ATR stop, on the loop's per-cycle mark prices, PAST the live exit — so it captures
whether the wider stop would have held through noise and recovered. Logs shadow-ROE vs
live-ROE per trade. NO live effect. This resolves the granularity ambiguity the offline
backtest couldn't (the live loop sees finer ticks than 15m candles). Flag-gated +
hot-read; any error degrades to no-op.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
STORE = os.path.join(_ROOT, ".volstop-shadow.json")
LOG = os.path.join(_ROOT, ".volstop-shadow.jsonl")


def _load() -> Dict[str, Any]:
    try:
        return json.load(open(STORE))
    except Exception:
        return {}


def _save(d: Dict[str, Any]) -> None:
    try:
        json.dump(d, open(STORE, "w"))
    except Exception:
        pass


def record_entry(coin: str, entry_px: float, side: str, leverage: float,
                 entry_atr_pct: float, config: Dict[str, Any]) -> None:
    """Open a shadow paper-position with the wider ATR stop. entry_atr_pct is in PERCENT."""
    cfg = (config or {}).get("volstop_shadow") or {}
    if not bool(cfg.get("enabled", False)) or entry_px <= 0:
        return
    mult = float(cfg.get("atr_mult", 2.0))
    floor = float(cfg.get("floor_pct", 1.0)) / 100.0
    ceil = float(cfg.get("ceiling_pct", 5.0)) / 100.0
    atr_frac = (entry_atr_pct or 0.0) / 100.0
    stop_frac = min(max(atr_frac * mult, floor), ceil) if atr_frac > 0 else floor
    d = _load()
    d[f"{coin}:{side}"] = {
        "coin": coin, "side": side, "entry_px": float(entry_px), "lev": float(leverage),
        "stop_frac": stop_frac, "peak": float(entry_px), "opened": time.time(),
        "protect": 0.0125, "retrace": 0.20, "armed": False, "live_exit_roe": None,
    }
    _save(d)
    logger.info(f"[volstop-shadow] opened {coin} {side}: ATR-stop {stop_frac*100:.2f}% spot "
                f"(vs live fixed ~0.4%) — paper-tracking forward")


def record_live_exit(coin: str, side: str, live_roe: float) -> None:
    """Tag the shadow with the live exit ROE for comparison; shadow keeps tracking."""
    d = _load()
    k = f"{coin}:{side}"
    if k in d:
        d[k]["live_exit_roe"] = round(float(live_roe), 2)
        _save(d)


def update_and_log(mids: Dict[str, float], config: Dict[str, Any]) -> None:
    """Called each monitor cycle. Advance shadow positions on current marks; when a shadow
    stop/trail fires, log shadow-ROE vs the live-ROE and close the shadow."""
    cfg = (config or {}).get("volstop_shadow") or {}
    if not bool(cfg.get("enabled", False)):
        return
    d = _load()
    if not d:
        return
    max_hold_min = float(cfg.get("max_hold_min", 1440))  # paper-close after N min if never hit
    changed = False
    for k, p in list(d.items()):
        try:
            mark = float((mids or {}).get(p["coin"], 0) or 0)
            if mark <= 0:
                continue
            is_long = p["side"] == "long"
            entry = p["entry_px"]
            p["peak"] = max(p["peak"], mark) if is_long else min(p["peak"], mark)
            exit_roe = None
            reason = None
            if is_long:
                stop_px = entry * (1 - p["stop_frac"])
                if mark <= stop_px:
                    exit_roe, reason = (stop_px / entry - 1) * p["lev"] * 100, "atr_stop"
                elif (p["peak"] - entry) / entry >= p["protect"]:
                    p["armed"] = True
                    floor = p["peak"] - (p["peak"] - entry) * p["retrace"]
                    if mark <= floor:
                        exit_roe, reason = (floor / entry - 1) * p["lev"] * 100, "trail"
            else:
                stop_px = entry * (1 + p["stop_frac"])
                if mark >= stop_px:
                    exit_roe, reason = (1 - stop_px / entry) * p["lev"] * 100, "atr_stop"
                elif (entry - p["peak"]) / entry >= p["protect"]:
                    p["armed"] = True
                    floor = p["peak"] + (entry - p["peak"]) * p["retrace"]
                    if mark >= floor:
                        exit_roe, reason = (1 - floor / entry) * p["lev"] * 100, "trail"
            held_min = (time.time() - p["opened"]) / 60.0
            if exit_roe is None and held_min >= max_hold_min:
                exit_roe = ((mark / entry - 1) if is_long else (1 - mark / entry)) * p["lev"] * 100
                reason = "max_hold"
            if exit_roe is not None:
                rec = {"ts": int(time.time()), "coin": p["coin"], "side": p["side"],
                       "shadow_roe": round(exit_roe, 2), "shadow_reason": reason,
                       "live_roe": p.get("live_exit_roe"), "held_min": round(held_min)}
                try:
                    with open(LOG, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                except Exception:
                    pass
                _delta = (f"{exit_roe - p['live_exit_roe']:+.1f}%ROE vs live"
                          if p.get("live_exit_roe") is not None else "live still open/unknown")
                logger.info(f"[volstop-shadow] {p['coin']} {p['side']} ATR-stop exit "
                            f"{exit_roe:+.1f}%ROE ({reason}) — {_delta}")
                del d[k]
            changed = True
        except Exception as e:
            logger.debug(f"[volstop-shadow] update failed for {k}: {e}")
    if changed:
        _save(d)
