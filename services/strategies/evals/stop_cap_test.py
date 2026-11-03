"""Daily stop-out cap backtest, 2026-11-02: baseline (no cap) vs a cap.

Correction (2026-11-03): this eval was written before `Params.risk_cap_frac`
existed (17:17 the same day), so its original run's "baseline"/"live" rows
were accurately full-port at the time. Today's live rule also carries the
15% risk cap (`LOSS_AUDIT_2026-11-02.md`); `bp`/`sp`/`fp` below now route
through `live_params.live_brent_params` or set `risk_cap_frac` directly, so
a fresh run of this file matches the real live rule. The cap only changes
position SIZE, never entry/exit gating or timing (`risk_capped_notional` is
applied after `check_leverage`/`check_levels` already decided whether a
setup trades), so re-running with the cap on does not flip this file's
"keep live, no cap" verdict -- see
`docs/strategy/STOP_CAP_TEST_2026-11-02.md`'s correction note.

Operator, 2026-11-02 ("go"): the silver desk shorted three times into a
rising market. Two shorts were stopped (-$1.82 at 01:05Z, -$2.56 at 05:30Z)
and a third opened at 05:35Z because a fresh thesis still said short and
the 2R gate passed. Would a daily stop-out cap help?

The rule (`desk_tick_sim.Params.daily_stop_cap` / `cap_per_side`): after N
"stop" exits on the desk in one UTC day, take no NEW entry for the rest of
that UTC day. An open position is untouched; targets and other exits do not
count; the count starts over at 00:00 UTC. Variants: N in {1, 2, 3} for the
whole desk ("cap_N") and per side ("side_N": N short stops block only shorts
that day).

Datasets, every one on the live rules (`live_params()`), $64 start:
  brent_journal    the live oil journal's theses (READ ONLY), first thesis to the data end
  brent_replay30   30 replayed Opus oil theses (price only, 2026-09-30 to 10-29), READ ONLY
  brent_<series>_<n>d   no-thesis controls: always_long / always_short / daily_sign, 30 and 50 days
  silver_theses    the 30-day silver Opus replay (SILVER_THESIS_TEST_2026-10-31.md), its cache
  silver_<series>  silver's no-thesis controls on the same window
  silver_theses_atr  the same replay with the live silver stop: 1.3 x the tick's own ATR14
                   beyond the 24h range (`Params.stop_buffer_atr`), not 1.3 x the mean ATR
  silver_journal   the live metals journal's SILVER theses (READ ONLY; the gold one is dropped),
                   fresh candles, live per-tick ATR stop: the 2026-11-02 incident itself

Per variant: final equity, return, max drawdown, trades, win rate, avg R,
and what every blocked entry would have done (`shadow_chain`): the first
setup the cap skipped is walked on with its own stop and target under the
sim's own per-bar order; the next skipped setup after it closes is walked
the same way (one slot), and so on. Each gets a reason (stop / target /
invalidation / direction_flip / end), its R before and after fees, and a
dollar estimate at the capped account's size at that moment. A negative
`blocked.r_net` sum means the cap saved that much; positive means it cost.

    python -m services.strategies.evals.stop_cap_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --metals-theses-dir <pathia>/.state/strategies/metals/theses \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \\
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
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, _full_size_notional, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.live_params import LIVE_RISK_CAP_FRAC, live_brent_params
from services.strategies.evals.profit_lock_test import score
from services.strategies.next_trade import ATR_BUFFER_MULT

START = 64.0
FIFTEEN_MS = 900_000
CAPS = (1, 2, 3)
CONTROLS = ("always_long", "always_short", "daily_sign")

# The live silver desk on 2026-11-02 (operator's numbers), for the spotlight.
LIVE_INCIDENT = {
    "coin": "xyz:SILVER", "day": "2026-11-02",
    "trades": [{"side": "short", "exit": "stop", "exit_utc": "01:05Z", "net_usd": -1.82},
               {"side": "short", "exit": "stop", "exit_utc": "05:30Z", "net_usd": -2.56},
               {"side": "short", "opened_utc": "05:35Z", "exit": "open"}],
}


# ── the grid (pure) ────────────────────────────────────────────────────────

def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; "baseline" is the live rule (no cap)."""
    out: Dict[str, Dict[str, Any]] = {"baseline": {}}
    for n in CAPS:
        out[f"cap_{n}"] = {"daily_stop_cap": n, "cap_per_side": False}
    for n in CAPS:
        out[f"side_{n}"] = {"daily_stop_cap": n, "cap_per_side": True}
    return out


