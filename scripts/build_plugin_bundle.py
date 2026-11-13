#!/usr/bin/env python3
"""Build the pathiel plugin bundle: only the files the plugin runs or reads.

The Claude plugin directory scans the whole folder it is given. Given the repo
root, it reads the maintainer's CLAUDE.md, the repo .mcp.json, .claude/skills,
VPS scripts, CI workflows and test fixtures as if they shipped to users, and
flags them (ROOT_CLAUDE_MD, DECLARED_IN_TWO_PLACES, LOCAL_CODE_FILE_TYPE,
RUNTIME_FETCH_EXEC). The bundle is the plugin without any of that, committed
to its own branch, `plugin`, whose root is the plugin root. The directory
tracks that branch.

What goes in, decided by rules a test pins (tests/test_plugin_bundle.py):

  listing    .claude-plugin/plugin.json + icon.png, README, LICENSE,
             SECURITY, CHANGELOG
  content    commands/, agents/, skills/ (what Claude Code loads)
  runtime    the launcher scripts, requirements.lock, config/public/, and
             every first-party Python module reachable from the entry
             scripts by import (static imports, lazy imports inside
             functions, and dotted module strings such as
             "services.intel.api:build_packet" all count), plus the
             package data files listed in DATA_INCLUDE

Everything else stays out. A data file under pathiel/, services/ or
contracts/ that neither DATA_INCLUDE nor DATA_EXCLUDE classifies fails the
test, so a new file is a decision, not an accident.

    python3 scripts/build_plugin_bundle.py list [--ref REF]
    python3 scripts/build_plugin_bundle.py build OUT_DIR [--ref REF]
    python3 scripts/build_plugin_bundle.py tree [--ref REF]
    python3 scripts/build_plugin_bundle.py publish [--ref REF] [--push]
    python3 scripts/build_plugin_bundle.py check --ref REF --against BRANCH

Every command reads the git objects of REF (default HEAD), never the working
tree, so uncommitted edits cannot leak into a published bundle. REF ":index"
means the staged files (list, tree and build only). `publish`
writes one commit on the local `plugin` branch with git plumbing (no
checkout, no worktree); the commit's tree is exactly `tree`'s answer, and
the parent is the previous bundle, so `--push` is always a fast-forward.
`check` is what the release workflow runs: the bundle a tag would build must
be the bundle on the branch, byte for byte.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "plugin"

LISTING_FILES = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/icon.png",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "CHANGELOG.md",
)

# Loaded by Claude Code as plugin content, shipped whole.
CONTENT_TREES = ("commands/", "agents/", "skills/")

# The plugin's own runtime, outside the Python packages.
RUNTIME_FILES = (
    "requirements.lock",          # pathiel_build_venv installs from it
    "config/public/agent-config.json",
    "config/public/env.local.example",
    "scripts/pathiel-mcp",        # plugin.json starts this with bash
    "scripts/pathiel-py",         # commands/ and agents/ run scripts through it
    "scripts/_plugin_env.sh",
)

# Python entry points: the MCP server, and every script commands/, agents/ or
# skills/ tell Claude to run. Their import closure is the Python that ships.
ENTRY_SCRIPTS = (
    "scripts/pathiel-mcp-server.py",
    "scripts/config_preset.py",      # pathiel_seed_config
    "scripts/public_status.py",      # /pathiel:setup
    "scripts/approve_builder_fee.py",  # /pathiel:setup
    "scripts/desk_slot.py",          # desk-tick
    "scripts/thesis_due.py",         # desk-tick
    "scripts/oil_levels.py",         # desk-tick
    "scripts/signer_check.py",       # desk-tick
    "scripts/weekend.py",            # desk-tick
)

# Scripts the skills mention that are the maintainer's tools, not the plugin's:
# they need the full checkout and are documented as such. Listed so a new
# mention of a script in plugin content is a decision too.
DEV_ONLY_SCRIPTS = {
    "scripts/restart.sh": "restarts the maintainer's dashboard stack",
    "scripts/mcp_smoke.py": "maintainer's MCP smoke test against a checkout",
    "scripts/mcp_tool_table.py": "regenerates docs/overhaul/MCP.md in a checkout",
    "scripts/audit_mcp_server.py": "the skill ships its own copy under skills/",
    "scripts/build_plugin_skill.py": "generates skills/ from .claude/ in a checkout",
}

PACKAGE_ROOTS = ("pathiel", "services", "contracts")
CODE_TREES = tuple(f"{p}/" for p in PACKAGE_ROOTS)

# Non-Python files under the package trees. Every one must match exactly one
# list (tests/test_plugin_bundle.py). Patterns are fnmatch on the repo path.
DATA_INCLUDE = (
    "pathiel/agents/unlock_map.json",
    "services/intel/data/*.json",
    "contracts/*/fixtures/*.json",    # contract example payloads, read by code
)
DATA_EXCLUDE = (
    "*/tests/*",                      # test suites and their fixtures
    "*/evals/*",                      # paid or slow eval lanes
    "*.md",                           # service docs; the README ships at the root
    "pathiel/static/*",               # the dashboard's browser assets
    "pathiel/templates/*",            # the dashboard's pages
    "services/pathiel_data_api/*",    # a separately deployed service
)

FIRST_PARTY = re.compile(r"^(pathiel|services|contracts)(\.[A-Za-z_]\w*)+(:[A-Za-z_]\w*)?$")


# ── git object access (REF, never the working tree) ─────────────────────────

def _git(*args: str, env: Optional[Dict[str, str]] = None, input_text: Optional[str] = None) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                         env=env, input=input_text)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


INDEX = ":index"


def ls_tree(ref: str) -> Dict[str, Tuple[str, str]]:
    """path -> (mode, blob sha) for every file at ref. ref ":index" reads the
    staged files instead of a commit: what the pre-commit gate checks, since
    the commit it guards does not exist yet."""
    files: Dict[str, Tuple[str, str]] = {}
    if ref == INDEX:
        for line in _git("ls-files", "-s", "-z").split("\0"):
            if line:
                meta, path = line.split("\t", 1)
                mode, sha, _stage = meta.split()
                files[path] = (mode, sha)
        return files
    for line in _git("ls-tree", "-r", "-z", ref).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob":
            files[path] = (mode, sha)
    return files


def read_blobs(shas: Iterable[str]) -> Dict[str, bytes]:
    """Blob contents in one `git cat-file --batch` call."""
    shas = list(dict.fromkeys(shas))
    if not shas:
        return {}
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, capture_output=True,
                          input=("\n".join(shas) + "\n").encode())
    if proc.returncode != 0:
        raise SystemExit(f"git cat-file failed: {proc.stderr.decode().strip()}")
    data, out, pos = proc.stdout, {}, 0
    for sha in shas:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].split()
        size = int(header[2])
        out[sha] = data[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return out


# ── the import closure ──────────────────────────────────────────────────────

def _module_files(name: str, files: Set[str]) -> List[str]:
    base = name.replace(".", "/")
    return [p for p in (f"{base}.py", f"{base}/__init__.py") if p in files]


def _package_of(path: str) -> List[str]:
    """Dotted package parts a module file lives in (for relative imports)."""
    parts = path[:-3].split("/")
    return parts[:-1]


def imports_of(path: str, source: bytes, files: Set[str]) -> Set[str]:
    """Repo files `path` imports: every Import/ImportFrom anywhere in the tree
    (lazy ones inside functions included), dotted first-party module strings,
    and for scripts/ files, siblings imported by bare name."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise SystemExit(f"{path}: cannot parse ({exc})")
    found: Set[str] = set()

    def add(name: str) -> None:
        found.update(_module_files(name, files))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
                if path.startswith("scripts/"):
                    found.update(p for p in (f"scripts/{alias.name}.py",) if p in files)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pkg = _package_of(path)
                pkg = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                mod = ".".join(pkg + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            add(mod)
            for alias in node.names:
                add(f"{mod}.{alias.name}")
            if path.startswith("scripts/") and not node.level:
                found.update(p for p in (f"scripts/{mod}.py",) if p in files)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if FIRST_PARTY.match(node.value):
                add(node.value.split(":", 1)[0])
    return found


def closure(entries: Iterable[str], files: Dict[str, Tuple[str, str]]) -> Set[str]:
    """Every .py file reachable from `entries`, with each package's __init__."""
    names = set(files)
    seen: Set[str] = set()
    todo = [e for e in entries]
    while todo:
        batch = [p for p in dict.fromkeys(todo) if p not in seen]
        todo = []
        if not batch:
            break
        blobs = read_blobs(files[p][1] for p in batch)
        for path in batch:
            seen.add(path)
            parts = path.split("/")
            for i in range(1, len(parts)):  # parent packages' __init__.py
                init = "/".join(parts[:i]) + "/__init__.py"
                if init in names and init not in seen:
                    todo.append(init)
            todo.extend(p for p in imports_of(path, blobs[files[path][1]], names) if p not in seen)
    return seen


# ── the bundle ──────────────────────────────────────────────────────────────

def classify_data(path: str) -> Optional[bool]:
    """True: ship it. False: keep it out. None: no rule says (a test fails)."""
    if any(fnmatch.fnmatch(path, pat) for pat in DATA_EXCLUDE):
        return False
    if any(fnmatch.fnmatch(path, pat) for pat in DATA_INCLUDE):
        return True
    return None


def bundle_paths(files: Dict[str, Tuple[str, str]]) -> List[str]:
    """The sorted repo paths the bundle holds, out of `files` (one ref)."""
    missing = [p for p in LISTING_FILES + RUNTIME_FILES + ENTRY_SCRIPTS if p not in files]
    if missing:
        raise SystemExit(f"bundle source is missing required files: {', '.join(missing)}")
    picked: Set[str] = set(LISTING_FILES) | set(RUNTIME_FILES)
    picked |= {p for p in files if p.startswith(CONTENT_TREES)}
    picked |= closure(ENTRY_SCRIPTS, files)
    picked |= {p for p in files
               if p.startswith(CODE_TREES) and not p.endswith(".py") and classify_data(p)}
    return sorted(picked)


def version_at(ref: str) -> str:
    spec = ":.claude-plugin/plugin.json" if ref == INDEX else f"{ref}:.claude-plugin/plugin.json"
    return json.loads(_git("show", spec))["version"]


def tree_sha(ref: str) -> str:
    """The git tree of the bundle built from ref. Same files, same sha."""
    files = ls_tree(ref)
    lines = "".join(f"{files[p][0]} {files[p][1]}\t{p}\n" for p in bundle_paths(files))
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        _git("read-tree", "--empty", env=env)
        _git("update-index", "--index-info", env=env, input_text=lines)
        return _git("write-tree", env=env).strip()


def build(ref: str, out: Path) -> List[str]:
    """Write the bundle's files into an empty directory, exec bits kept."""
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    files = ls_tree(ref)
    paths = bundle_paths(files)
    blobs = read_blobs(files[p][1] for p in paths)
    for p in paths:
        mode, sha = files[p]
        dest = out / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blobs[sha])
        if mode == "100755":
            dest.chmod(0o755)
    return paths


