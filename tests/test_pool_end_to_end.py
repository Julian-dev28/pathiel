"""The research pool, start to finish, through the real production code
every unit built: the MCP tool handlers (join_tools, report_tool), the CLI
(verify_cli, build_cli), and a throwaway git repository standing in for the
real pathiel checkout. Offline (a fake venue stands in for the network; the
root conftest's socket block proves nothing slips past it), a throwaway
eth_account key (exists only in this process), a temp state dir and a temp
repo root (PATHIEL_POOL_REPO_ROOT).

Only files named tests/test_pool_* may import contracts.pool
(contracts/pool/tests/test_thesis_boundary.py enforces it).

Three scenarios:

  1. The membership lifecycle: join (dry run, then confirm) through
     join_tools.handle_pool_join; the signed file copied into the repo and
     committed, the way a merged pull request would leave it; an offline
     scoreboard build before and after, proving the POOL-NEW line and the
     24 hour delay math via a synthetic bracket trade
     (services/pool/tests/pool_fakes.py); report_tool.handle_pool_report
     while a member; leave (dry run, then confirm) through
     handle_pool_leave, committed the same way; a third build that drops
     the row, deletes the ledger file, and counts the leave.
  2. The review finding (2026-11-12): a pull request that only deletes
     pool/leaves/<address>.json, with the member file unchanged, would put
     the account back in the pool from its OLD joined_at with no new
     consent. python -m services.pool verify --pr-base refuses it (code
     reinstated).
  3. The way back in that IS allowed: a new, later join, signed the same
     way, with the old leave file left in place. The same check passes it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

import contracts.pool as pool
from services.pool import account as account_module
from services.pool import build_cli, fetch, join, join_tools, paths, report_tool, venue, verify_cli
from services.pool.tests import pool_fakes as PF


# ── git, the same hardened pattern services/pool/tests/test_verify_cli.py
# uses: every GIT_* variable stripped before -C, because a process invoked
# from inside git's own pre-commit hook (this gate's own commit step) would
# otherwise inherit GIT_INDEX_FILE and silently operate on the REAL repo's
# in-progress index instead of this throwaway one. ───────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="pathiel-test", GIT_AUTHOR_EMAIL="test@pathiel.invalid",
              GIT_COMMITTER_NAME="pathiel-test", GIT_COMMITTER_EMAIL="test@pathiel.invalid")
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res


def _rev(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _rev(repo)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / pool.POOL_DIR / pool.MEMBERS_SUBDIR).mkdir(parents=True)
    (repo / pool.POOL_DIR / pool.LEAVES_SUBDIR).mkdir(parents=True)
    (repo / pool.POOL_DIR / pool.MEMBERS_SUBDIR / ".gitkeep").write_text("")
    (repo / pool.POOL_DIR / pool.LEAVES_SUBDIR / ".gitkeep").write_text("")
    (repo / pool.SCOREBOARD_DIR).mkdir(parents=True)
    _commit(repo, "base: empty pool")


# ── offline venue: no fills, no orders, no agents -- every request this
# flow could make is covered; anything else is a bug in the test, not a
# silent network read. ───────────────────────────────────────────────────────

def _fake_post_info(payload: Dict[str, Any]) -> Any:
    t = payload.get("type")
    if t == "userFillsByTime":
        return []
    if t == "historicalOrders":
        return []
    if t == "extraAgents":
        return []
    if t == "userRole":
        return {"role": "missing"}
    raise AssertionError(f"this flow should never make this venue request: {payload}")


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "pathiel_checkout"
    _init_repo(r)
    return r


@pytest.fixture
def new_account():
    """A fresh throwaway eth_account account per call. Its key exists only
    in this test process (services/pool/tests/conftest.py's own fixture of
    the same name, reimplemented here: tests/ and services/pool/tests/ are
    different conftest scopes)."""
    from eth_account import Account
    return Account.create


@pytest.fixture(autouse=True)
def pool_offline_env(tmp_path, repo, monkeypatch):
    """Mainnet (the pool is mainnet only), a throwaway state dir, the
    throwaway repo as PATHIEL_POOL_REPO_ROOT, no key in the environment (the
    identity comes from the _resolve_identity monkeypatch in each test),
    and the venue nailed shut."""
    monkeypatch.setenv("PATHIEL_NETWORK", "mainnet")
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PATHIEL_POOL_REPO_ROOT", str(repo))
    for key in ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_PRIVATE_KEY",
               "HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_MASTER_ADDRESS"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(venue, "post_info", _fake_post_info)
    # report.personal()'s live path (no bundle) calls fetch.load_candles ->
    # services.strategies.backtest.data.fetch_candles_cached, which would
    # otherwise try the real network for every pool coin with no cache file
    # yet, hitting (and waiting out) the conftest socket block. Nothing in
    # this flow needs real candles: the member report assertions are about
    # trade counts and state, not slice stats.
    monkeypatch.setattr(fetch, "load_candles", lambda cache_dir, fetch=None: ({}, {}))


def _configure_identity(monkeypatch, acct) -> None:
    """The throwaway key never leaves this function: services.pool.signing
    reads it through _resolve_identity, uses it for one signature, and
    drops it, exactly as it would a real master key."""
    import pathiel.client.exchange as exchange
    key = acct.key.hex()
    monkeypatch.setattr(exchange, "_resolve_identity", lambda: (key, acct.address, None, False))


def _save_empty_bundle(path: Path, as_of_ms: int) -> None:
    fetch.save_bundle(str(path), {"as_of_ms": as_of_ms, "inputs": {}, "bars5": {}, "bars1h": {}})


def _no_key_leak(key_hex: str, *texts: str) -> None:
    needles = (key_hex, key_hex.removeprefix("0x"), "0x" + key_hex.removeprefix("0x"))
    for text in texts:
        low = text.lower()
        for needle in needles:
            assert needle.lower() not in low, f"the test key leaked: {needle[:10]}..."


def _repo_file_texts(repo_root: Path) -> List[str]:
    out = []
    for p in repo_root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            try:
                out.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return out


def _state_file_texts(state_root: Path) -> List[str]:
    out = []
    if state_root.is_dir():
        for p in state_root.rglob("*"):
            if p.is_file():
                try:
                    out.append(p.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    return out


# ── scenario 1: the full membership lifecycle ───────────────────────────────

def test_the_full_membership_lifecycle_through_the_real_mcp_tools(
        repo, tmp_path, monkeypatch, new_account, capsys):
    acct = new_account()
    _configure_identity(monkeypatch, acct)
    address = acct.address.lower()
    key_hex = acct.key.hex()
    captured_outputs: List[str] = []

    # -- build 0: an empty pool, so the pull request's member later shows as
    # NEW (scoreboard.new_rows needs a valid PREVIOUS board to compare against;
    # with none, nothing is ever "new", which is the first thing this
    # end-to-end flow has to get right, not just assume). ---------------------
    build0_dir = tmp_path / "bundle-0"
    _save_empty_bundle(build0_dir, int(time.time() * 1000))
    rc0 = build_cli.main(["build", "--offline", str(build0_dir)])
    assert rc0 == 0
    board0 = json.loads((repo / pool.SCOREBOARD_DIR / pool.SCOREBOARD_JSON).read_text())
    assert pool.validate_scoreboard(board0) == [] and board0["members"] == []

    # -- join: dry run, then confirm, exactly as /pathiel:share walks a user
    # through it. -------------------------------------------------------------
    dry = json.loads(join_tools.handle_pool_join({}))
    captured_outputs.append(json.dumps(dry))
    assert dry["ok"] is True and dry["signed"] is False
    assert dry["account"] == dry["signer"] == address
    assert dry["message"] == pool.statement_text("join", address, address, dry["joined_at"])

    confirmed = json.loads(join_tools.handle_pool_join({"confirm": True, "joined_at": dry["joined_at"]}))
    captured_outputs.append(json.dumps(confirmed))
    assert confirmed["ok"] is True and confirmed["signed"] is True
    join_ms = pool.ms_from_iso(confirmed["joined_at"])
    outbox_join_file = Path(confirmed["file"])
    assert outbox_join_file.is_file()
    assert "pr_url" in confirmed

    # -- the pull request: copy the outbox file into the repo exactly where
    # it names, and commit it, standing in for a merge. ----------------------
    repo_join_path = repo / confirmed["repo_path"]
    repo_join_path.parent.mkdir(parents=True, exist_ok=True)
    repo_join_path.write_text(outbox_join_file.read_text(encoding="ascii"), encoding="ascii")
    _commit(repo, "pool: add member join")

    # -- verify, offline: the merged tree is clean. ---------------------------
    assert verify_cli.main(["verify"]) == 0

    # -- build 1: the member is active now, and has one closed bracket trade
    # on the pool's own coins (services/pool/tests/pool_fakes.py), entered
    # after they joined and comfortably inside the 24 hour delay window. -----
    coin = pool.POOL_COINS[0]
    entry_ms = join_ms + 3_600_000
    hold_ms = 2 * 3_600_000
    exit_ms = entry_ms + hold_ms
    build1_ms = exit_ms + 2 * pool.DAY_MS   # 2 days past exit: well past the 24h/48h delay window
    fills, orders = PF.bracket_trade(coin, "long", 80.0, 78.0, 10.0, entry_ms,
                                     target_px=84.0, exit_kind="stop", hold_ms=hold_ms)
    build1_dir = tmp_path / "bundle-1"
    fetch.save_bundle(str(build1_dir), {
        "as_of_ms": build1_ms, "inputs": {address: {"fills": fills, "orders": orders}},
        "bars5": {}, "bars1h": {},
    })
    rc1 = build_cli.main(["build", "--offline", str(build1_dir)])
    out1 = capsys.readouterr()
    captured_outputs.append(out1.out)
    captured_outputs.append(out1.err)
    assert rc1 == 0
    assert f"POOL-NEW {pool.short_address(address)}" in out1.err, out1.err

    board1 = json.loads((repo / pool.SCOREBOARD_DIR / pool.SCOREBOARD_JSON).read_text())
    assert pool.validate_scoreboard(board1) == []
    rows = {r["address"]: r for r in board1["members"]}
    assert address in rows, board1["members"]
    assert rows[address]["closed_trades"] == 1
    assert rows[address]["since"] == pool.day_of(join_ms)
    ledger_file = Path(paths.ledger_path(address))
    assert ledger_file.is_file(), "the build must have written a ledger file for an active member"

    # -- the member's own report: right now, not delayed, never published.
    # handle_pool_report takes no bundle, so it reads the (faked, empty)
    # live venue directly -- a separate read from the offline bundle the
    # scoreboard build above used. It legitimately sees 0 trades right now;
    # the point of this step is that it answers ok at all, as a member,
    # with no traceback, not that it repeats the build's own numbers. ------
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", address)
    monkeypatch.setattr(account_module, "configured_account", lambda: address)
    reported = json.loads(report_tool.handle_pool_report({}))
    captured_outputs.append(json.dumps(reported))
    assert reported["ok"] is True, reported
    assert reported["state"] == "member"
    assert reported["closed_trades"] == 0
    assert "Nothing in this report is published by this call" in reported["markdown"]

    # -- leave: dry run, then confirm. ----------------------------------------
    dry_leave = json.loads(join_tools.handle_pool_leave({}))
    captured_outputs.append(json.dumps(dry_leave))
    assert dry_leave["ok"] is True and dry_leave["signed"] is False
    confirmed_leave = json.loads(
        join_tools.handle_pool_leave({"confirm": True, "left_at": dry_leave["left_at"]}))
    captured_outputs.append(json.dumps(confirmed_leave))
    assert confirmed_leave["ok"] is True and confirmed_leave["signed"] is True
    leave_ms = pool.ms_from_iso(confirmed_leave["left_at"])
    assert leave_ms >= join_ms

    outbox_leave_file = Path(confirmed_leave["file"])
    repo_leave_path = repo / confirmed_leave["repo_path"]
    repo_leave_path.parent.mkdir(parents=True, exist_ok=True)
    repo_leave_path.write_text(outbox_leave_file.read_text(encoding="ascii"), encoding="ascii")
    _commit(repo, "pool: add member leave")
    assert verify_cli.main(["verify"]) == 0

    # -- build 2: the row is gone, the ledger file is deleted, counts.left
    # is 1. -------------------------------------------------------------------
    build2_ms = leave_ms + pool.DAY_MS
    rc2 = build_cli.main(["build", "--offline", str(build1_dir), "--as-of", pool.iso_from_ms(build2_ms)])
    out2 = capsys.readouterr()
    captured_outputs.append(out2.out)
    captured_outputs.append(out2.err)
    assert rc2 == 0
    board2 = json.loads((repo / pool.SCOREBOARD_DIR / pool.SCOREBOARD_JSON).read_text())
    assert pool.validate_scoreboard(board2) == []
    assert address not in {r["address"] for r in board2["members"]}
    assert board2["counts"]["left"] == 1
    assert not ledger_file.exists(), "a leave must purge the member's ledger file"

    # -- the key appears nowhere: not in any tool output, not in any file
    # under the repo or the state dir. ----------------------------------------
    state_root = tmp_path / "state"
    _no_key_leak(key_hex, *captured_outputs, *_repo_file_texts(repo), *_state_file_texts(state_root))


# ── scenario 2: the review finding, end to end ──────────────────────────────

def test_the_reinstated_hole_the_pull_request_check_closes(repo, monkeypatch, new_account):
    """A pull request that only deletes pool/leaves/<address>.json, with the
    member file untouched, must not bring the account back from its OLD
    joined_at. This is the exact hole contracts.pool.reinstatement_problem
    exists to close, proven here on a repo whose join and leave statements
    were produced by the real join.confirm, not a hand-built dict."""
    acct = new_account()
    _configure_identity(monkeypatch, acct)
    address = acct.address.lower()
    key_hex = acct.key.hex()

    join_at_ms = pool.ms_from_iso("2026-11-10T09:00:00Z")
    leave_at_ms = pool.ms_from_iso("2026-11-11T09:00:00Z")
    join_result = join.confirm("join", pool.iso_from_ms(join_at_ms), now_ms=join_at_ms)
    _copy_outbox_to_repo(repo, join_result)
    leave_result = join.confirm("leave", pool.iso_from_ms(leave_at_ms), now_ms=leave_at_ms)
    _copy_outbox_to_repo(repo, leave_result)
    base_ref = _commit(repo, "pool: join then leave")

    scanned_at_base = pool.scan(str(repo / pool.POOL_DIR), leave_at_ms + 1)
    assert scanned_at_base["left"] == [address]

    (repo / pool.POOL_DIR / pool.LEAVES_SUBDIR / pool.statement_filename(address)).unlink()
    _commit(repo, "delete the leave file only")

    # A plain scan of the resulting tree WOULD treat the account as a member
    # again, from the OLD joined_at: this is the hole the check closes, not
    # something the files alone would ever refuse.
    scanned_after_delete = pool.scan(str(repo / pool.POOL_DIR), join_at_ms + 10 ** 7)
    assert [m["address"] for m in scanned_after_delete["members"]] == [address]
    assert scanned_after_delete["members"][0]["count_from"] == pool.iso_from_ms(join_at_ms)

    def boom(payload):
        raise AssertionError("a master-signed join/leave needs no venue read")
    monkeypatch.setattr(venue, "post_info", boom)
    monkeypatch.setattr(verify_cli, "_now_ms", lambda: leave_at_ms + 2 * pool.DAY_MS)

    code = verify_cli.main(["verify", "--root", str(repo), "--pr-base", base_ref])
    assert code == 1

    _no_key_leak(key_hex, *_repo_file_texts(repo))


def _copy_outbox_to_repo(repo: Path, confirm_result: Dict[str, Any]) -> None:
    out_file = Path(confirm_result["file"])
    repo_path = repo / confirm_result["repo_path"]
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    repo_path.write_text(out_file.read_text(encoding="ascii"), encoding="ascii")


# ── scenario 3: the way back in that IS allowed ─────────────────────────────

def test_a_later_rejoin_is_accepted_by_the_pull_request_check(repo, monkeypatch, new_account):
    """The old leave file stays exactly where it was; only a NEW join, dated
    after everything the branch ever held for this address, brings the
    account back. That is the one way back in, and the check must let it
    through."""
    acct = new_account()
    _configure_identity(monkeypatch, acct)
    address = acct.address.lower()
    key_hex = acct.key.hex()

    join_at_ms = pool.ms_from_iso("2026-11-10T09:00:00Z")
    leave_at_ms = pool.ms_from_iso("2026-11-11T09:00:00Z")
    _copy_outbox_to_repo(repo, join.confirm("join", pool.iso_from_ms(join_at_ms), now_ms=join_at_ms))
    _copy_outbox_to_repo(repo, join.confirm("leave", pool.iso_from_ms(leave_at_ms), now_ms=leave_at_ms))
    base_ref = _commit(repo, "pool: join then leave")

    rejoin_at_ms = leave_at_ms + 2 * pool.DAY_MS
    rejoin_result = join.confirm("join", pool.iso_from_ms(rejoin_at_ms), now_ms=rejoin_at_ms)
    _copy_outbox_to_repo(repo, rejoin_result)   # overwrites pool/members/<addr>.json; the leave file stays
    _commit(repo, "pool: rejoin")

    def boom(payload):
        raise AssertionError("a master-signed join/leave needs no venue read")
    monkeypatch.setattr(venue, "post_info", boom)
    monkeypatch.setattr(verify_cli, "_now_ms", lambda: rejoin_at_ms)   # the new join is fresh right now

    code = verify_cli.main(["verify", "--root", str(repo), "--pr-base", base_ref])
    assert code == 0

    after = pool.membership(str(repo / pool.POOL_DIR), address)
    assert after["state"] == "member" and after["count_from_ms"] == rejoin_at_ms

    _no_key_leak(key_hex, *_repo_file_texts(repo))