# ── what a blocked entry would have done (pure) ────────────────────────────

def walk_entry(side: str, entry_t: int, entry_px: float, stop: float, target: float,
               c15: Sequence[Candle], c1h_by_close: Dict[int, Candle],
               direction_at: Callable[[int], Any], params: Params) -> Tuple[str, float, int]:
    """Where a market entry at `entry_t` would have closed, in the sim's own
    per-bar order. The entry bar: the stop only (a market fill at the open
    can be stopped by its own bar; the target is judged from the next bar).
    Each later bar: a 1h close through the thesis invalidation, then a
    direction flip at the open, then the stop (judged before the target),
    then the target (a trade-through in "limit" mode). Fixed stop, no lock.
    Returns (reason, price, time); "end" marks out at the last close."""
    is_long = side == "long"
    last: Optional[Candle] = None
    for bar in c15:
        if bar.t < entry_t:
            continue
        last = bar
        if bar.t > entry_t:
            c1 = c1h_by_close.get(bar.t)
            d = direction_at(bar.t)
            if c1 is not None and d is not None and d.invalidation_px is not None:
                if (c1.c <= d.invalidation_px) if is_long else (c1.c >= d.invalidation_px):
                    return "invalidation", c1.c, bar.t
            if d is not None and d.side != side:
                return "direction_flip", bar.o, bar.t
        if (bar.l <= stop) if is_long else (bar.h >= stop):
            return "stop", stop, bar.t
        if bar.t == entry_t:
            continue
        if params.target_mode == "limit":
            hit = (bar.h >= target + params.tick_size - 1e-9) if is_long else (bar.l <= target - params.tick_size + 1e-9)
        else:
            hit = (bar.h >= target) if is_long else (bar.l <= target)
        if hit:
            return "target", target, bar.t
    if last is None:
        return "end", entry_px, entry_t
    return "end", last.c, last.t


def equity_at(run: Run, t: int) -> float:
    """The run's marked equity on tick `t` (the last curve point at or before it)."""
    eq = run.params.starting_equity if run.params else START
    for ts, mark in run.equity_curve:
        if ts > t:
            break
        eq = mark
    return eq


def shadow_chain(run: Run, c15: Sequence[Candle], c1h: Sequence[Candle], direction_at,
                 params: Params) -> List[Dict[str, Any]]:
    """The entries the cap actually stopped, one slot at a time: the first
    skipped market setup, walked on (`walk_entry`); then the first skipped
    setup strictly after that exit, and so on. A skipped setup that would
    have rested a limit (edge entry mode) is listed as "rest_not_walked".
    Prices from the setup; fees at the live rates (taker in; maker on a
    limit target, else taker); no slippage or funding. `usd_est` sizes the
    trade at the capped run's equity at that tick, the way the sim would."""
    by_close = {c.t + HOUR_MS: c for c in c1h}
    rows: List[Dict[str, Any]] = []
    busy_until = -1
    for b in run.cap_blocked:
        if b["time"] <= busy_until:
            continue
        side, entry, stop, target = b["side"], b["entry_px"], b["stop_px"], b["target_px"]
        base = {"kind": b["kind"], "side": side, "time": _iso(b["time"]), "entry_px": round(entry, 4),
                "stop_px": round(stop, 4), "target_px": round(target, 4), "stops_today": b["stops_today"]}
        if not b["is_market"]:
            rows.append({**base, "reason": "rest_not_walked"})
            continue
        reason, px, t_exit = walk_entry(side, b["time"], entry, stop, target, c15, by_close, direction_at, params)
        busy_until = t_exit
        sign = 1.0 if side == "long" else -1.0
        risk = abs(entry - stop)
        exit_fee = params.maker_fee if (reason == "target" and params.target_mode != "market") else params.taker_fee
        fee_px = params.taker_fee * entry + exit_fee * px
        r_gross = sign * (px - entry) / risk if risk > 0 else 0.0
        r_net = (sign * (px - entry) - fee_px) / risk if risk > 0 else 0.0
        size = _full_size_notional(equity_at(run, b["time"]), params) / entry if entry > 0 else 0.0
        usd = size * (sign * (px - entry) - fee_px)
        rows.append({**base, "reason": reason, "exit_px": round(px, 4), "exit_time": _iso(t_exit),
                     "r_gross": round(r_gross, 3), "r_net": round(r_net, 3), "usd_est": round(usd, 2),
                     "outcome": "open" if reason == "end" else ("win" if r_net > 0 else "loss")})
    return rows


