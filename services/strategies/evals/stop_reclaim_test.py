"""Stop-reclaim backtest, 2026-11-10: the live rule vs the live rule plus
`Params.reclaim_after_stops = 2`.

Operator, 2026-11-09/10: the oil thesis kept calling "buy the dip", got
stopped, and the next thesis just moved the buy lower: longs at 96.10,
95.60, 93.90 and 92.60 were each stopped (four straight -1R) while Brent
slid 7.6% on Iran-deal optimism. The rule he approved ("yes"): after
RECLAIM_AFTER_STOPS stop-outs on one side inside RECLAIM_WINDOW_H hours, no
new entry on that side until a CLOSED 1h candle closes back beyond the most
recent stopped trade's stop price. A reclaim clears the block. Mirror for
shorts. (`desk_tick_sim.reclaim_blocks`; live: services/strategies/
stop_reclaim.py.)

House rule: test before changing live. The pass bar, stated before the run:

  on BOTH thesis datasets (the live oil journal, the 30-thesis Opus replay)
  the reclaim variant's net $ is not worse than the baseline's over the
  whole window, AND not worse in at least 2 of the 3 equal sub-windows
  (each third is its own run from the starting equity).

The no-thesis controls (always_long / always_short / daily_sign, 30 and 50
days) are reported for context and do not decide. Equity $194 and the live
6% risk cap (`risk_per_trade_pct` 6 since 2026-11-05), the live Brent
params otherwise (`live_params.live_brent_params`, size_slippage on).

Per dataset and variant: trades, wins, total R, net $, max drawdown, the
exits, how many setups the rule skipped, and what those skipped entries
would have done (the stop cap eval's one-slot `walk_entry` chain: a
negative blocked R means the rule saved that much). Plus the 11-09/10
incident itself: the baseline's trades and the reclaim variant's blocks on
those two days.

    PATHIEL_STATE_DIR=<pathia>/.state python3 -m services.strategies.evals.stop_reclaim_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses --cache-dir <dir> --out-dir <dir>

Reads candles through the eval disk cache and the venue's public /info;
never touches the live account, the live config or the journal. Exit 0 on
a pass, 1 on a fail, 2 when a thesis dataset is missing.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, risk_capped_notional, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.live_params import live_brent_params
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, _iso, closed_only, equity_at, walk_entry
from services.strategies.weekend import weekend_closed

START = 194.0                 # the account on 2026-11-10
RISK_CAP_FRAC = 0.06          # risk_per_trade_pct 6 since 2026-11-05
RECLAIM_AFTER_STOPS = 2
RECLAIM_WINDOW_H = 24.0
CONTROLS = ("always_long", "always_short", "daily_sign")
THESIS_SETS = ("brent_journal", "brent_replay30")
INCIDENT_DAYS = ("2026-11-09", "2026-11-10")
THIRDS = 3


# ── the grid (pure) ────────────────────────────────────────────────────────

def variants(n: int = RECLAIM_AFTER_STOPS, window_h: float = RECLAIM_WINDOW_H) -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; "baseline" is the live rule as it stands."""
    return {"baseline": {}, "reclaim": {"reclaim_after_stops": n, "reclaim_window_h": window_h}}


def base_params(start: float = START, risk_cap_frac: float = RISK_CAP_FRAC) -> Params:
    # live_brent_params pins the 15% cap of 11-02; the desk runs 6% since 11-05
    return dataclasses.replace(live_brent_params(size_slippage=True, starting_equity=start),
                               risk_cap_frac=risk_cap_frac)


# ── summaries (pure) ───────────────────────────────────────────────────────

def summarize(run: Run, start_eq: float) -> Dict[str, Any]:
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    exits: Dict[str, int] = {}
    for t in run.trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    return {
        "trades": len(run.trades),
        "wins": sum(1 for t in run.trades if t.net_pnl_usd > 0),
        "total_r": round(sum(rs), 2),
        "net_usd": round(run.final_equity - start_eq, 2),
        "final_equity": round(run.final_equity, 2),
        "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "liquidated": run.liquidated,
        "exits": dict(sorted(exits.items())),
        "blocked_ticks": len(run.reclaim_blocked),
        "refused_reclaim": int(run.refused.get("reclaim", 0)),
    }


