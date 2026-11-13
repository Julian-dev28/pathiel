"""Does every refusal and acceptance the membership contract promises
actually fire, end to end?

    python -m services.pool.evals.join_eval

Periodic lane: offline, seeded, under a few seconds. No network (every venue
read is a fake `post` function), no LLM. Three groups of rows:

  valid        a master join, an agent join an approving fake venue accepts,
               and a leave are all accepted with no problem
  offline.*    one tampered file per offline verification rule
               (contracts.pool.verify_statement / load_statement), refused
               with the rule's own code
  online.*     the rules that need a (faked) venue read: a stale statement,
               an unapproved or expired agent, a leave signed by a stranger
  pr.*         the pull-request-time checks that need a real git history:
               mixed_change, too_many, the four ways back into the pool
               without a new signature (`reinstated`), and the three
               legitimate changes that must NOT be refused
  replay       a join signature offered as a leave

Every key used here is made in this run, in `run()`, and lives nowhere else:
`main()` scans every byte this run printed, and every byte every file it
wrote holds, for all of them.

Gate: test_join_eval_smoke.py runs main() and asserts 0 (every row passes).
"""
from __future__ import annotations

import copy
import io
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import contracts.pool as pool
from services.pool import approval, verify_cli

DAY_MS = pool.DAY_MS

Row = Dict[str, Any]


def _row(check: str, got: Any, want: str, ok: bool) -> Row:
    return {"check": check, "got": got, "want": want, "ok": bool(ok)}


def _case(check: str, got: Any, want: Any) -> Row:
    return _row(check, got, want, got == want)


# ── throwaway keys, made here and nowhere else ──────────────────────────────

class _Keys:
    """Every private key this run makes, hex (no 0x), so `main()` can prove
    none of them ever appear in anything the run printed or wrote."""

    def __init__(self) -> None:
        self.hexes: List[str] = []

    def new(self) -> Any:
        from eth_account import Account
        acct = Account.create()
        self.hexes.append(acct.key.hex().removeprefix("0x").lower())
        return acct

    def sign(self, kind: str, account: Any, signer_account: Any, at: str) -> Dict[str, Any]:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        message = pool.statement_text(kind, account.address, signer_account.address, at)
        sig = Account.sign_message(encode_defunct(text=message), signer_account.key).signature.hex()
        return pool.build_statement_file(kind, account.address, signer_account.address, at,
                                         "0x" + sig.removeprefix("0x"))


