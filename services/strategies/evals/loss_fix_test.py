"""Loss fix backtest, 2026-11-02: a fix per cause from `loss_audit.py`, then the stack.

Operator, 2026-11-02: "can u backtest the solutions to what made us
unprofitable, fix the issues and show me the results". The audit
(`loss_audit.py`) ranks the causes by dollars; this eval tests a fix for
each on the datasets the earlier tests used, plus the live journals.

  S  sizing     20x full port (live), 5x / 10x / 15x full port, and a risk
                cap: size so a stop costs at most 5 / 10 / 15 / 20% of desk
                equity, leverage still 20x (`Params.risk_cap_frac`)
  W  whipsaw    no entry in the first N minutes of a call
                (`entry_delay_min`), until the side has held N hours
                (`side_age_h`), unless the last closed 1h candle agrees
                (`confirm_1h`), once the mid has broken the entry range
                (`no_break_entry`); the thesis's own levels with a resting
                limit at its entry (`ai_levels`); a resting limit at the 24h
                edge instead of market (`entry_mode` "edge")
  R  round trip the profit lock all week (1R / 1.5R / 2R trigger, lock 0.5R or
                1R, trails, break-even plus fees) against the weekend-only
                1R lock of BRENT_STOP_BUFFER_TEST_2026-11-02.md
  C  stack      the per-cause winners together, against live

Each study scores every variant against live on final equity (beats live
on most datasets, geo-mean ratio), the plateau rule on its grid, and the
20x line (stop inside half the way to liquidation). Sizing also gets a
seeded bootstrap of each run's trade sequence: P(equity under half the
start at any point) and the median final multiple. The real trades get
three deterministic lenses: each sizing applied to the actual fills, each
entry filter checked at the actual entry time, and each lock replayed on
5m bars from the actual fill.

Datasets ($64, Brent live params with size slippage and funding; silver
the live 1.3 x ATR14 stop with the 0.8% floor):
  brent_journal, brent_replay30, brent_<ctrl>_replay, brent_<ctrl>_30d
  silver_theses, silver_journal, silver_<ctrl>, silver_<ctrl>_recent

    python -m services.strategies.evals.loss_fix_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --metals-theses-dir <pathia>/.state/strategies/metals/theses

Writes /tmp/loss-audit/fix_report.json, fix_summary.csv, real_trades.csv.
Public candles only, read only; no LLM calls; never touches the account.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, _levels, simulate
from services.strategies.evals import brent_stop_buffer_test as B
from services.strategies.evals import loss_audit as LA
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction

BASELINE = "live"
START = 64.0
FIFTEEN_MS = 900_000
SEED = 20261102
N_PATHS = 2000
RUIN_FRAC = 0.5
CONTROLS = ("always_long", "always_short", "daily_sign")
LEVERAGES = (5, 10, 15)
RISK_CAPS = (5, 10, 15, 20)
DELAYS = (15, 30, 60, 120)
SIDE_AGES = (2, 4, 8, 12)
LOCK_TRIGGERS = (1.0, 1.5, 2.0)
TAKER = 0.00045
SLIP = 0.0003
SIZE_KEYS = ("leverage", "risk_cap_frac")
FILTER_KEYS = ("entry_delay_min", "side_age_h", "confirm_1h", "no_break_entry")
SILVER_SHARE_FROM_MS = int(datetime(2026, 10, 31, 16, tzinfo=timezone.utc).timestamp() * 1000)


# ── the grids (pure) ───────────────────────────────────────────────────────

def sizing_variants() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for lev in LEVERAGES:
        out[f"lev_{lev}"] = {"leverage": float(lev)}
    for pct in RISK_CAPS:
        out[f"risk_{pct}"] = {"risk_cap_frac": pct / 100.0}
    return out


SIZING_FAMILIES = {"lev": [f"lev_{x}" for x in LEVERAGES] + [BASELINE],
                   "risk": [f"risk_{x}" for x in RISK_CAPS]}


def filter_variants() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for m in DELAYS:
        out[f"delay_{m}"] = {"entry_delay_min": float(m)}
    for h in SIDE_AGES:
        out[f"age_{h}h"] = {"side_age_h": float(h)}
    out["confirm_1h"] = {"confirm_1h": True}
    out["no_break"] = {"no_break_entry": True}
    out["thesis_levels"] = {"ai_levels": True}
    out["edge_limit"] = {"entry_mode": "edge"}
    return out


FILTER_FAMILIES = {"delay": [f"delay_{m}" for m in DELAYS], "age": [f"age_{h}h" for h in SIDE_AGES]}


def lock_variants() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for trig in LOCK_TRIGGERS:
        out[f"wk_lock_t{trig:g}_l0.5"] = {"lock_weekend_only": True, "lock_trigger_r": trig, "lock_r": 0.5}
        out[f"wk_befees_t{trig:g}"] = {"lock_weekend_only": True, "lock_trigger_r": trig, "lock_r": 0.0,
                                       "lock_plus_fees": True}
    for trig in LOCK_TRIGGERS:
        out[f"lock_t{trig:g}_l0.5"] = {"lock_trigger_r": trig, "lock_r": 0.5}
        out[f"befees_t{trig:g}"] = {"lock_trigger_r": trig, "lock_r": 0.0, "lock_plus_fees": True}
        out[f"trail_t{trig:g}_l0.5"] = {"lock_trigger_r": trig, "lock_r": 0.5, "lock_trail": True}
    out["lock_t1.5_l1"] = {"lock_trigger_r": 1.5, "lock_r": 1.0}
    out["lock_t2_l1"] = {"lock_trigger_r": 2.0, "lock_r": 1.0}
    return out


LOCK_FAMILIES = {
    "wk_lock_l0.5": [f"wk_lock_t{t:g}_l0.5" for t in LOCK_TRIGGERS],
    "wk_befees": [f"wk_befees_t{t:g}" for t in LOCK_TRIGGERS],
    "lock_l0.5": [f"lock_t{t:g}_l0.5" for t in LOCK_TRIGGERS],
    "befees": [f"befees_t{t:g}" for t in LOCK_TRIGGERS],
    "trail_l0.5": [f"trail_t{t:g}_l0.5" for t in LOCK_TRIGGERS],
    "lock_l1": ["lock_t1.5_l1", "lock_t2_l1"],
}


def merge(*overrides: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for ov in overrides:
        out.update(ov)
    return out


# ── bootstrap and scoring (pure) ───────────────────────────────────────────

def trade_returns(run: Run) -> List[float]:
    """Each closed trade's net P&L as a share of the marked equity before it opened."""
    out = []
    for tr in run.trades:
        eq = B.equity_before(run, tr.entry_time)
        if eq > 0:
            out.append(tr.net_pnl_usd / eq)
    return out


