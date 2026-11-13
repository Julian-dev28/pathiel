"""Winner-traits test, 2026-11-13: the three entry-time traits the trade
attribution (TRADE_ATTRIBUTION_2026-11-13.md, `trade_attribution.py`) found
on the real winners, turned into mechanical rules and scored against the
live rule on the journaled theses, the same way the trend veto was.

Operator: "lets use the data to create a strat from these trades, maybe
deploy like 10% into the strat that has the highest roi".

The candidates (each a `desk_tick_sim.Params` override on top of the live
Brent rule; the primary value is the attribution's own cut, the two
neighbours are the plateau check):

  zone    market entry only in the bottom third of the 24h range (a long
          with (mid - low) / (high - low) <= 0.33; a short mirrored).
          `entry_zone_frac` 0.33, neighbours 0.25 / 0.50. Real fills: 3 of
          5 won, +$80.63 vs mid-range and through-the-edge entries.
  delay   no entry in the first two hours of a thesis. `entry_delay_min`
          120, neighbours 90 / 180. Real fills: entries under a thesis
          older than 2h won 3 of 4, +$70.68; inside 2h, 4 of 10, -$35.70.
  edge    a resting limit AT the 24h edge instead of taking the mid
          (`entry_mode="edge"`, within `atr_rest_frac` 0.25 ATR of it;
          neighbours 0.15 / 0.50). Real fills: the five resting entries
          (the desk's rule until 2026-10-29) won 3, +$60.85; the eighteen
          market entries won 6, -$98.69.

The bar, stated before the run, on the live oil journal (`brent_journal`)
for each candidate's primary value, all five:

  1. net $ beats the baseline (the live rule as it stands, refreshed to
     today's candles);
  2. max drawdown no worse;
  3. BOTH neighbours also beat the baseline (a plateau, not a spike);
  4. the last third's net $ is not negative and not below the baseline's;
  5. WTI (xyz:CL, the journal's SIDES applied as in trend_gate_test) is not
     negative.

Plus, for the candidate the no-thesis engine can express (zone: the
tournament's A_long with the zone gate), the strategy tournament's own
bar on 198 days of 1h Brent with WTI (`strategy_tournament.passes`),
reported alongside; delay needs a call start and edge a resting-limit
fill, which that engine does not model, so their no-thesis cross-check is
the sim's always_long / always_short / daily_sign controls (context).

Also: each candidate replayed at 10% of today's equity (the sleeve the
operator asked about), with its net $ per 30 days; and the review's FLUKE
rule on the edge itself (`one_day_flips`): the candidate's R delta over
the baseline by exit day, and whether dropping the biggest day leaves
nothing. A pass that one day carries is reported as a pass with the flag
up, never rounded up to an edge.

    PATHIEL_STATE_DIR=<pathia>/.state python3 -m services.strategies.evals.winner_traits_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \\
        --cache-dir <fresh dir> --tournament-cache-dir <the tournament's cache> --out-dir <dir> --equity 213.80

Reads candles through the eval disk cache and the venue's public /info;
never touches the live account, the live config or the journal. Exit 0
when at least one candidate passes, 1 when none does, 2 when the journal
is missing.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run, simulate, zone_blocks
from services.strategies.evals import strategy_tournament as T
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, _iso, closed_only
from services.strategies.evals.stop_reclaim_test import thirds
from services.strategies.evals.trend_gate_test import base_params, side_only

START = 182.09                # the tournament's and trend-gate test's start equity, so the baselines compare
RISK_CAP_FRAC = 0.06
EQUITY_NOW = 213.80           # account_overview, 2026-11-13 (read only); --equity overrides
SLEEVE = 0.10
BRENT = "xyz:BRENTOIL"
WTI = "xyz:CL"
PRIMARY_DATASET = "brent_journal"
WTI_DATASET = "wti_journal"
CONTROLS = ("always_long", "always_short", "daily_sign")

# candidate -> (primary override, {neighbour name: override})
CANDIDATES: Dict[str, Dict[str, Any]] = {
    "zone": {"primary": {"entry_zone_frac": 0.33},
             "neighbours": {"zone_25": {"entry_zone_frac": 0.25}, "zone_50": {"entry_zone_frac": 0.50}},
             "trait": "range=bottom third (at edge)"},
    "delay": {"primary": {"entry_delay_min": 120.0},
              "neighbours": {"delay_90": {"entry_delay_min": 90.0}, "delay_180": {"entry_delay_min": 180.0}},
              "trait": "rewrite=thesis older than 2h"},
    "edge": {"primary": {"entry_mode": "edge", "atr_rest_frac": 0.25},
             "neighbours": {"edge_rest15": {"entry_mode": "edge", "atr_rest_frac": 0.15},
                            "edge_rest50": {"entry_mode": "edge", "atr_rest_frac": 0.50}},
             "trait": "entry=resting"},
}


# ── the grid (pure) ────────────────────────────────────────────────────────

def variants(cands: Dict[str, Dict[str, Any]] = CANDIDATES) -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; "baseline" is the live rule as it stands."""
    out: Dict[str, Dict[str, Any]] = {"baseline": {}}
    for name, c in cands.items():
        out[name] = dict(c["primary"])
        for nb, ov in c["neighbours"].items():
            out[nb] = dict(ov)
    return out


