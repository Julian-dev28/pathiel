"""CLI for the trend lanes.

    python -m services.trend_engine.run --lane hl
    python -m services.trend_engine.run --lane updown --minutes 43200
    python -m services.trend_engine.run --lane politics --limit 40
    python -m services.trend_engine.run --lane recorders        # forward-graded P&L
    python -m services.trend_engine.run --backtest --days 400
    python -m services.trend_engine.run --lane hl --ai        # optional LLM pass

`--json` dumps the raw payload; without it you get the human summary the
dashboard renders.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


def _print_hl(p: Dict[str, Any]) -> None:
    reg = p.get("regime") or {}
    print(f"\nREGIME  {reg.get('label')}   BTC 7d {reg.get('btc_ret_7d')}%  "
          f"breadth {reg.get('breadth_pct')}%  trending {reg.get('trend_share_pct')}%  "
          f"dispersion {reg.get('dispersion_pct')}%  alt {reg.get('alt_strength_pct')}pp")
    print(f"scanned {p.get('scanned')} coins in {p.get('elapsed_s')}s\n")
    print(f"{'COIN':<10}{'7D%':>8}{'SLOPE':>8}{'EFF':>6}{'LABEL':>13}{'FC%':>8}{'P(UP)':>7}  FLAGS")
    for r in (p.get("reads") or [])[:25]:
        f = r.get("forecast") or {}
        flags = ",".join(x["code"] for x in (r.get("flags") or [])[:3])
        print(f"{r['coin']:<10}{r.get('ret_7d') or 0:>+8.1f}{r.get('slope_pct_day') or 0:>+8.2f}"
              f"{r.get('efficiency') or 0:>6.2f}{r.get('label'):>13}"
              f"{f.get('drift_pct', 0):>+8.1f}{f.get('prob_up', 0.5):>7.2f}  {flags}")
    print()
    for o in p.get("observations") or []:
        print(f" * {o}")


def _print_updown(p: Dict[str, Any]) -> None:
    pat = p.get("patterns") or {}
    cal = p.get("calibration") or {}
    live = p.get("live") or {}
    print(f"\nBTC 5m UP/DOWN — {pat.get('n_windows')} windows ({p.get('sample_days')}d)")
    print(f"base UP rate {pat.get('base_rate')} CI {pat.get('base_ci')} "
          f"p_vs_coinflip {pat.get('base_p_vs_coinflip')}")
    print(f"VERDICT: {pat.get('verdict')}\n")
    for fam in pat.get("families") or []:
        top = (fam.get("rows") or [{}])[0]
        print(f"  {fam['family']:<18} best={str(top.get('bucket')):<14} n={top.get('n'):<6} "
              f"rate={top.get('rate')} lift={top.get('lift_pp'):+.2f}pp p_bonf={top.get('p_bonf')}")
    print(f"\nIN-WINDOW MODEL @min3: Brier {cal.get('brier')} vs null {cal.get('brier_null')} "
          f"({cal.get('skill_pct')}% skill), cal err {cal.get('calibration_err_pp')}pp — {cal.get('verdict')}")
    if live.get("status") == "ok":
        print(f"LIVE: {live.get('move_bp')}bp move, {live.get('minutes_left')}min left, "
              f"model {live.get('p_up_randomwalk')} vs market {live.get('mkt_up')} "
              f"({live.get('edge_pp')}pp) — {live.get('note')}")


def _print_politics(p: Dict[str, Any]) -> None:
    brd = p.get("board") or {}
    print(f"\nPOLITICS — {brd.get('n')} markets, median weekly move "
          f"{brd.get('median_abs_move_pp')}pp, labels {brd.get('label_counts')}")
    print(f"drift null: {(p.get('momentum_test') or {}).get('verdict')}\n")
    print(f"{'NOW':>6}{'7D':>8}{'DAYS':>7}  {'LABEL':<14} QUESTION")
    for r in (p.get("reads") or [])[:20]:
        print(f"{r['p_now']:>6.2f}{r['delta_7d_pp']:>+8.1f}"
              f"{(r.get('days_left') if r.get('days_left') is not None else -1):>7.0f}  "
              f"{r['label']:<14} {r['question'][:70]}")
    print()
    for o in p.get("observations") or []:
        print(f" * {o}")


def _print_recorders(p: Dict[str, Any]) -> None:
    s = p.get("summary") or {}
    print(f"\nRECORDERS — {s.get('n_books')} books, {s.get('n_graded')} graded, "
          f"verdicts {s.get('verdicts')}, mean EV {s.get('mean_ev_pct')}%/signal\n")
    print(f"{'book':<26}{'sig':>5}{'res':>5}{'ev%':>9}{'@25':>8}{'win':>6}{'1st':>8}{'2nd':>8}  verdict")
    for b in p.get("books") or []:
        f = lambda v: (v if v is not None else 0)
        print(f"{b['book']:<26}{b['signals']:>5}{b['resolved']:>5}{f(b['ev_pct']):>9.3f}"
              f"{f(b['ev25_pct']):>8.3f}{f(b['win_rate']):>6.2f}{f(b['ev_first']):>8.2f}"
              f"{f(b['ev_second']):>8.2f}  {b['verdict']}")
    print()
    for lane, g in ((p.get("scout") or {}).get("lanes") or {}).items():
        print(f"  polymarket {lane:<12} rows={g.get('rows')} resolved={g.get('n')} "
              f"pnl/$={g.get('mean_pnl_per_$')} win={g.get('win_rate')} "
              f"brier {g.get('brier_llm')} vs mkt {g.get('brier_mkt')}")
    print()
    for o in p.get("observations") or []:
        print(f" * {o}")


def _print_edges(r: Dict[str, Any]) -> None:
    for key in ("tail_edge_60s", "tail_edge_120s"):
        t = r.get(key) or {}
        if t.get("status") != "ok":
            continue
        print(f"\nFAT TAIL @{t['secs_left']}s left — n={t['n']}, "
              f"tail_is_fat={t['tail_is_fat']}")
        print(f"{'leader priced':>14}{'n':>7}{'implied':>9}{'realized':>10}{'diff_pp':>9}  sig")
        for row in t.get("table") or []:
            print(f"{row['bucket']:>14}{row['n']:>7}{row['implied']:>9.3f}"
                  f"{row['realized']:>10.3f}{row['diff_pp']:>+9.1f}  {row['significant']}")
        print(f"  {t['verdict']}")
    a = r.get("arb") or {}
    print(f"\nARB: {a.get('verdict', a.get('hint', ''))}")
    if a.get("status") == "ok":
        print(f"  samples={a['samples']} windows={a['windows']} "
              f"mean pair cost ${a['mean_pair_cost']} min ${a['min_pair_cost']} "
              f"fee {a['mean_fee_bps']}bps")
    for key in ("price_calibration_30s", "price_calibration_60s"):
        c = r.get(key) or {}
        if c.get("status") != "ok":
            print(f"\n{key}: {c.get('status')} — {c.get('hint', '')}")
            continue
        print(f"\nMARKET PRICE CALIBRATION @{c['secs_left']}s — n={c['n']}")
        for row in c.get("rows") or []:
            print(f"{row['bucket']:>14}{row['n']:>7}{row['implied']:>9.3f}"
                  f"{row['realized']:>10.3f}{row['diff_pp']:>+9.1f}  {row['significant']}")
        print(f"  {c['verdict']}")
    ts = r.get("tail_strategy") or {}
    print(f"\nTAIL STRATEGY (buy <= {ts.get('max_ask')} with {ts.get('secs_left')}s left): "
          f"{ts.get('verdict', ts.get('hint', ts.get('status')))}")
    if ts.get("status") == "ok":
        print(f"  n={ts['n']} win {ts['win_rate']} (ci {ts['win_ci']}) vs breakeven "
              f"{ts['breakeven_win_rate']} | EV/$staked {ts['ev_per_$staked']}")
    lp = r.get("live_pair") or {}
    if lp.get("status") == "ok":
        print(f"\nLIVE PAIR {lp['slug']}: buy both ${(lp.get('buy_both') or {}).get('cost')} "
              f"net {(lp.get('buy_both') or {}).get('net_edge')} | "
              f"{lp.get('ticks_to_gross_arb')} ticks from a gross arb | fee {lp['fee_bps']}bps")


def main(argv: Any = None) -> int:
    # BEFORE any hermes_trader import: `rebalancer_owned` freezes the state
    # directory at import time, and a CLI run that skips this reads a different
    # shadow ledger than the bot writes (see env.py).
    from services.trend_engine import env
    env.load()

    ap = argparse.ArgumentParser(prog="trend_engine")
    ap.add_argument("--lane", choices=("hl", "updown", "politics", "recorders"), default="hl")
    ap.add_argument("--json", action="store_true", help="dump the raw payload")
    ap.add_argument("--ai", action="store_true", help="run the optional LLM pass")
    ap.add_argument("--web-search", action="store_true", help="let the AI pass search")
    ap.add_argument("--backtest", action="store_true", help="walk the HL forecaster forward")
    ap.add_argument("--sample-updown", action="store_true",
                    help="snapshot both 5m books at fixed offsets of ONE window "
                         "(blocks ~4min; the unbiased instrument for the price-"
                         "calibration and arb questions)")
    ap.add_argument("--sample-daemon", action="store_true",
                    help="run the book sampler forever, one window at a time. "
                         "Its own process on purpose: the scheduler fires jobs "
                         "SERIALLY, so a 4-minute sampler inside it would starve "
                         "every other job")
    ap.add_argument("--max-windows", type=int, default=0,
                    help="stop the sampler after N windows (0 = forever)")
    ap.add_argument("--edges", action="store_true",
                    help="microstructure edges: fat tail, arb frequency, price calibration")
    ap.add_argument("--save", action="store_true", help="write the lane to .state/trend_engine/")
    ap.add_argument("--refresh-all", action="store_true",
                    help="refresh lane caches (what the scheduler calls)")
    ap.add_argument("--lanes", default="",
                    help="comma-separated lanes for --refresh-all (default: all). "
                         "The price lanes and the recorders lane run on different "
                         "clocks, so the scheduler splits them with this")
    ap.add_argument("--top-n", type=int, default=40)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--minutes", type=int, default=30240)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=8,
                    help="resolved signals a book needs before it gets a verdict")
    a = ap.parse_args(argv)

    if a.refresh_all:
        from services.trend_engine.cache import refresh_all
        only = [x.strip() for x in a.lanes.split(",") if x.strip()] or None
        res = refresh_all(only=only, hl={"top_n": a.top_n},
                          updown={"minutes": a.minutes},
                          politics={"limit": a.limit}, recorders={"min_n": a.min_n})
        print(json.dumps(res, indent=1))
        # "fresh" = the walk-forward was still inside its cadence and was
        # skipped on purpose; only a real error is a non-zero exit, or the
        # scheduler would log a failure every single half-hour tick.
        return 0 if all(v.get("status") in ("ok", "fresh") for v in res.values()) else 1

    if a.sample_daemon:
        import time as _t
        from services.trend_engine.updown_edges import record_window, SAMPLES
        print(f"[sampler] writing {SAMPLES} — one window at a time, "
              f"{'forever' if not a.max_windows else a.max_windows} window(s)")
        done = 0
        while not a.max_windows or done < a.max_windows:
            try:
                rows = record_window()
                done += 1
                if rows:
                    last = rows[-1]
                    print(f"[sampler] {_t.strftime('%H:%M:%S')} window {done}: "
                          f"{len(rows)} snapshots, last pair "
                          f"{(last['up_ask'] or 0) + (last['down_ask'] or 0):.2f} "
                          f"@{last['secs_left']:.0f}s", flush=True)
                else:
                    print(f"[sampler] {_t.strftime('%H:%M:%S')} window {done}: "
                          f"no snapshots (market missing or late start)", flush=True)
            except KeyboardInterrupt:
                print("[sampler] stopped")
                return 0
            except Exception as exc:      # a dead API must not kill the daemon
                print(f"[sampler] error: {exc}", flush=True)
                _t.sleep(30)
        return 0

    if a.sample_updown:
        from services.trend_engine.updown_edges import record_window
        rows = record_window()
        for r in rows:
            print(f"{r['secs_left']:>5.0f}s left  up {r['up_bid']}/{r['up_ask']}  "
                  f"down {r['down_bid']}/{r['down_ask']}  pair "
                  f"{(r['up_ask'] or 0) + (r['down_ask'] or 0):.2f}  "
                  f"net {r['buy_both_net']}")
        print(f"recorded {len(rows)} snapshots")
        return 0 if rows else 1

    if a.edges:
        from services.trend_engine.updown_edges import read as edges_read
        res = edges_read()
        if a.json:
            print(json.dumps(res, indent=1, default=str))
            return 0
        _print_edges(res)
        return 0

    if a.backtest:
        from services.trend_engine.hl_trends import backtest, save_eval
        res = backtest(top_n=a.top_n, days=a.days)
        if a.save:
            print(f"saved -> {save_eval(res)}")
        print(json.dumps(res, indent=1))
        return 0

    if a.lane == "hl":
        from services.trend_engine.hl_trends import scan
        payload = scan(top_n=a.top_n)
        printer = _print_hl
    elif a.lane == "updown":
        from services.trend_engine.updown_trends import read
        payload = read(minutes=a.minutes)
        printer = _print_updown
    elif a.lane == "politics":
        from services.trend_engine.political_trends import read
        payload = read(limit=a.limit)
        printer = _print_politics
    else:
        from services.trend_engine.recorders import read
        payload = read(min_n=a.min_n)
        printer = _print_recorders

    if a.ai:
        from services.trend_engine.ai import analyze
        payload["ai"] = analyze(a.lane, payload, web_search=a.web_search)

    if a.save:
        from services.trend_engine.cache import save
        print(f"saved -> {save(a.lane, payload)}")

    if a.json:
        print(json.dumps(payload, indent=1, default=str))
        return 0

    printer(payload)
    ai = payload.get("ai") or {}
    if ai.get("status") == "ok":
        print(f"\nAI [{ai['model']}] {ai['headline']}\n{ai['narrative']}")
        for s in ai.get("setups") or []:
            print(f"  - {s.get('ticker')}: {s.get('read')} | trigger {s.get('trigger')} "
                  f"| invalidation {s.get('invalidation')} ({s.get('confidence')})")
    elif ai:
        print(f"\nAI pass {ai.get('status')}: {ai.get('error', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
