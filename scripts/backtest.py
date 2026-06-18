#!/usr/bin/env python3
"""Backtest the hermes-trader strategy on historical Hyperliquid candles.

Walks 1h-bar history per coin, evaluates the same triggers + TA-filter
logic as the live scanner, simulates entries with the current sizing
formula (equity_fraction x per-coin-max leverage), and exits via the DSL
two-phase trailing-stop engine. PnL is net of round-trip taker fees.

The AI research step is *substituted* with a deterministic heuristic that
mirrors the system prompt's entry rules — calling OpenRouter per signal
over historical bars would be too expensive. The mechanical edge is
tested; real AI judgment is not.

Caveats reported in the summary so they aren't lost.

Usage:
    python3 scripts/backtest.py                    # defaults: 14 days, 20 coins
    python3 scripts/backtest.py --days 30 --coins 30
    python3 scripts/backtest.py --equity 200 --interval 1h
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# load .env.local (HL is public; we just want the same module imports working)
_REPO = Path(__file__).resolve().parents[1]
_env = _REPO / ".env.local"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())
sys.path.insert(0, str(_REPO))

from hermes_trader.agents.config import get_config
from hermes_trader.agents.config_store import read_agent_config
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.client.universe import get_universe
from hermes_trader.indicators import math as ind
from hermes_trader.indicators import triggers as trig
from hermes_trader.models.types import Candle

# Hyperliquid perp taker fee model used by the live executor: 2.5 bps per side.
ROUND_TRIP_FEE_BPS = 5.0


@dataclass
class Trade:
    coin: str
    side: str           # "long" or "short"
    entry_bar: int
    entry_px: float
    notional: float
    margin: float
    leverage: int
    exit_bar: int = 0
    exit_px: float = 0.0
    pnl_usd: float = 0.0
    exit_reason: str = ""


@dataclass
class DSL:
    """Local re-implementation of dsl_exit's two-phase trailing stop."""
    side: str
    entry_px: float
    entry_bar: int
    peak_px: float
    max_loss_pct: float = 2.5
    protect_pct: float = 1.5
    retrace_threshold: float = 0.30
    hard_timeout_bars: int = 180

    def check_bar(self, bar_idx: int, bar: Candle) -> Tuple[bool, float, str]:
        """Did this bar trigger an exit? Stops fire intra-bar at the stop price."""
        is_long = self.side == "long"
        # Update peak using the bar's high/low (long peaks on high, short on low)
        if is_long and bar.h > self.peak_px:
            self.peak_px = bar.h
        if not is_long and bar.l < self.peak_px:
            self.peak_px = bar.l

        if bar_idx - self.entry_bar >= self.hard_timeout_bars:
            return True, bar.c, "hard_timeout"

        # Max-loss stop
        max_loss_px = (self.entry_px * (1 - self.max_loss_pct / 100) if is_long
                       else self.entry_px * (1 + self.max_loss_pct / 100))
        if is_long and bar.l <= max_loss_px:
            return True, max_loss_px, f"max_loss {self.max_loss_pct}%"
        if not is_long and bar.h >= max_loss_px:
            return True, max_loss_px, f"max_loss {self.max_loss_pct}%"

        # Phase-2 trailing floor (only active once protect_pct profit reached)
        if is_long:
            peak_profit_pct = (self.peak_px - self.entry_px) / self.entry_px * 100
            if peak_profit_pct >= self.protect_pct:
                profit_range = self.peak_px - self.entry_px
                floor = self.entry_px + profit_range * (1 - self.retrace_threshold)
                if bar.l <= floor:
                    return True, floor, "trailing_stop"
        else:
            peak_profit_pct = (self.entry_px - self.peak_px) / self.entry_px * 100
            if peak_profit_pct >= self.protect_pct:
                profit_range = self.entry_px - self.peak_px
                ceiling = self.entry_px - profit_range * (1 - self.retrace_threshold)
                if bar.h >= ceiling:
                    return True, ceiling, "trailing_stop"

        return False, 0.0, ""


