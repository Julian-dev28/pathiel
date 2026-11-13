"""5-day trend veto backtest, 2026-11-13: the live rule vs the live rule plus
`Params.trend_gate_span` (no new long while the 120h close-to-close return
is negative; no new short while it is positive).

Where it comes from: the strategy tournament (STRATEGY_TOURNAMENT_2026-11-10.md)
ran 15 mechanical rules on 198 days of 1h Brent and nothing passed; the
best was B_ret120, the dip-buy gated by the sign of the 120h return
(+$6.30 over 67 trades, no neighbour plateau). The tournament had no
thesis, and the desk's October profit came from the thesis, so the test
that matters is the veto on top of the REAL journaled theses. Operator:
"yes".

House rule: test before changing live. The bar, stated before the run, on
the live oil journal (`brent_journal`), the 120h span:

  1. net $ beats the baseline (the live rule as it stands);
  2. max drawdown no worse;
  3. the neighbouring spans (96h and 144h) also beat the baseline, so it
     is a plateau, not a lone spike;
  4. it does not lose on the last third: the veto's last-third net $ is
     not negative and not below the baseline's.

Everything else (the 30-thesis Opus replay, the no-thesis controls, WTI
with the journal's sides) is reported as context and does not decide.
Equity $182.09 and the live 6% risk cap, the same `base_params` as the
stop-reclaim eval (`live_brent_params`, size_slippage on), so the
baseline is that eval's +$83.23 on the 11-10 candle snapshot.

Per dataset and variant: trades, wins, total R, net $, max drawdown, the
exits, thirds, months, how many setups the veto skipped, and what those
skipped entries would have done (the stop cap eval's one-slot `walk_entry`
chain through `stop_reclaim_test.walk_weekend`: blocked winners vs blocked
losers; a negative blocked R means the veto saved that much).

    PATHIEL_STATE_DIR=<pathia>/.state python3 -m services.strategies.evals.trend_gate_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses --cache-dir <dir> --out-dir <dir>

Reads candles through the eval disk cache and the venue's public /info;
never touches the live account, the live config or the journal. Exit 0 on
a pass, 1 on a fail, 2 when the journal is missing.
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
from services.strategies.backtest.desk_tick_sim import Direction, Params, Run, risk_capped_notional, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, _iso, closed_only, equity_at
from services.strategies.evals.stop_reclaim_test import base_params as _reclaim_base_params
from services.strategies.evals.stop_reclaim_test import thirds, walk_weekend

START = 182.09                # the tournament's start equity
RISK_CAP_FRAC = 0.06          # risk_per_trade_pct 6 since 2026-11-05
PRIMARY_SPAN = 120
SPANS = (72, 96, 120, 144, 168)
NEIGHBOURS = (96, 144)
CONTROLS = ("always_long", "always_short", "daily_sign")
BRENT = "xyz:BRENTOIL"
WTI = "xyz:CL"
PRIMARY_DATASET = "brent_journal"
NOVEMBER_DAYS = ("2026-11-02", "2026-11-10")   # the live desk's slide: 11-02 .. 11-10 inclusive


# ── the grid (pure) ────────────────────────────────────────────────────────

def variant_name(span: int) -> str:
    return f"trend_{span}"


def variants(spans: Sequence[int] = SPANS) -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; "baseline" is the live rule as it stands."""
    out: Dict[str, Dict[str, Any]] = {"baseline": {}}
    for s in spans:
        out[variant_name(s)] = {"trend_gate_span": int(s)}
    return out


def base_params(start: float = START, risk_cap_frac: float = RISK_CAP_FRAC) -> Params:
    """The stop-reclaim eval's baseline at the tournament's equity: the live
    Brent rule, the 6% cap, size slippage on, every veto off."""
    p = _reclaim_base_params(start, risk_cap_frac)
    assert p.trend_gate_span is None and p.reclaim_after_stops is None
    return p


def side_only(direction_at: Callable[[int], Any]) -> Callable[[int], Any]:
    """The journal's SIDE on another instrument: the thesis's Brent-priced
    entry ceiling and invalidation mean nothing on WTI, so only the side
    and the call id carry over (the structural stop and target still rule)."""
    def at(t: int) -> Optional[Direction]:
        d = direction_at(t)
        if d is None:
            return None
        return Direction(side=d.side, regime_id=d.regime_id)
    return at


# ── summaries (pure) ───────────────────────────────────────────────────────