# ── summaries (pure) ───────────────────────────────────────────────────────

def summarize(run: Run, start_eq: float) -> Dict[str, Any]:
    rs = [t.r_multiple for t in run.trades if t.r_multiple is not None]
    exits: Dict[str, int] = {}
    for t in run.trades:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    return {
        "trades": len(run.trades), "wins": sum(1 for t in run.trades if t.net_pnl_usd > 0),
        "longs": sum(1 for t in run.trades if t.side == "long"),
        "total_r": round(sum(rs), 2), "net_usd": round(run.final_equity - start_eq, 2),
        "final_equity": round(run.final_equity, 2), "max_dd_pct": round(run.max_drawdown_pct * 100, 1),
        "liquidated": run.liquidated, "exits": dict(sorted(exits.items())),
        "rested": run.rested, "filled": run.filled, "cancelled_resting": run.cancelled_resting,
        "refused": {k: int(v) for k, v in sorted(run.refused.items()) if v},
    }


def _trade_rows(run: Run) -> List[Dict[str, Any]]:
    return [{"side": t.side, "entry": _iso(t.entry_time), "exit": _iso(t.exit_time), "entry_px": round(t.entry_px, 3),
             "stop_px": round(t.stop_px, 3), "exit_px": round(t.exit_px, 3) if t.exit_px is not None else None,
             "reason": t.exit_reason, "net_usd": round(t.net_pnl_usd, 2),
             "r": round(t.r_multiple, 2) if t.r_multiple is not None else None} for t in run.trades]


def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def run_dataset(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params,
                start_ms: int, end_ms: int, funding=None, named: Optional[Dict[str, Dict[str, Any]]] = None
                ) -> Dict[str, Any]:
    """Every variant on the whole window and on each third (each third its
    own run from the starting equity)."""
    named = named or variants()
    w15, w1h = _window(c15, c1h, start_ms, end_ms)
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "days": round((end_ms - start_ms) / DAY_MS, 1),
                           "variants": {}, "thirds": [], "trades": {}}
    for v, ov in named.items():
        run = simulate(w15, w1h, direction_at, dataclasses.replace(base, **ov), funding)
        out["variants"][v] = summarize(run, base.starting_equity)
        out["trades"][v] = _trade_rows(run)
    for a, b in thirds(start_ms, end_ms):
        t15, t1h = _window(c15, c1h, a, b)
        row: Dict[str, Any] = {"window": [_iso(a), _iso(b)]}
        for v, ov in named.items():
            run = simulate(t15, t1h, direction_at, dataclasses.replace(base, **ov), funding)
            row[v] = summarize(run, base.starting_equity)
        out["thirds"].append(row)
    return out