def _rev(name: str) -> Optional[str]:
    out = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{name}^{{commit}}"], cwd=ROOT,
                         capture_output=True, text=True)
    return out.stdout.strip() or None


def publish(ref: str, push: bool) -> str:
    """Commit the bundle of ref onto the `plugin` branch. Returns its sha."""
    if ref == INDEX:
        raise SystemExit("publish needs a commit (a tag or main), not the index")
    tree = tree_sha(ref)
    source = _git("rev-parse", f"{ref}^{{commit}}").strip()
    if push:
        subprocess.run(["git", "fetch", "-q", "origin", BRANCH], cwd=ROOT, capture_output=True)
    parent = _rev(f"refs/heads/{BRANCH}") or _rev(f"refs/remotes/origin/{BRANCH}")
    remote = _rev(f"refs/remotes/origin/{BRANCH}")
    if parent and remote and parent != remote:
        merged = subprocess.run(["git", "merge-base", "--is-ancestor", remote, parent], cwd=ROOT)
        if merged.returncode != 0:
            raise SystemExit(f"local {BRANCH} does not contain origin/{BRANCH}; "
                             f"run: git branch -f {BRANCH} origin/{BRANCH}")
    if parent and _git("rev-parse", f"{parent}^{{tree}}").strip() == tree:
        commit = parent
        print(f"{BRANCH}: already the bundle of {source[:7]} ({commit[:7]})", file=sys.stderr)
    else:
        msg = (f"plugin bundle {version_at(ref)} from {source[:12]}\n\n"
               f"Built by scripts/build_plugin_bundle.py from {source}.\n"
               f"Do not edit this branch by hand: publish again from main.\n")
        args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
        commit = _git(*args).strip()
        _git("update-ref", f"refs/heads/{BRANCH}", commit, *( [parent] if parent and
             _rev(f"refs/heads/{BRANCH}") else []))
        print(f"{BRANCH}: {commit[:7]} = bundle {version_at(ref)} of {source[:7]}", file=sys.stderr)
    if push:
        _git("push", "origin", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}")
        print(f"pushed {BRANCH}", file=sys.stderr)
    return commit


def check(ref: str, against: str) -> bool:
    want, have = tree_sha(ref), _git("rev-parse", f"{against}^{{tree}}").strip()
    if want == have:
        print(f"ok: {against} holds the bundle of {ref} ({version_at(ref)})")
        return True
    print(f"MISMATCH: the bundle of {ref} is tree {want[:12]}, {against} has {have[:12]}. "
          f"Run: python3 scripts/build_plugin_bundle.py publish --ref {ref} --push", file=sys.stderr)
    return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "tree", "build", "publish", "check"):
        p = sub.add_parser(name)
        p.add_argument("--ref", default="HEAD")
        if name == "build":
            p.add_argument("out", type=Path)
        if name == "publish":
            p.add_argument("--push", action="store_true")
        if name == "check":
            p.add_argument("--against", default=f"origin/{BRANCH}")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("\n".join(bundle_paths(ls_tree(a.ref))))
    elif a.cmd == "tree":
        print(tree_sha(a.ref))
    elif a.cmd == "build":
        paths = build(a.ref, a.out)
        print(f"{len(paths)} files -> {a.out}", file=sys.stderr)
    elif a.cmd == "publish":
        print(publish(a.ref, a.push))
    elif a.cmd == "check":
        return 0 if check(a.ref, a.against) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