def bootstrap(returns: Sequence[float], n_paths: int = N_PATHS, seed: int = SEED,
              floor: float = RUIN_FRAC) -> Dict[str, Optional[float]]:
    """Resample the trade sequence with replacement (`n_paths` paths of the
    same length, seeded) and compound it. p_ruin: share of paths whose
    equity falls under `floor` of the start at any point; p_loss: share
    that end under the start; median_multiple: the median final / start.
    None for under 3 trades (nothing to resample)."""
    rs = list(returns)
    if len(rs) < 3:
        return {"p_ruin": None, "p_loss": None, "median_multiple": None}
    rng = random.Random(seed)
    ruined = lost = 0
    finals = []
    for _ in range(n_paths):
        eq, low = 1.0, 1.0
        for _ in range(len(rs)):
            eq *= max(0.0, 1.0 + rs[rng.randrange(len(rs))])
            low = min(low, eq)
        ruined += low < floor
        lost += eq < 1.0
        finals.append(eq)
    finals.sort()
    mid = len(finals) // 2
    med = finals[mid] if len(finals) % 2 else 0.5 * (finals[mid - 1] + finals[mid])
    return {"p_ruin": round(ruined / n_paths, 3), "p_loss": round(lost / n_paths, 3),
            "median_multiple": round(med, 3)}