def passes(ds: Dict[str, Any], cand: str, wti: Optional[Dict[str, Any]] = None,
           cands: Dict[str, Dict[str, Any]] = CANDIDATES) -> Dict[str, Any]:
    """The five-leg bar on one candidate (module doc). `ds` is the journal's
    `run_dataset` output, `wti` the WTI one (None: the WTI leg fails)."""
    v = ds["variants"]
    b, p = v["baseline"], v[cand]
    net_ok = p["net_usd"] > b["net_usd"] + 1e-9
    dd_ok = p["max_dd_pct"] <= b["max_dd_pct"] + 1e-9
    nb = {n: v[n]["net_usd"] > b["net_usd"] + 1e-9 for n in cands[cand]["neighbours"] if n in v}
    plateau_ok = bool(nb) and all(nb.values())
    last = ds["thirds"][-1]
    last_p, last_b = last[cand]["net_usd"], last["baseline"]["net_usd"]
    last_ok = last_p >= -1e-9 and last_p >= last_b - 1e-9
    wti_net = wti["variants"][cand]["net_usd"] if wti else None
    wti_ok = wti_net is not None and wti_net >= -1e-9
    return {"candidate": cand, "net": net_ok, "max_dd": dd_ok, "plateau": plateau_ok, "neighbours": nb,
            "last_third": last_ok, "last_third_usd": {"baseline": last_b, cand: last_p},
            "wti": wti_ok, "wti_net_usd": wti_net, "delta_usd": round(p["net_usd"] - b["net_usd"], 2),
            "pass": net_ok and dd_ok and plateau_ok and last_ok and wti_ok}


def verdict(results: Dict[str, Dict[str, Any]], cands: Dict[str, Dict[str, Any]] = CANDIDATES,
            primary: str = PRIMARY_DATASET, wti: str = WTI_DATASET) -> Dict[str, Any]:
    """Per candidate, the bar on the journal; PASS when any clears it. No
    journal, no verdict. Pure."""
    if primary not in results:
        return {"judged": False, "pass": False, "reason": f"{primary} missing", "candidates": {}}
    per = {c: passes(results[primary], c, results.get(wti), cands) for c in cands}
    for c, p in per.items():
        d = delta_by_day(results[primary], c)
        p["delta_by_day"] = d
        p["one_day"] = one_day_flips(d)
    winners = [c for c, p in per.items() if p["pass"]]
    best = max(winners, key=lambda c: results[primary]["variants"][c]["net_usd"]) if winners else None
    return {"judged": True, "pass": bool(winners), "candidates": per, "passing": winners, "best": best}


def delta_by_day(ds: Dict[str, Any], cand: str) -> Dict[str, Dict[str, float]]:
    """Per UTC exit day: the candidate's total R and net $ minus the
    baseline's, from the two trade lists. Pure."""
    out: Dict[str, Dict[str, float]] = {}
    for name, sign in (("baseline", -1.0), (cand, 1.0)):
        for t in ds["trades"].get(name, []):
            day = (t["exit"] or t["entry"] or "")[:10]
            row = out.setdefault(day, {"r": 0.0, "net_usd": 0.0})
            row["r"] += sign * (t["r"] or 0.0)
            row["net_usd"] += sign * (t["net_usd"] or 0.0)
    return {d: {"r": round(v["r"], 2), "net_usd": round(v["net_usd"], 2)} for d, v in sorted(out.items())
            if abs(v["r"]) > 1e-9 or abs(v["net_usd"]) > 1e-9}


FLUKE_REST_R = 0.25   # what the other days must add up to for the edge to stand without the biggest one


def one_day_flips(deltas: Dict[str, Dict[str, float]], rest_r_min: float = FLUKE_REST_R) -> Dict[str, Any]:
    """The review's FLUKE rule on a candidate's edge, in R (dollars compound,
    so one avoided stop inflates every later trade): drop the day with the
    biggest |R delta|; if the other days together are under `rest_r_min`
    (a quarter of one R: less than one stop's worth), one day carries the
    whole difference."""
    if not deltas:
        return {"total_r": 0.0, "biggest_day": None, "biggest_r": 0.0, "rest_r": 0.0, "fluke": True}
    total = sum(v["r"] for v in deltas.values())
    day = max(deltas, key=lambda d: abs(deltas[d]["r"]))
    rest = total - deltas[day]["r"]
    return {"total_r": round(total, 2), "biggest_day": day, "biggest_r": deltas[day]["r"], "rest_r": round(rest, 2),
            "rest_r_min": rest_r_min, "fluke": rest < rest_r_min - 1e-9}