def _code_of(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    try:
        fn(*args, **kwargs)
        return "ok"
    except pool.PoolError as e:
        return e.code


def _problems_code(problems: List[Tuple[str, str]]) -> str:
    return problems[0][0] if problems else "ok"


# ── valid files, and the offline (file-only) tamper table ──────────────────

def _valid_rows(keys: _Keys, now: int, now_iso: str) -> List[Row]:
    master = keys.new()
    join_master = keys.sign("join", master, master, now_iso)
    rows = [_case("valid.join_master", _code_of(pool.verify_statement, join_master), "ok")]

    main_acct, agent_acct = keys.new(), keys.new()
    join_agent = keys.sign("join", main_acct, agent_acct, now_iso)
    v = pool.verify_statement(join_agent)

    def approving(payload: Dict[str, Any]) -> Any:
        if payload["type"] == "extraAgents":
            return [{"name": "p", "address": agent_acct.address.lower(), "validUntil": now + 1}]
        return {"role": "user"}
    problems = approval.check_statement(v, pool_root="/nonexistent-pool-root", now_ms=now, post=approving)
    rows.append(_case("valid.join_agent_approved", _problems_code(problems), "ok"))

    leave_master = keys.sign("leave", master, master, now_iso)
    v2 = pool.verify_statement(leave_master)
    problems2 = approval.check_statement(v2, pool_root="/nonexistent-pool-root", now_ms=now)
    rows.append(_case("valid.leave_master", _problems_code(problems2), "ok"))
    return rows


def _tamper(doc: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
    out = copy.deepcopy(doc)
    for key, value in changes.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def _offline_rows(keys: _Keys, now_iso: str, work: Path) -> List[Row]:
    acct, other = keys.new(), keys.new()
    good = keys.sign("join", acct, acct, now_iso)
    stranger = keys.sign("join", other, other, now_iso)

    rows = [
        _case("offline.not_object", _code_of(pool.verify_statement, [good]), "not_object"),
        _case("offline.kind", _code_of(pool.verify_statement, _tamper(good, kind="house")), "kind"),
        _case("offline.keys", _code_of(pool.verify_statement, _tamper(good, note="hi")), "keys"),
        _case("offline.schema", _code_of(pool.verify_statement, _tamper(good, schema=2)), "schema"),
        _case("offline.network", _code_of(pool.verify_statement, _tamper(good, network="testnet")), "network"),
        _case("offline.address", _code_of(pool.verify_statement, _tamper(good, address="0x12")), "address"),
        _case("offline.signer", _code_of(pool.verify_statement, _tamper(good, signer="nope")), "signer"),
        _case("offline.time", _code_of(pool.verify_statement, _tamper(good, joined_at="yesterday")), "time"),
        _case("offline.message",
             _code_of(pool.verify_statement, _tamper(good, message=good["message"] + " ")), "message"),
        _case("offline.signature_format",
             _code_of(pool.verify_statement, _tamper(good, signature="0x" + "0" * 12)), "signature_format"),
        _case("offline.signature",
             _code_of(pool.verify_statement, _tamper(good, signature=stranger["signature"])), "signature"),
    ]

    members = work / "offline_members"
    members.mkdir(parents=True, exist_ok=True)
    wrong_name = members / ("0x" + "11" * 20 + ".json")
    wrong_name.write_text(pool.dump_statement_file(good), encoding="ascii")
    rows.append(_case("offline.filename", _code_of(pool.load_statement, str(wrong_name), "join"), "filename"))

    big = members / ("0x" + "22" * 20 + ".json")
    big.write_text(" " * (pool.MAX_FILE_BYTES + 1), encoding="ascii")
    rows.append(_case("offline.too_big", _code_of(pool.load_statement, str(big), "join"), "too_big"))

    junk = members / ("0x" + "33" * 20 + ".json")
    junk.write_bytes(b"\xff\xfe{}")
    rows.append(_case("offline.not_json", _code_of(pool.load_statement, str(junk), "join"), "not_json"))

    missing = members / ("0x" + "44" * 20 + ".json")
    rows.append(_case("offline.unreadable", _code_of(pool.load_statement, str(missing), "join"), "unreadable"))

    as_leave = pool.build_statement_file("leave", good["address"], good["signer"], now_iso, good["signature"])
    rows.append(_case("replay.join_signature_as_leave", _code_of(pool.verify_statement, as_leave), "signature"))
    return rows


# ── online (faked venue) rows ────────────────────────────────────────────────

def _online_rows(keys: _Keys, now: int, now_iso: str) -> List[Row]:
    rows: List[Row] = []

    old_at = pool.iso_from_ms(now - pool.FRESH_MAX_AGE_S * 1000 - 1000)
    acct = keys.new()
    stale_doc = keys.sign("join", acct, acct, old_at)
    v = pool.verify_statement(stale_doc)
    problems = approval.check_statement(v, pool_root="/nonexistent-pool-root", now_ms=now)
    rows.append(_case("online.stale", _problems_code(problems), "stale"))

    main2, agent2 = keys.new(), keys.new()
    join2 = keys.sign("join", main2, agent2, now_iso)
    v2 = pool.verify_statement(join2)

    def denying(payload: Dict[str, Any]) -> Any:
        return [] if payload["type"] == "extraAgents" else {"role": "user"}
    problems2 = approval.check_statement(v2, pool_root="/nonexistent-pool-root", now_ms=now, post=denying)
    rows.append(_case("online.agent_not_approved", _problems_code(problems2), "agent_not_approved"))

    main3, agent3 = keys.new(), keys.new()

    def expired(payload: Dict[str, Any]) -> Any:
        return [{"name": "p", "address": agent3.address.lower(), "validUntil": now - 1}]
    approved, reason = approval.check_agent(main3.address, agent3.address, now_ms=now, post=expired)
    rows.append(_case("online.expired_agent", "ok" if approved else reason, "expired"))

    main4, stranger4 = keys.new(), keys.new()
    leave4 = keys.sign("leave", main4, stranger4, now_iso)      # no join on file at all
    v4 = pool.verify_statement(leave4)
    problems4 = approval.check_statement(v4, pool_root="/nonexistent-pool-root", now_ms=now, post=denying)
    rows.append(_case("online.leave_signer", _problems_code(problems4), "leave_signer"))
    return rows


# ── pull-request-time rows: a real throwaway git repository ────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # Every GIT_* variable stripped first: GIT_DIR / GIT_INDEX_FILE override
    # `-C` outright, and this eval has run inside `git commit`'s own
    # pre-commit hook, whose environment carries GIT_INDEX_FILE for the REAL
    # repository's in-progress commit. Without stripping it, `git -C <this
    # throwaway repo> add -A` silently adds to the real commit's index
    # instead, corrupting it. Confirmed with GIT_DIR set: `git -C <throwaway>
    # rev-parse --show-toplevel` fails rather than naming the throwaway repo.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="pathiel-join-eval", GIT_AUTHOR_EMAIL="join-eval@pathiel.invalid",
              GIT_COMMITTER_NAME="pathiel-join-eval", GIT_COMMITTER_EMAIL="join-eval@pathiel.invalid")
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _rev(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _put(repo: Path, kind: str, doc: Dict[str, Any]) -> None:
    sub = pool.MEMBERS_SUBDIR if kind == "join" else pool.LEAVES_SUBDIR
    folder = repo / pool.POOL_DIR / sub
    folder.mkdir(parents=True, exist_ok=True)
    (folder / pool.statement_filename(doc["address"])).write_text(
        pool.dump_statement_file(doc), encoding="ascii")


def _rm(repo: Path, kind: str, address: str) -> None:
    sub = pool.MEMBERS_SUBDIR if kind == "join" else pool.LEAVES_SUBDIR
    (repo / pool.POOL_DIR / sub / pool.statement_filename(address)).unlink()


def _new_repo(work: Path, name: str) -> Path:
    r = work / name
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    (r / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    (r / pool.POOL_DIR / pool.LEAVES_SUBDIR).mkdir(parents=True)
    (r / pool.POOL_DIR / pool.MEMBERS_SUBDIR / ".gitkeep").write_text("")
    (r / pool.POOL_DIR / pool.LEAVES_SUBDIR / ".gitkeep").write_text("")
    _commit(r, "base")
    return r


def _verify_pr(repo: Path, ref: str, transcript: List[str]) -> Tuple[int, str]:
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = verify_cli.main(["verify", "--root", str(repo), "--pr-base", ref])
    out = buf.getvalue()
    transcript.append(out)
    transcript.append(err.getvalue())
    return code, out


def _pr_rows(keys: _Keys, work: Path, now: int, transcript: List[str]) -> List[Row]:
    rows: List[Row] = []
    iso = pool.iso_from_ms
    original_now_ms = verify_cli._now_ms
    verify_cli._now_ms = lambda: now
    try:
        # mixed_change: a membership pull request must touch pool files only
        acct = keys.new()
        repo = _new_repo(work, "pr_mixed")
        ref = _rev(repo)
        _put(repo, "join", keys.sign("join", acct, acct, iso(now - DAY_MS)))
        (repo / "unrelated.txt").write_text("x\n", encoding="ascii")
        _commit(repo, "mixed")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.mixed_change", "mixed_change" if "mixed_change" in out else f"exit={code}",
                        "mixed_change", code == 1 and "mixed_change" in out))

        # too_many: at most two files, one address
        a, b = keys.new(), keys.new()
        repo = _new_repo(work, "pr_too_many")
        ref = _rev(repo)
        _put(repo, "join", keys.sign("join", a, a, iso(now - DAY_MS)))
        _put(repo, "join", keys.sign("join", b, b, iso(now - DAY_MS)))
        _commit(repo, "two addresses")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.too_many", "too_many" if "too_many" in out else f"exit={code}",
                        "too_many", code == 1 and "too_many" in out))

        # reinstated (a): the review finding, a deleted leave while the
        # member file stays would put the old join back in force
        acct = keys.new()
        t_join, t_leave = iso(now - 3 * DAY_MS), iso(now - 2 * DAY_MS)
        repo = _new_repo(work, "pr_reinstated_a")
        _put(repo, "join", keys.sign("join", acct, acct, t_join))
        _put(repo, "leave", keys.sign("leave", acct, acct, t_leave))
        _commit(repo, "join then leave")
        ref = _rev(repo)
        _rm(repo, "leave", acct.address.lower())
        _commit(repo, "delete only the leave")
        scanned = pool.scan(str(repo / pool.POOL_DIR), now)
        would_reinstate = any(m["address"] == acct.address.lower() and m["count_from"] == t_join
                              for m in scanned["members"])
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.reinstated.a_deleted_leave", "reinstated" if "reinstated" in out else f"exit={code}",
                        "reinstated", code == 1 and "reinstated" in out and would_reinstate))

        # reinstated (b): the leave replaced by an older one, dated before the join
        acct = keys.new()
        t_join, t_leave_real = iso(now - 3 * DAY_MS), iso(now - 2 * DAY_MS)
        t_leave_older = iso(now - 6 * DAY_MS)
        repo = _new_repo(work, "pr_reinstated_b")
        _put(repo, "join", keys.sign("join", acct, acct, t_join))
        _put(repo, "leave", keys.sign("leave", acct, acct, t_leave_real))
        _commit(repo, "join then leave")
        ref = _rev(repo)
        _put(repo, "leave", keys.sign("leave", acct, acct, t_leave_older))
        _commit(repo, "swap in an older leave")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.reinstated.b_older_leave", "reinstated" if "reinstated" in out else f"exit={code}",
                        "reinstated", code == 1 and "reinstated" in out))

        # reinstated (c): a removed member file put back with the same content
        acct = keys.new()
        doc = keys.sign("join", acct, acct, iso(now - 2 * DAY_MS))
        repo = _new_repo(work, "pr_reinstated_c")
        _put(repo, "join", doc)
        _commit(repo, "join")
        _rm(repo, "join", acct.address.lower())
        _commit(repo, "remove")
        ref = _rev(repo)
        _put(repo, "join", doc)
        _commit(repo, "put the same statement back")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.reinstated.c_replayed_from_history",
                        "reinstated" if "reinstated" in out else f"exit={code}", "reinstated",
                        code == 1 and "reinstated" in out))

        # reinstated (d): the member file swapped for an earlier join
        acct = keys.new()
        repo = _new_repo(work, "pr_reinstated_d")
        _put(repo, "join", keys.sign("join", acct, acct, iso(now - 3 * DAY_MS)))
        _commit(repo, "join")
        ref = _rev(repo)
        _put(repo, "join", keys.sign("join", acct, acct, iso(now - 5 * DAY_MS)))   # earlier
        _commit(repo, "swap for an earlier join")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.reinstated.d_earlier_join",
                        "reinstated" if "reinstated" in out else f"exit={code}", "reinstated",
                        code == 1 and "reinstated" in out))

        # allowed: a leave deleted together with its member file
        acct = keys.new()
        repo = _new_repo(work, "pr_allowed_both_deleted")
        _put(repo, "join", keys.sign("join", acct, acct, iso(now - 3 * DAY_MS)))
        _put(repo, "leave", keys.sign("leave", acct, acct, iso(now - 2 * DAY_MS)))
        _commit(repo, "join then leave")
        ref = _rev(repo)
        _rm(repo, "join", acct.address.lower())
        _rm(repo, "leave", acct.address.lower())
        _commit(repo, "remove both")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.allowed.leave_and_member_deleted", f"exit={code}", "exit=0",
                        code == 0 and "reinstated" not in out))

        # allowed: a rejoin, deleting the old leave
        acct = keys.new()
        repo = _new_repo(work, "pr_allowed_rejoin")
        _put(repo, "join", keys.sign("join", acct, acct, iso(now - 3 * DAY_MS)))
        _put(repo, "leave", keys.sign("leave", acct, acct, iso(now - 2 * DAY_MS)))
        _commit(repo, "join then leave")
        ref = _rev(repo)
        _rm(repo, "leave", acct.address.lower())
        _put(repo, "join", keys.sign("join", acct, acct, iso(now)))
        _commit(repo, "rejoin")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.allowed.rejoin", f"exit={code}", "exit=0", code == 0 and "reinstated" not in out))

        # allowed: a first join
        acct = keys.new()
        repo = _new_repo(work, "pr_allowed_first_join")
        ref = _rev(repo)
        _put(repo, "join", keys.sign("join", acct, acct, iso(now)))
        _commit(repo, "first join")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.allowed.first_join", f"exit={code}", "exit=0", code == 0 and "reinstated" not in out))

        # no membership change at all: fast, and must never read the venue
        repo = _new_repo(work, "pr_no_change")
        ref = _rev(repo)
        (repo / "README.md").write_text("x\n", encoding="ascii")
        _commit(repo, "unrelated")
        code, out = _verify_pr(repo, ref, transcript)
        rows.append(_row("pr.no_membership_change", f"exit={code}", "exit=0",
                        code == 0 and "no membership change" in out))
    finally:
        verify_cli._now_ms = original_now_ms
    return rows


