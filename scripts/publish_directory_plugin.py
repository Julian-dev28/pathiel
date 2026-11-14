#!/usr/bin/env python3
"""Publish the Claude plugin directory's build of pathiel: Markdown, JSON and
the icon only, on its own branch, `plugin-directory`.

Why (2026-11-13): the directory refused v0.6.0 ("Directory policy failed"):
from an organization that is not Verified it accepts no MCP server that runs
on the user's computer and no file outside Markdown, text, JSON, YAML and
images (no scripts, nothing starting with #!). The `plugin` branch (the
repo's own marketplace) bundles the Python server and the desk loop's
scripts, so it keeps doing that, unchanged; the directory gets this build:

    .claude-plugin/plugin.json   the plugin's own, without mcpServers or displayName
    .claude-plugin/icon.png      the icon
    commands/setup.md            directory/commands/setup.md (installs the server with Docker)
    commands/share.md            commands/share.md (research pool; calls MCP tools only)
    README.md                    directory/README.md
    LICENSE                      LICENSE

The server is the published container (ghcr.io/julian-dev28/pathiel-mcp),
which the user adds with one `claude mcp add` line in /pathiel:setup.

    python3 scripts/publish_directory_plugin.py list [--ref REF]
    python3 scripts/publish_directory_plugin.py check-policy [--ref REF]
    python3 scripts/publish_directory_plugin.py publish --ref origin/main [--push]
    python3 scripts/publish_directory_plugin.py check --ref REF [--against origin/plugin-directory]

Reads git objects of a ref (never the working tree), one signed commit per
change, fast-forward only, refused inside a git hook: the same plumbing as
scripts/publish_site.py and scripts/build_plugin_bundle.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plugin_bundle as B  # noqa: E402  (git helpers: ls_tree, _git, _rev, HOOK_ENV)

BRANCH = "plugin-directory"

# published path -> source path on the ref, copied as is
COPIED: Dict[str, str] = {
    ".claude-plugin/icon.png": ".claude-plugin/icon.png",
    "commands/setup.md": "directory/commands/setup.md",
    "commands/share.md": "commands/share.md",
    "README.md": "directory/README.md",
    "LICENSE": "LICENSE",
}
PLUGIN_JSON = ".claude-plugin/plugin.json"
# plugin.json keys this build drops: the bundled server, and a field the
# directory flagged as one Claude Code does not read
DROPPED_KEYS = ("mcpServers", "displayName", "hooks", "lspServers")

TEXT_SUFFIXES = (".md", ".json", ".txt", ".yaml", ".yml")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")
BARE_TEXT_NAMES = ("LICENSE", "README", ".gitignore")


def _blob(sha: str) -> bytes:
    return subprocess.run(["git", "cat-file", "blob", sha], cwd=B.ROOT, capture_output=True,
                          check=True).stdout


def directory_plugin_json(raw: bytes) -> bytes:
    data = json.loads(raw)
    for key in DROPPED_KEYS:
        data.pop(key, None)
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def contents(ref: str) -> Dict[str, bytes]:
    """published path -> file bytes, from the ref's git objects."""
    files = B.ls_tree(ref)
    missing = [src for src in list(COPIED.values()) + [PLUGIN_JSON] if src not in files]
    if missing:
        raise SystemExit(f"directory plugin source is missing: {', '.join(missing)}")
    out = {dst: _blob(files[src][1]) for dst, src in COPIED.items()}
    out[PLUGIN_JSON] = directory_plugin_json(_blob(files[PLUGIN_JSON][1]))
    return out


