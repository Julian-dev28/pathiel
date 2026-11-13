#!/usr/bin/env python3
"""Publish pathiel's GitHub Pages site: the landing page and the scoreboard only.

GitHub Pages first served the whole `docs/` folder from main (2026-11-13),
which put every research doc, backtest figures included, on the Pages host
as plain text. The site is now its own branch, `gh-pages`, holding exactly:

    index.html, sitemap.xml   site/
    icon.png                  .claude-plugin/icon.png (the directory icon)
    .nojekyll                 docs/.nojekyll (serve files as they are)
    scoreboard/*              docs/scoreboard/* (written by the pool build)

Pages serves the root of `gh-pages`. Sources stay on main, where the tests
check them; this script only copies their git blobs into a commit, with the
same plumbing as scripts/build_plugin_bundle.py: read from a ref or the
index (never the working tree), one signed commit per change on top of the
previous one, fast-forward pushes only, refused inside a git hook.

    python3 scripts/publish_site.py list [--ref REF]
    python3 scripts/publish_site.py tree [--ref REF]
    python3 scripts/publish_site.py publish [--ref REF] [--push]
    python3 scripts/publish_site.py check --ref REF [--against origin/gh-pages]

After each scoreboard build is committed and pushed to main (the pool's
build command, docs/overhaul/RESEARCH_POOL_2026-11-12.md):
`publish --ref origin/main --push`. This script never imports pool code; it
copies files.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plugin_bundle as B  # noqa: E402  (git helpers: ls_tree, _git, _rev, HOOK_ENV)

BRANCH = "gh-pages"

# published path -> source path on the ref
FIXED: Dict[str, str] = {
    "index.html": "site/index.html",
    "sitemap.xml": "site/sitemap.xml",
    "icon.png": ".claude-plugin/icon.png",
    ".nojekyll": "docs/.nojekyll",
}
SCOREBOARD_SRC = "docs/scoreboard/"
SCOREBOARD_DST = "scoreboard/"


def site_entries(files: Dict[str, Tuple[str, str]]) -> Dict[str, Tuple[str, str]]:
    """published path -> (mode, blob sha). Exactly the files above."""
    missing = [src for src in FIXED.values() if src not in files]
    if missing:
        raise SystemExit(f"site source is missing: {', '.join(missing)}")
    out = {dst: ("100644", files[src][1]) for dst, src in FIXED.items()}
    board = {p: v for p, v in files.items() if p.startswith(SCOREBOARD_SRC)}
    if SCOREBOARD_SRC + "index.html" not in board:
        raise SystemExit(f"site source is missing {SCOREBOARD_SRC}index.html")
    for p, (_mode, sha) in board.items():
        out[SCOREBOARD_DST + p[len(SCOREBOARD_SRC):]] = ("100644", sha)
    return out


def tree_sha(ref: str) -> str:
    entries = site_entries(B.ls_tree(ref))
    lines = "".join(f"{m} {s}\t{p}\n" for p, (m, s) in sorted(entries.items()))
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        B._git("read-tree", "--empty", env=env)
        B._git("update-index", "--index-info", env=env, input_text=lines)
        return B._git("write-tree", env=env).strip()


def publish(ref: str, push: bool) -> str:
    if ref == B.INDEX:
        raise SystemExit("publish needs a commit (main or a tag), not the index")
    hooked = sorted(k for k in B.HOOK_ENV if k in os.environ)
    if hooked:
        raise SystemExit(f"publish refused: {', '.join(hooked)} is set (a git hook or a "
                         f"nested git command); run it from a plain shell")
    tree = tree_sha(ref)
    source = B._git("rev-parse", f"{ref}^{{commit}}").strip()
    if push:
        subprocess.run(["git", "fetch", "-q", "origin", BRANCH], cwd=B.ROOT, capture_output=True)
    local, remote = B._rev(f"refs/heads/{BRANCH}"), B._rev(f"refs/remotes/origin/{BRANCH}")
    parent = local or remote
    if local and remote and local != remote:
        ok = subprocess.run(["git", "merge-base", "--is-ancestor", remote, local], cwd=B.ROOT)
        if ok.returncode != 0:
            raise SystemExit(f"local {BRANCH} does not contain origin/{BRANCH}; "
                             f"run: git branch -f {BRANCH} origin/{BRANCH}")
    if parent and B._git("rev-parse", f"{parent}^{{tree}}").strip() == tree:
        commit = parent
        if not local:
            B._git("update-ref", f"refs/heads/{BRANCH}", commit, "")
        print(f"{BRANCH}: already the site of {source[:7]} ({commit[:7]})", file=sys.stderr)
    else:
        msg = (f"site from {source[:12]}\n\nBuilt by scripts/publish_site.py from {source}.\n"
               f"Do not edit this branch by hand: publish again from main.\n")
        args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
        sign = subprocess.run(["git", "config", "--bool", "commit.gpgsign"], cwd=B.ROOT,
                              capture_output=True, text=True).stdout.strip()
        if sign == "true":
            args.insert(1, "-S")
        commit = B._git(*args).strip()
        B._git("update-ref", f"refs/heads/{BRANCH}", commit, *([local] if local else [""]))
        print(f"{BRANCH}: {commit[:7]} = site of {source[:7]}", file=sys.stderr)
    if push:
        B._git("push", "origin", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")
        print(f"pushed {BRANCH}", file=sys.stderr)
    return commit


def check(ref: str, against: str) -> bool:
    want, have = tree_sha(ref), B._git("rev-parse", f"{against}^{{tree}}").strip()
    if want == have:
        print(f"ok: {against} holds the site of {ref}")
        return True
    print(f"MISMATCH: the site of {ref} is tree {want[:12]}, {against} has {have[:12]}. "
          f"Run: python3 scripts/publish_site.py publish --ref {ref} --push", file=sys.stderr)
    return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "tree", "publish", "check"):
        p = sub.add_parser(name)
        p.add_argument("--ref", default="HEAD")
        if name == "publish":
            p.add_argument("--push", action="store_true")
        if name == "check":
            p.add_argument("--against", default=f"origin/{BRANCH}")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("\n".join(sorted(site_entries(B.ls_tree(a.ref)))))
    elif a.cmd == "tree":
        print(tree_sha(a.ref))
    elif a.cmd == "publish":
        print(publish(a.ref, a.push))
    elif a.cmd == "check":
        return 0 if check(a.ref, a.against) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