def summarize(run: Run, start_ms: int, end_ms: int) -> Dict[str, Any]:
    s = B.summarize(run, start_ms, end_ms)
    s.update(bootstrap(trade_returns(run)))
    return s


def verdict(scores: Dict[str, Dict[str, Any]], max_liq: Dict[str, float], name: Optional[str],
            families: Dict[str, List[str]]) -> Dict[str, Any]:
    """The operator's three rules. The plateau rule applies to a variant on
    a grid; a single switch (confirm_1h, no_break, ...) has no neighbours,
    so it is "n/a" there and must clear the other two."""
    if name is None or name not in scores:
        return {"adopt": False, "candidate": name, "fails": ["no candidate"]}
    on_grid = any(name in g for g in families.values())
    if on_grid:
        return B.verdict(scores, max_liq, name, families)
    v = B.verdict(scores, max_liq, name, None)
    v["plateau"] = "n/a"
    return v


def growth_beats(rows: Dict[str, Dict[str, Any]], name: str, baseline: str = BASELINE) -> Dict[str, int]:
    """On how many datasets `name`'s bootstrap median multiple is at least
    live's (the Kelly question: does a smaller bet grow the typical path as
    much?), and how many had a bootstrap at all."""
    beats = n = 0
    for by_v in rows.values():
        a, b = by_v.get(name, {}).get("median_multiple"), by_v.get(baseline, {}).get("median_multiple")
        if a is None or b is None:
            continue
        n += 1
        beats += a >= b - 1e-9
    return {"beats": beats, "of": n}


