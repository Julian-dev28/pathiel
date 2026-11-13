"""python -m services.pool verify [options]

    verify [--root DIR] [--json]                      offline: every file under <root>/pool
    verify --paths P [P ...] [--online] [--fresh] [--json]      the named statement files
    verify --pr-base REF [--json]                      the pull request check, on HEAD vs REF

Exit 0 pass, 1 a rule failed, 2 refused (shallow clone, REF not merged into
HEAD, a git command failed) or the venue could not be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import contracts.pool as pool
from services.pool import approval, paths
from services.pool import venue as venue_mod

FileResult = Dict[str, Any]

_RAW_LINE = re.compile(r"^:(\d{6}) (\d{6}) ([0-9a-f]{40}) ([0-9a-f]{40}) ([A-Z]\d*)\t")
_ZERO_SHA = "0" * 40


def _now_ms() -> int:
    return int(time.time() * 1000)


class VerifyRefused(RuntimeError):
    """A guard before the pull request check could even start (exit 2)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── small result builders ───────────────────────────────────────────────────

def _ok(path: str, kind: Optional[str], address: Optional[str], signer_role: Optional[str]) -> FileResult:
    return {"path": path, "ok": True, "kind": kind, "address": address, "signer_role": signer_role,
            "problems": []}


def _fail(path: str, kind: Optional[str], problems: List[Tuple[str, str]], *,
          address: Optional[str] = None, signer_role: Optional[str] = None) -> FileResult:
    return {"path": path, "ok": False, "kind": kind, "address": address, "signer_role": signer_role,
            "problems": [{"code": c, "message": m} for c, m in problems]}


# ── offline: every file under <root>/pool ───────────────────────────────────

def _walk(folder: str) -> List[str]:
    if not os.path.isdir(folder):
        return []
    return sorted(n for n in os.listdir(folder) if n != ".gitkeep")


def _offline_files(pool_root: str) -> List[FileResult]:
    out: List[FileResult] = []
    house_path = os.path.join(pool_root, pool.HOUSE_FILE)
    house_rel = f"{pool.POOL_DIR}/{pool.HOUSE_FILE}"
    if os.path.exists(house_path):
        try:
            house = pool.load_house(pool_root)
            out.append(_ok(house_rel, "house", house["address"], None))
        except pool.PoolError as e:
            out.append(_fail(house_rel, "house", [(e.code, str(e))]))
    for subdir, kind in ((pool.MEMBERS_SUBDIR, "join"), (pool.LEAVES_SUBDIR, "leave")):
        folder = os.path.join(pool_root, subdir)
        for name in _walk(folder):
            rel = f"{pool.POOL_DIR}/{subdir}/{name}"
            try:
                v = pool.load_statement(os.path.join(folder, name), kind)
                out.append(_ok(rel, kind, v["address"], v["signer_role"]))
            except pool.PoolError as e:
                out.append(_fail(rel, kind, [(e.code, str(e))]))
    return out


def _load_board(root: str) -> Any:
    path = os.path.join(root, pool.SCOREBOARD_DIR, pool.SCOREBOARD_JSON)
    try:
        with open(path, "r", encoding="ascii") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _offline(root: str, now: int) -> Tuple[List[FileResult], List[str]]:
    pool_root = os.path.join(root, pool.POOL_DIR)
    files = _offline_files(pool_root)
    notes: List[str] = []
    board = _load_board(root)
    if isinstance(board, dict) and pool.validate_scoreboard(board) == [] and all(f["ok"] for f in files):
        active = {m["address"] for m in pool.active_members(pool_root, now)}
        for row in board.get("members", []):
            addr = row.get("address")
            if isinstance(addr, str) and addr not in active:
                notes.append(f"{pool.short_address(addr)} is on the published scoreboard but is no "
                            "longer active: run python -m services.pool build")
    return files, notes


# ── --paths: the named statement files ──────────────────────────────────────

def _paths_mode(path_list: List[str], root: str, now: int, *, online: bool, fresh: bool) -> List[FileResult]:
    pool_root = os.path.join(root, pool.POOL_DIR)
    files: List[FileResult] = []
    for p in path_list:
        kind = "join" if os.path.basename(os.path.dirname(p)) == pool.MEMBERS_SUBDIR else "leave"
        try:
            v = pool.load_statement(p, kind)
        except pool.PoolError as e:
            files.append(_fail(p, kind, [(e.code, str(e))]))
            continue
        if online:
            problems = approval.check_statement(v, pool_root=pool_root, now_ms=now)
        else:
            problems = []
            if fresh:
                stale = pool.fresh_problem(v["at_ms"], now)
                if stale is not None:
                    problems.append(("stale", stale))
        if problems:
            files.append(_fail(p, kind, problems, address=v["address"], signer_role=v["signer_role"]))
        else:
            files.append(_ok(p, kind, v["address"], v["signer_role"]))
    return files


# ── --pr-base: the pull request check ───────────────────────────────────────

