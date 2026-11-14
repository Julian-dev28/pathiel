#!/usr/bin/env python3
"""Do plugin users get main's fixes? Fail when they wait too long.

Why (2026-11-14): a fix on main reaches nobody until it ships. pathiel had
that gap: the msgpack security fix (GHSA-j586-36cw-2gc2)
sat on main while the `plugin` branch, which every plugin install reads,
still shipped the vulnerable lock.

This compares the bundle main would publish (scripts/build_plugin_bundle.py)
with the bundle on the `plugin` branch. When they differ, it finds the oldest
commit on main since the published one that touches a bundled file, and fails
when that commit is older than the limit:

    7 days for any shipped file
    2 days when requirements.lock changed (dependency and security fixes)

    python3 scripts/plugin_freshness.py [--ref origin/main] [--against origin/plugin]

The daily workflow (.github/workflows/freshness.yml) runs it; a failure there
means: cut a release (CONTRIBUTING.md, Releasing). Read only.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plugin_bundle as B  # noqa: E402

MAX_DAYS = 7
LOCK_MAX_DAYS = 2
LOCK = "requirements.lock"


def decide(pending: List[Tuple[str, int, str, List[str]]], now: float,
           max_days: float = MAX_DAYS, lock_max_days: float = LOCK_MAX_DAYS) -> Tuple[bool, str]:
    """pending: (sha, commit time, subject, shipped paths it touched), oldest
    first. (ok, one-line verdict). Pure, so the rule is tested without git."""
    if not pending:
        return True, "fresh: the plugin branch ships everything main would"
    oldest = pending[0]
    age = (now - oldest[1]) / 86400
    lock_commits = [p for p in pending if LOCK in p[3]]
    if lock_commits:
        lock_age = (now - lock_commits[0][1]) / 86400
        if lock_age > lock_max_days:
            return False, (f"STALE: {LOCK} changed on main {lock_age:.1f} days ago "
                           f"({lock_commits[0][0][:7]} {lock_commits[0][2]}) and plugin users still "
                           f"install the old one (limit {lock_max_days:g} days). Cut a release.")
    if age > max_days:
        return False, (f"STALE: {len(pending)} unreleased change(s) to shipped files, the oldest "
                       f"{age:.1f} days old ({oldest[0][:7]} {oldest[2]}; limit {max_days:g} days). "
                       "Cut a release.")
    return True, (f"pending: {len(pending)} unreleased change(s) to shipped files, oldest "
                  f"{age:.1f} days (limits {max_days:g} days, {lock_max_days:g} for {LOCK})")


def published_source(against: str) -> Optional[str]:
    """The main commit the plugin branch was built from (its commit subject:
    'plugin bundle X from <sha>')."""
    subject = B._git("log", "-1", "--format=%s", against).strip()
    m = re.search(r"\bfrom ([0-9a-f]{7,40})\b", subject)
    return m.group(1) if m else None


def pending_changes(ref: str, against: str) -> List[Tuple[str, int, str, List[str]]]:
    source = published_source(against)
    if source is None:
        raise SystemExit(f"{against}: cannot read the source commit from its subject")
    shipped = set(B.bundle_paths(B.ls_tree(ref)))
    out = B._git("log", "--reverse", "--format=%x00%H %ct %s", "--name-only",
                 f"{source}..{ref}").split("\x00")
    pending = []
    for block in out:
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        sha, ts, subject = lines[0].split(" ", 2)
        touched = [p for p in lines[1:] if p in shipped]
        if touched:
            pending.append((sha, int(ts), subject, touched))
    return pending


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--against", default="origin/plugin")
    a = ap.parse_args(argv)
    if B.tree_sha(a.ref) == B._git("rev-parse", f"{a.against}^{{tree}}").strip():
        print("fresh: the plugin branch holds the bundle of " + a.ref)
        return 0
    pending = pending_changes(a.ref, a.against)
    ok, verdict = decide(pending, time.time())
    print(verdict)
    for sha, ts, subject, touched in pending[:10]:
        print(f"  {sha[:7]} {time.strftime('%Y-%m-%d', time.gmtime(ts))} {subject} [{', '.join(touched[:3])}]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