def mean_of(rows: Dict[str, Dict[str, Any]], name: str, key: str) -> Optional[float]:
    xs = [by_v[name][key] for by_v in rows.values() if by_v.get(name, {}).get(key) is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


# ── real trades (pure on their inputs) ─────────────────────────────────────

def desk_share(tr: Dict[str, Any]) -> float:
    """The desk's slice of equity when the trade opened: oil 100% until the
    silver desk started on 2026-10-31, then oil 90% and silver 10%."""
    if tr["coin"] == "xyz:SILVER":
        return 0.10
    return 0.90 if tr["entry_time"] >= SILVER_SHARE_FROM_MS else 1.0


def resize(tr: Dict[str, Any], ov: Dict[str, Any]) -> Dict[str, Any]:
    """One real trade under a sizing override, scaled off its own fills:
    leverage L caps the notional at L x desk equity; a risk cap at the
    notional whose stop-out (price to the initial stop, taker both ways,
    0.03% slip) costs that share of desk equity. Never above the actual
    size (the actual is the live rule). Net scales with the size."""
    eq = tr.get("equity_at_size")
    if not eq or not tr.get("stop_px"):
        return {"scale": 1.0, "net_usd": tr["net_usd"], "pct_desk": None}
    desk = eq * desk_share(tr)
    actual = tr["notional_usd"]
    new = actual
    if "leverage" in ov:
        new = min(new, ov["leverage"] * desk)
    if ov.get("risk_cap_frac") is not None:
        per = abs(tr["entry_px"] - tr["stop_px"]) / tr["entry_px"] + 2 * TAKER + SLIP
        new = min(new, ov["risk_cap_frac"] * desk / per)
    scale = new / actual if actual > 0 else 1.0
    net = tr["net_usd"] * scale
    return {"scale": round(scale, 3), "net_usd": round(net, 2), "pct_desk": round(100.0 * net / desk, 1)}


def side_since(t: int, theses: Sequence[Any]) -> Optional[int]:
    """When the side in force at `t` began: the first thesis of the run of
    same-side theses ending at the one in force (flat theses skipped, as
    in the sim)."""
    ordered = [th for th in sorted(theses, key=lambda x: x.created_at_ms) if th.created_at_ms <= t]
    sided = [th for th in ordered if th.side in ("long", "short")]
    if not sided:
        return None
    side, start = sided[-1].side, sided[-1].created_at_ms
    for th in reversed(sided):
        if th.side != side:
            break
        start = th.created_at_ms
    return start


def filter_hits(tr: Dict[str, Any], c1h: Sequence[Candle], theses: Sequence[Any],
                named: Dict[str, Dict[str, Any]]) -> Dict[str, bool]:
    """Would each entry filter have refused this real entry at its fill
    time? Market-mode filters only (thesis_levels and edge_limit change
    the entry, not whether to take it: not judged here)."""
    t = tr["entry_time"]
    hours = sorted(c1h, key=lambda c: c.t)
    closed = [c for c in hours if c.t + HOUR_MS <= t]
    last = closed[-1] if closed else None
    lv = _levels(closed[-24:], 24, 24) if len(closed) >= 24 else None
    th = LA.thesis_at(t, theses)
    since = side_since(t, theses)
    long_ = tr["side"] == "long"
    out: Dict[str, bool] = {}
    for v, ov in named.items():
        if v != BASELINE and not any(k in ov for k in FILTER_KEYS):
            continue
        hits = []
        if ov.get("entry_delay_min") is not None and th is not None:
            hits.append(t - th.created_at_ms < ov["entry_delay_min"] * 60_000)
        if ov.get("side_age_h") is not None and since is not None:
            hits.append(t - since < ov["side_age_h"] * HOUR_MS)
        if ov.get("confirm_1h") and last is not None:
            hits.append(not (last.c > last.o if long_ else last.c < last.o))
        if ov.get("no_break_entry") and lv is not None:
            hits.append(tr["entry_px"] < lv["entry_low"] if long_ else tr["entry_px"] > lv["entry_high"])
        out[v] = any(hits)
    return out


def fee_rate(tr: Dict[str, Any]) -> float:
    """The trade's own fee per dollar traded (fees over open + close notional)."""
    traded = tr["notional_usd"] + (tr["exit_px"] or 0.0) * tr["max_size"]
    return tr["fees"] / traded if traded > 0 else TAKER


def lock_real(tr: Dict[str, Any], bars5: Sequence[Candle], ov: Dict[str, Any], base: Params) -> Dict[str, Any]:
    """One real trade with a profit lock, replayed on 5m bars from the bar
    after its fill to its actual exit (`brent_stop_buffer_test.replay_
    locked`, target as a limit). No lock exit by then: the actual result.
    Net = the price move at the actual size less the trade's own fee rate
    (its fees over its traded notional: the xyz fills paid about 0.9 bps a
    side, not the sim's 4.5) on both legs."""
    if not tr.get("stop_px") or not tr.get("target_px") or tr["exit_time"] is None:
        return {"reason": "actual", "net_usd": tr["net_usd"]}
    p = dataclasses.replace(base, **ov)
    t0 = tr["entry_time"] - tr["entry_time"] % LA.FIVE_MS + LA.FIVE_MS
    bars = [b for b in bars5 if t0 <= b.t and b.t + LA.FIVE_MS <= tr["exit_time"]]
    r = B.replay_locked(tr["side"], tr["entry_px"], tr["stop_px"], tr["target_px"], bars, p)
    if r["reason"] not in ("lock",):
        return {"reason": "actual", "net_usd": tr["net_usd"]}
    sign = 1.0 if tr["side"] == "long" else -1.0
    gross = sign * (r["exit_px"] - (tr["avg_entry_px"] or tr["entry_px"])) * tr["max_size"]
    fees = fee_rate(tr) * (tr["notional_usd"] + r["exit_px"] * tr["max_size"])
    return {"reason": "lock", "exit_time": LA._iso(r["exit_time"]), "exit_px": round(r["exit_px"], 4),
            "net_usd": round(gross - fees, 2)}


def stack_real(tr: Dict[str, Any], ov: Dict[str, Any], bars5: Sequence[Candle], c1h: Sequence[Candle],
               theses: Sequence[Any], base: Params) -> float:
    """One real trade under a stack: refused by its entry filters at the
    fill minute -> 0 (an upper bound: the desk may have entered later),
    else the lock replay scaled by the sizing. Pure."""
    filt = {k: v for k, v in ov.items() if k in FILTER_KEYS}
    if filt and filter_hits(tr, c1h, theses, {"f": filt}).get("f"):
        return 0.0
    lock = {k: v for k, v in ov.items() if k not in SIZE_KEYS and k not in FILTER_KEYS}
    net = lock_real(tr, bars5, lock, base)["net_usd"] if lock else tr["net_usd"]
    return round(net * resize(tr, {k: v for k, v in ov.items() if k in SIZE_KEYS})["scale"], 2)


# ── the 2026-11-02 long, simulated (pure) ──────────────────────────────────

def nov2_long(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: Callable[[int], Any], base: Params,
              named: Dict[str, Dict[str, Any]], start_ms: int, end_ms: int) -> Dict[str, Any]:
    """Each variant on the 2026-11-02 Brent window: the first trade it
    opened (side, entry, stop, exit, net, % of equity) or why it took none."""
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    out = {}
    for v, ov in named.items():
        run = simulate(w15, w1h, direction_at, dataclasses.replace(base, **ov))
        if run.trades:
            tr = run.trades[0]
            out[v] = {"entry": LA._iso(tr.entry_time), "side": tr.side, "entry_px": round(tr.entry_px, 3),
                      "stop_px": round(tr.orig_stop_px or tr.stop_px, 3), "exit": LA._iso(tr.exit_time),
                      "reason": tr.exit_reason, "net_usd": round(tr.net_pnl_usd, 2),
                      "pct_equity": round(100.0 * tr.net_pnl_usd / base.starting_equity, 1)}
        elif run.open_position_at_end:
            o = run.open_position_at_end
            out[v] = {"open": True, "side": o["side"], "entry_px": round(o["entry_px"], 3),
                      "unrealized_usd": round(o["unrealized_pnl_usd"], 2)}
        else:
            out[v] = {"no_trade": dict(run.refused)}
    return out


# ── runs ───────────────────────────────────────────────────────────────────

def run_dataset(c15, c1h, direction_at, base: Params, start_ms: int, end_ms: int,
                named: Dict[str, Dict[str, Any]], funding=None) -> Dict[str, Any]:
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    out: Dict[str, Any] = {"window": [LA._iso(start_ms), LA._iso(end_ms)], "variants": {}}
    for v, ov in named.items():
        run = simulate(w15, w1h, direction_at, dataclasses.replace(base, **ov), funding)
        out["variants"][v] = summarize(run, start_ms, end_ms)
    return out


def study(sets: Dict[str, Tuple], base: Params, named: Dict[str, Dict[str, Any]], funding=None) -> Dict[str, Any]:
    return {ds: run_dataset(c15, c1h, d_at, base, st, en, named, funding)
            for ds, (c15, c1h, d_at, st, en) in sets.items()}


def score_study(res: Dict[str, Any], named: Dict[str, Dict[str, Any]], families: Dict[str, List[str]],
                thesis_sets: Sequence[str]) -> Dict[str, Any]:
    rows = {ds: r["variants"] for ds, r in res.items()}
    all_scores = B.score_vs_live(rows)
    th_rows = {k: rows[k] for k in thesis_sets if k in rows}
    th_scores = B.score_vs_live(th_rows) if th_rows else {}
    # The 20x line: no variant here changes the stop rule, and every entry
    # goes through the live check_leverage gate (0.50 of the way to
    # liquidation at the mid), so the rule holds by construction, as the
    # lock and weekend studies of BRENT_STOP_BUFFER_TEST_2026-11-02.md
    # treated it. The fill-price maximum (market slippage adds up to 0.01)
    # is still measured and reported.
    max_liq = {v: max(r["variants"][v]["max_stop_liq_frac"] for r in res.values()) for v in named}
    gate_liq = {v: 0.0 for v in named}
    per = {}
    for v in named:
        if v == BASELINE:
            continue
        per[v] = {**verdict(all_scores, gate_liq, v, families), "thesis": th_scores.get(v),
                  "growth": growth_beats(rows, v), "p_ruin_mean": mean_of(rows, v, "p_ruin"),
                  "max_dd_mean": mean_of(rows, v, "max_dd_pct"), "worst_pct_mean": mean_of(rows, v, "worst_loss_pct")}
    base_row = {"p_ruin_mean": mean_of(rows, BASELINE, "p_ruin"), "max_dd_mean": mean_of(rows, BASELINE, "max_dd_pct"),
                "worst_pct_mean": mean_of(rows, BASELINE, "worst_loss_pct")}
    passing = sorted(v for v, x in per.items() if x["adopt"])
    return {"scores": all_scores, "thesis_scores": th_scores, "per_variant": per, "live": base_row,
            "max_liq": max_liq, "pass": passing, "best": B.pick_best({v: all_scores[v] for v in per}) if per else None}


SUMMARY_FIELDS = ["study", "desk", "dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "trades",
                  "win_rate", "avg_r", "worst_loss_usd", "worst_loss_pct", "max_stop_liq_frac", "p_ruin", "p_loss",
                  "median_multiple"]


def summary_rows(study_name: str, desk: str, res: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"study": study_name, "desk": desk, "dataset": ds, "variant": v,
             **{k: s.get(k) for k in SUMMARY_FIELDS[4:]}}
            for ds, r in res.items() for v, s in r["variants"].items()]


