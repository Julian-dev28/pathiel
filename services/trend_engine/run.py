"""CLI for the trend lanes.

    python -m services.trend_engine.run --lane hl
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
    for o in p.get("observations") or []:
        print(f" * {o}")


def main(argv: Any = None) -> int:
    # BEFORE any pathia import: `rebalancer_owned` freezes the state
    # directory at import time, and a CLI run that skips this reads a different
    # shadow ledger than the bot writes (see env.py).
    from services.trend_engine import env
    env.load()

    ap = argparse.ArgumentParser(prog="trend_engine")
    ap.add_argument("--lane", choices=("hl", "recorders"), default="hl")
    ap.add_argument("--json", action="store_true", help="dump the raw payload")
    ap.add_argument("--ai", action="store_true", help="run the optional LLM pass")
    ap.add_argument("--web-search", action="store_true", help="let the AI pass search")
    ap.add_argument("--backtest", action="store_true", help="walk the HL forecaster forward")
    ap.add_argument("--save", action="store_true", help="write the lane to .state/trend_engine/")
    ap.add_argument("--refresh-all", action="store_true",
                    help="refresh lane caches (what the scheduler calls)")
    ap.add_argument("--lanes", default="",
                    help="comma-separated lanes for --refresh-all (default: all). "
                         "The hl lane and the recorders lane run on different "
                         "clocks, so the scheduler splits them with this")
    ap.add_argument("--top-n", type=int, default=40)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--min-n", type=int, default=8,
                    help="resolved signals a book needs before it gets a verdict")
    a = ap.parse_args(argv)

    if a.refresh_all:
        from services.trend_engine.cache import refresh_all
        only = [x.strip() for x in a.lanes.split(",") if x.strip()] or None
        res = refresh_all(only=only, hl={"top_n": a.top_n},
                          recorders={"min_n": a.min_n})
        print(json.dumps(res, indent=1))
        # "fresh" = the walk-forward was still inside its cadence and was
        # skipped on purpose; only a real error is a non-zero exit, or the
        # scheduler would log a failure every single half-hour tick.
        return 0 if all(v.get("status") in ("ok", "fresh") for v in res.values()) else 1





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
    else:
        from services.trend_engine.recorders import read
        payload = read(min_n=a.min_n)
        printer = _print_recorders

    from services.trend_engine.playbook import build as build_playbook
    payload["playbook"] = build_playbook(a.lane, payload)

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
    pb = payload.get("playbook") or {}
    if pb.get("actions"):
        print("\n─── WHAT TO DO ───")
        order = {"do": 0, "dont": 1, "watch": 2}
        for act in sorted(pb["actions"], key=lambda x: order.get(x["kind"], 9)):
            print(f"  [{act['kind'].upper():5s}] {act['do']}")
            print(f"          because: {act['because']}")
            if act.get("trigger"):
                print(f"          trigger: {act['trigger']}")
            if act.get("invalidate"):
                print(f"          stop if: {act['invalidate']}")
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