# ── the run ──────────────────────────────────────────────────────────────────

def run() -> Tuple[List[Row], bool, List[str]]:
    """(rows, no_key_leaked, transcript). Deterministic: no seed to vary,
    since every input here is either fixed or a fresh throwaway key whose
    hex never appears in a row's `got`/`want`."""
    keys = _Keys()
    now = int(time.time() * 1000)
    now_iso = pool.iso_from_ms(now)
    transcript: List[str] = []
    with tempfile.TemporaryDirectory(prefix="pathiel-join-eval-") as tmp:
        work = Path(tmp)
        rows = (_valid_rows(keys, now, now_iso) + _offline_rows(keys, now_iso, work)
               + _online_rows(keys, now, now_iso) + _pr_rows(keys, work, now, transcript))
        file_texts = []
        for p in work.rglob("*"):
            if p.is_file():
                try:
                    file_texts.append(p.read_bytes().decode("utf-8", errors="replace"))
                except OSError:
                    pass
        haystack = "\n".join(transcript + file_texts + [str(r) for r in rows]).lower()
    no_leak = not any(h and h in haystack for h in keys.hexes)
    rows.append(_row("no_key_leak", "clean" if no_leak else "LEAK", "clean", no_leak))
    return rows, no_leak, transcript


def main(argv: Optional[List[str]] = None) -> int:
    rows, _no_leak, _transcript = run()
    width = max(len(r["check"]) for r in rows)
    for r in rows:
        mark = "ok" if r["ok"] else "FAIL"
        print(f"{r['check']:<{width}}  got={r['got']!s:<24}  want={r['want']!s:<20}  {mark}")
    passed = sum(1 for r in rows if r["ok"])
    print(f"JOIN-EVAL passed={passed}/{len(rows)}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
