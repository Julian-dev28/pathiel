#!/usr/bin/env python3
"""Grid-search logged Hermes decisions across realistic live config blends.

This complements backtest_logged.py. The older replay is good for entry/exit
direction, but it assumes fraction-based sizing. Live Hermes can now use ATR
equal-risk sizing, so this script tests both the current live sizing mode and
more aggressive alternatives before changing .agent-config.json.
"""
from __future__ import annotations

import argparse
import copy
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

import backtest_logged as btlog
from _memory_io import load_memory
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.executor import _runner_entry_block_reason
from hermes_trader.agents.sizing import atr_equal_risk_notional
from hermes_trader.indicators.math import atr as calc_atr


@dataclass(frozen=True)
class Candidate:
    ts: int
    coin: str
    verdict: str
    side: str
    conf: float
    composite: float
    burst_fired: bool
    slow_fired: bool
    fresh_impulse: bool
    daily_mover: bool
    downtrend: bool
    is_hip3: bool
    regime: str
    analysis: dict[str, Any]
    entry_px: float
    forward: list[Any]
    atr4h: float


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_finite(v: float) -> bool:
    return not (math.isnan(v) or math.isinf(v))


def _entry_atr4h(coin: str, ts: int) -> float:
    candles = btlog.fetch_candles_at(coin, "4h", 80, ts)
    if not candles or len(candles) < 20:
        return 0.0
    vals = [v for v in calc_atr(candles, 14) if _is_finite(v)]
    return float(vals[-1]) if vals else 0.0


def _trigger_map(triggers: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        str(t.get("name")): bool(t.get("fired"))
        for t in triggers
        if isinstance(t, dict)
    }


