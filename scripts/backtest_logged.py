#!/usr/bin/env python3
"""Re-filter logged AI verdicts through CURRENT gates + DSL config.

Reads ~200 cached analyses from .agent-memory.json, joins each to its
perception for composite/triggers, then simulates execution + DSL exit
on historical 5m bars using the live config. Tells you "what would
today's strategy have done on yesterday's actual AI verdicts."

Free (no LLM calls). This is the primary current per-trade replay for strategy
EV work. Use backtest_portfolio.py when concurrency, gross exposure, and margin
contention matter.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.sizing import atr_equal_risk_notional
from hermes_trader.client.exchange import get_max_leverage
from hermes_trader.indicators.math import atr as calc_atr
from hermes_trader.client.hl_client import _http_post
from hermes_trader.models.types import Candle
from _memory_io import load_memory

_INTERVAL_MS = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000}
_CANDLE_CACHE: Dict[Tuple[str, str, int, int], Optional[List[Candle]]] = {}
_DISK_CANDLE_CACHE: Dict[str, Any] = {}
_DISK_CACHE_FILE = ""
_API_FAILURES = 0
_API_SLEEP_S = 0.0


def _cache_key(coin: str, interval: str, count: int, end_ms: int) -> str:
    return json.dumps([coin, interval, count, end_ms], separators=(",", ":"))


def _load_disk_cache(path: str) -> None:
    global _DISK_CANDLE_CACHE
    if not path:
        return
    try:
        with open(path) as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            _DISK_CANDLE_CACHE = raw
    except FileNotFoundError:
        _DISK_CANDLE_CACHE = {}
    except Exception:
        _DISK_CANDLE_CACHE = {}


def _save_disk_cache(path: str) -> None:
    if not path:
        return
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(_DISK_CANDLE_CACHE, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _candles_from_json(raw: Any) -> Optional[List[Candle]]:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    return [Candle(t=c["t"], o=float(c["o"]), h=float(c["h"]), l=float(c["l"]),
                   c=float(c["c"]), v=float(c.get("v", "0"))) for c in raw]


def _candles_to_json(candles: List[Candle]) -> List[Dict[str, Any]]:
    return [{"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v} for c in candles]


def fetch_candles_at(coin: str, interval: str, count: int, end_ms: int) -> Optional[List[Candle]]:
    global _API_FAILURES
    key = (coin, interval, count, end_ms)
    if key in _CANDLE_CACHE:
        return _CANDLE_CACHE[key]
    disk_key = _cache_key(coin, interval, count, end_ms)
    if disk_key in _DISK_CANDLE_CACHE:
        candles = _candles_from_json(_DISK_CANDLE_CACHE[disk_key])
        _CANDLE_CACHE[key] = candles
        return candles
    if _API_SLEEP_S > 0:
        time.sleep(_API_SLEEP_S)
    step = _INTERVAL_MS[interval]
    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": interval,
                       "startTime": end_ms - step * count, "endTime": end_ms}}
    try:
        raw = _http_post("/info", payload)
    except Exception:
        raw = None
    if not isinstance(raw, list):
        _API_FAILURES += 1
        _CANDLE_CACHE[key] = None
        return None
    candles = [Candle(t=c["t"], o=float(c["o"]), h=float(c["h"]), l=float(c["l"]),
                      c=float(c["c"]), v=float(c.get("v", "0"))) for c in raw]
    _CANDLE_CACHE[key] = candles
    _DISK_CANDLE_CACHE[disk_key] = _candles_to_json(candles)
    return candles


def detect_regime_at(end_ms: int, proxy: str = "BTC") -> str:
    from hermes_trader.indicators.math import ema
    candles = fetch_candles_at(proxy, "1h", 100, end_ms)
    if not candles or len(candles) < 50:
        return "neutral"
    closes = [c.c for c in candles]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    if len(fast) < 9:
        return "neutral"
    f_prev = fast[-9]
    if f_prev == 0:
        return "neutral"
    slope = (fast[-1] - f_prev) / abs(f_prev)
    if fast[-1] > slow[-1] and slope > 0.002:
        return "up"
    if fast[-1] < slow[-1] and slope < -0.002:
        return "down"
    return "neutral"


def entry_atr4h(coin: str, end_ms: int) -> float:
    candles = fetch_candles_at(coin, "4h", 80, end_ms)
    if not candles or len(candles) < 20:
        return 0.0
    vals = [
        float(v) for v in calc_atr(candles, 14)
        if not (v != v or v in (float("inf"), float("-inf")))
    ]
    return vals[-1] if vals else 0.0


def max_leverage_for(coin: str, fallback: int) -> int:
    try:
        lev = int(get_max_leverage(coin))
    except Exception:
        lev = int(fallback)
    return max(1, min(int(fallback), lev))


def asset_notional_multiplier(coin: str, cfg: Dict[str, Any]) -> float:
    raw = cfg.get("asset_notional_multiplier", {}) or {}
    if not isinstance(raw, dict):
        return 1.0
    key = "hip3" if ":" in (coin or "") else "crypto"
    try:
        mult = float(raw.get(key, 1.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(mult, 1.0))


def live_sized_notional(
    *,
    coin: str,
    entry_px: float,
    entry_ms: int,
    equity: float,
    equity_fraction: float,
    leverage: int,
    cfg: Dict[str, Any],
    dsl_cfg: Dict[str, Any],
) -> Tuple[float, str]:
    cap = float(cfg.get("max_trade_notional_usd", 0) or 0)
    coin_lev = max_leverage_for(coin, leverage)
    sizing_label = "legacy_fraction"
    atr_cfg = cfg.get("atr_risk_sizing", {}) or {}
    if bool(atr_cfg.get("enabled", False)):
        risk_pct = float(atr_cfg.get("risk_per_trade_pct", 0.0075) or 0.0)
        basis = str(atr_cfg.get("sizing_basis", "backup_stop") or "backup_stop").lower()
        if basis in ("primary_stop", "dsl_stop"):
            stop_frac = min(
                float(dsl_cfg.get("max_loss_pct", 2.0) or 2.0),
                float(dsl_cfg.get("max_loss_roe_pct", 40.0) or 40.0) / max(1, coin_lev),
            ) / 100.0
            if equity <= 0 or risk_pct <= 0 or stop_frac <= 0:
                return 0.0, "primary_stop_invalid"
            notional = (risk_pct * equity) / stop_frac
            notional = min(notional, equity * coin_lev)
            if cap > 0:
                notional = min(notional, cap)
            sizing_label = f"primary_stop risk={risk_pct:g}"
            mult = asset_notional_multiplier(coin, cfg)
            if mult < 1.0:
                notional *= mult
                sizing_label += f" asset_mult={mult:g}"
            return notional, sizing_label
        atr4h = entry_atr4h(coin, entry_ms)
        sz = atr_equal_risk_notional(
            equity=equity,
            risk_per_trade_pct=risk_pct,
            atr_abs=atr4h,
            entry_px=entry_px,
            sl_atr_mult=float(cfg.get("sl_atr_mult", 1.5) or 1.5),
            max_trade_notional_usd=cap,
            coin_max_leverage=coin_lev,
            config_max_leverage=leverage,
        )
        notional = sz.notional_usd
        sizing_label = f"backup_stop risk={risk_pct:g}"
        mult = asset_notional_multiplier(coin, cfg)
        if mult < 1.0:
            notional *= mult
            sizing_label += f" asset_mult={mult:g}"
        return notional, sizing_label

    notional = equity * equity_fraction * coin_lev
    if cap > 0:
        notional = min(notional, cap)
    mult = asset_notional_multiplier(coin, cfg)
    if mult < 1.0:
        notional *= mult
        sizing_label += f" asset_mult={mult:g}"
    return notional, sizing_label


def passes_counter_regime(side: str, regime: str, conf: float, composite: float,
                          burst_fired: bool, slow_fired: bool, min_conf: float) -> bool:
    if regime == "neutral":
        return True
    aligned = (regime == "up" and side == "long") or (regime == "down" and side == "short")
    if aligned:
        return True
    return conf >= min_conf or composite >= 50 or burst_fired or slow_fired


def simulate_dsl_exit(entry_px: float, side: str, leverage: int,
                      forward_5m: List[Candle], dsl_cfg: Dict[str, Any]) -> Tuple[float, str, int, float]:
    max_loss_pct = float(dsl_cfg.get("max_loss_pct", 2.0))
    max_loss_roe_pct = float(dsl_cfg.get("max_loss_roe_pct", 40.0))
    protect_pct = float(dsl_cfg.get("protect_pct", 0.5))
    retrace = float(dsl_cfg.get("retrace_threshold", 0.30))
    hard_timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 180.0))
    timeout_bars = int(hard_timeout_min // 5)
    lev = max(1, leverage)
    effective_max = min(max_loss_pct, max_loss_roe_pct / lev)
    is_long = side == "long"
    peak = entry_px

    for i, bar in enumerate(forward_5m):
        if i >= timeout_bars:
            spot_pct = (bar.c - entry_px)/entry_px*100 if is_long else (entry_px - bar.c)/entry_px*100
            return (spot_pct * lev, "hard_timeout", i, bar.c)
        loss_pct = (entry_px - bar.l)/entry_px*100 if is_long else (bar.h - entry_px)/entry_px*100
        if loss_pct >= effective_max:
            stop_px = entry_px * (1 - effective_max/100) if is_long else entry_px * (1 + effective_max/100)
            return (-effective_max * lev, "max_loss", i, stop_px)
        if is_long and bar.h > peak: peak = bar.h
        elif not is_long and bar.l < peak: peak = bar.l
        if is_long:
            profit_pct = (peak - entry_px)/entry_px*100
            if profit_pct >= protect_pct:
                floor_px = peak - (peak - entry_px) * retrace
                if bar.l <= floor_px:
                    return (((floor_px - entry_px)/entry_px*100) * lev, "floor_breach", i, floor_px)
        else:
            profit_pct = (entry_px - peak)/entry_px*100
            if profit_pct >= protect_pct:
                floor_px = peak + (entry_px - peak) * retrace
                if bar.h >= floor_px:
                    return (((entry_px - floor_px)/entry_px*100) * lev, "floor_breach", i, floor_px)

    if not forward_5m:
        return (0.0, "no_data", 0, entry_px)
    last = forward_5m[-1]
    spot_pct = (last.c - entry_px)/entry_px*100 if is_long else (entry_px - last.c)/entry_px*100
    return (spot_pct * lev, "end_of_window", len(forward_5m), last.c)


def main() -> int:
    global _API_SLEEP_S, _DISK_CACHE_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--equity", type=float, default=250.0)
    ap.add_argument("--dedup-min", type=int, default=None,
                    help="Treat same-coin analyses within N minutes as one trade "
                         "(default: live cooldown_min)")
    ap.add_argument("--loss-cooldown-min", type=int, default=None,
                    help="Block same-coin re-entry for N minutes after a simulated loss "
                         "(default: off; live uses loss_cooldown_min)")
    ap.add_argument("--mode", default="ai", choices=["ai", "lowconf", "force", "sidestep"],
                    help="ai=as-is; lowconf=lower min-conf; force=+composite-force PASS->LONG; "
                         "sidestep=ignore AI, take all TA-confirmed LONGs")
    ap.add_argument("--min-conf", type=float, default=0.60, help="min conf for lowconf mode")
    ap.add_argument("--force-bar", type=float, default=30.0, help="composite bar for force/sidestep")
    ap.add_argument("--long-only", action="store_true", help="Skip admitted SHORT entries")
    ap.add_argument("--regime-mode", choices=["live", "neutral", "up", "down"], default="live",
                    help="Counter-regime model. live calls HL/BTC regime; fixed modes are deterministic.")
    ap.add_argument("--leverage", type=int, default=0, help="override leverage (0=use config)")
    ap.add_argument("--equity-fraction", type=float, default=0.0, help="override fraction (0=use config)")
    ap.add_argument("--max-notional", type=float, default=0.0, help="override max_trade_notional_usd (0=config)")
    ap.add_argument("--risk-pct", type=float, default=0.0, help="override atr_risk_sizing.risk_per_trade_pct (0=config)")
    ap.add_argument("--sizing-basis", default="", help="override atr_risk_sizing.sizing_basis")
    ap.add_argument("--roe-cap", type=float, default=0.0, help="override max_loss_roe_pct (0=config)")
    ap.add_argument("--max-loss", type=float, default=0.0, help="override max_loss_pct spot stop (0=config)")
    ap.add_argument("--protect", type=float, default=0.0, help="override dsl_exit.protect_pct (0=config)")
    ap.add_argument("--retrace", type=float, default=0.0, help="override dsl_exit.retrace_threshold (0=config)")
    ap.add_argument("--taker-fee-bps", type=float, default=2.5,
                    help="Per-side taker fee in bps, converted to ROE by leverage")
    ap.add_argument("--slippage-bps", type=float, default=0.0,
                    help="Optional adverse slippage per side in bps for stress tests")
    ap.add_argument("--exclude-hip3", action="store_true",
                    help="Skip colon-namespaced HIP-3 markets in the replay")
    ap.add_argument("--api-sleep", type=float, default=0.0,
                    help="Seconds to sleep before uncached Hyperliquid candle requests")
    ap.add_argument("--summary-only", action="store_true",
                    help="Suppress per-trade rows; print only aggregate results")
    ap.add_argument("--cache-file", default=os.path.join(tempfile.gettempdir(), "hermes_backtest_logged_candles.json"),
                    help="Disk cache for historical candles; set empty string to disable")
    ap.add_argument("--apply-runner-gate", action="store_true",
                    help="Apply executor.runner_entry_gate to admitted trades")
    ap.add_argument("--runner-min-confidence", type=float, default=None,
                    help="Override runner_entry_gate.min_confidence for this replay")
    ap.add_argument("--runner-min-composite", type=float, default=None,
                    help="Override runner_entry_gate.min_composite for this replay")
    ap.add_argument("--runner-min-crypto-composite", type=float, default=None,
                    help="Override runner_entry_gate.min_crypto_composite for this replay")
    ap.add_argument("--runner-min-hip3-composite", type=float, default=None,
                    help="Override runner_entry_gate.min_hip3_composite for this replay")
    ap.add_argument("--runner-mover-min-confidence", type=float, default=None,
                    help="Override runner_entry_gate.mover_min_confidence for this replay")
    ap.add_argument("--runner-mover-min-composite", type=float, default=None,
                    help="Override runner_entry_gate.mover_min_composite for this replay")
    args = ap.parse_args()
    _API_SLEEP_S = max(0.0, float(args.api_sleep or 0.0))
    _DISK_CACHE_FILE = args.cache_file
    _load_disk_cache(_DISK_CACHE_FILE)

    cfg = read_agent_config()
    if args.max_notional:
        cfg = dict(cfg)
        cfg["max_trade_notional_usd"] = args.max_notional
    if args.risk_pct or args.sizing_basis:
        cfg = dict(cfg)
        atr_cfg = dict(cfg.get("atr_risk_sizing", {}) or {})
        if args.risk_pct:
            atr_cfg["risk_per_trade_pct"] = args.risk_pct
        if args.sizing_basis:
            atr_cfg["sizing_basis"] = args.sizing_basis
        cfg["atr_risk_sizing"] = atr_cfg
    dsl_cfg = dict(cfg.get("dsl_exit", {}))
    if args.roe_cap:
        dsl_cfg["max_loss_roe_pct"] = args.roe_cap
    if args.max_loss:
        dsl_cfg["max_loss_pct"] = args.max_loss
    if args.protect:
        dsl_cfg["protect_pct"] = args.protect
    if args.retrace:
        dsl_cfg["retrace_threshold"] = args.retrace
    counter_regime_min_conf = float(cfg.get("counter_regime_min_conf", 0.65))
    equity_fraction = float(args.equity_fraction or cfg.get("equity_fraction_per_trade", 0.04))
    base_leverage = int(args.leverage or cfg.get("leverage", 10))
    min_ai_conf = float(cfg.get("min_ai_confidence", 0.35))
    dedup_min = int(args.dedup_min if args.dedup_min is not None
                    else cfg.get("cooldown_min", 30))

    runner_cfg = cfg
    runner_overrides = {
        "min_confidence": args.runner_min_confidence,
        "min_composite": args.runner_min_composite,
        "min_crypto_composite": args.runner_min_crypto_composite,
        "min_hip3_composite": args.runner_min_hip3_composite,
        "mover_min_confidence": args.runner_mover_min_confidence,
        "mover_min_composite": args.runner_mover_min_composite,
    }
    if args.apply_runner_gate and any(v is not None for v in runner_overrides.values()):
        gate = dict(cfg.get("runner_entry_gate") or {})
        for key, val in runner_overrides.items():
            if val is not None:
                gate[key] = float(val)
        runner_cfg = dict(cfg)
        runner_cfg["runner_entry_gate"] = gate

    mem = load_memory(_REPO / ".agent-memory.json")
    analyses = mem.get("analyses", [])
    perceptions_by_id = {p["id"]: p for p in mem.get("perceptions", []) if "id" in p}

    now_ms = int(time.time() * 1000)
    cutoff = now_ms - args.hours * 3600_000
    analyses = [a for a in analyses if a.get("created_at", 0) >= cutoff]
    if args.exclude_hip3:
        analyses = [a for a in analyses if ":" not in (a.get("coin") or "")]
    analyses.sort(key=lambda a: a.get("created_at", 0))

    print(f"# Counterfactual replay of {len(analyses)} logged analyses (last {args.hours}h)")
    print(f"# Equity ${args.equity:.0f} | leverage {base_leverage}x | fraction {equity_fraction}")
    print(f"# Sizing: {cfg.get('atr_risk_sizing', {}) if cfg.get('atr_risk_sizing') else 'legacy fraction'} "
          f"| cap ${float(cfg.get('max_trade_notional_usd', 0) or 0):g}")
    print(f"# Same-coin cooldown/dedup: {dedup_min}min")
    round_trip_cost_roe = ((args.taker_fee_bps + args.slippage_bps) * 2 * base_leverage / 100.0)
    print(f"# Costs: taker {args.taker_fee_bps:g}bps/side"
          f"{' + slippage ' + str(args.slippage_bps) + 'bps/side' if args.slippage_bps else ''}"
          f" = {round_trip_cost_roe:.2f}% ROE/trade")
    if args.apply_runner_gate:
        print(f"# Runner gate: {runner_cfg.get('runner_entry_gate', {})}")
    print(f"# DSL: max_loss={dsl_cfg.get('max_loss_pct')}% / {dsl_cfg.get('max_loss_roe_pct')}% ROE | "
          f"protect={dsl_cfg.get('protect_pct')}% | timeout={dsl_cfg.get('hard_timeout_minutes')}min")
    print()

    # Dedup window: skip same-coin within N minutes of a previous trade
    dedup_ms = dedup_min * 60_000
    last_trade_by_coin: Dict[str, int] = {}

    # Cache regime per coarse 30-min bucket to save HL calls
    regime_cache: Dict[int, str] = {}
    def _regime_at(t: int) -> str:
        if args.regime_mode != "live":
            return args.regime_mode
        bucket = t // (30 * 60_000)
        if bucket not in regime_cache:
            regime_cache[bucket] = detect_regime_at(t)
        return regime_cache[bucket]

    pnl_total = 0.0
    wins, losses = [], []
    skipped_pass, skipped_dup, skipped_conf, skipped_regime = 0, 0, 0, 0
    skipped_nodata, skipped_size, skipped_loss_cooldown = 0, 0, 0
    by_reason: Dict[str, List[float]] = {}
    by_reason_pnl: Dict[str, List[float]] = {}
    trades: List[Tuple[Any, ...]] = []
    notionals: List[float] = []
    sizing_labels: Dict[str, int] = {}

    n_forced = 0
    loss_block_until: Dict[str, int] = {}
    loss_cooldown_ms = 0
    if args.loss_cooldown_min is not None:
        loss_cooldown_ms = max(0, int(args.loss_cooldown_min) * 60_000)
    for a in analyses:
        verdict = a.get("verdict")
        coin = a.get("coin")
        ts = int(a.get("created_at", 0))
        if not coin or ts == 0:
            continue
        conf = float(a.get("confidence", 0))
        # perception (composite/triggers) — needed for force/sidestep admission
        perc = perceptions_by_id.get(a.get("perception_id"))
        composite = float(
            (perc or {}).get("composite_score", a.get("composite_score", 0)) or 0
        )
        triggers = (perc or {}).get("triggers", []) or []
        burst_fired = (
            any(t.get("name") == "momentumBurst" and t.get("fired") for t in triggers)
            or bool(a.get("momentum_burst_fired", False))
        )
        slow_count = sum(
            1 for t in triggers
            if t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            and t.get("fired")
        )
        if slow_count <= 0:
            slow_count = int(a.get("slow_burn_count", 0) or 0)
        slow_fired = slow_count > 0
        ta_confirmed = (
            composite >= args.force_bar
            or burst_fired
        )

        # ── mode-aware admission ─────────────────────────────────────────────
        ai_ls = verdict in ("LONG", "SHORT")
        admit, side, forced, sidestep_override = False, None, False, False
        if args.mode == "ai":
            admit = ai_ls and conf >= min_ai_conf
            side = ("long" if verdict == "LONG" else "short") if ai_ls else None
        elif args.mode == "lowconf":
            admit = ai_ls and conf >= args.min_conf
            side = ("long" if verdict == "LONG" else "short") if ai_ls else None
        elif args.mode == "force":
            if ai_ls and conf >= min_ai_conf:
                admit, side = True, ("long" if verdict == "LONG" else "short")
            elif composite >= args.force_bar:            # composite-force PASS -> LONG
                admit, side, forced = True, "long", True
                conf = max(conf, min_ai_conf)
        elif args.mode == "sidestep":                    # ignore AI; take all TA-confirmed LONGs
            if ta_confirmed:
                admit, side, forced, sidestep_override = True, "long", (not ai_ls), True
                conf = max(conf, min_ai_conf)
            elif ai_ls and conf >= min_ai_conf:
                admit, side = True, ("long" if verdict == "LONG" else "short")
        if not admit:
            skipped_pass += 1
            continue
        if args.long_only and side == "short":
            skipped_pass += 1
            continue
        if forced:
            n_forced += 1
        if coin in last_trade_by_coin and (ts - last_trade_by_coin[coin]) < dedup_ms:
            skipped_dup += 1
            continue
        if loss_cooldown_ms > 0 and ts < loss_block_until.get(coin, 0):
            skipped_loss_cooldown += 1
            continue

        if args.apply_runner_gate:
            from hermes_trader.agents.executor import _runner_entry_block_reason
            gate_analysis = dict(a)
            gate_analysis["side"] = side
            if forced:
                gate_analysis["confidence"] = conf
            if sidestep_override:
                gate_analysis["sidestep_override"] = True
            blocked = _runner_entry_block_reason(gate_analysis, runner_cfg)
            if blocked:
                skipped_pass += 1
                continue

        regime = _regime_at(ts)
        if not passes_counter_regime(side, regime, conf, composite, burst_fired, slow_fired,
                                     counter_regime_min_conf):
            skipped_regime += 1
            continue

        # Fetch the entry bar + forward 5m bars (DSL window)
        timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 180.0))
        forward_end = ts + int(timeout_min * 60_000) + 600_000  # +10min padding
        forward = fetch_candles_at(coin, "5m", int(timeout_min // 5) + 10, forward_end)
        if forward is None:
            skipped_nodata += 1
            continue
        forward = [b for b in forward if b.t >= ts]
        if not forward:
            skipped_nodata += 1
            continue

        entry_px = forward[0].o  # open of the first bar after analysis
        if entry_px <= 0:
            skipped_nodata += 1
            continue
        forward = forward[1:]  # bars STRICTLY after entry bar's open
        if not forward:
            skipped_nodata += 1
            continue

        notional, sizing_label = live_sized_notional(
            coin=coin,
            entry_px=entry_px,
            entry_ms=ts,
            equity=args.equity,
            equity_fraction=equity_fraction,
            leverage=base_leverage,
            cfg=cfg,
            dsl_cfg=dsl_cfg,
        )
        if notional < 10.5:
            skipped_size += 1
            continue

        gross_roe, reason, bars, exit_px = simulate_dsl_exit(entry_px, side, base_leverage, forward, dsl_cfg)
        roe = gross_roe - round_trip_cost_roe
        margin = notional / max(1, base_leverage)
        pnl_usd = roe / 100 * margin
        pnl_total += pnl_usd
        notionals.append(notional)
        sizing_labels[sizing_label] = sizing_labels.get(sizing_label, 0) + 1
        last_trade_by_coin[coin] = ts
        if pnl_usd < 0 and loss_cooldown_ms > 0:
            exit_ts = ts + int(bars * 5 * 60_000)
            loss_block_until[coin] = exit_ts + loss_cooldown_ms
        (wins if pnl_usd > 0 else losses).append(pnl_usd)
        by_reason.setdefault(reason, []).append(roe)
        by_reason_pnl.setdefault(reason, []).append(pnl_usd)
        trades.append((ts, coin, side, conf, composite, roe, reason, pnl_usd))
        if not args.summary_only:
            print(f"  {_iso(ts)}  {coin:<14} {side:<5} conf={conf:.2f} comp={composite:>4.0f}  "
                  f"entry={entry_px:.6g} exit={exit_px:.6g}  {reason:<14} ROE={roe:+6.1f}%  ${pnl_usd:+6.2f}")

    n = len(trades)
    wr = len(wins) / n if n else 0
    print()
    print("=" * 80)
    print(f"Trades:       {n}  ({len(wins)}W / {len(losses)}L, win rate {wr*100:.0f}%)")
    print(f"Total PnL:    ${pnl_total:+.2f}  ({pnl_total/args.equity*100:+.1f}% on ${args.equity:.0f})")
    if notionals:
        print(f"Avg notional: ${sum(notionals)/len(notionals):.0f}  sizing={sizing_labels}")
    print(f"Skipped:      {skipped_pass} PASS, {skipped_dup} dedup, {skipped_conf} low-conf, "
          f"{skipped_regime} counter-regime, {skipped_nodata} no-data, "
          f"{skipped_size} below-size, {skipped_loss_cooldown} loss-cooldown")
    print(f"API failures: {_API_FAILURES}")
    print()
    print("Exits by reason:")
    for reason in sorted(by_reason.keys(), key=lambda r: -sum(by_reason_pnl.get(r, []))):
        roes = by_reason[reason]
        avg = sum(roes)/len(roes)
        tot_pnl = sum(by_reason_pnl.get(reason, []))
        print(f"  {reason:<14} n={len(roes):>3}  avg ROE {avg:+6.1f}%  total ${tot_pnl:+7.2f}")
    _save_disk_cache(_DISK_CACHE_FILE)
    return 0


def _iso(ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ms/1000, tz=datetime.timezone.utc).strftime("%m-%d %H:%M")


if __name__ == "__main__":
    raise SystemExit(main())