def blocked_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Count and price the blocked entries. `r_net` / `usd_est` are what
    they would have made: negative means the cap saved that, positive
    means it cost that. An "open" one (still running at the data end) is
    marked at the last close."""
    walked = [r for r in rows if "r_net" in r]
    reasons = Counter(r["reason"] for r in rows)
    return {
        "entries": len(rows),
        "wins": sum(1 for r in walked if r["outcome"] == "win"),
        "losses": sum(1 for r in walked if r["outcome"] == "loss"),
        "open": sum(1 for r in walked if r["outcome"] == "open"),
        "r_net": round(sum(r["r_net"] for r in walked), 2),
        "usd_est": round(sum(r["usd_est"] for r in walked), 2),
        "reasons": dict(sorted(reasons.items())),
    }


def stops_per_day(run: Run) -> Dict[str, int]:
    """How many UTC days had 1, 2, 3, 4+ stop exits (the cap's raw material)."""
    by_day: Counter = Counter()
    for t in run.trades:
        if t.exit_reason == "stop" and t.exit_time is not None:
            by_day[t.exit_time // DAY_MS] += 1
    hist = {"1": 0, "2": 0, "3": 0, "4+": 0}
    for n in by_day.values():
        hist["4+" if n >= 4 else str(n)] += 1
    return hist


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
        "cap_days": len({b["time"] // DAY_MS for b in run.cap_blocked}),
        "blocked_ticks": len(run.cap_blocked),
    }


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def closed_only(candles: Sequence[Candle], interval_ms: int, asof_ms: int) -> List[Candle]:
    """Drop a bar still forming at `asof_ms` (its close time is after it)."""
    return [c for c in candles if c.t + interval_ms <= asof_ms]


def same_market(theses: Sequence[D.ThesisDirection], c1h: Sequence[Candle], tol: float = 0.25
                ) -> List[D.ThesisDirection]:
    """Theses whose entry sits within `tol` of the coin's price when they
    were written. The metals journal held a GOLD thesis (entry 4028) before
    the desk moved to silver; this keeps only the silver ones. A thesis with
    no entry (flat) is kept when it falls after the first kept one."""
    bars = sorted(c1h, key=lambda c: c.t)
    out: List[D.ThesisDirection] = []
    for th in sorted(theses, key=lambda x: x.created_at_ms):
        px = next((c.c for c in reversed(bars) if c.t <= th.created_at_ms), None)
        if th.entry_px is None:
            if out:
                out.append(th)
            continue
        if px and abs(float(th.entry_px) / px - 1.0) <= tol:
            out.append(th)
    return out


# ── data + runs ────────────────────────────────────────────────────────────

def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def run_dataset(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params,
                start_ms: int, end_ms: int, funding=None, named: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """Every variant on one dataset."""
    named = named or variants()
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "variants": {}, "blocked_rows": {},
                           "trades": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, start_ms, end_ms)
        out["trades"][v] = [{"side": t.side, "entry": _iso(t.entry_time), "exit": _iso(t.exit_time),
                             "reason": t.exit_reason, "net_usd": round(t.net_pnl_usd, 2),
                             "r": round(t.r_multiple, 2) if t.r_multiple is not None else None}
                            for t in run.trades]
        if v == "baseline":
            out["stops_per_day"] = stops_per_day(run)
        else:
            rows = shadow_chain(run, w15, w1h, direction_at, p)
            s["blocked"] = blocked_summary(rows)
            out["blocked_rows"][v] = rows
        out["variants"][v] = s
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import direction_series, load_coin_candles, scouting_params

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--metals-theses-dir", default="", help="READ ONLY: the live metals journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-theses-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--silver-cache-dir", default="/tmp/thesis-backtest/cache",
                    help="the 30-day silver replay's own candle cache")
    ap.add_argument("--silver-max-leverage", type=float, default=None,
                    help="default: the venue's own maxLeverage, as thesis_backtest reads it")
    ap.add_argument("--cache-dir", default="/tmp/stop-cap/cache", help="fresh Brent + silver candles")
    ap.add_argument("--out-dir", default="/tmp/stop-cap")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    named = variants()
    results: Dict[str, Dict[str, Any]] = {}

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} stop-cap: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def fresh(coin: str) -> Tuple[List[Candle], List[Candle]]:
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)

    # Brent: one fresh cache for every Brent dataset
    b15, b1h = fresh("xyz:BRENTOIL")
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
        results[label] = run_dataset(b15, b1h, journaled_direction(theses), bp, start, end, funding, named)
        results[label]["n_theses"] = len(theses)
        log(f"{label}: {len(theses)} theses done ({time.time() - t0:.0f}s)")

    for days in (30, 50):
        start = b_end - days * DAY_MS
        for s in CONTROLS:
            label = f"brent_{s}_{days}d"
            results[label] = run_dataset(b15, b1h, direction_series(s, b1h), bp, start, b_end, funding, named)
            log(f"{label} done ({time.time() - t0:.0f}s)")

    if a.silver_max_leverage is None:
        from services.strategies.evals.market_scout import fetch_market_meta
        a.silver_max_leverage = float((fetch_market_meta(["xyz:SILVER"]).get("xyz:SILVER") or {})
                                      .get("maxLeverage") or 20)

    # Silver replay: the 30-day replay's own cache and rules (thesis_backtest.score)
    s_theses = D.load_journaled_theses(a.silver_theses_dir)
    if s_theses:
        silver = load_coin_candles("xyz:SILVER", a.silver_cache_dir)
        s15, s1h = silver["c15"], silver["c1h"]
        atr, _ = mean_atr(s1h)
        sp = scouting_params(a.silver_max_leverage, atr)
        # risk_cap_frac only (2026-11-02's 15% cap): deliberately NOT routed through
        # live_silver_params, which would also swap in the per-tick ATR stop + its
        # floor -- this dataset intentionally keeps the OLD fixed, mean-ATR stop
        # (contrast with silver_theses_atr/silver_journal below, which use the real
        # live per-tick rule). See live_params.py's docstring.
        sp = dataclasses.replace(sp, starting_equity=START, risk_cap_frac=LIVE_RISK_CAP_FRAC)
        s_start = min(th.created_at_ms for th in s_theses)
        s_end = max(c.t for c in s15) + FIFTEEN_MS
        results["silver_theses"] = run_dataset(s15, s1h, journaled_direction(s_theses), sp, s_start, s_end,
                                               None, named)
        results["silver_theses"]["n_theses"] = len(s_theses)
        for s in CONTROLS:
            results[f"silver_{s}"] = run_dataset(s15, s1h, direction_series(s, s1h), sp, s_start, s_end,
                                                 None, named)
        spa = dataclasses.replace(sp, stop_buffer_atr=ATR_BUFFER_MULT)
        results["silver_theses_atr"] = run_dataset(s15, s1h, journaled_direction(s_theses), spa, s_start, s_end,
                                                   None, named)
        results["silver_theses_atr"]["n_theses"] = len(s_theses)
        log(f"silver replay done ({time.time() - t0:.0f}s)")

    # Silver live journal: the 2026-11-02 incident, on fresh candles
    incident: Dict[str, Any] = {"live": LIVE_INCIDENT}
    m_all = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    if m_all:
        f15, f1h = fresh("xyz:SILVER")
        m_theses = same_market(m_all, f1h)
        atr, _ = mean_atr(f1h)
        fp = dataclasses.replace(scouting_params(a.silver_max_leverage, atr), starting_equity=START,
                                 stop_buffer_atr=ATR_BUFFER_MULT, risk_cap_frac=LIVE_RISK_CAP_FRAC)
        m_start = min(th.created_at_ms for th in m_theses)
        m_end = f15[-1].t + FIFTEEN_MS
        results["silver_journal"] = run_dataset(f15, f1h, journaled_direction(m_theses), fp, m_start, m_end,
                                                None, named)
        results["silver_journal"]["n_theses"] = len(m_theses)
        results["silver_journal"]["dropped_theses"] = len(m_all) - len(m_theses)
        results["silver_journal"]["stop_buffer"] = f"{ATR_BUFFER_MULT} x ATR14 per tick"
        day = "2026-11-02"
        sj = results["silver_journal"]
        incident["sim"] = {v: [t for t in tr if (t["entry"] or "").startswith(day) or (t["exit"] or "").startswith(day)]
                           for v, tr in sj["trades"].items()}
        incident["blocked"] = {v: [r for r in rows if r["time"].startswith(day)]
                               for v, rows in sj["blocked_rows"].items()}
        incident["data_end"] = _iso(m_end)
        log(f"silver_journal: {len(m_theses)} silver theses done ({time.time() - t0:.0f}s)")

    groups = {
        "thesis": [k for k in results
                   if k in ("brent_journal", "brent_replay30", "silver_theses", "silver_theses_atr", "silver_journal")],
        "brent_controls": [f"brent_{s}_{d}d" for d in (30, 50) for s in CONTROLS],
        "silver_controls": [f"silver_{s}" for s in CONTROLS if f"silver_{s}" in results],
        "all": list(results),
    }
    scores = {g: score({k: results[k]["variants"] for k in ks}) for g, ks in groups.items() if ks}

    csv_path = os.path.join(a.out_dir, "blocked_entries.csv")
    fields = ["dataset", "variant", "kind", "side", "time", "entry_px", "stop_px", "target_px", "stops_today",
              "reason", "exit_px", "exit_time", "r_gross", "r_net", "usd_est", "outcome"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ds, r in results.items():
            for v, rows in r["blocked_rows"].items():
                for row in rows:
                    w.writerow({"dataset": ds, "variant": v, **row})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end),
        "datasets": {k: {kk: vv for kk, vv in r.items() if kk not in ("blocked_rows", "trades")}
                     for k, r in results.items()},
        "scores": scores, "incident_2026_11_02": incident,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nblocked entries: {csv_path}")
    return 0


