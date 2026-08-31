#!/usr/bin/env python3
"""Snapshot the state that cannot be recreated. Runs daily from the scheduler.

What is actually irreplaceable
------------------------------
Code is in git. Logs rotate. Prices can be refetched. Three things cannot:

  .agent-memory.json      trades, closes, equity history
  .state/shadow_ledger/   every signal every book ever recorded — the evidence
                          base under every VALIDATED and REFUTED verdict
  .state/capital_flows.jsonl  deposits and withdrawals, without which the
                          drawdown number is not flow-neutral and cannot be

All three are gitignored, all three live on one laptop, and none had a copy.
Losing them does not just lose history: every book's verdict loses the evidence
that justified it, and there is no way to re-derive it after the fact.

Where it goes
-------------
Outside the repo by default (~/hermes-backups), because the most likely way to
lose the state is to lose the directory it sits in. Override with
HERMES_BACKUP_DIR.

What it never contains
----------------------
.env.local and anything else matching a secret name. A plaintext copy of the
Hyperliquid keys in a tarball nobody is watching is a new way to be robbed, not
a backup. Enforced by SECRET_NAMES and pinned by a test.

Every run verifies the archive it just wrote by reading it back. A backup no
one has ever restored is a guess.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _state_env
_state_env.load_env_local(ROOT)
STATE_DIR = _state_env.state_dir(ROOT)
BACKUP_DIR = os.environ.get("HERMES_BACKUP_DIR") or os.path.expanduser("~/hermes-backups")
KEEP = int(os.environ.get("HERMES_BACKUP_KEEP", "14"))
RECEIPT = os.path.join(STATE_DIR, "backup.json")

# Relative to ROOT. Missing entries are reported, never fatal — a fresh install
# has no shadow ledger yet.
SOURCES: Tuple[str, ...] = (
    ".agent-memory.json",
    ".state/shadow_ledger",
    ".state/capital_flows.jsonl",
)

# Never archived, at any path depth.
SECRET_NAMES = (".env", ".env.local", ".env.production", "id_rsa", ".netrc",
                "credentials.json", "secrets.json")


def is_secret(path: str) -> bool:
    base = os.path.basename(path)
    return base in SECRET_NAMES or base.endswith(".pem") or base.endswith(".key")


def _walk(path: str) -> List[str]:
    if os.path.isfile(path):
        return [path]
    out = []
    for dirpath, _dirs, files in os.walk(path):
        out.extend(os.path.join(dirpath, f) for f in files)
    return out


def collect(root: str = ROOT, sources: Tuple[str, ...] = SOURCES
            ) -> Tuple[List[str], List[str]]:
    """(files to archive, sources that do not exist)."""
    files: List[str] = []
    missing: List[str] = []
    for rel in sources:
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            missing.append(rel)
            continue
        files.extend(f for f in _walk(full) if not is_secret(f))
    return sorted(files), missing


def write_archive(dest: str, files: List[str], root: str = ROOT) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".partial"
    with tarfile.open(tmp, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=os.path.relpath(f, root))
    os.replace(tmp, dest)          # a half-written archive is never named .tar.gz
    return dest


def verify(archive: str, expect: int) -> Tuple[bool, str]:
    """Read the archive back. A backup nobody has restored is a guess."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            leaked = [n for n in names if is_secret(n)]
            if leaked:
                return False, f"archive contains secrets: {leaked[:3]}"
            if len(names) != expect:
                return False, f"archive has {len(names)} entries, expected {expect}"
            # actually decompress one member — a truncated gzip passes getnames()
            biggest = max(tar.getmembers(), key=lambda m: m.size, default=None)
            if biggest is not None and biggest.isfile():
                fh = tar.extractfile(biggest)
                if fh is None or len(fh.read()) != biggest.size:
                    return False, f"{biggest.name} did not read back at full size"
        return True, f"{expect} entries verified"
    except Exception as exc:                                  # noqa: BLE001
        return False, f"unreadable: {type(exc).__name__}: {exc}"


def prune(backup_dir: str, keep: int, now: Optional[float] = None) -> List[str]:
    """Delete all but the newest `keep` archives. Returns what was removed."""
    if keep < 1:
        return []
    try:
        entries = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
                   if f.startswith("hermes-state-") and f.endswith(".tar.gz")]
    except FileNotFoundError:
        return []
    entries.sort(key=os.path.getmtime, reverse=True)
    removed = []
    for old in entries[keep:]:
        try:
            os.remove(old)
            removed.append(old)
        except OSError:
            pass
    return removed


def _write_receipt(payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(RECEIPT), exist_ok=True)
    tmp = RECEIPT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, RECEIPT)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default=BACKUP_DIR)
    ap.add_argument("--keep", type=int, default=KEEP)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    files, missing = collect()
    total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    for m in missing:
        print(f"[backup] {m} does not exist — nothing to snapshot from it")
    if not files:
        print("[backup] nothing to back up", file=sys.stderr)
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive = os.path.join(args.dest, f"hermes-state-{stamp}.tar.gz")
    print(f"[backup] {len(files)} files, {total / 1e6:.1f}MB -> {archive}")
    if args.dry_run:
        return 0

    write_archive(archive, files)
    ok, detail = verify(archive, len(files))
    size = os.path.getsize(archive)
    removed = prune(args.dest, args.keep)

    _write_receipt({"ts": time.time(), "archive": archive, "bytes": size,
                    "files": len(files), "source_bytes": total,
                    "verified": ok, "detail": detail, "missing": missing,
                    "pruned": len(removed)})
    print(f"[backup] {'verified' if ok else 'FAILED'}: {detail} "
          f"({size / 1e6:.1f}MB compressed, pruned {len(removed)})")
    if not ok:
        # A corrupt archive must not be mistaken for a good one.
        try:
            os.rename(archive, archive + ".corrupt")
        except OSError:
            pass
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
