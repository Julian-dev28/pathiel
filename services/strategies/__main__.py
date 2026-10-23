"""CLI: python -m services.strategies {list,brief <desk>,refresh <desk|--all> [--force],grade}

Human-readable by default; pass --json (before the subcommand) for raw JSON.
`refresh --all` is the one-line scheduler hook — see services/strategies/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from services.strategies import desks, facade


def _print_json(obj: Any) -> None:
    if isinstance(obj, list):
        print(json.dumps([o.model_dump(mode="json") for o in obj], indent=2))
    else:
        print(json.dumps(obj.model_dump(mode="json"), indent=2))


def cmd_list(args: argparse.Namespace) -> int:
    cards = facade.list_cards()
    if args.json:
        _print_json(cards)
        return 0
    if not cards:
        print("no desks registered")
        return 0
    for c in cards:
        mid = f"{c.mid:,.4g}" if c.mid is not None else "-"
        ret1d = f"{c.ret_1d:+.2%}" if c.ret_1d is not None else "-"
        call = f"{c.call_side}: {c.one_liner}" if c.one_liner else "no thesis yet"
        print(f"{c.id:<10} {c.name:<16} {c.primary_label:<10} mid={mid:<10} 1d={ret1d:<8} {call}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    try:
        brief = facade.get_brief(args.desk, wait=True)
    except desks.UnknownDeskError:
        print(f"unknown desk '{args.desk}'; known: {', '.join(desks.list_desk_ids())}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(brief)
        return 0

    print(f"{brief.spec.name} ({brief.spec.id}) - {brief.spec.tagline}")
    if brief.analytics:
        for snap in brief.analytics.instruments:
            print(f"  {snap.label:<10} {snap.coin:<14} mid={snap.mid:<10.4g} "
                 f"1d={snap.ret_1d:+.2%} 20d={snap.ret_20d:+.2%}")
        for rung in brief.analytics.ladder:
            print(f"  ladder {rung.coin} {rung.side:<5} {rung.leverage:g}x  "
                 f"liq={rung.liq_px:.4g}  P(liq)={rung.p_liq:.0%}  EV={rung.ev_pct:+.1%}  "
                 f"verdict={rung.verdict}")
    else:
        print("  analytics: unavailable")
    print(f"  thesis: {brief.thesis.call.one_liner}" if brief.thesis else "  thesis: none yet - run `refresh` first")
    if brief.track_record.n_graded:
        print(f"  track record: {brief.track_record.n_graded} graded, "
             f"direction hit rate {brief.track_record.direction_hit_rate:.0%}" )
    if brief.stale:
        print(f"  stale: {', '.join(brief.stale)}")
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    desk_ids = desks.list_desk_ids() if args.all else [args.desk]
    jobs = [facade.refresh(desk_id, force=args.force) for desk_id in desk_ids]
    if args.json:
        _print_json(jobs if args.all else jobs[0])
    else:
        for job in jobs:
            print(f"{job.desk_id}: {job.state} - {job.message or ''}")
    return 1 if any(job.state == "error" for job in jobs) else 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Thesis grading, then trade grading (every registered account's fills
    against every graded-or-expired call) -- the same two calls the
    server's hourly background sweep and the desk_grade MCP tool make."""
    n_theses = facade.grade_due()
    n_trades = facade.grade_due_trades()
    if args.json:
        print(json.dumps({"theses_graded": n_theses, "trades_graded": n_trades}))
    else:
        print(f"graded {n_theses} {'thesis' if n_theses == 1 else 'theses'}, "
             f"{n_trades} trade grade{'' if n_trades == 1 else 's'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.strategies",
                                     description="Desks CLI: list, brief, refresh, grade.")
    parser.add_argument("--json", action="store_true", help="print raw JSON instead of a summary")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="one line per desk")

    p_brief = sub.add_parser("brief", help="full brief for one desk")
    p_brief.add_argument("desk")

    p_refresh = sub.add_parser("refresh", help="run the refresh pipeline for one desk, or --all")
    p_refresh.add_argument("desk", nargs="?", help="desk id; omit when passing --all")
    p_refresh.add_argument("--all", action="store_true", help="refresh every registered desk")
    p_refresh.add_argument("--force", action="store_true", help="bypass the token gate")

    sub.add_parser("grade", help="grade every expired thesis, then every account's trades against it")

    args = parser.parse_args(argv)
    if args.command == "refresh" and not args.all and not args.desk:
        parser.error("refresh needs a desk id or --all")
    handlers = {"list": cmd_list, "brief": cmd_brief, "refresh": cmd_refresh, "grade": cmd_grade}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