def monthly(ds: Dict[str, Any], cand: str) -> Dict[str, Any]:
    """Net $ per 30 days of one variant on one dataset (the sleeve question)."""
    days = ds["days"] or 1.0
    s = ds["variants"][cand]
    return {"net_usd": s["net_usd"], "days": days, "per_30d_usd": round(s["net_usd"] / days * 30.0, 2),
            "trades": s["trades"], "wins": s["wins"], "max_dd_pct": s["max_dd_pct"]}


# ── the no-thesis engine: the zone gate on the tournament's A_long ─────────

def make_zone_long(frac: float, buffer: float = 0.75, min_r: float = 2.0) -> T.Strategy:
    """The tournament's live dip-buy (A_long) with the zone gate: no entry
    unless the open sits within `frac` of the 24h range from its low."""
    def setup(i: int, mid: float, f: T.Features) -> Optional[T.Setup]:
        if f.lo24[i] is None:
            return None
        if zone_blocks(mid, f.lo24[i], f.hi24[i], "long", frac):
            return None
        return T.range_setup(1, mid, f.lo24[i], f.hi24[i], buffer, min_r, T.LIVE_LEV_FIT)
    return T.Strategy(f"Z_zone{int(round(frac * 100))}", "Z", {"zone_frac": frac, "buffer": buffer, "min_r": min_r,
                                                                 "lev_fit": "20/15"}, setup)


def zone_tournament(brent: Sequence[Candle], wti: Sequence[Candle], fracs: Sequence[float] = (0.25, 0.33, 0.50)
                    ) -> Dict[str, Any]:
    """`strategy_tournament.tournament` with the baseline, the zone variants
    and flat; the candidate rows only (the selectors are not the question)."""
    strategies = [T.make_baseline(1)] + [make_zone_long(f) for f in fracs] + [T.FLAT]
    out = T.tournament(brent, wti, strategies=strategies)
    rows = []
    for r in out["candidates"]:
        rows.append({k: r.get(k) for k in ("name", "family", "trades", "wins", "net", "max_dd", "thirds", "avg_r",
                                           "longs", "pass", "checks", "refused")}
                    | {"wti_net": (r.get("wti") or {}).get("net"), "wti_trades": (r.get("wti") or {}).get("trades")})
    return {"window": out["window"], "baseline_net": out["baseline_net"], "rows": rows}


# ── runs ───────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import direction_series, load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/winner-traits/cache", help="15m/1h candles (fetched once, then reused)")
    ap.add_argument("--tournament-cache-dir", default="", help="the strategy tournament's 1h cache (198 days); empty skips it")
    ap.add_argument("--out-dir", default="/tmp/winner-traits")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--risk-cap-frac", type=float, default=RISK_CAP_FRAC)
    ap.add_argument("--equity", type=float, default=EQUITY_NOW, help="today's equity; the sleeve is 10% of it")
    ap.add_argument("--no-wti", action="store_true")
    ap.add_argument("--no-controls", action="store_true")
    ap.add_argument("--no-lev-fit", dest="lev_fit", action="store_false", default=True,
                    help="drop the live 20/15 leverage fit from the baseline (the pre-2026-11-13 eval baseline), for comparison")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    named = variants()
    results: Dict[str, Dict[str, Any]] = {}

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} winner-traits: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        end = c15[-1].t + FIFTEEN_MS
        return c15, c1h, end, D.fetch_funding_cached(coin, c15[0].t, end, a.cache_dir)

    b15, b1h, b_end, b_fund = load(BRENT)
    bp = base_params(a.start_equity, a.risk_cap_frac)       # carries the live 20/15 fit (live_brent_params, 2026-11-13)
    if not a.lev_fit:
        bp = dataclasses.replace(bp, entry_fit_leverage=None)
    log(f"Brent candles to {_iso(b_end)}; variants {list(named)}; leverage fit {bp.entry_fit_leverage}")

    thesis_sets: List[Tuple[str, Any, int, int, Callable]] = []
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
    sleeve: Dict[str, Any] = {}
    if PRIMARY_DATASET in results and thesis_sets:
        label, theses, start, end, direction = thesis_sets[0]
        sp = dataclasses.replace(base_params(round(a.equity * SLEEVE, 2), a.risk_cap_frac),
                                 entry_fit_leverage=bp.entry_fit_leverage)
        ds = run_dataset(b15, b1h, direction, sp, start, end, b_fund,
                         {"baseline": {}, **{c: CANDIDATES[c]["primary"] for c in CANDIDATES}})
        sleeve = {"equity_now": a.equity, "sleeve_equity": sp.starting_equity,
                  **{c: monthly(ds, c) for c in ("baseline", *CANDIDATES)}}
        log(f"sleeve at ${sp.starting_equity} done ({time.time() - t0:.0f}s)")
    tourney: Dict[str, Any] = {}
    if a.tournament_cache_dir:
        brent = T.load_bars(BRENT, a.tournament_cache_dir)
        wti = [] if a.no_wti else T.load_bars(WTI, a.tournament_cache_dir)
        tourney = zone_tournament(brent, wti)
        log(f"tournament leg done ({time.time() - t0:.0f}s)")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "start_equity": a.start_equity, "risk_cap_frac": a.risk_cap_frac, "entry_fit_leverage": bp.entry_fit_leverage,
        "candidates": {c: {k: v for k, v in CANDIDATES[c].items()} for c in CANDIDATES}, "variants": named,
        "datasets": {k: {kk: vv for kk, vv in r.items() if kk != "trades"} for k, r in results.items()},
        "verdict": ver, "sleeve": sleeve, "tournament": tourney,
        "journal_trades": results[PRIMARY_DATASET]["trades"] if PRIMARY_DATASET in results else {},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}")
    if not ver.get("judged"):
        return 2
    return 0 if ver["pass"] else 1


