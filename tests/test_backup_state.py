"""The state backup: does it capture the irreplaceable, and can it be restored?

Code is in git, logs rotate, prices refetch. Three things cannot be recreated:
.agent-memory.json (trades and closes), .state/shadow_ledger/ (every signal
every book ever recorded — the evidence under every VALIDATED and REFUTED
verdict) and .state/capital_flows.jsonl (without which drawdown is not
flow-neutral). All gitignored, all on one laptop, none had a copy.

The two ways a backup is worthless: it does not contain what you needed, or it
does not read back. Both are tested here. The third way it is worse than
worthless — containing the private keys — is tested hardest.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tarfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        "backup_state", os.path.join(ROOT, "scripts", "backup_state.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


B = _load()


@pytest.fixture
def tree(tmp_path):
    """A miniature of the real layout, secrets included."""
    (tmp_path / ".state" / "shadow_ledger").mkdir(parents=True)
    (tmp_path / ".agent-memory.json").write_text(json.dumps({"trades": [1, 2]}))
    (tmp_path / ".state" / "capital_flows.jsonl").write_text('{"usd": 24.46}\n')
    (tmp_path / ".state" / "shadow_ledger" / "unlock_short.jsonl").write_text(
        '{"coin": "ETH"}\n' * 50)
    (tmp_path / ".env.local").write_text("HYPERLIQUID_PRIVATE_KEY=0xdeadbeef\n")
    (tmp_path / ".state" / "signing.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    return tmp_path


# ── what it must contain ─────────────────────────────────────────────────────

def test_it_captures_everything_that_cannot_be_recreated(tree):
    files, missing = B.collect(str(tree))
    names = {os.path.relpath(f, tree) for f in files}
    assert ".agent-memory.json" in names
    assert ".state/capital_flows.jsonl" in names
    assert ".state/shadow_ledger/unlock_short.jsonl" in names
    assert not missing


def test_a_missing_source_is_reported_not_fatal(tmp_path):
    """A fresh install has no shadow ledger. That must not abort the backup of
    everything else."""
    (tmp_path / ".agent-memory.json").write_text("{}")
    files, missing = B.collect(str(tmp_path))
    assert len(files) == 1
    assert ".state/shadow_ledger" in missing


# ── what it must NEVER contain ───────────────────────────────────────────────

def test_secrets_are_never_archived(tree):
    """A plaintext copy of the Hyperliquid keys in an unwatched tarball is a new
    way to be robbed, not a backup."""
    files, _ = B.collect(str(tree))
    assert not any("env" in os.path.basename(f) for f in files)
    assert not any(f.endswith(".pem") for f in files)


@pytest.mark.parametrize("name", [
    ".env", ".env.local", ".env.production", "id_rsa", "signing.pem",
    "agent.key", "credentials.json", "secrets.json"])
def test_secret_filenames_are_recognised(name):
    assert B.is_secret(f"/anywhere/deep/{name}") is True


def test_ordinary_state_is_not_mistaken_for_a_secret():
    for ok in (".agent-memory.json", "capital_flows.jsonl", "unlock_short.jsonl"):
        assert B.is_secret(f"/x/{ok}") is False


def test_verify_rejects_an_archive_containing_a_secret(tree, tmp_path):
    """Defence in depth: if collect() ever let one through, verify must catch it
    before the archive is called good."""
    archive = str(tmp_path / "leak.tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(str(tree / ".env.local"), arcname=".env.local")
    ok, detail = B.verify(archive, 1)
    assert ok is False and "secrets" in detail


# ── it must read back ────────────────────────────────────────────────────────

def test_the_archive_restores_byte_for_byte(tree, tmp_path):
    files, _ = B.collect(str(tree))
    archive = B.write_archive(str(tmp_path / "a.tar.gz"), files, root=str(tree))
    ok, detail = B.verify(archive, len(files))
    assert ok, detail

    dest = tmp_path / "restored"
    dest.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    for f in files:
        rel = os.path.relpath(f, tree)
        assert (dest / rel).read_bytes() == open(f, "rb").read()


def test_a_truncated_archive_fails_verification(tree, tmp_path):
    """getnames() succeeds on a truncated gzip. Only reading a member out
    catches it — which is the difference between a backup and a guess."""
    files, _ = B.collect(str(tree))
    archive = B.write_archive(str(tmp_path / "a.tar.gz"), files, root=str(tree))
    data = open(archive, "rb").read()
    open(archive, "wb").write(data[:len(data) // 2])
    ok, _ = B.verify(archive, len(files))
    assert ok is False


def test_a_partial_write_is_never_named_like_a_finished_archive(tree, tmp_path):
    files, _ = B.collect(str(tree))
    B.write_archive(str(tmp_path / "a.tar.gz"), files, root=str(tree))
    assert not list(tmp_path.glob("*.partial"))


def test_verify_notices_a_missing_member(tree, tmp_path):
    files, _ = B.collect(str(tree))
    archive = B.write_archive(str(tmp_path / "a.tar.gz"), files[:-1], root=str(tree))
    ok, detail = B.verify(archive, len(files))
    assert ok is False and "expected" in detail


# ── rotation ─────────────────────────────────────────────────────────────────

def test_pruning_keeps_the_newest_and_only_our_own_files(tmp_path):
    for i in range(6):
        f = tmp_path / f"pathia-state-2026083{i}-000000.tar.gz"
        f.write_text("x")
        os.utime(f, (time.time() - (10 - i) * 86400,) * 2)
    (tmp_path / "someone-elses-backup.tar.gz").write_text("x")

    removed = B.prune(str(tmp_path), keep=3)
    left = sorted(p.name for p in tmp_path.glob("*.tar.gz"))
    assert len(removed) == 3
    assert "someone-elses-backup.tar.gz" in left, "pruning touched a foreign file"
    assert "pathia-state-20260835-000000.tar.gz" in left, "newest was deleted"


def test_pruning_a_missing_directory_is_not_an_error(tmp_path):
    assert B.prune(str(tmp_path / "nope"), keep=3) == []


def test_keep_zero_never_deletes_everything(tmp_path):
    """A misconfigured keep must not wipe every backup there is."""
    (tmp_path / "pathia-state-20260830-000000.tar.gz").write_text("x")
    assert B.prune(str(tmp_path), keep=0) == []


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_scheduler_runs_the_backup_daily():
    spec = importlib.util.spec_from_file_location(
        "sched_backup", os.path.join(ROOT, "scripts", "scheduler.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    job = m.JOBS["backup-state"]
    assert job["args"][-1].endswith("backup_state.py")
    assert "hour" in job, "a daily job, not an interval one"


def test_an_unverified_backup_reports_as_no_backup(monkeypatch, tmp_path):
    """A corrupt archive must alert exactly like a missing one. Anything else
    lets a broken backup read as a working one."""
    import importlib as il

    monkeypatch.setenv("PATHIA_STATE_DIR", str(tmp_path))
    (tmp_path / "backup.json").write_text(
        json.dumps({"ts": time.time(), "verified": False, "detail": "truncated"}))

    import pathia.agents.rebalancer_owned as ro
    from pathia import metrics
    il.reload(ro)
    try:
        metrics._refresh()
        assert metrics.BACKUP_AGE._value.get() > 129600, (
            "an unverified archive must read as stale enough to alert")
        (tmp_path / "backup.json").write_text(
            json.dumps({"ts": time.time(), "verified": True}))
        metrics._refresh()
        assert metrics.BACKUP_AGE._value.get() < 60
    finally:
        il.reload(ro)


def test_backup_staleness_is_alerted_on():
    import yaml
    doc = yaml.safe_load(open(os.path.join(ROOT, "k8s", "prometheusrule.yaml")))
    names = {r["alert"] for g in doc["spec"]["groups"] for r in g["rules"]}
    assert "PathiaBackupStale" in names
