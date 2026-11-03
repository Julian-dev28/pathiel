"""Profit lock backtest, 2026-11-01: baseline (fixed stop) vs locking profit.

Correction (2026-11-03): this eval predates `Params.risk_cap_frac` (added
2026-11-02, `LOSS_AUDIT_2026-11-02.md`), so its published numbers in
`docs/strategy/PROFIT_LOCK_TEST_2026-11-01.md` are accurately full-port
for their own time. `bp`/`sp` below now carry the 15% cap
(`live_brent_params`/`risk_cap_frac=LIVE_RISK_CAP_FRAC`). This does not
risk flipping the verdict: every lock/trail variant here shares the SAME
entry stop distance as the baseline (only the EXIT moves, via the lock),
and `risk_capped_notional` sizes once, at entry, off that shared distance
-- so baseline and every candidate size identically either way.

Operator, 2026-11-01, after a Brent long from 111.48 (stop 111.02, target
113.66) reached +1.9R and drifted back to close +$7.83: "we were up like 35$
and we round tripped" ... "yeah backtest it".

The rule (`desk_tick_sim.Params.lock_*`): once the best price since the fill
is TRIGGER R in favour, move the stop to entry + LOCK R (mirror for a
short). Only tightens; the target does not move. Grid: TRIGGER in
{1.0, 1.5, 2.0} x LOCK in {0, 0.5, 1.0} with LOCK < TRIGGER, each as a fixed
lock and as a trail (TRIGGER - LOCK R behind the best price), each with the
same-bar close-confirm fill (default) and without it (next bar only: what a
15-minute tick with no price watcher could do).

Datasets, every one on the live rules (`live_params()`), $64 start:
  brent_journal    the live oil journal's theses (READ ONLY), first thesis to the data end
  brent_replay30   30 replayed Opus oil theses (price only, 2026-09-30 to 10-29), READ ONLY
  brent_<series>_<n>d   no-thesis controls: always_long / always_short / daily_sign, 30 and 50 days
  silver_theses    the 30-day silver Opus replay (SILVER_THESIS_TEST_2026-10-31.md), its cache
  silver_<series>  silver's no-thesis controls on the same window

Per variant: final equity, return, max drawdown, trades, win rate, avg R,
and an attribution of every exit the lock caused: walk the same position on
with its original stop and target (`counterfactual_exit`), and count
  cut_winner     it would have filled the target (cost)
  saved_stop     it would have hit the original stop (benefit)
  saved_giveback / cut_runner   it closed later by invalidation, a direction
                 flip or the data end, worse / better than the lock (benefit / cost)
plus the R the lock gained or lost on those exits. Prices only (no fees) in
the attribution; final equity carries everything.

    python -m services.strategies.evals.profit_lock_test \\
        --oil-theses-dir <journal dir> --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \\
        --silver-theses-dir /tmp/thesis-replay-silver/theses --silver-cache-dir /tmp/thesis-backtest/cache

Reads candles through the eval disk cache and the venue's public /info;
never touches the live account, the live config or the journal.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, Trade, profit_lock_stop, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.live_params import LIVE_RISK_CAP_FRAC, live_brent_params

START = 64.0
FIFTEEN_MS = 900_000
TRIGGERS = (1.0, 1.5, 2.0)
LOCKS = (0.0, 0.5, 1.0)
CONTROLS = ("always_long", "always_short", "daily_sign")


# ── the grid (pure) ────────────────────────────────────────────────────────

def variant_name(trigger: float, lock: float, trail: bool, confirm: bool) -> str:
    return f"{'trail' if trail else 'lock'}_t{trigger:g}_l{lock:g}{'' if confirm else '_nextbar'}"


def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; "baseline" is the live rule (no lock)."""
    out: Dict[str, Dict[str, Any]] = {"baseline": {}}
    for confirm in (True, False):
        for trail in (False, True):
            for trig in TRIGGERS:
                for lock in LOCKS:
                    if lock < trig:
                        out[variant_name(trig, lock, trail, confirm)] = {
                            "lock_trigger_r": trig, "lock_r": lock, "lock_trail": trail,
                            "lock_confirm_close": confirm}
    return out


# ── counterfactual attribution (pure) ──────────────────────────────────────