def thirds(start_ms: int, end_ms: int, n: int = THIRDS) -> List[Tuple[int, int]]:
    """`n` equal, contiguous sub-windows of [start_ms, end_ms)."""
    step = (end_ms - start_ms) // n
    out = [(start_ms + i * step, start_ms + (i + 1) * step) for i in range(n)]
    out[-1] = (out[-1][0], end_ms)
    return out


def walk_weekend(side: str, entry_t: int, entry_px: float, stop: float, target: float,
                 c15: Sequence[Candle], c1h_by_close: Dict[int, Candle], direction_at: Callable[[int], Any],
                 params: Params) -> Tuple[str, float, int]:
    """`walk_entry` (the stop cap eval's per-bar walk) plus the live weekend
    flat: with `weekend_off == "flatten"` a trade still open at the first
    bar inside the oil weekend is closed at that bar's open ("weekend"),
    the way the sim does. Without it the 11-04 long that the sim closed
    +3.4R at the weekend would be walked on into the next week."""
    if params.weekend_off != "flatten":
        return walk_entry(side, entry_t, entry_px, stop, target, c15, c1h_by_close, direction_at, params)
    cut = next((b for b in c15 if b.t > entry_t and weekend_closed(b.t)), None)
    bars = c15 if cut is None else [b for b in c15 if b.t < cut.t]
    reason, px, t_exit = walk_entry(side, entry_t, entry_px, stop, target, bars, c1h_by_close, direction_at, params)
    if reason == "end" and cut is not None:
        return "weekend", cut.o, cut.t
    return reason, px, t_exit


def shadow_chain(run: Run, c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: Callable[[int], Any],
                 params: Params) -> List[Dict[str, Any]]:
    """What the entries the rule skipped would have done, one slot at a
    time (the stop cap eval's method): the first skipped market setup is
    walked on with its own stop and target, then the first skipped setup
    after that exit, and so on. `usd_est` sizes it at the run's equity at
    that tick. Negative r_net = the rule saved that."""
    by_close = {c.t + HOUR_MS: c for c in c1h}
    rows: List[Dict[str, Any]] = []
    busy_until = -1
    for b in run.reclaim_blocked:
        if b["time"] <= busy_until:
            continue
        side, entry, stop, target = b["side"], b["entry_px"], b["stop_px"], b["target_px"]
        base = {"kind": b["kind"], "side": side, "time": _iso(b["time"]), "entry_px": round(entry, 4),
                "stop_px": round(stop, 4), "target_px": round(target, 4), "reclaim_px": round(b["reclaim_px"], 4),
                "stops_in_window": b["stops_in_window"]}
        if not b["is_market"]:
            rows.append({**base, "reason": "rest_not_walked"})
            continue
        reason, px, t_exit = walk_weekend(side, b["time"], entry, stop, target, c15, by_close, direction_at, params)
        busy_until = t_exit
        sign = 1.0 if side == "long" else -1.0
        risk = abs(entry - stop)
        exit_fee = params.maker_fee if (reason == "target" and params.target_mode != "market") else params.taker_fee
        fee_px = params.taker_fee * entry + exit_fee * px
        r_net = (sign * (px - entry) - fee_px) / risk if risk > 0 else 0.0
        size = risk_capped_notional(equity_at(run, b["time"]), params, None, entry, stop) / entry if entry > 0 else 0.0
        rows.append({**base, "reason": reason, "exit_px": round(px, 4), "exit_time": _iso(t_exit),
                     "r_net": round(r_net, 3), "usd_est": round(size * (sign * (px - entry) - fee_px), 2),
                     "outcome": "open" if reason == "end" else ("win" if r_net > 0 else "loss")})
    return rows


