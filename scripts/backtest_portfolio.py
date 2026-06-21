#!/usr/bin/env python3
"""PORTFOLIO-level backtest — the one the per-trade replays can't do.

Unlike backtest_logged/backtest (which sim each trade in isolation), this walks a
GLOBAL 5-min clock with a SHARED equity pool and enforces the real portfolio gates:
  - max_concurrent      (only N positions open at once)
  - max_total_notional  (gross exposure cap vs equity)
  - min_available_margin (margin floor)
So capital contention AND correlated drawdowns (many positions stopping in the
same dip → equity drops → margin tightens) actually show up. Drives off the REAL
logged AI verdicts; exits use the live DSL (scalp) config.

Lets you answer "what happens at max_concurrent=N / gross cap=X%" for real.

Usage: python3 scripts/backtest_portfolio.py --max-concurrent 8
       python3 scripts/backtest_portfolio.py --sweep-concurrent 4,6,8,10,15
"""
from __future__ import annotations

import argparse
import tempfile
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

import backtest_logged as btlog
from backtest_logged import (  # reuse validated primitives
    fetch_candles_at, detect_regime_at, passes_counter_regime,
    _load_disk_cache, _save_disk_cache,
)
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.agents.executor import _runner_entry_block_reason
from hermes_trader.agents.rotation import decide_rotation  # validate the REAL mechanism
from hermes_trader.models.types import Candle
from _memory_io import load_memory

STEP_MS = 300_000  # 5-min clock
ROUND_TRIP_FEE_RATE = 0.0005  # live executor model: 2.5 bps in + 2.5 bps out


class Position:
    """One open trade; step(bar) returns (exit_px, reason) or None."""
    def __init__(self, coin, side, entry_px, lev, notional, margin, dsl, cost_rate):
        self.coin, self.side, self.entry_px = coin, side, entry_px
        self.lev, self.notional, self.margin = lev, notional, margin
        self.cost_rate = cost_rate
        self.peak = entry_px
        self.max_loss = min(float(dsl.get("max_loss_pct", 0.75)),
                            float(dsl.get("max_loss_roe_pct", 6.0)) / max(1, lev))
        self.protect = float(dsl.get("protect_pct", 1.5))
        self.retrace = float(dsl.get("retrace_threshold", 0.30))

    def step(self, bar: Candle):
        is_long = self.side == "long"
        loss = (self.entry_px - bar.l) / self.entry_px * 100 if is_long \
            else (bar.h - self.entry_px) / self.entry_px * 100
        if loss >= self.max_loss:
            px = self.entry_px * (1 - self.max_loss / 100) if is_long \
                else self.entry_px * (1 + self.max_loss / 100)
            return px, "max_loss"
        if is_long and bar.h > self.peak: self.peak = bar.h
        elif not is_long and bar.l < self.peak: self.peak = bar.l
        if is_long:
            if (self.peak - self.entry_px) / self.entry_px * 100 >= self.protect:
                floor = self.peak - (self.peak - self.entry_px) * self.retrace
                if bar.l <= floor:
                    return floor, "floor_breach"
        else:
            if (self.entry_px - self.peak) / self.entry_px * 100 >= self.protect:
                ceil = self.peak + (self.entry_px - self.peak) * self.retrace
                if bar.h >= ceil:
                    return ceil, "floor_breach"
        return None

    def pnl_usd(self, exit_px):
        gross = (exit_px - self.entry_px) / self.entry_px if self.side == "long" \
            else (self.entry_px - exit_px) / self.entry_px
        return self.notional * (gross - self.cost_rate)