def summarize(run: Run, start_eq: float) -> Dict[str, Any]:
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    exits: Dict[str, int] = {}
    for t in run.trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    return {
        "trades": len(run.trades),
        "wins": sum(1 for t in run.trades if t.net_pnl_usd > 0),
        "longs": sum(1 for t in run.trades if t.side == "long"),
        "total_r": round(sum(rs), 2),
        "net_usd": round(run.final_equity - start_eq, 2),
        "final_equity": round(run.final_equity, 2),
        "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "liquidated": run.liquidated,
        "exits": dict(sorted(exits.items())),
        "blocked_ticks": len(run.trend_blocked),
        "refused_trend": int(run.refused.get("trend", 0)),
        "months": months(run),
    }


def months(run: Run) -> Dict[str, Dict[str, Any]]:
    """Net $ (the sim's compounding dollars), trades and wins by the month
    the trade was ENTERED (UTC)."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in run.trades:
        key = (_iso(t.entry_time) or "")[:7]
        row = out.setdefault(key, {"trades": 0, "wins": 0, "net_usd": 0.0})
        row["trades"] += 1
        row["wins"] += 1 if t.net_pnl_usd > 0 else 0
        row["net_usd"] = round(row["net_usd"] + t.net_pnl_usd, 2)
    return dict(sorted(out.items()))


def shadow_chain(run: Run, c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: Callable[[int], Any],
                 params: Params) -> List[Dict[str, Any]]:
    """What the entries the veto skipped would have done, one slot at a
    time (the stop cap eval's method, the reclaim eval's weekend flat):
    the first skipped market setup is walked on with its own stop and
    target, then the first skipped setup after that exit, and so on.
    `usd_est` sizes it at the run's equity at that tick. Negative r_net =
    the veto saved that."""
    by_close = {c.t + HOUR_MS: c for c in c1h}
    rows: List[Dict[str, Any]] = []
    busy_until = -1
    for b in run.trend_blocked:
        if b["time"] <= busy_until:
            continue
        side, entry, stop, target = b["side"], b["entry_px"], b["stop_px"], b["target_px"]
        base = {"kind": b["kind"], "side": side, "time": _iso(b["time"]), "entry_px": round(entry, 4),
                "stop_px": round(stop, 4), "target_px": round(target, 4),
                "ret_pct": round(b["ret"] * 100, 3) if b.get("ret") is not None else None, "span_h": b["span_h"]}
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


def passes(ds: Dict[str, Any], span: int = PRIMARY_SPAN, neighbours: Sequence[int] = NEIGHBOURS) -> Dict[str, Any]:
    """The bar on one dataset's results (`run_dataset`'s output). Pure."""
    v = ds["variants"]
    b, p = v["baseline"], v[variant_name(span)]
    net_ok = p["net_usd"] > b["net_usd"] + 1e-9
    dd_ok = p["max_dd_pct"] <= b["max_dd_pct"] + 1e-9
    nb = {variant_name(s): v[variant_name(s)]["net_usd"] > b["net_usd"] + 1e-9 for s in neighbours
          if variant_name(s) in v}
    plateau_ok = bool(nb) and all(nb.values())
    last = ds["thirds"][-1]
    last_p, last_b = last[variant_name(span)]["net_usd"], last["baseline"]["net_usd"]
    last_ok = last_p >= -1e-9 and last_p >= last_b - 1e-9
    return {"net": net_ok, "max_dd": dd_ok, "plateau": plateau_ok, "neighbours": nb, "last_third": last_ok,
            "last_third_usd": {"baseline": last_b, variant_name(span): last_p},
            "delta_usd": round(p["net_usd"] - b["net_usd"], 2),
            "pass": net_ok and dd_ok and plateau_ok and last_ok}


def verdict(results: Dict[str, Dict[str, Any]], primary: str = PRIMARY_DATASET,
            span: int = PRIMARY_SPAN) -> Dict[str, Any]:
    """PASS only when the primary dataset (the live journal) clears `passes`;
    no journal, no pass. Pure."""
    if primary not in results:
        return {"dataset": primary, "pass": False, "judged": False, "reason": f"{primary} missing"}
    return {"dataset": primary, "judged": True, "span_h": span, **passes(results[primary], span)}


def november(ds: Dict[str, Any], span: int = PRIMARY_SPAN,
             days: Tuple[str, str] = NOVEMBER_DAYS) -> Dict[str, Any]:
    """The live desk's 11-02..11-10 slide on the journal: the baseline's
    trades entered in those days, and what the veto did with them."""
    lo, hi = days

    def inside(s: Optional[str]) -> bool:
        return bool(s) and lo <= s[:10] <= hi
    name = variant_name(span)
    base = [t for t in ds["trades"]["baseline"] if inside(t["entry"])]
    kept = [t for t in ds["trades"].get(name, []) if inside(t["entry"])]
    blocked = [r for r in ds["blocked_rows"].get(name, []) if inside(r["time"])]
    return {"days": list(days), "baseline": base, "veto": kept, "blocked": blocked,
            "baseline_longs": sum(1 for t in base if t["side"] == "long"),
            "veto_longs": sum(1 for t in kept if t["side"] == "long"),
            "baseline_net_usd": round(sum(t["net_usd"] for t in base), 2),
            "veto_net_usd": round(sum(t["net_usd"] for t in kept), 2)}


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
    """Every variant on the whole window and on each third."""
    named = named or variants()
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "days": round((end_ms - start_ms) / DAY_MS, 1),
                           "variants": {}, "thirds": [], "trades": {}, "blocked_rows": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, base.starting_equity)
        out["trades"][v] = _trade_rows(run)
        if v != "baseline":
            rows = shadow_chain(run, w15, w1h, direction_at, p)
            s["blocked"] = blocked_summary(rows)
            out["blocked_rows"][v] = rows
        out["variants"][v] = s
    for a, b in thirds(start_ms, end_ms):
        t15, t1h = _window(c15, c1h, a, b)
        row: Dict[str, Any] = {"window": [_iso(a), _iso(b)]}
        for v, ov in named.items():
            run = simulate(t15, t1h, direction_at, dataclasses.replace(base, **ov), funding)
            row[v] = summarize(run, base.starting_equity)
            row[v].pop("months", None)
        out["thirds"].append(row)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import direction_series, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/trend-gate/cache", help="candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/trend-gate")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--risk-cap-frac", type=float, default=RISK_CAP_FRAC)
    ap.add_argument("--spans", type=int, nargs="+", default=list(SPANS))
    ap.add_argument("--no-wti", action="store_true", help="skip the WTI cross-check")
    ap.add_argument("--no-controls", action="store_true", help="skip the no-thesis controls")
    ap.add_argument("--no-lev-fit", dest="lev_fit", action="store_false", default=True,
                    help="drop the live 20/15 leverage fit from the baseline (the pre-2026-11-13 eval baseline), for comparison")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    spans = sorted(set(a.spans) | {PRIMARY_SPAN} | set(NEIGHBOURS))
    named = variants(spans)
    results: Dict[str, Dict[str, Any]] = {}

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} trend-gate: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        end = c15[-1].t + FIFTEEN_MS
        fund = D.fetch_funding_cached(coin, c15[0].t, end, a.cache_dir)
        return c15, c1h, end, fund

    b15, b1h, b_end, b_fund = load(BRENT)
    bp = base_params(a.start_equity, a.risk_cap_frac)       # carries the live 20/15 fit (live_brent_params, 2026-11-13)
    if not a.lev_fit:
        bp = dataclasses.replace(bp, entry_fit_leverage=None)
    log(f"Brent candles to {_iso(b_end)}; spans {spans}; leverage fit {bp.entry_fit_leverage}")

    thesis_sets = []
    for label, tdir, end_rule in (("brent_journal", a.oil_theses_dir, "data"),
                                  ("brent_replay30", a.oil_replay_dir, "last+1d")):
        theses = D.load_journaled_theses(tdir) if tdir else []
        if not theses:
            log(f"{label}: no theses at {tdir!r}, skipped")
            continue
        start = min(th.created_at_ms for th in theses)
        end = b_end if end_rule == "data" else min(b_end, max(th.created_at_ms for th in theses) + DAY_MS)
        direction = journaled_direction(theses)
        results[label] = run_dataset(b15, b1h, direction, bp, start, end, b_fund, named)
        results[label]["n_theses"] = len(theses)
        thesis_sets.append((label, theses, start, end, direction))
        log(f"{label}: {len(theses)} theses done ({time.time() - t0:.0f}s)")
    if not a.no_controls:
        for days in (30, 50):
            start = b_end - days * DAY_MS
            for s in CONTROLS:
                label = f"brent_{s}_{days}d"
                results[label] = run_dataset(b15, b1h, direction_series(s, b1h), bp, start, b_end, b_fund, named)
                log(f"{label} done ({time.time() - t0:.0f}s)")
    if not a.no_wti:
        w15, w1h, w_end, w_fund = load(WTI)
        for label, theses, start, end, direction in thesis_sets:
            wl = label.replace("brent_", "wti_")
            results[wl] = run_dataset(w15, w1h, side_only(direction), bp, start, min(end, w_end), w_fund, named)
            results[wl]["n_theses"] = len(theses)
            log(f"{wl}: journal sides on WTI done ({time.time() - t0:.0f}s)")
        if not a.no_controls:
            for days in (30, 50):
                start = w_end - days * DAY_MS
                for s in CONTROLS:
                    label = f"wti_{s}_{days}d"
                    results[label] = run_dataset(w15, w1h, direction_series(s, w1h), bp, start, w_end, w_fund, named)
                    log(f"{label} done ({time.time() - t0:.0f}s)")

    ver = verdict(results)
    nov = november(results[PRIMARY_DATASET]) if PRIMARY_DATASET in results else {}
    csv_path = os.path.join(a.out_dir, "blocked_entries.csv")
    fields = ["dataset", "variant", "kind", "side", "time", "entry_px", "stop_px", "target_px", "ret_pct", "span_h",
              "reason", "exit_px", "exit_time", "r_net", "usd_est", "outcome"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ds, r in results.items():
            for v, rows in r["blocked_rows"].items():
                for row in rows:
                    w.writerow({"dataset": ds, "variant": v, **row})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "start_equity": a.start_equity, "risk_cap_frac": a.risk_cap_frac, "entry_fit_leverage": bp.entry_fit_leverage,
        "spans": spans, "primary_span": PRIMARY_SPAN, "neighbours": list(NEIGHBOURS),
        "datasets": {k: {kk: vv for kk, vv in r.items() if kk not in ("blocked_rows", "trades")}
                     for k, r in results.items()},
        "verdict": ver, "november_2026": nov,
        "journal_trades": {v: rows for v, rows in results[PRIMARY_DATASET]["trades"].items()}
        if PRIMARY_DATASET in results else {},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\nblocked entries: {csv_path}")
    if not ver.get("judged"):
        return 2
    return 0 if ver["pass"] else 1


def _print(rep: Dict[str, Any]) -> None:
    for ds, r in rep["datasets"].items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]} ({r['days']}d)")
        print(f"  {'variant':<10} {'n':>3} {'wins':>4} {'R':>7} {'net$':>8} {'dd%':>5} {'blk':>3} "
              f"{'blkW':>4} {'blkL':>4} {'blk$':>8}  thirds net$")
        for v, s in r["variants"].items():
            b = s.get("blocked") or {}
            th = " / ".join(f"{t[v]['net_usd']:+.2f}" for t in r["thirds"])
            print(f"  {v:<10} {s['trades']:>3} {s['wins']:>4} {s['total_r']:>7.2f} {s['net_usd']:>8.2f} "
                  f"{s['max_dd_pct']:>5.1f} {s['blocked_ticks']:>3} {b.get('wins', ''):>4} {b.get('losses', ''):>4} "
                  f"{b.get('usd_est', ''):>8}  {th}")
    v = rep["verdict"]
    print(f"\nverdict on {v.get('dataset')}: {'PASS' if v.get('pass') else 'FAIL'}")
    if v.get("judged"):
        print(f"  net beats baseline: {v['net']} ({v['delta_usd']:+.2f}$); max DD no worse: {v['max_dd']}; "
              f"neighbours {v['neighbours']} -> plateau {v['plateau']}; "
              f"last third {v['last_third_usd']} -> {v['last_third']}")
    nov = rep.get("november_2026") or {}
    if nov:
        print(f"\nthe {nov['days'][0]}..{nov['days'][1]} slide on the live journal: baseline {len(nov['baseline'])} trades "
              f"({nov['baseline_longs']} longs) {nov['baseline_net_usd']:+.2f}$; veto {len(nov['veto'])} trades "
              f"({nov['veto_longs']} longs) {nov['veto_net_usd']:+.2f}$; {len(nov['blocked'])} blocked entries")
        for t in nov["baseline"]:
            print(f"  baseline {t['side']:<5} {t['entry']} @{t['entry_px']} -> {t['exit']} {t['reason']:<12} "
                  f"{t['r'] if t['r'] is not None else '-':>6} ${t['net_usd']}")
        for row in nov["blocked"]:
            print(f"  blocked  {row['side']:<5} {row['time']} @{row['entry_px']} ret {row['ret_pct']}% -> "
                  f"{row.get('reason')} {row.get('exit_time', '')} R {row.get('r_net', '-')} ${row.get('usd_est', '-')}")


if __name__ == "__main__":
    raise SystemExit(main())