def _evaluate(window: List[Candle], cfg: Dict[str, Any]) -> Tuple[float, list]:
    """Run the 6 live triggers + composite score on the trailing window."""
    th, w = cfg["thresholds"], cfg["weights"]
    hits = [
        trig.pct_move_spike(window, th["sigmaThreshold"]),
        trig.volume_spike(window, th["sigmaThreshold"]),
        trig.breakout(window, th["breakoutLookback"]),
        trig.range_compression(window, th["bbLength"], th["bbStdDev"]),
        trig.trend_strength(window, th["adxPeriod"]),
        trig.momentum_burst(window, th["momentumLookback"], th["momentumPct"]),
    ]
    return trig.composite_score(hits, w), hits


def _trend_and_atr_pct(window: List[Candle]) -> Tuple[Optional[bool], Optional[float], Optional[float]]:
    """4h-style EMA trend, ATR% of price, ADX(14). None if insufficient data."""
    closes = [c.c for c in window]
    if len(closes) < 30:
        return None, None, None
    e8 = ind.ema(closes, 8)[-1]; e21 = ind.ema(closes, 21)[-1]
    if not (math.isfinite(e8) and math.isfinite(e21)):
        return None, None, None
    a = ind.atr(window, 14)[-1]
    if not math.isfinite(a) or closes[-1] == 0:
        return None, None, None
    atr_pct = a / closes[-1] * 100
    adx14 = ind.adx(window, 14)[-1]
    return e8 > e21, atr_pct, (adx14 if math.isfinite(adx14) else None)


def _heuristic_verdict(score: float, hits, bullish: Optional[bool],
                       atr_pct: Optional[float]) -> Optional[str]:
    """Stand-in for AI: 'score >= 25 OR directional trend with ATR >= 0.4%'."""
    if bullish is None:
        return None
    burst = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
    score_ok = score >= 25
    trend_ok = atr_pct is not None and atr_pct >= 0.4
    if not (score_ok or trend_ok or burst):
        return None
    return "LONG" if bullish else "SHORT"


def _ta_confirmed(bullish, atr_pct, adx14, composite: float) -> bool:
    """Local proxy for ta_filter.analyze_perception's CONFIRMED gate (score >= 45)."""
    if bullish is None or atr_pct is None:
        return False
    s = 20  # trend present
    if 30 < (atr_pct * 10) < 700:  # very loose proxy for RSI window
        s += 15
    if atr_pct >= 0.5:
        s += 15
    if adx14 is not None and adx14 >= 25:
        s += 15
    s += min(15, composite / 100 * 15)
    return s >= 45


def _simulate(coin: str, candles: List[Candle], max_lev: int, *,
              equity: float, equity_fraction: float, lev_ceiling: int,
              cfg: Dict[str, Any], warmup: int = 100,
              max_loss_pct: float = 2.5, protect_pct: float = 1.5,
              retrace_threshold: float = 0.30,
              atr_mult: float = 0.0, atr_floor: float = 1.0,
              atr_ceiling: float = 4.0,
              stop_widths: Optional[list] = None) -> List[Trade]:
    trades: List[Trade] = []
    open_t: Optional[Trade] = None
    open_dsl: Optional[DSL] = None
    fee_pct = ROUND_TRIP_FEE_BPS / 10000.0

    for i in range(warmup, len(candles) - 1):
        window = candles[: i + 1]
        bar = candles[i]
        next_bar = candles[i + 1]

        # Manage open position
        if open_t and open_dsl:
            done, exit_px, reason = open_dsl.check_bar(i, bar)
            if done:
                gross_pct = ((exit_px - open_t.entry_px) / open_t.entry_px
                             if open_t.side == "long"
                             else (open_t.entry_px - exit_px) / open_t.entry_px)
                open_t.exit_bar = i
                open_t.exit_px = exit_px
                open_t.pnl_usd = open_t.notional * (gross_pct - fee_pct)
                open_t.exit_reason = reason
                trades.append(open_t)
                open_t = open_dsl = None
            else:
                continue   # one open trade per coin at a time

        # Look for entry
        score, hits = _evaluate(window, cfg)
        bullish, atr_pct, adx14 = _trend_and_atr_pct(window)
        verdict = _heuristic_verdict(score, hits, bullish, atr_pct)
        if verdict is None:
            continue
        burst = any(h["name"] == "momentumBurst" and h["fired"] for h in hits)
        if not _ta_confirmed(bullish, atr_pct, adx14, score) and not burst:
            continue

        side = "long" if verdict == "LONG" else "short"
        lev = min(lev_ceiling, max_lev)
        notional = equity * equity_fraction * lev
        margin = equity * equity_fraction
        open_t = Trade(coin=coin, side=side, entry_bar=i + 1, entry_px=next_bar.o,
                       notional=notional, margin=margin, leverage=lev)
        # ATR-stop mode: stop width = atr_mult × ATR% at entry, clamped — mirrors
        # the live dsl_exit.atr_stop feature. atr_mult=0 keeps the fixed stop.
        eff_max_loss = max_loss_pct
        if atr_mult > 0 and atr_pct is not None and atr_pct > 0:
            eff_max_loss = min(max(atr_pct * atr_mult, atr_floor), atr_ceiling)
            if stop_widths is not None:
                stop_widths.append(eff_max_loss)
        open_dsl = DSL(side=side, entry_px=next_bar.o, entry_bar=i + 1,
                       peak_px=next_bar.o, max_loss_pct=eff_max_loss,
                       protect_pct=protect_pct, retrace_threshold=retrace_threshold)
    return trades