def build_candidates(args, cfg) -> List[Dict[str, Any]]:
    dsl_cfg = cfg.get("dsl_exit", {})
    min_conf = args.min_conf or float(cfg.get("min_ai_confidence", 0.70))
    counter_min = float(cfg.get("counter_regime_min_conf", 0.8))
    timeout_min = float(dsl_cfg.get("hard_timeout_minutes", 1800.0))
    mem = load_memory(_REPO / ".agent-memory.json")
    analyses = sorted(mem.get("analyses", []), key=lambda a: a.get("created_at", 0))
    percs = {p["id"]: p for p in mem.get("perceptions", []) if "id" in p}
    import time as _t
    cutoff = int(_t.time() * 1000) - args.hours * 3600_000
    out = []
    regime_cache: Dict[int, str] = {}
    for a in analyses:
        if a.get("created_at", 0) < cutoff:
            continue
        v = a.get("verdict")
        conf = float(a.get("confidence", 0))
        ts = int(a.get("created_at", 0))
        coin = a.get("coin")
        if not coin:
            continue
        perc = percs.get(a.get("perception_id"))
        comp = float(perc.get("composite_score", 0)) if perc else 0.0
        trg = (perc or {}).get("triggers", []) or []
        burst = any(t.get("name") == "momentumBurst" and t.get("fired") for t in trg)
        slow_count = sum(
            1 for t in trg
            if t.get("name") in ("volumeBuildup1h", "trendFlip1h", "higherLows1h")
            and t.get("fired")
        )
        slow = slow_count > 0
        ai_ls = v in ("LONG", "SHORT")
        ta_confirmed = (
            comp >= args.force_bar
            or burst
        )
        side: Optional[str] = None
        sidestep_override = False
        if args.mode == "ai":
            if not ai_ls or conf < min_conf:
                continue
            side = "long" if v == "LONG" else "short"
        elif args.mode == "lowconf":
            if not ai_ls or conf < min_conf:
                continue
            side = "long" if v == "LONG" else "short"
        elif args.mode == "force":
            if ai_ls and conf >= min_conf:
                side = "long" if v == "LONG" else "short"
            elif comp >= args.force_bar:
                side = "long"
                conf = max(conf, min_conf)
            else:
                continue
        elif args.mode == "sidestep":
            if ta_confirmed:
                side = "long"
                conf = max(conf, min_conf)
                sidestep_override = True
            elif ai_ls and conf >= min_conf:
                side = "long" if v == "LONG" else "short"
            else:
                continue
        else:
            raise ValueError(f"unknown mode {args.mode}")
        if args.long_only and side == "short":
            continue
        bk = ts // (30 * 60_000)
        if bk not in regime_cache:
            regime_cache[bk] = "neutral" if args.cache_only else detect_regime_at(ts)
        if not passes_counter_regime(side, regime_cache[bk], conf, comp, burst, slow, counter_min):
            continue
        if not args.skip_runner_gate:
            gate_analysis = dict(a)
            gate_analysis["side"] = side
            gate_analysis["confidence"] = conf
            gate_analysis["composite_score"] = comp
            if sidestep_override:
                gate_analysis["sidestep_override"] = True
            blocked = _runner_entry_block_reason(gate_analysis, cfg)
            if blocked:
                continue
        candle_count = int(timeout_min // 5) + 10
        candle_end = ts + int(timeout_min * 60_000) + 600_000
        if args.cache_only:
            disk_key = btlog._cache_key(coin, "5m", candle_count, candle_end)
            fwd = btlog._candles_from_json(btlog._DISK_CANDLE_CACHE.get(disk_key))
        else:
            try:
                fwd = fetch_candles_at(coin, "5m", candle_count, candle_end)
            except Exception:
                fwd = None
        if not fwd:
            continue
        fwd = [b for b in fwd if b.t >= ts]
        if not fwd:
            continue
        out.append({"ts": ts, "coin": coin, "side": side, "entry_px": fwd[0].o,
                    "candles": fwd[1:], "conf": conf, "comp": comp})
    return out


def _attempt_rotation(open_pos, cand, clock, closes, rot_cfg, block_kind):
    """Mirror the LIVE rotation decision: when `cand` is blocked purely by a capital
    gate, ask the real decide_rotation() whether to evict the weakest non-winner.
    If yes, realize that position at its current mark (incl. its own round-trip cost)
    and free the slot. Returns (freed: bool, realized_pnl: float)."""
    descs = []
    for ec, st in open_pos.items():
        p = st["pos"]
        gross = (st["last_px"] - p.entry_px) / p.entry_px if p.side == "long" \
            else (p.entry_px - st["last_px"]) / p.entry_px
        descs.append({"coin": ec, "roe_pct": gross * p.lev * 100.0,
                      "age_minutes": (clock - st["entry_ts"]) / 60_000.0})
    dec = decide_rotation(
        candidate_coin=cand["coin"],
        candidate_composite=float(cand.get("comp", 0.0)),
        blocked_reasons=[block_kind],
        open_positions=descs,
        min_candidate_composite=float(rot_cfg.get("min_candidate_composite", 40.0)),
        min_hold_minutes=float(rot_cfg.get("min_hold_minutes", 30.0)),
        protect_winner_roe_pct=float(rot_cfg.get("protect_winner_roe_pct", 3.0)),
    )
    if not dec.should_rotate:
        return (False, 0.0)
    st = open_pos[dec.evict_coin]
    pnl = st["pos"].pnl_usd(st["last_px"])   # realize evictee at current price + cost
    closes.append({"coin": dec.evict_coin, "pnl": pnl, "reason": "rotated_out"})
    del open_pos[dec.evict_coin]
    return (True, pnl)


def run(args, cfg, max_concurrent, max_notional_pct, rotate=False) -> Dict[str, Any]:
    dsl_cfg = cfg.get("dsl_exit", {})
    rot_cfg = cfg.get("capital_rotation", {}) or {}
    rotations = 0
    frac = float(cfg.get("equity_fraction_per_trade", 0.12))
    lev = args.leverage or int(cfg.get("leverage", 10))
    min_margin = float(cfg.get("min_available_margin_pct", 0.10))
    equity = args.equity
    cands = sorted(_CANDS, key=lambda c: c["ts"])
    if not cands:
        return {"trades": 0}
    t0, t1 = cands[0]["ts"], max(c["ts"] for c in cands) + int(
        float(dsl_cfg.get("hard_timeout_minutes", 1800)) * 60_000)
    by_entry = {}
    for c in cands:
        by_entry.setdefault(c["ts"] // STEP_MS, []).append(c)

    open_pos: Dict[str, Dict[str, Any]] = {}
    last_entry_by_coin: Dict[str, int] = {}
    loss_block_until: Dict[str, int] = {}
    closes, peak_eq, max_dd = [], equity, 0.0
    blk_conc = blk_notional = blk_margin = blk_dup = blk_size = blk_cooldown = blk_loss_cool = 0
    cooldown_ms = int(float(cfg.get("cooldown_min", 0) or 0) * 60_000)
    loss_cooldown_ms = int(float(args.loss_cooldown_min or 0) * 60_000)
    clock = t0
    while clock <= t1 or open_pos:
        # exits
        for coin, st in list(open_pos.items()):
            pos, cs = st["pos"], st["candles"]
            while st["i"] < len(cs) and cs[st["i"]].t <= clock:
                ex = pos.step(cs[st["i"]])
                st["last_px"] = cs[st["i"]].c
                st["i"] += 1
                if ex:
                    px, reason = ex
                    pnl = pos.pnl_usd(px)
                    equity += pnl
                    closes.append({"coin": coin, "pnl": pnl, "reason": reason})
                    if pnl < 0 and loss_cooldown_ms > 0:
                        loss_block_until[coin] = clock + loss_cooldown_ms
                    del open_pos[coin]
                    break
            else:
                if st["i"] >= len(cs) and coin in open_pos:  # ran out of candles
                    pos = st["pos"]; last = cs[-1] if cs else None
                    if last:
                        gross = (last.c - pos.entry_px) / pos.entry_px if pos.side == "long" \
                            else (pos.entry_px - last.c) / pos.entry_px
                        pnl = pos.notional * (gross - pos.cost_rate)
                        equity += pnl
                        closes.append({"coin": coin, "pnl": pnl, "reason": "end"})
                        if pnl < 0 and loss_cooldown_ms > 0:
                            loss_block_until[coin] = clock + loss_cooldown_ms
                    del open_pos[coin]
        # entries this step
        for c in by_entry.get(clock // STEP_MS, []):
            coin = c["coin"]
            if coin in open_pos:
                blk_dup += 1; continue
            if loss_cooldown_ms > 0 and c["ts"] < loss_block_until.get(coin, 0):
                blk_loss_cool += 1; continue
            last_entry = last_entry_by_coin.get(coin)
            if last_entry is not None and cooldown_ms > 0 and c["ts"] - last_entry < cooldown_ms:
                blk_cooldown += 1; continue
            if len(open_pos) >= max_concurrent:
                if rotate:
                    freed, dpnl = _attempt_rotation(open_pos, c, clock, closes,
                                                    rot_cfg, "max positions reached")
                    if freed:
                        equity += dpnl; rotations += 1
                    else:
                        blk_conc += 1; continue
                else:
                    blk_conc += 1; continue
            eff_lev = btlog.max_leverage_for(coin, lev)
            open_notional = sum(s["pos"].notional for s in open_pos.values())
            new_notional, _sizing = btlog.live_sized_notional(
                coin=coin,
                entry_px=c["entry_px"],
                entry_ms=c["ts"],
                equity=equity,
                equity_fraction=frac,
                leverage=eff_lev,
                cfg=cfg,
                dsl_cfg=dsl_cfg,
            )
            if new_notional < 10.5:
                blk_size += 1; continue
            if open_notional + new_notional > equity * max_notional_pct:
                if rotate:
                    freed, dpnl = _attempt_rotation(open_pos, c, clock, closes,
                                                    rot_cfg, "total notional would exceed")
                    if freed:
                        equity += dpnl; rotations += 1
                        open_notional = sum(s["pos"].notional for s in open_pos.values())
                    if open_notional + new_notional > equity * max_notional_pct:
                        blk_notional += 1; continue
                else:
                    blk_notional += 1; continue
            used_margin = sum(s["pos"].margin for s in open_pos.values())
            new_margin = new_notional / max(1, eff_lev)
            if (equity - used_margin - new_margin) / equity < min_margin:
                blk_margin += 1; continue
            cost_rate = ROUND_TRIP_FEE_RATE + (float(args.slippage_bps or 0.0) * 2.0 / 10000.0)
            pos = Position(coin, c["side"], c["entry_px"], eff_lev, new_notional,
                           new_margin, dsl_cfg, cost_rate)
            open_pos[coin] = {"pos": pos, "candles": c["candles"], "i": 0,
                              "entry_ts": c["ts"], "last_px": c["entry_px"]}
            last_entry_by_coin[coin] = c["ts"]
        peak_eq = max(peak_eq, equity)
        max_dd = max(max_dd, peak_eq - equity)
        clock += STEP_MS

    n = len(closes)
    wins = [c for c in closes if c["pnl"] > 0]
    net = sum(c["pnl"] for c in closes)
    rot_out = [c for c in closes if c.get("reason") == "rotated_out"]
    return {"trades": n, "win": (len(wins) / n * 100 if n else 0),
            "net": net, "exp": (net / n if n else 0), "end_eq": equity,
            "max_dd": max_dd, "blk_conc": blk_conc, "blk_notional": blk_notional,
            "blk_margin": blk_margin, "blk_size": blk_size, "blk_cooldown": blk_cooldown,
            "blk_loss_cool": blk_loss_cool, "rotations": rotations,
            "rot_out_pnl": sum(c["pnl"] for c in rot_out)}


_CANDS: List[Dict[str, Any]] = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--equity", type=float, default=200.0)
    ap.add_argument("--mode", default="ai", choices=["ai", "lowconf", "force", "sidestep"])
    ap.add_argument("--force-bar", type=float, default=30.0)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--loss-cooldown-min", type=float, default=0.0)
    ap.add_argument("--leverage", type=int, default=0)
    ap.add_argument("--max-notional", type=float, default=0.0)
    ap.add_argument("--risk-pct", type=float, default=0.0)
    ap.add_argument("--sizing-basis", default="")
    ap.add_argument("--max-loss", type=float, default=0.0)
    ap.add_argument("--roe-cap", type=float, default=0.0)
    ap.add_argument("--protect", type=float, default=0.0)
    ap.add_argument("--retrace", type=float, default=0.0)
    ap.add_argument("--cooldown", type=float, default=None)
    ap.add_argument("--slippage-bps", type=float, default=0.0)
    ap.add_argument("--min-margin", type=float, default=None)
    ap.add_argument("--min-conf", type=float, default=0.0)
    ap.add_argument("--runner-min-confidence", type=float, default=None)
    ap.add_argument("--runner-min-composite", type=float, default=None)
    ap.add_argument("--runner-min-hip3-composite", type=float, default=None)
    ap.add_argument("--runner-min-short-confidence", type=float, default=None)
    ap.add_argument("--runner-min-short-composite", type=float, default=None)
    ap.add_argument("--runner-mover-min-confidence", type=float, default=None)
    ap.add_argument("--runner-mover-min-composite", type=float, default=None)
    ap.add_argument("--max-concurrent", type=int, default=0)
    ap.add_argument("--max-notional-pct", type=float, default=0.0)
    ap.add_argument("--sweep-concurrent", default="", help="e.g. 4,6,8,10,15")
    ap.add_argument("--skip-runner-gate", action="store_true",
                    help="Do not apply the current live runner_entry_gate to candidates")
    ap.add_argument("--rotate", action="store_true",
                    help="Add a ROTATE arm (capital_rotation: evict weakest non-winner for a "
                         "stronger capital-blocked candidate) and compare vs HOLD baseline")
    ap.add_argument("--cache-file", default=f"{tempfile.gettempdir()}/hermes_backtest_logged_candles.json",
                    help="Disk candle cache shared with backtest_logged.py")
    ap.add_argument("--cache-only", action="store_true",
                    help="Use only cached candles; skip uncached candidates instead of hitting HL")
    args = ap.parse_args()
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
    if args.max_loss or args.roe_cap or args.protect or args.retrace:
        cfg = dict(cfg)
        dsl = dict(cfg.get("dsl_exit", {}) or {})
        if args.max_loss:
            dsl["max_loss_pct"] = args.max_loss
        if args.roe_cap:
            dsl["max_loss_roe_pct"] = args.roe_cap
        if args.protect:
            dsl["protect_pct"] = args.protect
        if args.retrace:
            dsl["retrace_threshold"] = args.retrace
        cfg["dsl_exit"] = dsl
    if args.cooldown is not None:
        cfg = dict(cfg)
        cfg["cooldown_min"] = args.cooldown
    runner_overrides = {
        "min_confidence": args.runner_min_confidence,
        "min_composite": args.runner_min_composite,
        "min_hip3_composite": args.runner_min_hip3_composite,
        "min_short_confidence": args.runner_min_short_confidence,
        "min_short_composite": args.runner_min_short_composite,
        "mover_min_confidence": args.runner_mover_min_confidence,
        "mover_min_composite": args.runner_mover_min_composite,
    }
    if any(v is not None for v in runner_overrides.values()):
        cfg = dict(cfg)
        gate = dict(cfg.get("runner_entry_gate") or {})
        for key, val in runner_overrides.items():
            if val is not None:
                gate[key] = float(val)
        cfg["runner_entry_gate"] = gate
    if args.min_margin is not None:
        cfg = dict(cfg)
        cfg["min_available_margin_pct"] = args.min_margin
    _load_disk_cache(args.cache_file)
    global _CANDS
    print("# building candidates from real AI verdicts (fetching forward candles)...")
    _CANDS = build_candidates(args, cfg)
    mnp = args.max_notional_pct or float(cfg.get("max_total_notional_pct", 8.0))
    lev = args.leverage or int(cfg.get("leverage", 10))
    print(f"# {len(_CANDS)} admitted candidates | equity ${args.equity:.0f} | lev {lev}x | "
          f"gross cap {mnp:.0f}x | last {args.hours}h")
    print(f"# runner gate: {'skipped' if args.skip_runner_gate else cfg.get('runner_entry_gate', {})}\n")
    concs = [int(x) for x in args.sweep_concurrent.split(",")] if args.sweep_concurrent \
        else [args.max_concurrent or int(cfg.get("max_concurrent", 15))]
    arms = [("HOLD", False)] + ([("ROTATE", True)] if args.rotate else [])
    if args.rotate:
        print(f"# capital_rotation: {cfg.get('capital_rotation', {})}\n")
    print(f"{'arm':>7} {'max_conc':>9} {'trades':>7} {'win%':>6} {'exp/trade':>10} {'net':>9} "
          f"{'endEq':>8} {'maxDD':>7} {'rot':>4}  blocks(conc/notnl/margin/size/cool/loss)")
    for mc in concs:
        base_net = None
        for label, rot in arms:
            r = run(args, cfg, mc, mnp, rotate=rot)
            if not r.get("trades"):
                print(f"{label:>7} {mc:>9}  no trades"); continue
            delta = ""
            if label == "HOLD":
                base_net = r["net"]
            elif base_net is not None:
                delta = f"  (Δnet {r['net'] - base_net:+.2f})"
            print(f"{label:>7} {mc:>9} {r['trades']:>7} {r['win']:>5.0f}% {r['exp']:>+9.2f} "
                  f"{r['net']:>+8.2f} {r['end_eq']:>7.0f} {r['max_dd']:>6.1f} {r['rotations']:>4}  "
                  f"{r['blk_conc']}/{r['blk_notional']}/{r['blk_margin']}/{r['blk_size']}/"
                  f"{r['blk_cooldown']}/{r['blk_loss_cool']}{delta}")
    _save_disk_cache(args.cache_file)


if __name__ == "__main__":
    main()