def counterfactual_exit(trade: Trade, c15: Sequence[Candle], c1h_by_close: Dict[int, Candle],
                        direction_at: Callable[[int], Any], params: Params) -> Tuple[str, float, int]:
    """Where `trade` would have closed with its ORIGINAL stop and target,
    walking on from the bar the lock closed it in. The sim's own order per
    bar: a 1h close through the thesis invalidation, then a direction flip
    at the open, then the stop (judged before the target), then the target
    (a trade-through in "limit" mode). The lock's own exit bar is judged on
    stop and target only (its open was already behind the position).
    Returns (reason, price, time); "end" marks out at the last close."""
    is_long = trade.side == "long"
    stop = trade.orig_stop_px if trade.orig_stop_px is not None else trade.stop_px
    target = trade.target_px
    last: Optional[Candle] = None
    for bar in c15:
        if bar.t < trade.exit_time:
            continue
        last = bar
        if bar.t > trade.exit_time:
            c1 = c1h_by_close.get(bar.t)
            d = direction_at(bar.t)
            if c1 is not None and d is not None and d.invalidation_px is not None:
                if (c1.c <= d.invalidation_px) if is_long else (c1.c >= d.invalidation_px):
                    return "invalidation", c1.c, bar.t
            if d is not None and d.side != trade.side:
                return "direction_flip", bar.o, bar.t
        if (bar.l <= stop) if is_long else (bar.h >= stop):
            return "stop", stop, bar.t
        if params.target_mode == "limit":
            hit = (bar.h >= target + params.tick_size - 1e-9) if is_long else (bar.l <= target - params.tick_size + 1e-9)
        else:
            hit = (bar.h >= target) if is_long else (bar.l <= target)
        if hit:
            return "target", target, bar.t
    if last is None:
        return "end", float(trade.exit_px or trade.entry_px), int(trade.exit_time or 0)
    return "end", last.c, last.t


def classify(cf_reason: str, lock_px: float, cf_px: float, side: str) -> str:
    if cf_reason == "target":
        return "cut_winner"
    if cf_reason == "stop":
        return "saved_stop"
    better = (lock_px > cf_px) if side == "long" else (lock_px < cf_px)
    return "saved_giveback" if better else "cut_runner"


def attribute(run: Run, c15: Sequence[Candle], c1h: Sequence[Candle], direction_at,
              params: Params) -> Dict[str, Any]:
    """Every "lock" exit of `run`, classified against its counterfactual."""
    by_close = {c.t + HOUR_MS: c for c in c1h}
    counts = {"cut_winner": 0, "saved_stop": 0, "saved_giveback": 0, "cut_runner": 0}
    delta_r = {k: 0.0 for k in counts}
    rows: List[Dict[str, Any]] = []
    for t in run.trades:
        if t.exit_reason != "lock":
            continue
        reason, cf_px, cf_t = counterfactual_exit(t, c15, by_close, direction_at, params)
        sign = 1.0 if t.side == "long" else -1.0
        risk = abs(t.entry_px - (t.orig_stop_px if t.orig_stop_px is not None else t.stop_px))
        d_r = sign * (t.exit_px - cf_px) / risk if risk > 0 else 0.0
        kind = classify(reason, t.exit_px, cf_px, t.side)
        counts[kind] += 1
        delta_r[kind] += d_r
        rows.append({"side": t.side, "entry": _iso(t.entry_time), "entry_px": round(t.entry_px, 4),
                     "orig_stop": round(t.orig_stop_px, 4), "target": round(t.target_px, 4),
                     "lock_exit": _iso(t.exit_time), "lock_px": round(t.exit_px, 4),
                     "cf_reason": reason, "cf_px": round(cf_px, 4), "cf_time": _iso(cf_t),
                     "kind": kind, "delta_r": round(d_r, 3)})
    return {"counts": counts, "delta_r": {k: round(v, 2) for k, v in delta_r.items()},
            "net_delta_r": round(sum(delta_r.values()), 2), "rows": rows}


def mfe_r(trade: Trade, c15: Sequence[Candle]) -> Optional[float]:
    """Best excursion in R between a trade's entry and exit bars (inclusive)."""
    stop = trade.orig_stop_px if trade.orig_stop_px is not None else trade.stop_px
    risk = abs(trade.entry_px - stop)
    if risk <= 0 or trade.exit_time is None:
        return None
    bars = [c for c in c15 if trade.entry_time <= c.t <= trade.exit_time]
    if not bars:
        return None
    if trade.side == "long":
        return (max(c.h for c in bars) - trade.entry_px) / risk
    return (trade.entry_px - min(c.l for c in bars)) / risk