def build_candidates(args: argparse.Namespace, cfg: dict[str, Any]) -> list[Candidate]:
    mem = load_memory(_REPO / ".agent-memory.json")
    analyses = sorted(mem.get("analyses", []), key=lambda a: a.get("created_at", 0))
    percs = {p["id"]: p for p in mem.get("perceptions", []) if "id" in p}
    cutoff = int(time.time() * 1000) - args.hours * 3600_000
    timeout_min = float((cfg.get("dsl_exit") or {}).get("hard_timeout_minutes", 1800.0))
    counter_min = float(cfg.get("counter_regime_min_conf", 0.8))
    out: list[Candidate] = []
    regime_cache: dict[int, str] = {}

    for a in analyses:
        ts = int(a.get("created_at", 0) or 0)
        if ts < cutoff:
            continue
        verdict = str(a.get("verdict") or "")
        if verdict not in ("LONG", "SHORT"):
            continue
        coin = str(a.get("coin") or "")
        if not coin:
            continue
        side = "long" if verdict == "LONG" else "short"
        conf = _f(a.get("confidence"))
        perc = percs.get(a.get("perception_id")) or {}
        composite = _f(a.get("composite_score"), _f(perc.get("composite_score")))
        triggers = (perc.get("triggers") or []) if isinstance(perc, dict) else []
        tmap = _trigger_map(triggers)
        burst = bool(a.get("momentum_burst_fired", tmap.get("momentumBurst", False)))
        slow_count = int(a.get("slow_burn_count") or sum(
            1 for k in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            if bool(tmap.get(k))
        ))
        slow = slow_count > 0
        volume_spike = bool(a.get("volume_spike_fired", tmap.get("volumeSpike", False)))
        breakout = bool(a.get("breakout_fired", tmap.get("breakout", False)))
        uptrend = bool(a.get("uptrend_momentum_fired", tmap.get("uptrendMomentum", False)))
        downtrend = bool(a.get("downtrend_momentum_fired", tmap.get("downtrendMomentum", False)))
        daily_mover = bool(a.get("daily_mover_fired", tmap.get("dailyMover", False)))
        fresh = burst or breakout or volume_spike
        bucket = ts // (30 * 60_000)
        if bucket not in regime_cache:
            regime_cache[bucket] = btlog.detect_regime_at(ts)
        regime = regime_cache[bucket]
        if not btlog.passes_counter_regime(side, regime, conf, composite, burst, slow, counter_min):
            continue

        forward_end = ts + int(timeout_min * 60_000) + 600_000
        forward = btlog.fetch_candles_at(coin, "5m", int(timeout_min // 5) + 10, forward_end)
        if not forward:
            continue
        forward = [b for b in forward if b.t >= ts]
        if len(forward) < 2:
            continue
        entry_px = float(forward[0].o)
        if entry_px <= 0:
            continue
        atr4h = _entry_atr4h(coin, ts)
        aa = dict(a)
        aa["side"] = side
        aa["composite_score"] = composite
        aa["volume_spike_fired"] = volume_spike
        aa["breakout_fired"] = breakout
        aa["momentum_burst_fired"] = burst
        aa["uptrend_momentum_fired"] = uptrend
        aa["downtrend_momentum_fired"] = downtrend
        aa["fresh_impulse_fired"] = fresh
        aa["daily_mover_fired"] = daily_mover
        aa["slow_burn_count"] = slow_count
        out.append(Candidate(
            ts=ts,
            coin=coin,
            verdict=verdict,
            side=side,
            conf=conf,
            composite=composite,
            burst_fired=burst,
            slow_fired=slow,
            fresh_impulse=fresh,
            daily_mover=daily_mover,
            downtrend=downtrend,
            is_hip3=(":" in coin),
            regime=regime,
            analysis=aa,
            entry_px=entry_px,
            forward=forward[1:],
            atr4h=atr4h,
        ))
    return out


def _gate_cfg(base: dict[str, Any], opt: dict[str, Any]) -> dict[str, Any]:
    cfg = copy.deepcopy(base)
    cfg["min_ai_confidence"] = opt["min_conf"]
    cfg["counter_regime_min_conf"] = opt["counter_conf"]
    gate = dict(cfg.get("runner_entry_gate") or {})
    gate.update({
        "enabled": True,
        "allow_shorts": opt["allow_shorts"],
        "min_confidence": opt["min_conf"],
        "min_composite": opt["min_composite"],
        "min_hip3_composite": opt["min_hip3_composite"],
        "min_short_confidence": opt["min_short_conf"],
        "min_short_composite": opt["min_short_composite"],
        "mover_min_confidence": opt["mover_conf"],
        "mover_min_composite": opt["mover_composite"],
    })
    cfg["runner_entry_gate"] = gate
    return cfg


def _admitted(c: Candidate, cfg: dict[str, Any], opt: dict[str, Any]) -> bool:
    if c.conf < opt["min_conf"]:
        return False
    if c.side == "short" and not opt["allow_shorts"]:
        return False
    blocked = _runner_entry_block_reason(c.analysis, cfg)
    return not blocked


def _notional(c: Candidate, opt: dict[str, Any], equity: float) -> float:
    lev = btlog.max_leverage_for(c.coin, int(opt["leverage"]))
    cap = float(opt["max_notional"])
    if opt["sizing"] == "legacy":
        raw = equity * float(opt["fraction"]) * lev
        return min(raw, cap) if cap > 0 else raw

    if opt["sizing"] == "atr_backup":
        sz = atr_equal_risk_notional(
            equity=equity,
            risk_per_trade_pct=float(opt["risk_pct"]),
            atr_abs=c.atr4h,
            entry_px=c.entry_px,
            sl_atr_mult=float(opt["sl_atr_mult"]),
            max_trade_notional_usd=cap,
            config_max_leverage=lev,
        )
        return sz.notional_usd

    if opt["sizing"] == "dsl_stop":
        spot_stop = min(float(opt["max_loss"]), float(opt["roe_cap"]) / max(1, lev)) / 100.0
        if spot_stop <= 0:
            return 0.0
        raw = equity * float(opt["risk_pct"]) / spot_stop
        raw = min(raw, equity * lev)
        return min(raw, cap) if cap > 0 else raw

    if opt["sizing"] == "hybrid":
        dsl_stop = min(float(opt["max_loss"]), float(opt["roe_cap"]) / max(1, lev)) / 100.0
        atr_stop = float(opt["sl_atr_mult"]) * c.atr4h / c.entry_px if c.atr4h > 0 else 0.0
        # Avoid sizing to an ultra-wide disaster stop, but still respect some
        # volatility by not going tighter than one-third of 1.5x 4h ATR.
        stop = max(dsl_stop, atr_stop / 3.0) if atr_stop > 0 else dsl_stop
        if stop <= 0:
            return 0.0
        raw = equity * float(opt["risk_pct"]) / stop
        raw = min(raw, equity * lev)
        return min(raw, cap) if cap > 0 else raw

    raise ValueError(f"unknown sizing mode {opt['sizing']}")


def evaluate(
    candidates: list[Candidate],
    base_cfg: dict[str, Any],
    opt: dict[str, Any],
    *,
    equity: float,
    taker_bps: float,
    slippage_bps: float,
    split_ts: int | None = None,
    admission_cache: dict[tuple[Any, ...], bool] | None = None,
    notional_cache: dict[tuple[Any, ...], float] | None = None,
    exit_cache: dict[tuple[Any, ...], tuple[float, str]] | None = None,
) -> dict[str, Any]:
    cfg = _gate_cfg(base_cfg, opt)
    dsl = copy.deepcopy(base_cfg.get("dsl_exit") or {})
    dsl["max_loss_pct"] = opt["max_loss"]
    dsl["max_loss_roe_pct"] = opt["roe_cap"]
    dsl["protect_pct"] = opt["protect"]
    dsl["retrace_threshold"] = opt["retrace"]
    cooldown_ms = int(opt["cooldown"]) * 60_000
    last_by_coin: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    admitted_pre_size = 0
    skipped_size = 0
    skipped_cooldown = 0

    for c in sorted(candidates, key=lambda x: x.ts):
        admit_key = (
            c.ts, c.coin, c.side,
            opt["allow_shorts"], opt["min_conf"], opt["min_composite"],
            opt["min_hip3_composite"], opt["min_short_conf"],
            opt["min_short_composite"], opt["mover_conf"], opt["mover_composite"],
        )
        if admission_cache is not None and admit_key in admission_cache:
            admitted = admission_cache[admit_key]
        else:
            admitted = _admitted(c, cfg, opt)
            if admission_cache is not None:
                admission_cache[admit_key] = admitted
        if not admitted:
            continue
        admitted_pre_size += 1
        last = last_by_coin.get(c.coin)
        if last is not None and c.ts - last < cooldown_ms:
            skipped_cooldown += 1
            continue
        notional_key = (
            c.ts, c.coin, opt["sizing"], equity, opt["leverage"], opt["fraction"],
            opt["risk_pct"], opt["max_notional"], opt["max_loss"], opt["roe_cap"],
            opt["sl_atr_mult"],
        )
        if notional_cache is not None and notional_key in notional_cache:
            notional = notional_cache[notional_key]
        else:
            notional = _notional(c, opt, equity)
            if notional_cache is not None:
                notional_cache[notional_key] = notional
        if notional < 10.5:
            skipped_size += 1
            continue
        eff_lev = btlog.max_leverage_for(c.coin, int(opt["leverage"]))
        round_trip_cost_roe = (taker_bps + slippage_bps) * 2 * eff_lev / 100.0
        exit_key = (
            c.ts, c.coin, c.side, eff_lev,
            opt["max_loss"], opt["roe_cap"], opt["protect"], opt["retrace"],
        )
        if exit_cache is not None and exit_key in exit_cache:
            gross_roe, reason = exit_cache[exit_key]
        else:
            gross_roe, reason, _, _ = btlog.simulate_dsl_exit(
                c.entry_px, c.side, eff_lev, c.forward, dsl
            )
            if exit_cache is not None:
                exit_cache[exit_key] = (gross_roe, reason)
        roe = gross_roe - round_trip_cost_roe
        pnl = (roe / 100.0) * (notional / eff_lev)
        rows.append({
            "ts": c.ts,
            "coin": c.coin,
            "pnl": pnl,
            "roe": roe,
            "reason": reason,
            "notional": notional,
        })
        last_by_coin[c.coin] = c.ts

    net = sum(r["pnl"] for r in rows)
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] < 0]
    gross_win = sum(r["pnl"] for r in wins)
    gross_loss = -sum(r["pnl"] for r in losses)
    avg_notional = sum(r["notional"] for r in rows) / len(rows) if rows else 0.0
    if split_ts is None and rows:
        split_ts = sorted(r["ts"] for r in rows)[len(rows) // 2]
    first = sum(r["pnl"] for r in rows if split_ts is not None and r["ts"] <= split_ts)
    second = sum(r["pnl"] for r in rows if split_ts is not None and r["ts"] > split_ts)
    eq = equity
    peak = equity
    max_dd = 0.0
    for r in rows:
        eq += r["pnl"]
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return {
        **opt,
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(rows) * 100.0) if rows else 0.0,
        "net": net,
        "first": first,
        "second": second,
        "pf": (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0),
        "avg_pnl": (net / len(rows)) if rows else 0.0,
        "avg_notional": avg_notional,
        "max_dd": max_dd,
        "admitted": admitted_pre_size,
        "size_skips": skipped_size,
        "cooldown_skips": skipped_cooldown,
    }


