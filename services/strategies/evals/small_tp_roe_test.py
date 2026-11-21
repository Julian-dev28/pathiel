"""Small-gain-only (ROE take-profit) backtest, 2026-11-20
(docs/strategy/SMALL_TP_ROE_2026-11-20.md).

Operator, 2026-11-20: "back test a strategy where we aim for small gains
only, 10,20,30% 40% max." Interpreted here as a take-profit set at that
percent RETURN ON THE POSITION'S MARGIN (ROE) at each desk's own
leverage -- the number `place_bracket`'s `tp_px` would need to hit for the
position to show that gain on margin, not on price. ROE is gross
(leverage x price move, before fees/funding): `roe_price_move`. At HYPE's
live 10x, 10% ROE is a 1.0% price move; at oil's (xyz:BRENTOIL) live 20x,
10% ROE is 0.5%. At HYPE's own venue max (10x) this is exact; the
operator's "9-10x" range in the task brief covers a lower effective
leverage this eval does not trade (see the doc for both endpoints).

Reuses, UNMODIFIED: `sp500_strategy_test`'s whole engine (`Costs`,
`Tick`/`Order`/`Trade`, `build_ticks`, `simulate`, `summarize`, `split_ms`,
`edge_rule`, `funding_index`, `close_trade`, `equity_path`, `Dataset`,
`summarize`'s own `max_dd_r`) and each desk's own live-rule constants and fee
schedule (`hype_strategy_test`'s HYPE `Costs`/`VENUE_MAX_LEVERAGE`,
`hype_oil_strategy_test`'s oil leverage/share numbers, `next_trade`'s
k_atr/floor_frac/offset/sides). Entries, the stop's own formula, fees and
the leverage CAP are exactly the desk's live rule
(`oil_entry_rule`/`hype_entry_rule`); the only thing this module changes
is what an open position's `Order.tp`/`Order.stop` get filled in with.

Leverage is FIXED per desk for the ROE math (oil 20x, HYPE 10x), no 20/15
leverage-fit refit: live oil can refit a stop that fails 20x down to 15x
on a small share of entries (`next_trade.LEVERAGE_FIT`); modelling that
here would make "NN% ROE" ambiguous (the same ROE target needs a
DIFFERENT price move at 15x than at 20x), so this eval fixes 20x and
reports the gap in the doc instead of guessing which entries would have
refit.

Exit families compared per desk (one family = one way of filling the
Order once the SAME live entry rule has already said yes):
  baseline       the desk's current structural exit: stop beyond the 24h
                 edge, target the far 24h edge (the live offset),
                 unmodified -- `oil_entry_rule`/`hype_entry_rule`'s own
                 Order, untouched.
  tpNN           TP at NN% ROE (10/20/30/40), the desk's structural stop
                 UNCHANGED (`small_tp_rule(..., stop_mode="desk_stop")`).
  tpNN_sym       TP at NN% ROE, stop at the SAME ROE on the loss side (a
                 symmetric bracket, `stop_mode="symmetric"`).
  tpNN_2xstop    TP at NN% ROE, stop at 2x that ROE on the loss side
                 (`stop_mode="2x_tp"`).
  tpNN_be        TP at NN% ROE, the desk's structural stop, moved to
                 breakeven (the raw entry price, not entry+fees) once
                 price reaches half the distance from entry to the ROE
                 target -- the one family that needs a second, custom
                 walk (`simulate_breakeven`) since every other family's
                 Order is fixed for the life of the trade and the base
                 engine's own `simulate` has no hook to move a stop
                 mid-trade.

17 variants a desk (1 baseline + 4 TP levels x 4 stop treatments). Because
every variant wraps the SAME `entry_rule` (same side, same entry price,
same stop/leverage-gate decision -- `small_tp_rule` only overwrites the
Order AFTER the entry rule has already said yes), all `tpNN*` variants for
one desk share the exact same set of entry times; they differ only in how
long a trade is held and where it exits. A smaller target closes positions
faster, so the walk re-enters MORE often over the same fixed window --
`tpNN` variants routinely show more trades than the baseline over the
identical sample (verified directly against `summarize()["trades"]`, not
assumed).

Each variant is scored on the pinned 1h dataset with the repo's own 70/30
time split (`split_ms`) and labelled by `services.review.stats.label_slice`
(EDGE / LEAK / LEANING+ / LEANING- / FLUKE / NOISE) on the TAIL (held-out)
trades' own R -- the same labels the live trade-review skill uses, chosen
over `sp500_strategy_test.verdict()` because the operator asked for this
exact label set, and `label_slice`'s bootstrap CI + permutation p already
answer "could this be chance" on one slice of trades. Multiple testing:
with 17 shots on goal per desk, a RAW one-sample p that would call a
single backtest significant does not mean much after trying 17 variants;
`labeled_verdict` applies a plain Bonferroni correction (the raw one-sample
p x the number of variants tried, capped at 1.0) before handing it to
`label_slice`, so a label only survives when it would ALSO survive a much
harsher single-shot bar.

`pathiel.agents.bracket.DEFAULT_MIN_R` (2R) is the live floor `place_bracket`
enforces on every real order. It is NOT re-applied to the ROE exit itself:
the ENTRY still needs 2R against the STRUCTURAL target to fire at all
(`entry_rule`'s own `min_r`, unchanged, so "keep the real entries" holds
exactly) -- but once a trade is open, swapping in a small ROE target buys
an R multiple almost always under 2 (see `roe_table`'s own `r_multiple`
column), which the live `place_bracket` call would simply refuse as a NEW
bracket. The doc says plainly what would have to change for any winning
small-TP rule to actually ship (a dedicated bracket path with its own,
lower R floor, or scaling position size up to compensate) and the risk
that opens (a lower R floor admits more noise trades industry-wide, not
just the ones this backtest happens to show a profit on).

    .venv/bin/python -m services.strategies.evals.small_tp_roe_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil

Writes <out-dir>/report.json and summary.csv (default /tmp/small-tp-roe).
Read only: no account, no live config, no pathiel MCP tool, no LLM call.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.agents.bracket import DEFAULT_MIN_R, max_leverage_for_stop
from pathiel.models.types import Candle
from services.review import stats as RS
from services.strategies import next_trade as NT
from services.strategies.evals.hype_oil_strategy_test import (
    HYPE_SHARE, OIL, OIL_LEVERAGE, OIL_SHARE, OIL_VENUE_MAX_LEVERAGE,
)
from services.strategies.evals.hype_strategy_test import MEASURED as HYPE_MEASURED
from services.strategies.evals.hype_strategy_test import STRESS as HYPE_STRESS
from services.strategies.evals.hype_strategy_test import \
    VENUE_MAX_LEVERAGE as HYPE_VENUE_MAX_LEVERAGE
from services.strategies.evals.sp500_strategy_test import (
    MEASURED as OIL_MEASURED, STRESS as OIL_STRESS, Costs, Dataset, Order, Rule, Tick,
    Trade, build_ticks, close_trade, edge_rule, equity_path, funding_index,
    split_ms, summarize,
)

HYPE = NT.HYPE
HYPE_LEVERAGE = NT.HYPE_LEVERAGE
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS

ROE_LEVELS: Tuple[float, ...] = (10.0, 20.0, 30.0, 40.0)   # percent of margin (ROE)
STOP_MODES: Tuple[str, ...] = ("desk_stop", "symmetric", "2x_tp")
BREAKEVEN_TRIGGER_FRAC = 0.5     # move to breakeven after half the distance to the ROE TP

# Operator's own numbers, 2026-11-20 ("HYPE slice 60% of about $182 equity,
# oil 32%, risk 6%"). OIL_SHARE/HYPE_SHARE come from hype_oil_strategy_test
# (the live desk shares); TOTAL_EQUITY_USD/RISK_PCT are this task's own.
TOTAL_EQUITY_USD = 182.0
RISK_PCT = 6.0


# ── ROE <-> price math (deterministic; gate-tested) ────────────────────────

def roe_price_move(roe_pct: float, leverage: float) -> float:
    """The fractional price move needed for `roe_pct` percent return ON
    MARGIN at `leverage`, gross (before fees/funding/slippage): a
    leveraged position's margin moves `leverage` times faster than price
    itself (margin = notional / leverage, PnL = notional x price_move, so
    PnL / margin = leverage x price_move). Pure."""
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    return (roe_pct / 100.0) / leverage


def roe_target_px(entry_px: float, side: str, roe_pct: float, leverage: float) -> float:
    """The take-profit price `roe_pct` ROE away from `entry_px` at
    `leverage`, in the direction of profit for `side`. Pure."""
    move = roe_price_move(roe_pct, leverage)
    return entry_px * (1.0 + move) if side == "long" else entry_px * (1.0 - move)


def roe_loss_px(entry_px: float, side: str, roe_pct: float, leverage: float) -> float:
    """The stop price `roe_pct` ROE of LOSS away from `entry_px` at
    `leverage` -- the mirror of `roe_target_px`. Pure."""
    move = roe_price_move(roe_pct, leverage)
    return entry_px * (1.0 - move) if side == "long" else entry_px * (1.0 + move)


def r_multiple_of_roe(roe_pct: float, leverage: float, stop_frac: float) -> float:
    """The R multiple an `roe_pct` ROE target pays against a stop that
    sits `stop_frac` of price away (R = price move / stop distance, the
    same R the live rule's `check_levels` and every trade in this eval are
    scored in). Pure."""
    if stop_frac <= 0:
        raise ValueError("stop_frac must be positive")
    return roe_price_move(roe_pct, leverage) / stop_frac


# ── each desk's real entry rule, fixed leverage, unmodified exit params ───

def oil_entry_rule(min_r: float = DEFAULT_MIN_R) -> Rule:
    """Oil's live structural rule (`sp500_strategy_test.oil_as_is`'s own
    numbers: 1.3xATR14 / 0.8% floor stop buffer, 0.5% target offset, both
    sides, 24h range), at a FIXED 20x -- no 20/15 leverage-fit refit (see
    the module docstring)."""
    return edge_rule(k_atr=NT.ATR_BUFFER_MULT, floor_frac=NT.STOP_BUFFER_FLOOR_FRAC, offset_frac=0.005,
                     sides="both", entry_lookback_h=24, leverage=OIL_LEVERAGE, min_r=min_r,
                     max_leverage=OIL_VENUE_MAX_LEVERAGE, fit=())


def hype_entry_rule(min_r: float = DEFAULT_MIN_R) -> Rule:
    """HYPE's live structural rule (`next_trade.HYPE_RULE`: 1.0xATR14, no
    floor, no target offset, the 120h trend gate), at its fixed 10x venue
    max (no fit exists for HYPE live either)."""
    p = NT.HYPE_RULE
    return edge_rule(k_atr=p["k_atr"], floor_frac=p["floor_frac"], offset_frac=p["target_offset_frac"],
                     sides=p["backtest_sides"], entry_lookback_h=24, leverage=HYPE_LEVERAGE, min_r=min_r,
                     max_leverage=HYPE_VENUE_MAX_LEVERAGE, fit=())


# ── the exit swap ──────────────────────────────────────────────────────────

def small_tp_rule(entry_rule: Rule, leverage: float, roe_pct: float, stop_mode: str) -> Rule:
    """Wraps `entry_rule`: the SAME entry decision (same gate, same side,
    same structural stop) -- only the Order's own stop/tp are swapped for
    the small-gain exit, after the entry rule has already said yes.
    `stop_mode`: "desk_stop" (the structural stop `entry_rule` already
    computed, unchanged), "symmetric" (a stop at the SAME `roe_pct` on the
    loss side) or "2x_tp" (a stop at 2x `roe_pct` on the loss side). Pure
    (closes only over its own arguments)."""
    if stop_mode not in STOP_MODES:
        raise ValueError(f"unknown stop_mode {stop_mode!r}")

    def rule(i: int, ticks: Sequence[Any], bars: Sequence[Candle], st: Dict[str, Any],
            flat: bool = True) -> Optional[Order]:
        order = entry_rule(i, ticks, bars, st, flat)
        if order is None:
            return None
        entry_px = ticks[i].mid
        tp = roe_target_px(entry_px, order.side, roe_pct, leverage)
        if stop_mode == "desk_stop":
            stop = order.stop
        elif stop_mode == "symmetric":
            stop = roe_loss_px(entry_px, order.side, roe_pct, leverage)
        else:
            stop = roe_loss_px(entry_px, order.side, 2.0 * roe_pct, leverage)
        return Order(side=order.side, stop=stop, tp=tp)
    return rule


def simulate_breakeven(ticks: Sequence[Optional[Tick]], bars: Sequence[Candle], step_ms: int, rule: Rule,
                       costs: Costs, fund: Tuple[List[int], List[float]], venue_max_leverage: float, *,
                       trigger_frac: float = BREAKEVEN_TRIGGER_FRAC) -> List[Trade]:
    """Same walk as `sp500_strategy_test.simulate` (one position at a time,
    the stop checked before the target within a bar -- a bar that hits
    both resolves as a stop, never a target: no look-ahead), plus one
    extra, STATEFUL rule: once price has moved `trigger_frac` of the way
    from entry to the open position's OWN take-profit (the bar's high for
    a long, low for a short), a SEPARATE `live_stop` (never the Trade's own
    `.stop`, which stays the ORIGINAL planned risk the engine normalizes R
    against -- see `close_trade`'s own docstring, "R is judged against the
    planned risk") moves to the raw entry price for every LATER bar. The
    bar that crosses the trigger is still judged against the PRE-trigger
    stop (the move only takes effect from the next bar), so this is still
    no-look-ahead. Pure."""
    trades: List[Trade] = []
    st: Dict[str, Any] = {}
    pos: Optional[Trade] = None
    live_stop: Optional[float] = None
    moved_to_be = False
    for i, tk in enumerate(ticks):
        if tk is None:
            continue
        bar = bars[i]
        if pos is not None:
            long = pos.side == "long"
            hit_stop = bar.l <= live_stop if long else bar.h >= live_stop
            hit_tp = pos.tp is not None and (bar.h > pos.tp if long else bar.l < pos.tp)
            if hit_stop:
                px = min(live_stop, bar.o) if long else max(live_stop, bar.o)
                trades.append(close_trade(pos, tk.t, px, "stop", costs, fund))
                pos, live_stop, moved_to_be = None, None, False
            elif hit_tp:
                trades.append(close_trade(pos, tk.t, pos.tp, "target", costs, fund))
                pos, live_stop, moved_to_be = None, None, False
            elif not moved_to_be and pos.tp is not None:
                trigger = (pos.entry_px + trigger_frac * (pos.tp - pos.entry_px) if long
                          else pos.entry_px - trigger_frac * (pos.entry_px - pos.tp))
                reached = bar.h >= trigger if long else bar.l <= trigger
                if reached:
                    live_stop = pos.entry_px
                    moved_to_be = True
        new = rule(i, ticks, bars, st, pos is None)
        if new is None or pos is not None or i == len(ticks) - 1:
            continue
        risk_frac = abs(tk.mid - new.stop) / tk.mid
        pos = Trade(side=new.side, entry_t=tk.t, entry_px=tk.mid, stop=new.stop, tp=new.tp, stop_frac=risk_frac,
                    max_lev_ok=max_leverage_for_stop(tk.mid, new.stop, venue_max_leverage))
        live_stop = new.stop
        moved_to_be = False
    return trades


# ── scoring helpers this eval needs beyond summarize() ─────────────────────

def win_loss_r(trades: Sequence[Trade]) -> Dict[str, Any]:
    """Average R of the winners and the losers in `trades` separately
    (summarize()'s own `mean_r` blends both). A win is `r > 0`, matching
    summarize()'s own win_rate definition. Pure."""
    wins = [t.r for t in trades if t.r > 0]
    losses = [t.r for t in trades if t.r <= 0]
    return {"avg_win_r": round(sum(wins) / len(wins), 3) if wins else None,
            "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
            "n_win": len(wins), "n_loss": len(losses)}


def longest_losing_streak(trades: Sequence[Trade]) -> int:
    """The longest run of consecutive trades (ordered by exit time) with
    `r <= 0`. Pure."""
    ts = sorted(trades, key=lambda t: t.exit_t)
    best = cur = 0
    for t in ts:
        if t.r <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def net_usd(trades: Sequence[Trade], leverage: float, risk_pct: float, slice_equity: float) -> Dict[str, Any]:
    """Dollar P&L at today's sizing: `equity_path`'s own compounding curve
    (each trade risks `risk_pct` of the slice, or less when the stop is too
    wide for `leverage` to carry that much) scaled onto `slice_equity`
    dollars. Pure."""
    eq = equity_path(trades, leverage, risk_pct)
    return {"final_multiple": eq["final_multiple"], "max_dd_pct": eq["max_dd_pct"],
            "net_usd": round(slice_equity * (eq["final_multiple"] - 1.0), 2)}


def breakeven_win_rate(stop_frac: float, tp_frac: float, costs: Costs) -> Optional[float]:
    """The win rate at which a trade risking `stop_frac` of price to make
    `tp_frac` nets to exactly zero expectancy after `costs`' fees and
    slippage -- solved from the SAME `close_trade` formula every real trade
    in this eval is scored with (apples to apples with a variant's own
    REALIZED win rate), on a synthetic long trade (entry=100; `close_trade`'s
    d=+-1 formulation makes the short side's solve identical). None when
    even a 100% win rate could not break even -- either because hitting the
    TP itself nets a loss after costs (`win_r <= 0`: the round-trip fee ate
    the whole target), or because the win leg's own R does not clear the
    loss leg's. Pure."""
    entry = 100.0
    stop = entry * (1.0 - stop_frac)
    tp = entry * (1.0 + tp_frac)
    win = close_trade(Trade(side="long", entry_t=0, entry_px=entry, stop=stop, tp=tp),
                      1, tp, "target", costs, ([], []))
    loss = close_trade(Trade(side="long", entry_t=0, entry_px=entry, stop=stop, tp=tp),
                       1, stop, "stop", costs, ([], []))
    win_r, loss_r = win.r, loss.r
    if win_r <= 0 or win_r <= loss_r:
        return None
    return round(-loss_r / (win_r - loss_r), 4)


def labeled_verdict(rs: Sequence[float], n_variants: int) -> Dict[str, Any]:
    """EDGE / LEAK / LEANING+ / LEANING- / FLUKE / NOISE for one variant's
    trade R list (`services.review.stats.label_slice`), Bonferroni-corrected
    for `n_variants` simultaneous comparisons: the raw one-sample p
    (`RS.sign_flip_p`, a single slice against zero) is scaled by
    `n_variants` and capped at 1.0 before `label_slice`'s own p<0.05/p<0.20
    cutoffs apply, so trying many variants needs a correspondingly stronger
    single result to call EDGE/LEAK/LEANING. Deterministic: `RS`'s bootstrap
    and permutation routines are seeded. Pure."""
    rs = list(rs)
    if not rs:
        return {"label": RS.NOISE, "n": 0, "mean_r": None, "ci": (None, None), "p_raw": None, "p_bonf": None}
    ci = RS.bootstrap_ci(rs)
    p_raw = RS.sign_flip_p(rs)
    p_bonf = None if p_raw is None else min(1.0, p_raw * n_variants)
    label = RS.label_slice(rs, ci, p_bonf)
    return {"label": label, "n": len(rs), "mean_r": round(RS.mean(rs), 4), "ci": ci,
            "p_raw": p_raw, "p_bonf": p_bonf}


# ── the variant grid ─────────────────────────────────────────────────────

def variants_for() -> Dict[str, Dict[str, Any]]:
    """name -> {kind, roe, stop_mode}: 1 baseline + 4 ROE levels x 4 stop
    treatments (desk_stop / symmetric / 2x_tp / desk_stop-with-breakeven) =
    17. `kind` "tp_breakeven" is the one family `rule_for` routes to
    `simulate_breakeven` instead of the base engine's `simulate`. Pure."""
    out: Dict[str, Dict[str, Any]] = {"baseline": {"kind": "baseline"}}
    for roe in ROE_LEVELS:
        out[f"tp{roe:g}"] = {"kind": "tp", "roe": roe, "stop_mode": "desk_stop"}
        out[f"tp{roe:g}_sym"] = {"kind": "tp", "roe": roe, "stop_mode": "symmetric"}
        out[f"tp{roe:g}_2xstop"] = {"kind": "tp", "roe": roe, "stop_mode": "2x_tp"}
        out[f"tp{roe:g}_be"] = {"kind": "tp_breakeven", "roe": roe, "stop_mode": "desk_stop"}
    return out


def rule_for(entry_rule: Rule, leverage: float, v: Dict[str, Any]) -> Rule:
    if v["kind"] == "baseline":
        return entry_rule
    return small_tp_rule(entry_rule, leverage, v["roe"], v["stop_mode"])


def trades_for(ds: Dataset, rule: Rule, v: Dict[str, Any], costs: Costs,
               fund: Tuple[List[int], List[float]], venue_max_leverage: float) -> List[Trade]:
    if v["kind"] == "tp_breakeven":
        return simulate_breakeven(ds.ticks, ds.bars, ds.step_ms, rule, costs, fund, venue_max_leverage)
    from services.strategies.evals.sp500_strategy_test import simulate
    return simulate(ds.ticks, ds.bars, ds.step_ms, rule, costs, fund)


# ── one desk's full report ─────────────────────────────────────────────────

def desk_report(*, coin: str, entry_rule_fn, leverage: float, venue_max_leverage: float, costs: Costs,
                stress: Costs, share: float, ds: Dataset, fund: Tuple[List[int], List[float]],
                total_equity: float = TOTAL_EQUITY_USD, risk_pct: float = RISK_PCT) -> Dict[str, Any]:
    named = variants_for()
    n_variants = len(named)
    entry_rule = entry_rule_fn()
    slice_equity = total_equity * share
    cut_ms = split_ms(ds.start_ms, ds.end_ms)

    baseline_trades = trades_for(ds, entry_rule, named["baseline"], costs, fund, venue_max_leverage)
    baseline_full = summarize(baseline_trades, ds.start_ms, ds.end_ms)
    baseline_stop_frac = (baseline_full["median_stop_pct"] or 0.0) / 100.0

    rows: Dict[str, Any] = {}
    for name, v in named.items():
        rule = rule_for(entry_rule, leverage, v)
        trades = trades_for(ds, rule, v, costs, fund, venue_max_leverage)
        full = summarize(trades, ds.start_ms, ds.end_ms)
        train = summarize(trades, ds.start_ms, cut_ms)
        tail = summarize(trades, cut_ms, ds.end_ms)
        tail_rs = [t.r for t in trades if cut_ms <= t.entry_t < ds.end_ms]
        full_rs = [t.r for t in trades if ds.start_ms <= t.entry_t < ds.end_ms]
        rows[name] = {
            "params": v, "full": full, "train": train, "tail": tail,
            "win_loss_r": win_loss_r(trades), "longest_losing_streak": longest_losing_streak(trades),
            "max_dd_r": full["max_dd_r"], "net_usd": net_usd(trades, leverage, risk_pct, slice_equity),
            "label_tail": labeled_verdict(tail_rs, n_variants), "label_full": labeled_verdict(full_rs, n_variants),
        }

    roe_table = [
        {"roe_pct": roe, "price_move_pct": round(roe_price_move(roe, leverage) * 100, 4),
         "r_multiple_at_desk_stop": round(r_multiple_of_roe(roe, leverage, baseline_stop_frac), 3)
         if baseline_stop_frac > 0 else None,
         "clears_2r": (baseline_stop_frac > 0 and r_multiple_of_roe(roe, leverage, baseline_stop_frac) >= 2.0)}
        for roe in ROE_LEVELS]

    breakeven_table = [
        {"roe_pct": roe,
         "breakeven_win_rate": breakeven_win_rate(baseline_stop_frac, roe_price_move(roe, leverage), costs)
         if baseline_stop_frac > 0 else None,
         "achieved_win_rate": rows[f"tp{roe:g}"]["full"]["win_rate"]}
        for roe in ROE_LEVELS]

    return {
        "coin": coin, "leverage": leverage, "venue_max_leverage": venue_max_leverage,
        "costs": {"measured": costs.__dict__, "stress": stress.__dict__},
        "share": share, "slice_equity_usd": round(slice_equity, 2), "risk_pct": risk_pct,
        "window": {"start": _iso(ds.start_ms), "end": _iso(ds.end_ms), "tail_from": _iso(cut_ms),
                  "days": round((ds.end_ms - ds.start_ms) / DAY_MS, 1)},
        "baseline_median_stop_pct": baseline_full["median_stop_pct"],
        "roe_table": roe_table, "breakeven_table": breakeven_table,
        "variants": rows,
    }


# ── run / CLI ────────────────────────────────────────────────────────────

def _dataset(snap) -> Dataset:
    ds = Dataset("1h", snap.c1h, HOUR_MS)
    ds.ticks = build_ticks(ds.bars, ds.step_ms, snap.c1h)
    return ds


def run(snapshot_dir: str, out_dir: str, total_equity: float = TOTAL_EQUITY_USD,
        risk_pct: float = RISK_PCT) -> Dict[str, Any]:
    from services.strategies.backtest import snapshot as SNAP
    oil_snap = SNAP.load_snapshot(snapshot_dir, OIL)
    hype_snap = SNAP.load_snapshot(snapshot_dir, HYPE)
    if not oil_snap.c1h or not hype_snap.c1h:
        raise SystemExit(f"no 1h candles for {OIL} and/or {HYPE} in {snapshot_dir}: "
                         f"run services.strategies.backtest.snapshot for both coins first")
    oil_ds, hype_ds = _dataset(oil_snap), _dataset(hype_snap)
    oil_fund, hype_fund = funding_index(oil_snap.funding), funding_index(hype_snap.funding)

    report: Dict[str, Any] = {
        "snapshot_dir": snapshot_dir, "total_equity_usd": total_equity, "risk_pct": risk_pct,
        "interpretation": "take-profit at the stated percent RETURN ON MARGIN (ROE) at the desk's own "
                          "leverage; roe_price_move(roe_pct, leverage) = (roe_pct / 100) / leverage",
        "desks": {
            "oil": desk_report(coin=OIL, entry_rule_fn=oil_entry_rule, leverage=OIL_LEVERAGE,
                               venue_max_leverage=OIL_VENUE_MAX_LEVERAGE, costs=OIL_MEASURED, stress=OIL_STRESS,
                               share=OIL_SHARE, ds=oil_ds, fund=oil_fund, total_equity=total_equity,
                               risk_pct=risk_pct),
            "hype": desk_report(coin=HYPE, entry_rule_fn=hype_entry_rule, leverage=HYPE_LEVERAGE,
                                venue_max_leverage=HYPE_VENUE_MAX_LEVERAGE, costs=HYPE_MEASURED, stress=HYPE_STRESS,
                                share=HYPE_SHARE, ds=hype_ds, fund=hype_fund, total_equity=total_equity,
                                risk_pct=risk_pct),
        },
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _write_csv(report, out_dir)
    return report


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _write_csv(report: Dict[str, Any], out_dir: str) -> None:
    rows = []
    for desk, d in report["desks"].items():
        for name, v in d["variants"].items():
            rows.append({
                "desk": desk, "variant": name, "n_full": v["full"]["trades"], "n_tail": v["tail"]["trades"],
                "win_rate_full": v["full"]["win_rate"], "avg_win_r": v["win_loss_r"]["avg_win_r"],
                "avg_loss_r": v["win_loss_r"]["avg_loss_r"], "mean_r_full": v["full"]["mean_r"],
                "net_r_full": v["full"]["net_r"], "max_dd_r": v["max_dd_r"],
                "longest_losing_streak": v["longest_losing_streak"], "net_usd_full": v["net_usd"]["net_usd"],
                "label_tail": v["label_tail"]["label"], "label_full": v["label_full"]["label"],
            })
    if not rows:
        return
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _line(name: str, v: Dict[str, Any]) -> str:
    f = v["full"]
    return (f"  {name:16s} n={f['trades']:4d} win={f['win_rate']} meanR={f['mean_r']} netR={f['net_r']:+.2f} "
            f"ddR={v['max_dd_r']:.2f} streak={v['longest_losing_streak']} netUSD={v['net_usd']['net_usd']:+.2f} "
            f"tail_label={v['label_tail']['label']:9s} full_label={v['label_full']['label']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-dir", default="/tmp/small-tp-roe")
    ap.add_argument("--total-equity", type=float, default=TOTAL_EQUITY_USD)
    ap.add_argument("--risk-pct", type=float, default=RISK_PCT)
    a = ap.parse_args(argv)
    rep = run(os.path.expanduser(a.snapshot_dir), a.out_dir, a.total_equity, a.risk_pct)
    for desk, d in rep["desks"].items():
        print(f"\n== {desk} ({d['coin']}, {d['leverage']:g}x, slice ${d['slice_equity_usd']:.2f}, "
              f"window {d['window']['start']}..{d['window']['end']}) ==")
        print("  roe_table:", d["roe_table"])
        print("  breakeven_table:", d["breakeven_table"])
        for name, v in d["variants"].items():
            print(_line(name, v))
    print(f"\nreport: {os.path.join(a.out_dir, 'report.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