# The live trade that started this (operator, 2026-11-01): opened Sat
# 2026-10-31 16:17Z inside the 16:15 bar, 40.73 Brent at 20x; peak mid
# 112.36 (+1.9R, +$35.84); closed +$7.83 net.
LIVE_TRADE = {"side": "long", "entry_px": 111.48, "stop_px": 111.02, "target_px": 113.66,
              "size": 40.73, "entry_bar_utc": (2026, 10, 31, 16, 15), "closed_net_usd": 7.83}


def replay_position(side: str, entry: float, stop: float, target: float, bars: Sequence[Candle],
                    params: Params) -> Dict[str, Any]:
    """One already-open position walked over `bars` (the bars AFTER its
    fill bar) with the simulator's own per-bar order: the stop as it stood
    at the bar's open (a moved stop the bar opens through fills at the
    open), then the target (a trade-through in "limit" mode), then the
    profit lock from the bar's best price, filling in the same bar only on
    a close through it (`lock_confirm_close`). Prices only, no slippage.
    Parity with `simulate` is gate-tested. Pure."""
    is_long = side == "long"
    sign = 1.0 if is_long else -1.0
    risk = abs(entry - stop)
    cur, moved, best, best_r = stop, False, None, 0.0
    for bar in bars:
        if (bar.l <= cur) if is_long else (bar.h >= cur):
            px = (min(cur, bar.o) if is_long else max(cur, bar.o)) if moved else cur
            return {"reason": "lock" if moved else "stop", "exit_px": px, "exit_time": bar.t, "best_r": best_r}
        if params.target_mode == "limit":
            hit = (bar.h >= target + params.tick_size - 1e-9) if is_long else (bar.l <= target - params.tick_size + 1e-9)
        else:
            hit = (bar.h >= target) if is_long else (bar.l <= target)
        if hit:
            return {"reason": "target", "exit_px": target, "exit_time": bar.t, "best_r": best_r}
        b = bar.h if is_long else bar.l
        best = b if best is None else (max(best, b) if is_long else min(best, b))
        best_r = max(best_r, sign * (best - entry) / risk)
        new = profit_lock_stop(side, entry, risk, best, params)
        if new is not None and ((new > cur + 1e-12) if is_long else (new < cur - 1e-12)):
            cur, moved = new, True
            if params.lock_confirm_close and ((bar.c <= cur) if is_long else (bar.c >= cur)):
                return {"reason": "lock", "exit_px": cur, "exit_time": bar.t, "best_r": best_r}
    last = bars[-1] if bars else None
    return {"reason": "open", "exit_px": last.c if last else entry, "exit_time": last.t if last else None,
            "best_r": best_r}