def _print(rep: Dict[str, Any]) -> None:
    for ds, r in rep["datasets"].items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]}  baseline days with 1/2/3/4+ stops: "
              f"{r['stops_per_day']}")
        print(f"  {'variant':<9} {'$end':>8} {'ret%':>7} {'dd%':>5} {'n':>3} {'win':>4} {'avgR':>6} "
              f"{'capd':>4} {'blk':>3} {'w/l/o':>7} {'blkR':>6} {'blk$':>7}")
        for v, s in r["variants"].items():
            b = s.get("blocked") or {}
            win = f"{s['win_rate']:.0%}" if s["win_rate"] is not None else "-"
            ar = f"{s['avg_r']:.2f}" if s["avg_r"] is not None else "-"
            wlo = f"{b['wins']}/{b['losses']}/{b['open']}" if b else ""
            print(f"  {v:<9} {s['final_equity']:8.2f} {s['return_pct']:7.1f} {s['max_dd_pct']:5.1f} "
                  f"{s['trades']:>3} {win:>4} {ar:>6} {s['cap_days']:>4} {b.get('entries', ''):>3} {wlo:>7} "
                  f"{b.get('r_net', ''):>6} {b.get('usd_est', ''):>7}")
    for g, sc in rep["scores"].items():
        print(f"\nscore [{g}] (beats/loses baseline of N, geo-mean final/baseline):")
        for v, x in sorted(sc.items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"  {v:<9} {x['beats']:>2}/{x['loses']:>2} of {x['of']:<2} {x['geo_mean_ratio']:.3f}")
    inc = rep.get("incident_2026_11_02") or {}
    if inc.get("sim"):
        print(f"\nthe 2026-11-02 silver incident, sim on the live metals journal (data to {inc['data_end']}):")
        for t in inc["sim"]["baseline"]:
            print(f"  baseline {t['side']:<5} {t['entry']} -> {t['exit']} {t['reason']:<14} ${t['net_usd']}")
        for v, rows in inc["blocked"].items():
            for row in rows:
                print(f"  {v:<8} blocked {row['side']} {row['time']} -> {row.get('reason')} "
                      f"{row.get('exit_time', '')} R {row.get('r_net', '-')} ${row.get('usd_est', '-')}")


if __name__ == "__main__":
    raise SystemExit(main())