def _option_space(args: argparse.Namespace) -> list[dict[str, Any]]:
    opts: list[dict[str, Any]] = []
    if args.dense:
        leverages = range(1, 21)
        legacy_fracs = (0.08, 0.10, 0.12, 0.16, 0.20, 0.28, 0.35)
        risks = (0.0075, 0.01, 0.0125, 0.015, 0.02, 0.025, 0.03, 0.04)
        caps = (75.0, 100.0, 120.0, 150.0, 180.0, 220.0, 250.0, 300.0, 350.0, 400.0, 500.0, 650.0, 800.0)
        stop_pairs = (
            (0.50, 4.0), (0.50, 6.0), (0.60, 4.0), (0.60, 6.0),
            (0.75, 6.0), (0.75, 8.0), (1.00, 8.0), (1.20, 10.0),
        )
        mover_comps = (10.0, 15.0, 20.0, 25.0, 30.0)
    else:
        leverages = (8, 10, 12, 15)
        legacy_fracs = (0.10, 0.12, 0.20, 0.28)
        risks = (0.01, 0.015, 0.02, 0.03, 0.04)
        caps = (120.0, 180.0, 250.0, 350.0, 500.0)
        stop_pairs = ((0.60, 6.0), (0.75, 6.0), (0.75, 8.0), (1.00, 8.0))
        mover_comps = (10.0, 20.0, 25.0, 30.0)
    for sizing in ("atr_backup", "hybrid", "dsl_stop", "legacy"):
        for leverage in leverages:
            for fraction in (legacy_fracs if sizing == "legacy" else (0.12,)):
                for risk_pct in (risks if sizing != "legacy" else (0.0,)):
                    for max_notional in caps:
                        for max_loss, roe_cap in stop_pairs:
                            for mover_comp in mover_comps:
                                opts.append({
                                    "sizing": sizing,
                                    "leverage": leverage,
                                    "fraction": fraction,
                                    "risk_pct": risk_pct,
                                    "max_notional": max_notional,
                                    "max_loss": max_loss,
                                    "roe_cap": roe_cap,
                                    "protect": 1.5,
                                    "retrace": 0.30,
                                    "sl_atr_mult": 1.5,
                                    "cooldown": 60,
                                    "min_conf": 0.70,
                                    "counter_conf": 0.80,
                                    "min_composite": 30.0,
                                    "min_hip3_composite": 50.0,
                                    "allow_shorts": True,
                                    "min_short_conf": 0.72,
                                    "min_short_composite": 25.0,
                                    "mover_conf": 0.72,
                                    "mover_composite": mover_comp,
                                })
    return opts