def live_trade_replay(c15: Sequence[Candle], named: Dict[str, Dict[str, Any]], base: Params) -> Dict[str, Any]:
    """Every variant on the real 2026-10-31 position, from the bar after its fill."""
    lt = LIVE_TRADE
    fill_bar = int(datetime(*lt["entry_bar_utc"], tzinfo=timezone.utc).timestamp() * 1000)
    bars = [c for c in c15 if c.t > fill_bar]
    out: Dict[str, Any] = {"trade": lt, "bars": len(bars), "data_end": _iso(bars[-1].t + FIFTEEN_MS) if bars else None,
                           "variants": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        r = replay_position(lt["side"], lt["entry_px"], lt["stop_px"], lt["target_px"], bars, p)
        gross = (r["exit_px"] - lt["entry_px"]) * lt["size"]
        exit_fee = (base.maker_fee if r["reason"] == "target" else base.taker_fee) * r["exit_px"] * lt["size"]
        entry_fee = base.taker_fee * lt["entry_px"] * lt["size"]
        out["variants"][v] = {**r, "exit_time": _iso(r["exit_time"]), "exit_px": round(r["exit_px"], 3),
                              "best_r": round(r["best_r"], 2), "gross_usd": round(gross, 2),
                              "net_usd": round(gross - exit_fee - entry_fee, 2)}
    return out


def summarize(run: Run, start_ms: int, end_ms: int) -> Dict[str, Any]:
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    exits: Dict[str, int] = {}
    for t in run.trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    n = len(run.trades)
    return {
        "days": round((end_ms - start_ms) / DAY_MS, 1),
        "final_equity": round(run.final_equity, 2),
        "return_pct": round((run.final_equity / START - 1.0) * 100, 1),
        "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "trades": n,
        "win_rate": round(sum(1 for t in run.trades if t.net_pnl_usd > 0) / n, 3) if n else None,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
        "total_r": round(sum(rs), 2),
        "liquidated": run.liquidated,
        "exits": dict(sorted(exits.items())),
    }


def give_back(run: Run, c15: Sequence[Candle]) -> Dict[str, int]:
    """Baseline trades that reached 1 / 1.5 / 2R and still did not fill the target."""
    out = {"1.0": 0, "1.5": 0, "2.0": 0}
    for t in run.trades:
        if t.exit_reason == "target":
            continue
        m = mfe_r(t, c15)
        for k in out:
            if m is not None and m >= float(k):
                out[k] += 1
    return out


def score(rows: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per variant across datasets: how many it beats the baseline on (final
    equity) and the geometric mean of final/baseline (a liquidated run
    counts as a ratio of 0.01 so one wipe-out cannot hide). Pure."""
    names = next(iter(rows.values())).keys()
    out: Dict[str, Dict[str, Any]] = {}
    for v in names:
        if v == "baseline":
            continue
        ratios, wins, losses = [], 0, 0
        for ds, by_v in rows.items():
            b, x = by_v["baseline"]["final_equity"], by_v[v]["final_equity"]
            r = max(x, 0.01 * max(b, 1e-9)) / b if b > 0 else (1.0 if x <= 0 else 2.0)
            ratios.append(r)
            wins += x > b + 1e-9
            losses += x < b - 1e-9
        g = 1.0
        for r in ratios:
            g *= r
        out[v] = {"beats": wins, "loses": losses, "of": len(ratios),
                  "geo_mean_ratio": round(g ** (1.0 / len(ratios)), 4)}
    return out


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


# ── data + runs ────────────────────────────────────────────────────────────

def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def run_dataset(name: str, c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params,
                start_ms: int, end_ms: int, funding=None, named: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """Every variant on one dataset. Returns {"variants": {name: summary}, ...}."""
    named = named or variants()
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "variants": {}, "lock_rows": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, start_ms, end_ms)
        if v == "baseline":
            out["give_back"] = give_back(run, w15)
        else:
            att = attribute(run, w15, w1h, direction_at, p)
            s["lock"] = {k: att[k] for k in ("counts", "delta_r", "net_delta_r")}
            out["lock_rows"][v] = att["rows"]
        out["variants"][v] = s
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import direction_series, load_coin_candles, scouting_params

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache")
    ap.add_argument("--silver-max-leverage", type=float, default=None,
                    help="default: the venue's own maxLeverage, as thesis_backtest reads it")
    ap.add_argument("--cache-dir", default="/tmp/profit-lock/cache")
    ap.add_argument("--out-dir", default="/tmp/profit-lock")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    named = variants()
    results: Dict[str, Dict[str, Any]] = {}

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} profit-lock: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    # Brent: one fresh cache for every Brent dataset
    brent = load_coin_candles("xyz:BRENTOIL", a.cache_dir)
    b15, b1h = brent["c15"], brent["c1h"]
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = live_brent_params(size_slippage=True, starting_equity=START)

    for label, tdir, end_rule in (("brent_journal", a.oil_theses_dir, "data"),
                                  ("brent_replay30", a.oil_replay_dir, "last+1d")):
        theses = D.load_journaled_theses(tdir) if tdir else []
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        start = min(th.created_at_ms for th in theses)
        end = b_end if end_rule == "data" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        results[label] = run_dataset(label, b15, b1h, journaled_direction(theses), bp, start, end, funding, named)
        results[label]["n_theses"] = len(theses)
        log(f"{label}: {len(theses)} theses done ({time.time() - t0:.0f}s)")

    for days in (30, 50):
        start = b_end - days * DAY_MS
        for s in CONTROLS:
            label = f"brent_{s}_{days}d"
            results[label] = run_dataset(label, b15, b1h, direction_series(s, b1h), bp, start, b_end,
                                         funding, named)
            log(f"{label} done ({time.time() - t0:.0f}s)")

    # Silver: the 30-day replay's own cache and rules (thesis_backtest.score)
    s_theses = D.load_journaled_theses(a.silver_theses_dir)
    if s_theses:
        silver = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
        s15, s1h = silver["c15"], silver["c1h"]
        atr, _ = mean_atr(s1h)
        if a.silver_max_leverage is None:
            from services.strategies.evals.market_scout import fetch_market_meta
            a.silver_max_leverage = float((fetch_market_meta(["xyz:SILVER"]).get("xyz:SILVER") or {})
                                          .get("maxLeverage") or 20)
        # risk_cap_frac only (the live 15% cap); this file's own mean-ATR,
        # no-floor stop-rule shape (thesis_backtest.score's original rule) is
        # intentional here, not live_silver_params's per-tick ATR + floor.
        sp = dataclasses.replace(scouting_params(a.silver_max_leverage, atr), risk_cap_frac=LIVE_RISK_CAP_FRAC)
        s_start = min(th.created_at_ms for th in s_theses)
        s_end = max(c.t for c in s15) + FIFTEEN_MS
        results["silver_theses"] = run_dataset("silver_theses", s15, s1h, journaled_direction(s_theses), sp,
                                               s_start, s_end, None, named)
        results["silver_theses"]["n_theses"] = len(s_theses)
        for s in CONTROLS:
            results[f"silver_{s}"] = run_dataset(f"silver_{s}", s15, s1h, direction_series(s, s1h), sp,
                                                 s_start, s_end, None, named)
        log(f"silver done ({time.time() - t0:.0f}s)")

    # the 2026-11-01 trade, as the sim took it under the journal
    spotlight = live_trade_replay(b15, named, bp)

    groups = {
        "thesis": [k for k in results if k in ("brent_journal", "brent_replay30", "silver_theses")],
        "brent_controls": [f"brent_{s}_{d}d" for d in (30, 50) for s in CONTROLS],
        "all": list(results),
    }
    scores = {g: score({k: results[k]["variants"] for k in ks}) for g, ks in groups.items() if ks}

    csv_path = os.path.join(a.out_dir, "lock_exits.csv")
    with open(csv_path, "w", newline="") as fh:
        w = None
        for ds, r in results.items():
            for v, rows in r["lock_rows"].items():
                for row in rows:
                    row = {"dataset": ds, "variant": v, **row}
                    if w is None:
                        w = csv.DictWriter(fh, fieldnames=list(row))
                        w.writeheader()
                    w.writerow(row)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end),
        "datasets": {k: {kk: vv for kk, vv in r.items() if kk != "lock_rows"}
                     for k, r in results.items()},
        "scores": scores, "live_trade_2026_10_31": spotlight,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nlock exits: {csv_path}")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    for ds, r in rep["datasets"].items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]}  baseline give-backs (>=1/1.5/2R, no target): "
              f"{r['give_back']}")
        print(f"  {'variant':<24} {'$end':>8} {'ret%':>7} {'dd%':>5} {'n':>3} {'win':>4} {'avgR':>6} "
              f"{'cut_win':>7} {'sv_stop':>7} {'sv_gb':>5} {'cut_run':>7} {'netdR':>6}")
        for v, s in r["variants"].items():
            lk = s.get("lock") or {}
            c = lk.get("counts") or {}
            win = f"{s['win_rate']:.0%}" if s["win_rate"] is not None else "-"
            ar = f"{s['avg_r']:.2f}" if s["avg_r"] is not None else "-"
            print(f"  {v:<24} {s['final_equity']:8.2f} {s['return_pct']:7.1f} {s['max_dd_pct']:5.1f} "
                  f"{s['trades']:>3} {win:>4} {ar:>6} {c.get('cut_winner', ''):>7} {c.get('saved_stop', ''):>7} "
                  f"{c.get('saved_giveback', ''):>5} {c.get('cut_runner', ''):>7} {lk.get('net_delta_r', ''):>6}")
    for g, sc in rep["scores"].items():
        print(f"\nscore [{g}] (beats/loses baseline of N, geo-mean final/baseline):")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"  {v:<24} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    lt = rep.get("live_trade_2026_10_31")
    if lt:
        print(f"\nthe live trade (long 111.48, stop 111.02, target 113.66, 40.73 Brent), {lt['bars']} bars to "
              f"{lt['data_end']}; it actually closed +${lt['trade']['closed_net_usd']} net:")
        for v, r in lt["variants"].items():
            print(f"  {v:<24} {r['reason']:<6} {r['exit_px']:>8} {r['exit_time']}  best {r['best_r']}R  "
                  f"net ${r['net_usd']}")


if __name__ == "__main__":
    raise SystemExit(main())
