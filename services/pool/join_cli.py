"""python -m services.pool {join,leave,status} [options]

    status [--json]
    join   [--master-prompt] [--yes] [--json]
    leave  [--master-prompt] [--yes] [--json]

Without --yes, join/leave are a dry run: the statement is printed and
nothing is signed. With --yes, the same statement is printed, then signed
in the same run and the outbox file, its repo path and the pull request
link are printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

import contracts.pool as pool
from services.pool import join, signing

PROG = "python -m services.pool"


def _parser(command: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{PROG} {command}")
    if command in ("join", "leave"):
        ap.add_argument("--master-prompt", action="store_true",
                        help="sign with the MASTER key, typed at a hidden terminal prompt, "
                             "instead of the configured key")
        ap.add_argument("--yes", action="store_true",
                        help="sign and write the file (default: show the statement and sign nothing)")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    return ap


def _print_statement(info: dict) -> None:
    print(info["message"])
    print()
    time_key = "joined_at" if info["kind"] == "join" else "left_at"
    print(f"{info['kind']} as {info['signer_role']}: {pool.short_address(info['account'])} "
         f"at {info[time_key]}")
    if info["kind"] == "join":
        print("Published:")
        for line in info["published"]:
            print(f"  - {line}")
        print("Not published:")
        for line in info["not_published"]:
            print(f"  - {line}")
    else:
        print(info["effect"])


def _fail(kind: str, e: Exception, as_json: bool) -> int:
    code = getattr(e, "code", "no_key")
    message = str(e)
    if as_json:
        out = {"ok": False, "code": code, "error": message}
        if code == "no_key":
            out["master_command"] = join.master_command(kind)
        print(json.dumps(out))
    else:
        print(f"pathiel pool: {code}: {message}", file=sys.stderr)
        if code == "no_key":
            print(f"run this in your own terminal: {join.master_command(kind)}", file=sys.stderr)
    return 2


def _status(args: argparse.Namespace) -> int:
    info = join.status()
    if args.json:
        print(json.dumps(info))
        return 0
    print(f"network: {info['network']}  account: {info['account'] or '(none)'}  state: {info['state']}")
    if info["joined_at"]:
        print(f"joined at: {info['joined_at']}")
    if info["left_at"]:
        print(f"left at: {info['left_at']}")
    if info["pool"]:
        print(f"pool: day={info['pool']['day']} cutoff={info['pool']['cutoff']} counts={info['pool']['counts']}")
    print(f"scoreboard: {info['scoreboard_url']}")
    print(f"join: {info['how_to_join']}   leave: {info['how_to_leave']}")
    print(info["copy_note"])
    for p in info["problems"]:
        print(f"problem: {p['path']}: {p['code']}: {p['message']}", file=sys.stderr)
    return 0


def _join_or_leave(kind: str, args: argparse.Namespace) -> int:
    try:
        dry = join.prepare(kind, master_prompt=args.master_prompt)
    except (pool.PoolError, signing.NoKey) as e:
        return _fail(kind, e, args.json)
    if not args.yes:
        if args.json:
            print(json.dumps(dry))
        else:
            _print_statement(dry)
        return 0
    if not args.json:
        _print_statement(dry)
    time_key = "joined_at" if kind == "join" else "left_at"
    try:
        done = join.confirm(kind, dry[time_key], master_prompt=args.master_prompt)
    except (pool.PoolError, signing.NoKey) as e:
        return _fail(kind, e, args.json)
    if args.json:
        print(json.dumps(done))
    else:
        print(f"wrote {done['file']}")
        print(f"repo path: {done['repo_path']}")
        print(f"open: {done['pr_url']}")
        if done.get("pr_note"):
            print(done["pr_note"])
    return 0


def main(argv: List[str]) -> int:
    command = argv[0]
    args = _parser(command).parse_args(argv[1:])
    if command == "status":
        return _status(args)
    if command in ("join", "leave"):
        return _join_or_leave(command, args)
    print(f"pathiel pool: unknown command {command!r}", file=sys.stderr)
    return 2