def _fmt(r: dict[str, Any]) -> str:
    return (
        f"{r['net']:>+8.2f} {r['first']:>+7.2f} {r['second']:>+7.2f} "
        f"{r['trades']:>3} {r['win_rate']:>5.0f}% {r['pf']:>5.2f} "
        f"{r['max_dd']:>6.2f} {r['avg_notional']:>7.0f} "
        f"{r['admitted']:>3}/{r['size_skips']:<2} "
        f"{r['sizing']:<10} lev={r['leverage']:<2} frac={r['fraction']:<4.2f} "
        f"risk={r['risk_pct']:<4.3f} cap={r['max_notional']:<5.0f} "
        f"stop={r['max_loss']}/{r['roe_cap']} mover={r['mover_composite']:<4.0f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=168)
    ap.add_argument("--equity", type=float, default=250.0)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-trades", type=int, default=6)
    ap.add_argument("--dense", action="store_true",
                    help="Search every integer leverage 1..20 and wider cap/risk/stop/gate grids.")
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--taker-fee-bps", type=float, default=2.5)
    ap.add_argument("--cache-file", default=os.path.join(tempfile.gettempdir(), "hermes_backtest_logged_candles.json"))
    args = ap.parse_args()

    btlog._load_disk_cache(args.cache_file)
    cfg = read_agent_config()
    candidates = build_candidates(args, cfg)
    split_ts = sorted(c.ts for c in candidates)[len(candidates) // 2] if candidates else None
    print(f"# candidates={len(candidates)} hours={args.hours} equity=${args.equity:.0f} "
          f"costs={args.taker_fee_bps:g}bps + {args.slippage_bps:g}bps slippage per side")
    if not candidates:
        return 1

    admission_cache: dict[tuple[Any, ...], bool] = {}
    notional_cache: dict[tuple[Any, ...], float] = {}
    exit_cache: dict[tuple[Any, ...], tuple[float, str]] = {}
    results = [
        evaluate(candidates, cfg, opt, equity=args.equity,
                 taker_bps=args.taker_fee_bps, slippage_bps=args.slippage_bps,
                 split_ts=split_ts, admission_cache=admission_cache,
                 notional_cache=notional_cache, exit_cache=exit_cache)
        for opt in _option_space(args)
    ]
    results = [r for r in results if r["trades"] >= args.min_trades]
    stable = [r for r in results if r["first"] > 0 and r["second"] > 0 and r["pf"] >= 1.2]
    stable.sort(key=lambda r: (r["net"], r["pf"], -r["max_dd"]), reverse=True)
    results.sort(key=lambda r: (r["net"], r["pf"], -r["max_dd"]), reverse=True)

    header = "     net   first  second   n   win%    pf  maxDD  avgNot adm/low config"
    print("\n# Stable top (positive on both halves)")
    print(header)
    for r in stable[:args.top]:
        print(_fmt(r))
    print("\n# Raw top")
    print(header)
    for r in results[:args.top]:
        print(_fmt(r))
    print("\n# Best by max_trade_notional_usd")
    print(header)
    for cap in sorted({r["max_notional"] for r in results}):
        cap_rows = [r for r in results if r["max_notional"] == cap]
        if not cap_rows:
            continue
        print(_fmt(max(cap_rows, key=lambda r: (r["net"], r["pf"], -r["max_dd"]))))
    print("\n# Best by leverage")
    print(header)
    for lev in sorted({r["leverage"] for r in results}):
        lev_rows = [r for r in results if r["leverage"] == lev]
        if not lev_rows:
            continue
        print(_fmt(max(lev_rows, key=lambda r: (r["net"], r["pf"], -r["max_dd"]))))
    print("\n# Best by sizing mode")
    print(header)
    for sizing in sorted({r["sizing"] for r in results}):
        sizing_rows = [r for r in results if r["sizing"] == sizing]
        if not sizing_rows:
            continue
        print(_fmt(max(sizing_rows, key=lambda r: (r["net"], r["pf"], -r["max_dd"]))))
    print("\n# Current-live approximation")
    current_sizing = "atr_backup"
    _current_basis = str(((cfg.get("atr_risk_sizing") or {}).get("sizing_basis", "")) or "").lower()
    if _current_basis in ("primary_stop", "dsl_stop"):
        current_sizing = "dsl_stop"
    current = {
        "sizing": current_sizing,
        "leverage": int(cfg.get("leverage", 8)),
        "fraction": float(cfg.get("equity_fraction_per_trade", 0.12)),
        "risk_pct": float((cfg.get("atr_risk_sizing") or {}).get("risk_per_trade_pct", 0.01)),
        "max_notional": float(cfg.get("max_trade_notional_usd", 120.0)),
        "max_loss": float((cfg.get("dsl_exit") or {}).get("max_loss_pct", 0.75)),
        "roe_cap": float((cfg.get("dsl_exit") or {}).get("max_loss_roe_pct", 6.0)),
        "protect": float((cfg.get("dsl_exit") or {}).get("protect_pct", 1.5)),
        "retrace": float((cfg.get("dsl_exit") or {}).get("retrace_threshold", 0.30)),
        "sl_atr_mult": float(cfg.get("sl_atr_mult", 1.5)),
        "cooldown": int(cfg.get("cooldown_min", 60)),
        "min_conf": float(cfg.get("min_ai_confidence", 0.70)),
        "counter_conf": float(cfg.get("counter_regime_min_conf", 0.80)),
        "min_composite": float((cfg.get("runner_entry_gate") or {}).get("min_composite", 30.0)),
        "min_hip3_composite": float((cfg.get("runner_entry_gate") or {}).get("min_hip3_composite", 50.0)),
        "allow_shorts": bool((cfg.get("runner_entry_gate") or {}).get("allow_shorts", False)),
        "min_short_conf": float((cfg.get("runner_entry_gate") or {}).get("min_short_confidence", 0.72)),
        "min_short_composite": float((cfg.get("runner_entry_gate") or {}).get("min_short_composite", 25.0)),
        "mover_conf": float((cfg.get("runner_entry_gate") or {}).get("mover_min_confidence", 0.72)),
        "mover_composite": float((cfg.get("runner_entry_gate") or {}).get("mover_min_composite", 20.0)),
    }
    print(header)
    print(_fmt(evaluate(candidates, cfg, current, equity=args.equity,
                        taker_bps=args.taker_fee_bps, slippage_bps=args.slippage_bps,
                        split_ts=split_ts, admission_cache=admission_cache,
                        notional_cache=notional_cache, exit_cache=exit_cache)))
    btlog._save_disk_cache(args.cache_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