# The combined stack. Chosen from the per-cause results (LOSS_AUDIT_2026-11-02.md
# says why); the eval scores every one of these against live the same way.
WK_BEFEES = {"lock_weekend_only": True, "lock_trigger_r": 1.0, "lock_r": 0.0, "lock_plus_fees": True}
STACKS: Dict[str, Dict[str, Any]] = {
    "risk_10+wk_befees": {"risk_cap_frac": 0.10, **WK_BEFEES},
    "risk_15+wk_befees": {"risk_cap_frac": 0.15, **WK_BEFEES},
    "risk_20+wk_befees": {"risk_cap_frac": 0.20, **WK_BEFEES},
    "risk_15+wk_befees+age_8h": {"risk_cap_frac": 0.15, "side_age_h": 8.0, **WK_BEFEES},
    "risk_15+wk_befees+no_break": {"risk_cap_frac": 0.15, "no_break_entry": True, **WK_BEFEES},
    "risk_10+no_break": {"risk_cap_frac": 0.10, "no_break_entry": True},
}
STACK_FAMILIES = {"risk": ["risk_10+wk_befees", "risk_15+wk_befees", "risk_20+wk_befees"]}


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import direction_series, load_coin_candles, scouting_params
    from services.strategies.evals.stop_cap_test import closed_only, same_market
    from services.strategies.evals.strategy_sweep import base_params

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""))
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""))
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache")
    ap.add_argument("--silver-max-leverage", type=float, default=25.0)
    ap.add_argument("--cache-dir", default="/tmp/loss-audit/cache")
    ap.add_argument("--out-dir", default="/tmp/loss-audit")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} loss-fix: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    def fresh(coin: str) -> Tuple[List[Candle], List[Candle]]:
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)

    # ── datasets
    b15, b1h = fresh("xyz:BRENTOIL")
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = dataclasses.replace(base_params(), starting_equity=START)
    oil = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    brent: Dict[str, Tuple] = {}
    if oil:
        brent["brent_journal"] = (b15, b1h, journaled_direction(oil), min(th.created_at_ms for th in oil), b_end)
    rp = D.load_journaled_theses(a.oil_replay_dir)
    if rp:
        rs, re_ = min(th.created_at_ms for th in rp), min(b_end, max(th.created_at_ms for th in rp) + DAY_MS)
        brent["brent_replay30"] = (b15, b1h, journaled_direction(rp), rs, re_)
        for s in CONTROLS:
            brent[f"brent_{s}_replay"] = (b15, b1h, direction_series(s, b1h), rs, re_)
    for s in CONTROLS:
        brent[f"brent_{s}_30d"] = (b15, b1h, direction_series(s, b1h), b_end - 30 * DAY_MS, b_end)

    silver_c = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
    s15, s1h = silver_c["c15"], silver_c["c1h"]
    atr, _ = mean_atr(s1h)
    sp = dataclasses.replace(scouting_params(a.silver_max_leverage, atr), starting_equity=START, **B.SILVER_LIVE)
    silver: Dict[str, Tuple] = {}
    s_theses = D.load_journaled_theses(a.silver_theses_dir)
    if s_theses:
        ss, se = min(th.created_at_ms for th in s_theses), max(c.t for c in s15) + FIFTEEN_MS
        silver["silver_theses"] = (s15, s1h, journaled_direction(s_theses), ss, se)
        for s in CONTROLS:
            silver[f"silver_{s}"] = (s15, s1h, direction_series(s, s1h), ss, se)
    f15, f1h = fresh("xyz:SILVER")
    f_end = f15[-1].t + FIFTEEN_MS
    metals = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    metals = same_market(metals, f1h) if metals else []
    if metals:
        silver["silver_journal"] = (f15, f1h, journaled_direction(metals), min(th.created_at_ms for th in metals), f_end)
    for s in CONTROLS:
        silver[f"silver_{s}_recent"] = (f15, f1h, direction_series(s, f1h), f_end - 30 * DAY_MS, f_end)
    log(f"datasets: brent {list(brent)}, silver {list(silver)}; brent to {LA._iso(b_end)}, silver to {LA._iso(f_end)}")

    thesis_b = ("brent_journal", "brent_replay30")
    thesis_s = ("silver_theses", "silver_journal")
    studies = {"S": (sizing_variants(), SIZING_FAMILIES), "W": (filter_variants(), FILTER_FAMILIES),
               "R": (lock_variants(), LOCK_FAMILIES),
               "C": ({BASELINE: {}, **STACKS}, STACK_FAMILIES)}
    results: Dict[str, Dict[str, Any]] = {}
    for key, (named, fams) in studies.items():
        rb = study(brent, bp, named, funding)
        rsil = study(silver, sp, named)
        results[key] = {"variants": named,
                        "brent": {"datasets": rb, **score_study(rb, named, fams, thesis_b)},
                        "silver": {"datasets": rsil, **score_study(rsil, named, fams, thesis_s)}}
        log(f"study {key}: {len(named)} variants ({time.time() - t0:.0f}s)")

    # ── the real trades (the audit's own rebuild)
    inputs = LA.load_inputs(os.path.join(a.out_dir, "raw"))
    bars5 = {c: D.fetch_candles_cached(c, "5m", 5000, a.cache_dir) for c in LA.COINS}
    audit = LA.run_audit(inputs, bars5, {"oil": oil, "metals": metals})
    closed = [t for t in audit["trades"] if t["exit_time"] is not None]
    c1h_by = {"xyz:BRENTOIL": b1h, "xyz:SILVER": f1h}
    th_by = {"oil": oil, "metals": metals}
    real_rows = []
    for tr in closed:
        row = {"coin": tr["coin"], "side": tr["side"], "entry": LA._iso(tr["entry_time"]), "net_usd": round(tr["net_usd"], 2),
               "desk_equity": round(tr["equity_at_size"] * desk_share(tr), 2) if tr.get("equity_at_size") else None}
        for v, ov in sizing_variants().items():
            row[f"S:{v}"] = resize(tr, ov)["net_usd"] if v != BASELINE else row["net_usd"]
        for v, hit in filter_hits(tr, c1h_by[tr["coin"]], th_by[tr["desk"]], filter_variants()).items():
            if v != BASELINE:
                row[f"W:{v}"] = "blocked" if hit else "taken"
        base = bp if tr["coin"] == "xyz:BRENTOIL" else sp
        for v, ov in lock_variants().items():
            if v != BASELINE:
                row[f"R:{v}"] = lock_real(tr, bars5[tr["coin"]], ov, base)["net_usd"]
        for v, ov in STACKS.items():
            row[f"C:{v}"] = stack_real(tr, ov, bars5[tr["coin"]], c1h_by[tr["coin"]], th_by[tr["desk"]], base)
        real_rows.append(row)
    def totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        return {k: round(sum(r[k] for r in rows if isinstance(r.get(k), (int, float))), 2)
                for k in (rows[0] if rows else {}) if k.startswith(("S:", "R:", "C:")) or k == "net_usd"}
    real_tot = {"since_10_20": totals(real_rows),
                "since_10_31": totals([r for r in real_rows if r["entry"] >= "2026-10-31"])}
    real_filters = {}
    for k in (real_rows[0] if real_rows else {}):
        if k.startswith("W:"):
            blocked = [r for r in real_rows if r[k] == "blocked"]
            # refused AT THAT MINUTE: the desk may well have entered a little later
            real_filters[k] = {"blocked": len(blocked), "saved_usd": round(-sum(min(0, r["net_usd"]) for r in blocked), 2),
                               "missed_usd": round(sum(max(0, r["net_usd"]) for r in blocked), 2)}

    # ── the 2026-11-02 Brent long in the sim, every per-cause variant
    nov2 = {}
    if oil:
        named_all = {**sizing_variants(), **filter_variants(), **lock_variants(), **STACKS}
        nov2 = nov2_long(b15, b1h, journaled_direction(oil), bp, named_all,
                         int(datetime(2026, 11, 2, 3, 45, tzinfo=timezone.utc).timestamp() * 1000),
                         int(datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc).timestamp() * 1000))

    with open(os.path.join(a.out_dir, "fix_summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for key, r in results.items():
            for desk in ("brent", "silver"):
                w.writerows(summary_rows(key, desk, r[desk]["datasets"]))
    if real_rows:
        with open(os.path.join(a.out_dir, "real_trades.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(real_rows[0]))
            w.writeheader()
            w.writerows(real_rows)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
              "brent_data_end": LA._iso(b_end), "silver_data_end": LA._iso(f_end),
              "studies": results, "real": {"rows": real_rows, "totals": real_tot, "filters": real_filters},
              "nov2_long": nov2}
    with open(os.path.join(a.out_dir, "fix_report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\n{a.out_dir}/fix_report.json\n{a.out_dir}/fix_summary.csv\n{a.out_dir}/real_trades.csv")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"brent to {rep['brent_data_end']}, silver to {rep['silver_data_end']}")
    for key, r in rep["studies"].items():
        for desk in ("brent", "silver"):
            sc = r[desk]
            print(f"\n== {key} {desk}  live: {json.dumps(sc['live'])}  pass: {sc['pass']}  best: {sc['best']}")
            ds_names = list(sc["datasets"])
            print("  " + f"{'variant':<28}" + "".join(f"{d[-14:]:>15}" for d in ds_names))
            for v in r["variants"]:
                print("  " + f"{v:<28}" + "".join(f"{sc['datasets'][d]['variants'][v]['final_equity']:>15.2f}"
                                                  for d in ds_names))
            for v, x in sc["per_variant"].items():
                th = x.get("thesis") or {}
                print(f"  {v:<28} beats {x.get('beats')}/{x.get('of')} geo {x.get('geo_mean_ratio')} "
                      f"thesis {th.get('beats')}/{th.get('of')} plateau {x.get('plateau')} liq {x.get('liq_ok')} "
                      f"adopt {x['adopt']} growth {x['growth']['beats']}/{x['growth']['of']} "
                      f"pruin {x['p_ruin_mean']} dd {x['max_dd_mean']} worst% {x['worst_pct_mean']}")
    print("\nreal trades totals:", json.dumps(rep["real"]["totals"]))
    print("real trades filters:", json.dumps(rep["real"]["filters"]))
    for row in rep["real"]["rows"]:
        print("  ", json.dumps(row))
    print("\n2026-11-02 Brent long in the sim:")
    for v, x in rep["nov2_long"].items():
        print(f"  {v:<28} {json.dumps(x)}")


if __name__ == "__main__":
    raise SystemExit(main())