def blocked_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    walked = [r for r in rows if "r_net" in r]
    return {"entries": len(rows), "wins": sum(1 for r in walked if r["outcome"] == "win"),
            "losses": sum(1 for r in walked if r["outcome"] == "loss"),
            "open": sum(1 for r in walked if r["outcome"] == "open"),
            "r_net": round(sum(r["r_net"] for r in walked), 2),
            "usd_est": round(sum(r["usd_est"] for r in walked), 2)}


def passes(ds: Dict[str, Any]) -> Dict[str, Any]:
    """The bar on one dataset's results (`run_dataset`'s output): the
    reclaim variant's whole-window net $ is not below the baseline's, and
    not below it in at least 2 of the 3 thirds. Pure."""
    b, r = ds["variants"]["baseline"], ds["variants"]["reclaim"]
    full_ok = r["net_usd"] >= b["net_usd"] - 1e-9
    third_ok = [tr["reclaim"]["net_usd"] >= tr["baseline"]["net_usd"] - 1e-9 for tr in ds["thirds"]]
    return {"full_window": full_ok, "thirds_not_worse": sum(third_ok), "thirds": third_ok,
            "pass": full_ok and sum(third_ok) >= 2, "delta_usd": round(r["net_usd"] - b["net_usd"], 2)}


def verdict(results: Dict[str, Dict[str, Any]], thesis_sets: Sequence[str] = THESIS_SETS) -> Dict[str, Any]:
    """PASS only when every thesis dataset present clears `passes`. Pure."""
    per = {k: passes(results[k]) for k in thesis_sets if k in results}
    return {"per_dataset": per, "pass": bool(per) and all(p["pass"] for p in per.values()),
            "datasets_judged": list(per)}


def incident(ds: Dict[str, Any], days: Sequence[str] = INCIDENT_DAYS) -> Dict[str, Any]:
    """The 11-09/10 sequence: the baseline's and the reclaim variant's trades
    on those days, and the setups the rule skipped."""
    def on_days(s: Optional[str]) -> bool:
        return bool(s) and any(s.startswith(d) for d in days)
    return {"trades": {v: [t for t in rows if on_days(t["entry"]) or on_days(t["exit"])]
                       for v, rows in ds["trades"].items()},
            "blocked": [r for r in ds["blocked_rows"] if on_days(r["time"])]}


# ── runs ───────────────────────────────────────────────────────────────────

def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def _trade_rows(run: Run) -> List[Dict[str, Any]]:
    return [{"side": t.side, "entry": _iso(t.entry_time), "exit": _iso(t.exit_time), "entry_px": round(t.entry_px, 3),
             "stop_px": round(t.stop_px, 3), "exit_px": round(t.exit_px, 3) if t.exit_px is not None else None,
             "reason": t.exit_reason, "net_usd": round(t.net_pnl_usd, 2),
             "r": round(t.r_multiple, 2) if t.r_multiple is not None else None} for t in run.trades]


