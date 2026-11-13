"""Membership: show the join or leave statement, sign it, write the file a
pull request carries, and say where this machine's account stands.

Three verbs, each pure about refusals and side-effecting only where named:

  prepare(kind)   shows the statement. Signs nothing, writes nothing.
  confirm(kind, at)   signs the statement `prepare` showed and writes the
                  outbox file a pull request would add to pool/.
  status()        this account's standing and the pool's published counts.

Every refusal is a contracts.pool.PoolError with a short, stable `code`
(see each function's docstring for its set). The key is never read here
directly: it is read, used and dropped inside services.pool.signing.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Optional, Tuple

import contracts.pool as pool
from contracts import runtime
from services.pool import account as account_module
from services.pool import paths, signing

LEAVE_EFFECT = ("The next scoreboard build drops this address and its graded results. "
               "Coming back takes a new signed join.")


def _now(now_ms: Optional[int]) -> int:
    return int(now_ms) if now_ms is not None else int(time.time() * 1000)


def _check_standing(kind: str, account: str, standing: pool.Standing) -> None:
    """The refusals `pool.local_standing` decides, for either verb:
    'invalid_local_files' for state invalid (lists the problems); for a join,
    'house' for the house row and 'already_joined' (member, signed_not_merged);
    for a leave, 'not_member' (none, left)."""
    if standing["state"] == "invalid":
        detail = "; ".join(f"{p['path']}: {p['code']}" for p in standing["problems"])
        raise pool.PoolError("invalid_local_files",
                             f"local pool files for {pool.short_address(account)} do not verify: {detail}")
    if kind == "join":
        if standing["state"] == "house":
            raise pool.PoolError("house", f"{pool.short_address(account)} is the project's house row "
                                 "already; it is graded from its own file and does not join")
        if standing["state"] in ("member", "signed_not_merged"):
            joined = standing["join"]["at"] if standing["join"] else "an earlier time"
            raise pool.PoolError("already_joined", f"{pool.short_address(account)} already joined "
                                 f"at {joined}")
    else:
        if standing["state"] in ("none", "left"):
            raise pool.PoolError("not_member", f"{pool.short_address(account)} is not in the research "
                                 "pool; there is nothing to leave")


def _identity_and_standing(kind: str, master_prompt: bool) -> Tuple[str, str, str, pool.Standing]:
    """(account, signer, signer_role, standing), or the refusal:
    'testnet' off mainnet; 'no_key' with the configured key and
    master_prompt=False (signing.NoKey wrapped, never raised bare: one
    exception type for every caller to catch); 'no_account' with
    master_prompt=True and no account configured; then whatever
    `_check_standing` decides."""
    if kind not in ("join", "leave"):
        raise pool.PoolError("kind", f"kind must be join or leave, got {kind!r}")
    if runtime.network() != "mainnet":
        raise pool.PoolError("testnet", "the research pool is mainnet only; this process is set to "
                             "testnet (PATHIEL_NETWORK)")
    if master_prompt:
        account = account_module.configured_account()
        if not account:
            raise pool.PoolError("no_account", "no account is configured (HYPERLIQUID_MASTER_ADDRESS "
                                 "or HYPERLIQUID_WALLET_ADDRESS)")
        signer = account
    else:
        try:
            account, signer = signing.identity()
        except signing.NoKey as e:
            raise pool.PoolError("no_key", str(e)) from e
    signer_role = "master" if signer == account else "agent"
    standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), account)
    _check_standing(kind, account, standing)
    return account, signer, signer_role, standing


def prepare(kind: str, *, now_ms: Optional[int] = None, master_prompt: bool = False) -> Dict[str, Any]:
    """Show the statement `confirm` would sign. Signs nothing, writes
    nothing. Refusals: see `_identity_and_standing`."""
    now = _now(now_ms)
    account, signer, signer_role, _standing = _identity_and_standing(kind, master_prompt)
    at = pool.iso_from_ms(now)
    message = pool.statement_text(kind, account, signer, at)
    time_key = "joined_at" if kind == "join" else "left_at"
    out: Dict[str, Any] = {
        "kind": kind, "account": account, "signer": signer, "signer_role": signer_role,
        time_key: at, "message": message, "statement_sha256": pool.statement_sha256(message),
    }
    if kind == "join":
        out["published"] = list(pool.PUBLISHED)
        out["not_published"] = list(pool.NOT_PUBLISHED)
    else:
        out["effect"] = LEAVE_EFFECT
    return out


def _atomic_write(path: str, content: str) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-pool-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def confirm(kind: str, at: str, *, now_ms: Optional[int] = None, master_prompt: bool = False,
           getpass_fn=None, isatty=None) -> Dict[str, Any]:
    """Sign the statement `prepare` showed for time `at` and write the
    outbox file a pull request would add under pool/. Same refusals as
    `prepare`, recomputed (an account's standing can change between the two
    calls), plus: 'expired' when `at` is no longer within
    CONFIRM_MAX_AGE_S of now (or more than two minutes ahead of it): the
    shown time stays signable only that long, so a stale dry run cannot be
    replayed into a fresh signature; 'signer_changed' when the key that
    actually signs is not the one `prepare` showed (the configured key
    changed, or the typed master key does not match what `identity()` or
    `account_module.configured_account()` resolved just now)."""
    now = _now(now_ms)
    account, signer, signer_role, _standing = _identity_and_standing(kind, master_prompt)
    at_ms = pool.ms_from_iso(at)
    if not (now - pool.CONFIRM_MAX_AGE_S * 1000 <= at_ms <= now + 120_000):
        raise pool.PoolError("expired", "that statement time is no longer signable: run it again "
                             "without confirm to get a fresh one")
    message = pool.statement_text(kind, account, signer, at)
    if master_prompt:
        signed_signer, signature = signing.sign_prompted(message, account, getpass_fn=getpass_fn,
                                                          isatty=isatty)
    else:
        signed_signer, signature = signing.sign_configured(message)
    if signed_signer != signer:
        raise pool.PoolError("signer_changed", f"the key that just signed ({pool.short_address(signed_signer)}) "
                             f"is not the one shown ({pool.short_address(signer)}): run it again")

    doc = pool.build_statement_file(kind, account, signed_signer, at, signature)
    pool.verify_statement(doc, kind=kind)      # self-check: never write what does not verify
    content = pool.dump_statement_file(doc)

    if kind == "join":
        out_path = pool.member_path(paths.outbox_root(), account)
        repo_file = pool.member_path(paths.repo_pool_root(), account)
        repo_path = pool.member_repo_path(account)
    else:
        out_path = pool.leave_path(paths.outbox_root(), account)
        repo_file = pool.leave_path(paths.repo_pool_root(), account)
        repo_path = pool.leave_repo_path(account)
    _atomic_write(out_path, content)

    time_key = "joined_at" if kind == "join" else "left_at"
    result: Dict[str, Any] = {
        "kind": kind, time_key: at, "file": out_path, "repo_path": repo_path, "content": content,
        "statement_sha256": pool.statement_sha256(message), "signer_role": signer_role,
    }
    if os.path.exists(repo_file):
        # A rejoin, or a second leave: GitHub's prefilled new-file link works
        # for a file that does not exist yet. The one that is already there
        # is edited, never replaced by deleting it first: deleting the old
        # leave file while the member file stays is exactly what
        # contracts.pool.reinstatement_problem exists to refuse at the pull
        # request, and a rejoin is always a NEW join with a NEW time, not a
        # resurrected old one.
        result["pr_url"] = f"https://github.com/{pool.REPO_SLUG}/edit/{pool.DEFAULT_BRANCH}/{repo_path}"
        result["pr_note"] = (f"{repo_path} already exists on {pool.DEFAULT_BRANCH}. Open that link and "
                             "replace the file's whole content with the `content` this call returned, "
                             "then open the pull request from there. Never delete the existing file "
                             "and add a new one in its place.")
    else:
        result["pr_url"] = pool.pr_new_file_url(repo_path, content)
    return result


def status() -> Dict[str, Any]:
    """This machine's view: the configured account's standing (local files
    only: the repo copy of pool/, which is stale until the next pull or
    plugin update, and the outbox of statements signed here) and the
    published scoreboard's counts, when one is shipped and valid. A missing
    pool/ or scoreboard is not an error."""
    network = runtime.network()
    account = account_module.configured_account()
    out: Dict[str, Any] = {
        "ok": True, "network": network,
        "account": pool.short_address(account) if account else None,
        "state": "no_account", "joined_at": None, "left_at": None,
        "published": list(pool.PUBLISHED), "not_published": list(pool.NOT_PUBLISHED),
        "pool": None, "scoreboard_url": pool.SCOREBOARD_URL,
        "how_to_join": "/pathiel:share", "how_to_leave": "/pathiel:share leave, or ask the maintainer in an issue",
        "copy_note": ("membership is read from this install's copy of the repository and from "
                      "statements signed on this machine; a merged pull request shows after the "
                      "next plugin update; the live list is "
                      "https://github.com/Julian-dev28/pathiel/tree/main/pool/members"),
        "problems": [],
    }
    if account:
        standing = pool.local_standing(paths.repo_pool_root(), paths.outbox_root(), account)
        out["state"] = standing["state"]
        out["joined_at"] = standing["join"]["at"] if standing["join"] else None
        out["left_at"] = standing["leave"]["at"] if standing["leave"] else None
        out["problems"] = standing["problems"]
    try:
        with open(paths.scoreboard_json(), "r", encoding="ascii") as fh:
            doc = json.load(fh)
        if pool.validate_scoreboard(doc) == []:
            out["pool"] = {"day": doc["day"], "cutoff": doc["cutoff"], "counts": doc["counts"]}
    except (OSError, ValueError):
        pass
    return out


def master_command(kind: str) -> str:
    """The line a user pastes into their OWN terminal for the hidden master
    key prompt (join --master-prompt / leave --master-prompt). It must match
    the launcher's own paste-ready commands (scripts/_plugin_env.sh
    pathiel_print_commands): on a plugin install the three variables
    CLAUDE_PLUGIN_DATA, PATHIEL_HOME and PATHIEL_PYTHON are set the same way,
    so the command finds the same venv the launcher built instead of
    building (or finding the wrong) one. Without PATHIEL_ROOT (a source
    checkout, not a plugin install) it uses the running interpreter, never
    a bare 'python3': a plain terminal's python3 can resolve to a different,
    too-old interpreter (macOS ships 3.9 at /usr/bin/python3)."""
    import shlex
    import sys
    root = os.environ.get("PATHIEL_ROOT")
    if root:
        data = os.environ.get("PATHIEL_DATA", "")
        home = os.environ.get("PATHIEL_HOME", "")
        venv = os.environ.get("PATHIEL_VENV") or f"{data}/venv"
        python = f"{venv}/bin/python"
        script = f"{root}/scripts/pathiel-py"
        tokens = ["env", f"CLAUDE_PLUGIN_DATA={data}", f"PATHIEL_HOME={home}", f"PATHIEL_PYTHON={python}",
                 script, "-m", "services.pool", kind, "--master-prompt", "--yes"]
        return " ".join(shlex.quote(t) for t in tokens)
    code_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f"cd {shlex.quote(code_root)} && {shlex.quote(sys.executable)} -m services.pool {kind} --master-prompt --yes"