def _print(rep: Dict[str, Any]) -> None:
    for ds, r in rep["datasets"].items():
        print(f"\n{ds}  {r['window'][0]} -> {r['window'][1]} ({r['days']}d)")
        print(f"  {'variant':<12} {'n':>3} {'wins':>4} {'R':>7} {'net$':>8} {'dd%':>5}  thirds net$              refused")
        for v, s in r["variants"].items():
            th = " / ".join(f"{t[v]['net_usd']:+.2f}" for t in r["thirds"])
            print(f"  {v:<12} {s['trades']:>3} {s['wins']:>4} {s['total_r']:>7.2f} {s['net_usd']:>8.2f} "
                  f"{s['max_dd_pct']:>5.1f}  {th:<26} {s['refused']}")
    v = rep["verdict"]
    print(f"\nverdict: {'PASS' if v.get('pass') else 'FAIL'}" + (f" ({v.get('best')})" if v.get("best") else ""))
    for c, p in (v.get("candidates") or {}).items():
        print(f"  {c:<6} net {p['net']} ({p['delta_usd']:+.2f}$) dd {p['max_dd']} plateau {p['plateau']} {p['neighbours']} "
              f"last third {p['last_third']} {p['last_third_usd']} wti {p['wti']} ({p['wti_net_usd']}) -> "
              f"{'PASS' if p['pass'] else 'fail'}")
        od = p.get("one_day") or {}
        if od.get("biggest_day"):
            print(f"         R delta by exit day: {p['delta_by_day']}")
            print(f"         one day carries it: {od['fluke']} ({od['biggest_day']} {od['biggest_r']:+.2f}R of "
                  f"{od['total_r']:+.2f}R; the rest {od['rest_r']:+.2f}R, needs {od['rest_r_min']:+.2f}R)")
    sl = rep.get("sleeve") or {}
    if sl:
        print(f"\n10% sleeve: ${sl['sleeve_equity']} of ${sl['equity_now']}")
        for c in ("baseline", *CANDIDATES):
            m = sl[c]
            print(f"  {c:<9} {m['trades']:>3} trades {m['wins']:>3} wins net {m['net_usd']:+.2f}$ over {m['days']}d "
                  f"= {m['per_30d_usd']:+.2f}$ / 30d, max DD {m['max_dd_pct']}%")
    tn = rep.get("tournament") or {}
    if tn:
        print(f"\ntournament (no thesis, {tn['window']['days']}d 1h Brent, baseline A_long {tn['baseline_net']:+.2f}$):")
        for r in tn["rows"]:
            print(f"  {r['name']:<10} {r['trades']:>3} trades {r['wins']:>3} wins net {r['net']:+.2f}$ dd {100 * r['max_dd']:.1f}% "
                  f"thirds {r['thirds']} wti {r['wti_net']} -> {'PASS' if r['pass'] else 'fail'}")


if __name__ == "__main__":
    raise SystemExit(main())