def run_dataset(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params,
                start_ms: int, end_ms: int, funding=None, named: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """Both variants on the whole window and on each third."""
    named = named or variants()
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "days": round((end_ms - start_ms) / DAY_MS, 1),
                           "variants": {}, "thirds": [], "trades": {}, "blocked_rows": []}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, base.starting_equity)
        out["trades"][v] = _trade_rows(run)
        if v != "baseline":
            rows = shadow_chain(run, w15, w1h, direction_at, p)
            s["blocked"] = blocked_summary(rows)
            out["blocked_rows"] = rows
        out["variants"][v] = s
    for a, b in thirds(start_ms, end_ms):
        t15, t1h = _window(c15, c1h, a, b)
        row: Dict[str, Any] = {"window": [_iso(a), _iso(b)]}
        for v, ov in named.items():
            run = simulate(t15, t1h, direction_at, dataclasses.replace(base, **ov), funding)
            row[v] = summarize(run, base.starting_equity)
        out["thirds"].append(row)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import direction_series, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/stop-reclaim/cache", help="fresh Brent candles")
    ap.add_argument("--out-dir", default="/tmp/stop-reclaim")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--risk-cap-frac", type=float, default=RISK_CAP_FRAC)
    ap.add_argument("--after-stops", type=int, default=RECLAIM_AFTER_STOPS)
    ap.add_argument("--window-h", type=float, default=RECLAIM_WINDOW_H)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    named = variants(a.after_stops, a.window_h)
    results: Dict[str, Dict[str, Any]] = {}

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} stop-reclaim: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    got = load_coin_candles("xyz:BRENTOIL", a.cache_dir)
    asof = int(os.path.getmtime(D._cache_path(a.cache_dir, "xyz:BRENTOIL", "candles_15m_4820")) * 1000)
    b15, b1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = base_params(a.start_equity, a.risk_cap_frac)

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

    ver = verdict(results)
    inc = incident(results["brent_journal"]) if "brent_journal" in results else {}
    csv_path = os.path.join(a.out_dir, "blocked_entries.csv")
    fields = ["dataset", "kind", "side", "time", "entry_px", "stop_px", "target_px", "reclaim_px", "stops_in_window",
              "reason", "exit_px", "exit_time", "r_net", "usd_est", "outcome"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ds, r in results.items():
            for row in r["blocked_rows"]:
                w.writerow({"dataset": ds, **row})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "start_equity": a.start_equity, "risk_cap_frac": a.risk_cap_frac,
        "rule": {"reclaim_after_stops": a.after_stops, "reclaim_window_h": a.window_h},
        "datasets": {k: {kk: vv for kk, vv in r.items() if kk not in ("blocked_rows", "trades")}
                     for k, r in results.items()},
        "verdict": ver, "incident_2026_11_09": inc,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nblocked entries: {csv_path}")
    if not ver["datasets_judged"]:
        return 2
    return 0 if ver["pass"] else 1


def _print(rep: Dict[str, Any]) -> None:
    for ds, r in rep["datasets"].items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]} ({r['days']}d)")
        print(f"  {'variant':<9} {'n':>3} {'wins':>4} {'R':>7} {'net$':>8} {'dd%':>5} {'blk':>3} {'blkR':>6} {'blk$':>7}  thirds net$")
        for v, s in r["variants"].items():
            b = s.get("blocked") or {}
            th = " / ".join(f"{t[v]['net_usd']:+.2f}" for t in r["thirds"])
            print(f"  {v:<9} {s['trades']:>3} {s['wins']:>4} {s['total_r']:>7.2f} {s['net_usd']:>8.2f} "
                  f"{s['max_dd_pct']:>5.1f} {s['blocked_ticks']:>3} {b.get('r_net', ''):>6} {b.get('usd_est', ''):>7}  {th}")
    v = rep["verdict"]
    print(f"\nverdict: {'PASS' if v['pass'] else 'FAIL'} on {v['datasets_judged']}")
    for ds, p in v["per_dataset"].items():
        print(f"  {ds}: full window {'ok' if p['full_window'] else 'worse'} ({p['delta_usd']:+.2f}$), "
              f"thirds not worse {p['thirds_not_worse']}/3 -> {'pass' if p['pass'] else 'fail'}")
    inc = rep.get("incident_2026_11_09") or {}
    if inc.get("trades"):
        print("\nthe 2026-11-09/10 sequence on the live journal:")
        for v, rows in inc["trades"].items():
            for t in rows:
                print(f"  {v:<8} {t['side']:<5} {t['entry']} @{t['entry_px']} stop {t['stop_px']} -> {t['exit']} "
                      f"{t['reason']:<12} {t['r'] if t['r'] is not None else '-':>6} ${t['net_usd']}")
        for row in inc["blocked"]:
            print(f"  blocked  {row['side']:<5} {row['time']} @{row['entry_px']} until 1h close "
                  f"{'>' if row['side'] == 'long' else '<'} {row['reclaim_px']} -> {row.get('reason')} "
                  f"{row.get('exit_time', '')} R {row.get('r_net', '-')} ${row.get('usd_est', '-')}")


if __name__ == "__main__":
    raise SystemExit(main())