def _print_summary(all_trades: List[Trade], equity: float, days: int) -> None:
    print("\n=== SUMMARY ===")
    n = len(all_trades)
    if n == 0:
        print("no trades fired")
        return
    wins = [t for t in all_trades if t.pnl_usd > 0]
    losses = [t for t in all_trades if t.pnl_usd < 0]
    pnl_total = sum(t.pnl_usd for t in all_trades)
    avg_win = (sum(t.pnl_usd for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(t.pnl_usd for t in losses) / len(losses)) if losses else 0.0
    expectancy = pnl_total / n
    by_reason: Dict[str, int] = {}
    for t in all_trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1

    print(f"trades        : {n}")
    print(f"win rate      : {len(wins)}/{n} = {len(wins) / n * 100:.1f}%")
    print(f"avg win       : ${avg_win:+.2f}")
    print(f"avg loss      : ${avg_loss:+.2f}")
    print(f"expectancy    : ${expectancy:+.3f} per trade")
    print(f"total PnL     : ${pnl_total:+.2f}  ({pnl_total / equity * 100:+.1f}% on ${equity:.0f}, over {days} days)")
    print(f"exit reasons  : {by_reason}")

    # Sample worst and best
    sorted_t = sorted(all_trades, key=lambda t: t.pnl_usd)
    print("\nworst 3       :")
    for t in sorted_t[:3]:
        print(f"  {t.coin:6} {t.side:5} bars {t.entry_bar}->{t.exit_bar}  "
              f"${t.pnl_usd:+.2f}  {t.exit_reason}")
    print("best 3        :")
    for t in sorted_t[-3:][::-1]:
        print(f"  {t.coin:6} {t.side:5} bars {t.entry_bar}->{t.exit_bar}  "
              f"${t.pnl_usd:+.2f}  {t.exit_reason}")

    print("\nCaveats:")
    print("  - AI verdict substituted with a heuristic (score / trend / burst). Real LLM not replayed.")
    print(f"  - No funding cost, no slippage beyond a {ROUND_TRIP_FEE_BPS:.1f}-bps round-trip fee.")
    print("  - One open position per coin at a time; max_concurrent cap NOT enforced across coins.")
    print("  - Equity held constant (no compounding); cooldown_min not applied.")
    print("  - Past performance does NOT imply future results.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--coins", type=int, default=20)
    ap.add_argument("--interval", default="1h", choices=["5m", "15m", "1h", "4h", "1d"])
    ap.add_argument("--equity", type=float, default=100.0)
    ap.add_argument("--equity-fraction", type=float, default=0.0,
                    help="margin fraction per trade (default: .agent-config.json)")
    ap.add_argument("--leverage-ceiling", type=int, default=0,
                    help="max leverage to simulate (default: .agent-config.json)")
    ap.add_argument("--max-loss", type=float, default=None,
                    help="DSL max_loss_pct spot stop (default: .agent-config.json)")
    ap.add_argument("--protect", type=float, default=None,
                    help="DSL protect_pct spot profit threshold (default: .agent-config.json)")
    ap.add_argument("--retrace", type=float, default=None,
                    help="DSL phase-2 retrace threshold 0-1 (default: .agent-config.json)")
    ap.add_argument("--atr-mult", type=float, default=None,
                    help="ATR stop mult (default: live atr_stop setting; 0 = fixed --max-loss)")
    ap.add_argument("--atr-floor", type=float, default=None,
                    help="ATR stop floor spot pct (default: .agent-config.json)")
    ap.add_argument("--atr-ceiling", type=float, default=None,
                    help="ATR stop ceiling spot pct (default: .agent-config.json)")
    args = ap.parse_args()

    live = read_agent_config()
    live_dsl = live.get("dsl_exit", {}) or {}
    live_atr = live_dsl.get("atr_stop", {}) or {}
    equity_fraction = float(args.equity_fraction or live.get("equity_fraction_per_trade", 0.10))
    leverage_ceiling = int(args.leverage_ceiling or live.get("leverage", 8))
    max_loss = float(args.max_loss if args.max_loss is not None else live_dsl.get("max_loss_pct", 2.5))
    protect = float(args.protect if args.protect is not None else live_dsl.get("protect_pct", 1.5))
    retrace = float(args.retrace if args.retrace is not None else live_dsl.get("retrace_threshold", 0.30))
    if args.atr_mult is not None:
        atr_mult = float(args.atr_mult)
    else:
        atr_mult = float(live_atr.get("atr_mult", 0.0)) if bool(live_atr.get("enabled", False)) else 0.0
    atr_floor = float(args.atr_floor if args.atr_floor is not None else live_atr.get("floor_pct", 1.0))
    atr_ceiling = float(args.atr_ceiling if args.atr_ceiling is not None else live_atr.get("ceiling_pct", 4.0))

    bars_per_day = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}[args.interval]
    total_bars = args.days * bars_per_day + 100  # +warmup

    cfg = get_config()
    universe = get_universe()
    perps = [m for m in universe if m["type"] == "perp" and not m["coin"].startswith("@")]
    coins = sorted(perps, key=lambda m: m.get("dayNtlVlm", 0), reverse=True)[: args.coins]

    print("=== hermes-trader backtest ===")
    print(f"period: {args.days} days   interval: {args.interval}   universe: top-{args.coins} by 24h volume")
    print(f"equity: ${args.equity:.0f}   fraction: {equity_fraction:.0%}   leverage ceiling: {leverage_ceiling}x")
    print(f"DSL: max_loss={max_loss}%  protect={protect}%  retrace={retrace}  atr_mult={atr_mult}")
    print(f"triggers config: sigma={cfg['thresholds']['sigmaThreshold']}  "
          f"momentumPct={cfg['thresholds']['momentumPct']}\n")

    all_trades: List[Trade] = []
    stop_widths: List[float] = []
    for m in coins:
        coin = m["coin"]; max_lev = int(m.get("maxLeverage", 5))
        try:
            candles = fetch_hl_candles(coin, args.interval, total_bars)
            if len(candles) < 110:
                print(f"  {coin:8} skip ({len(candles)} bars — insufficient)")
                continue
            trades = _simulate(
                coin, candles, max_lev,
                equity=args.equity, equity_fraction=equity_fraction,
                lev_ceiling=leverage_ceiling, cfg=cfg,
                max_loss_pct=max_loss, protect_pct=protect,
                retrace_threshold=retrace,
                atr_mult=atr_mult, atr_floor=atr_floor,
                atr_ceiling=atr_ceiling, stop_widths=stop_widths,
            )
            pnl = sum(t.pnl_usd for t in trades)
            w = sum(1 for t in trades if t.pnl_usd > 0)
            print(f"  {coin:8} {len(trades):3} trades  win {w:3}  PnL ${pnl:+7.2f}  (max_lev {max_lev}x)")
            all_trades.extend(trades)
        except Exception as e:
            print(f"  {coin:8} error: {e}")

    _print_summary(all_trades, args.equity, args.days)
    if stop_widths:
        sw = sorted(stop_widths)
        n = len(sw)
        print(f"\nATR stop widths (spot %): n={n}  "
              f"min={sw[0]:.2f}  p25={sw[n//4]:.2f}  median={sw[n//2]:.2f}  "
              f"p75={sw[3*n//4]:.2f}  max={sw[-1]:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