def _clean_git_env() -> Dict[str, str]:
    """os.environ with every GIT_* variable stripped. GIT_DIR, GIT_WORK_TREE
    and (above all) GIT_INDEX_FILE override `-C` outright: a process invoked
    from inside a git hook inherits GIT_INDEX_FILE pointing at the REAL
    repository's in-progress commit, and without this, `git -C <root>` would
    silently operate on that index instead of `root` (confirmed: with
    GIT_DIR set, `git -C <throwaway> rev-parse --show-toplevel` fails rather
    than naming the throwaway directory). Every git call this module makes
    goes through `_git`, so this is the one place that matters."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=_clean_git_env())


def _guard(root: str, ref: str) -> None:
    shallow = _git(root, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0:
        raise VerifyRefused("shallow", f"could not read the repository at {root}: "
                            f"{shallow.stderr.strip()}")
    if shallow.stdout.strip() == "true":
        raise VerifyRefused("shallow", "the checkout is shallow; the pull request check reads full "
                            "history (git fetch --unshallow, or checkout with fetch-depth: 0)")
    anc = _git(root, "merge-base", "--is-ancestor", ref, "HEAD")
    if anc.returncode != 0:
        raise VerifyRefused("not_merged", f"HEAD does not contain {ref}: in CI HEAD is the pull "
                            "request's merge commit; locally, update the branch first")


def _changed_paths(root: str, ref: str) -> List[Tuple[str, str]]:
    diff = _git(root, "diff", "--name-status", "--no-renames", f"{ref}...HEAD")
    if diff.returncode != 0:
        raise VerifyRefused("git_failed", diff.stderr.strip() or "git diff failed")
    out = []
    for line in diff.stdout.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        out.append((status.strip(), path.strip()))
    return out


def _is_pool_path(path: str) -> bool:
    return path.startswith(f"{pool.POOL_DIR}/{pool.MEMBERS_SUBDIR}/") \
        or path.startswith(f"{pool.POOL_DIR}/{pool.LEAVES_SUBDIR}/")


def _show_at_ref(root: str, ref: str, repo_path: str) -> Optional[str]:
    res = _git(root, "show", f"{ref}:{repo_path}")
    return res.stdout if res.returncode == 0 else None


def _membership_at_ref(root: str, ref: str, address: str) -> pool.Membership:
    with tempfile.TemporaryDirectory() as tmp:
        for subdir, repo_path_of in ((pool.MEMBERS_SUBDIR, pool.member_repo_path),
                                     (pool.LEAVES_SUBDIR, pool.leave_repo_path)):
            folder = os.path.join(tmp, subdir)
            os.makedirs(folder, exist_ok=True)
            content = _show_at_ref(root, ref, repo_path_of(address))
            if content is not None:
                with open(os.path.join(folder, pool.statement_filename(address)), "w", encoding="ascii") as fh:
                    fh.write(content)
        return pool.membership(tmp, address)


def _held_ms(root: str, ref: str, repo_path: str) -> Tuple[List[int], List[str]]:
    """Every joined_at / left_at the base branch has EVER held for
    `repo_path`: the new blob of every non-deletion raw diff line in its
    history on `ref`. A blob that does not parse is a NOTE, not a failure."""
    held: List[int] = []
    notes: List[str] = []
    res = _git(root, "log", ref, "--format=", "--raw", "--no-abbrev", "--no-renames", "--", repo_path)
    if res.returncode != 0:
        raise VerifyRefused("git_failed", res.stderr.strip() or f"git log failed for {repo_path}")
    for line in res.stdout.splitlines():
        m = _RAW_LINE.match(line)
        if not m:
            continue
        new_sha = m.group(4)
        if new_sha == _ZERO_SHA:
            continue
        blob = _git(root, "cat-file", "blob", new_sha)
        if blob.returncode != 0:
            notes.append(f"{repo_path}: could not read a historical version ({new_sha[:12]}); skipped")
            continue
        try:
            doc = json.loads(blob.stdout)
            at = doc.get("joined_at") or doc.get("left_at")
            held.append(pool.ms_from_iso(at))
        except (ValueError, TypeError, pool.PoolError):
            notes.append(f"{repo_path}: a historical version does not parse as a statement; skipped")
    return held, notes


def _reinstated_check(root: str, ref: str, address: str) -> Tuple[Optional[str], List[str]]:
    before = _membership_at_ref(root, ref, address)
    after = pool.membership(os.path.join(root, pool.POOL_DIR), address)
    held: List[int] = []
    notes: List[str] = []
    for repo_path_of in (pool.member_repo_path, pool.leave_repo_path):
        h, n = _held_ms(root, ref, repo_path_of(address))
        held += h
        notes += n
    return pool.reinstatement_problem(before, after, held), notes


def _pr_base(root: str, ref: str, now: int, *, post=None) -> Tuple[List[FileResult], List[str]]:
    _guard(root, ref)
    changes = _changed_paths(root, ref)
    touched = [(status, path) for status, path in changes if _is_pool_path(path)]
    if not touched:
        return [], ["no membership change"]

    other = [path for status, path in changes if not _is_pool_path(path)]
    if other:
        msg = ("a membership pull request touches only pool/members/ or pool/leaves/; this one "
              "also touches " + ", ".join(sorted(other)))
        return [_fail(path, None, [("mixed_change", msg)]) for status, path in touched], []

    addresses = {os.path.splitext(os.path.basename(path))[0] for _, path in touched}
    if len(touched) > 2 or len(addresses) > 1:
        msg = ("at most two files, one address: pool/members/<addr>.json and/or "
              "pool/leaves/<addr>.json for the SAME address")
        return [_fail(path, None, [("too_many", msg)]) for status, path in touched], []

    address_raw = next(iter(addresses))
    try:
        address = pool.normalize_address(address_raw)
    except pool.PoolError as e:
        return [_fail(path, None, [(e.code, str(e))]) for _, path in touched], []

    pool_root = os.path.join(root, pool.POOL_DIR)
    files: List[FileResult] = []
    for status, path in touched:
        if status == "D":
            continue
        kind = "join" if f"/{pool.MEMBERS_SUBDIR}/" in path else "leave"
        try:
            v = pool.load_statement(os.path.join(root, path), kind)
        except pool.PoolError as e:
            files.append(_fail(path, kind, [(e.code, str(e))]))
            continue
        problems = approval.check_statement(v, pool_root=pool_root, now_ms=now, post=post)
        if problems:
            files.append(_fail(path, kind, problems, address=v["address"], signer_role=v["signer_role"]))
        else:
            files.append(_ok(path, kind, v["address"], v["signer_role"]))

    problem, notes = _reinstated_check(root, ref, address)
    member_repo_path, leave_repo_path = pool.member_repo_path(address), pool.leave_repo_path(address)
    if problem is not None:
        files.append(_fail(member_repo_path, "join", [("reinstated", problem)], address=address))
    else:
        after = pool.membership(pool_root, address)
        for status, path in touched:
            if status != "D":
                continue
            if path == member_repo_path:
                notes.append(f"{member_repo_path}: deleted: the address goes out; the maintainer "
                            "confirms the owner asked")
            elif path == leave_repo_path:
                if after["state"] == "member":
                    notes.append(f"{leave_repo_path}: deleted: its join is newer than this leave")
                else:
                    notes.append(f"{leave_repo_path}: deleted: the address stays out")

    # The whole-pool offline scan must stay clean: a membership pull request
    # is judged on the files it touches, but a pre-existing broken file
    # elsewhere in pool/ must still fail the check, not pass silently.
    whole = pool.scan(pool_root, now)
    seen = {f["path"] for f in files}
    for p in whole["problems"]:
        rel = f"{pool.POOL_DIR}/{p['path']}"
        if rel not in seen:
            files.append(_fail(rel, None, [(p["code"], p["message"])]))
    return files, notes


# ── reporting ────────────────────────────────────────────────────────────────

def _file_lines(f: FileResult) -> List[str]:
    if f["ok"]:
        role = f["signer_role"] or "-"
        addr = pool.short_address(f["address"]) if f["address"] else "-"
        kind = f["kind"] or "-"
        return [f"OK   {f['path']} {kind} {role} {addr}"]
    return [f"FAIL {f['path']}: {p['code']}: {p['message']}" for p in f["problems"]]


def _print_report(mode: str, files: List[FileResult], notes: List[str], now: int, as_json: bool) -> int:
    ok = sum(1 for f in files if f["ok"])
    fail = len(files) - ok
    at = pool.iso_from_ms(now)
    if as_json:
        print(json.dumps({"mode": mode, "ok": fail == 0, "at": at, "files": files, "notes": notes}))
    else:
        for f in files:
            for line in _file_lines(f):
                print(line)
        for n in notes:
            print(f"NOTE {n}")
        print(f"POOL-VERIFY mode={mode} files={len(files)} ok={ok} fail={fail} notes={len(notes)} at={at}")
    return 0 if fail == 0 else 1


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m services.pool verify")
    ap.add_argument("--root", default=None)
    ap.add_argument("--paths", nargs="+", default=None)
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--pr-base", default=None)
    ap.add_argument("--json", action="store_true")
    return ap


def main(argv: List[str]) -> int:
    args = _parser().parse_args(argv[1:])
    root = args.root or paths.repo_root()
    now = _now_ms()
    try:
        if args.pr_base:
            try:
                files, notes = _pr_base(root, args.pr_base, now)
            except VerifyRefused as e:
                print(f"pathiel pool verify: {e.code}: {e}", file=sys.stderr)
                return 2
            return _print_report("pr", files, notes, now, args.json)
        if args.paths:
            files = _paths_mode(args.paths, root, now, online=args.online, fresh=args.fresh)
            return _print_report("paths", files, [], now, args.json)
        files, notes = _offline(root, now)
        return _print_report("offline", files, notes, now, args.json)
    except venue_mod.VenueError as e:
        print(f"pathiel pool verify: venue: {e}", file=sys.stderr)
        return 2