def policy_problems(tree: Dict[str, bytes]) -> List[str]:
    """What the directory's automatic check would refuse, as we read its
    rules (2026-11-13): types, #!, UTF-8, NUL, a bundled server, a script."""
    problems: List[str] = []
    for path, data in sorted(tree.items()):
        name = path.rsplit("/", 1)[-1]
        image = name.lower().endswith(IMAGE_SUFFIXES)
        text = name.endswith(TEXT_SUFFIXES) or name in BARE_TEXT_NAMES
        if not (image or text):
            problems.append(f"{path}: not Markdown, text, JSON, YAML or an image")
            continue
        if data.startswith(b"#!"):
            problems.append(f"{path}: starts with #!")
        if text:
            if b"\x00" in data:
                problems.append(f"{path}: has a NUL byte")
            try:
                body = data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{path}: not UTF-8")
                continue
            if "CLAUDE_PLUGIN_ROOT}/scripts" in body or "CLAUDE_PLUGIN_ROOT/scripts" in body:
                problems.append(f"{path}: tells Claude to run a bundled script")
        if path.startswith(".claude-plugin/") and not (image or name in ("plugin.json", "marketplace.json")):
            problems.append(f"{path}: .claude-plugin holds only plugin.json, marketplace.json and images")
        if path.split("/")[0] in (".claude", "egress", "scripts", "hooks") or name in (
                ".mcp.json", "hooks.json", ".lsp.json", "monitors.json"):
            problems.append(f"{path}: not allowed in a directory plugin")
    manifest = json.loads(tree.get(PLUGIN_JSON, b"{}"))
    for key in DROPPED_KEYS:
        if key in manifest:
            problems.append(f"{PLUGIN_JSON}: still has {key}")
    return problems


def tree_sha(ref: str) -> Tuple[str, Dict[str, bytes]]:
    tree = contents(ref)
    problems = policy_problems(tree)
    if problems:
        raise SystemExit("directory plugin breaks the directory policy:\n  " + "\n  ".join(problems))
    lines = []
    for path, data in sorted(tree.items()):
        sha = _hash(data)
        lines.append(f"100644 {sha}\t{path}\n")
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        B._git("read-tree", "--empty", env=env)
        B._git("update-index", "--index-info", env=env, input_text="".join(lines))
        return B._git("write-tree", env=env).strip(), tree


def _hash(data: bytes) -> str:
    return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=B.ROOT, input=data,
                          capture_output=True, check=True).stdout.decode().strip()


def publish(ref: str, push: bool) -> str:
    if ref == B.INDEX:
        raise SystemExit("publish needs a commit (main or a tag), not the index")
    hooked = sorted(k for k in B.HOOK_ENV if k in os.environ)
    if hooked:
        raise SystemExit(f"publish refused: {', '.join(hooked)} is set (a git hook or a "
                         f"nested git command); run it from a plain shell")
    tree, _ = tree_sha(ref)
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
        print(f"{BRANCH}: already the directory plugin of {source[:7]} ({commit[:7]})", file=sys.stderr)
    else:
        msg = (f"directory plugin from {source[:12]}\n\nBuilt by scripts/publish_directory_plugin.py "
               f"from {source}.\nDo not edit this branch by hand: publish again from main.\n")
        args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
        sign = subprocess.run(["git", "config", "--bool", "commit.gpgsign"], cwd=B.ROOT,
                              capture_output=True, text=True).stdout.strip()
        if sign == "true":
            args.insert(1, "-S")
        commit = B._git(*args).strip()
        B._git("update-ref", f"refs/heads/{BRANCH}", commit, *([local] if local else [""]))
        print(f"{BRANCH}: {commit[:7]} = directory plugin of {source[:7]}", file=sys.stderr)
    if push:
        B._git("push", "origin", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")
        print(f"pushed {BRANCH}", file=sys.stderr)
    return commit


def check(ref: str, against: str) -> bool:
    want, _ = tree_sha(ref)
    have = B._git("rev-parse", f"{against}^{{tree}}").strip()
    if want == have:
        print(f"ok: {against} holds the directory plugin of {ref}")
        return True
    print(f"MISMATCH: the directory plugin of {ref} is tree {want[:12]}, {against} has {have[:12]}. "
          f"Run: python3 scripts/publish_directory_plugin.py publish --ref {ref} --push", file=sys.stderr)
    return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "check-policy", "publish", "check"):
        p = sub.add_parser(name)
        p.add_argument("--ref", default="HEAD")
        if name == "publish":
            p.add_argument("--push", action="store_true")
        if name == "check":
            p.add_argument("--against", default=f"origin/{BRANCH}")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("\n".join(sorted(contents(a.ref))))
    elif a.cmd == "check-policy":
        problems = policy_problems(contents(a.ref))
        print("\n".join(problems) if problems else "ok: no directory policy problems")
        return 1 if problems else 0
    elif a.cmd == "publish":
        print(publish(a.ref, a.push))
    elif a.cmd == "check":
        return 0 if check(a.ref, a.against) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
