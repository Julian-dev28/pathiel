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
             SECURITY, docs/BUILDER_FEE.md
  content    commands/, agents/, skills/ (what Claude Code loads)
  runtime    the launcher scripts, requirements.lock, config/public/, the
             pool's repo-side JSON (REPO_DATA), and
             every first-party Python module reachable from the entry
             scripts by import (static imports, lazy imports inside
             functions, and dotted module strings such as
             "services.intel.api:build_packet" all count), plus the
             package data files listed in DATA_INCLUDE

The listing Markdown files (README, SECURITY, BUILDER_FEE) ship transformed,
by bundle_text(): a block between `<!-- bundle:omit -->` and
`<!-- /bundle:omit -->` becomes one line pointing at that section on GitHub
(the README's container command and maintainer clone setup, which the
directory read as download-and-run), and a relative link to a file the
bundle does not hold becomes a GitHub URL, so no link in the bundle is dead.

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
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "plugin"
HOOK_ENV = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR")

LISTING_FILES = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/icon.png",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "docs/BUILDER_FEE.md",        # the fee disclosure the README links to
)

# Loaded by Claude Code as plugin content, shipped whole.
CONTENT_TREES = ("commands/", "agents/", "skills/")

# The plugin's own runtime, outside the Python packages.
RUNTIME_FILES = (
    "requirements.lock",          # pathiel_build_venv installs from it
    "config/public/agent-config.json",
    "config/public/env.local.example",
    "scripts/pathiel-mcp.sh",     # plugin.json starts this with bash
    "scripts/pathiel-mcp",        # the same launcher under its old name (a shim)
    "scripts/pathiel-py",         # commands/ and agents/ run scripts through it
    "scripts/_plugin_env.sh",
)

# Repo-side data the pool tools read (services/pool/paths.py; Dockerfile.mcp
# copies the same). JSON only: the scoreboard page's html/css/js is for
# GitHub Pages, not for a tool.
REPO_DATA = (
    "pool/house.json",
    "pool/members/*.json",
    "pool/leaves/*.json",
    "docs/scoreboard/scoreboard.json",
)

# Python entry points: the MCP server, and every script commands/, agents/ or
# skills/ tell Claude to run. Their import closure is the Python that ships.
ENTRY_SCRIPTS = (
    "scripts/pathiel-mcp-server.py",
    "scripts/config_preset.py",      # pathiel_seed_config
    "scripts/public_status.py",      # /pathiel:setup
    "scripts/approve_builder_fee.py",  # /pathiel:setup
    "scripts/connect_wallet.py",     # /pathiel:setup step 3 (wallet_connect is the MCP twin)
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
    "services/research_mcp/*",        # a second, separate plugin (its own
                                      # .claude-plugin/plugin.json, launcher
                                      # and bundle; see
                                      # services/research_mcp/README.md)
)

REPO_URL = "https://github.com/Julian-dev28/pathiel"
# Shipped transformed by bundle_text(); everything else ships byte for byte.
# CHANGELOG.md does not ship (2026-11-13): it names .claude-plugin/icon.png
# and old install lines, which the directory held as UNREAD_ASSET_REFERENCED
# and RUNTIME_FETCH_EXEC. Links to it point at GitHub.
TRANSFORMED = ("README.md", "SECURITY.md", "docs/BUILDER_FEE.md")
OMIT_START, OMIT_END = "<!-- bundle:omit -->", "<!-- /bundle:omit -->"
_LINK = re.compile(r"\]\((?!https?://|mailto:|#)([^)\s#]+)(#[^)\s]*)?\)")
# A marker counts only alone on its line, so prose that names it (this
# file, the CHANGELOG) is not one.
_MARKER = re.compile(r"^[ \t]*(<!-- bundle:omit -->|<!-- /bundle:omit -->)[ \t]*\n?", re.M)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)

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


def _is_forbidden(name: str, forbidden: FrozenSet[str]) -> bool:
    return any(name == f or name.startswith(f + ".") for f in forbidden)


def imports_of(path: str, source: bytes, files: Set[str],
              forbidden: FrozenSet[str] = frozenset()) -> Set[str]:
    """Repo files `path` imports: every Import/ImportFrom anywhere in the tree
    (lazy ones inside functions included), dotted first-party module strings,
    and for scripts/ files, siblings imported by bare name. `forbidden`
    (dotted module names, exact or by prefix) are never resolved to a file,
    so a shipped file's own dead/lazy import of one of them (e.g.
    services/strategies/levels.py's `check()` importing pathiel.agents.
    bracket, never called by services/research_mcp) cannot pull that file
    or its own imports into the result -- see RESEARCH_FORBIDDEN_MODULES."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise SystemExit(f"{path}: cannot parse ({exc})")
    found: Set[str] = set()

    def add(name: str) -> None:
        if _is_forbidden(name, forbidden):
            return
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


def closure(entries: Iterable[str], files: Dict[str, Tuple[str, str]],
           forbidden: FrozenSet[str] = frozenset()) -> Set[str]:
    """Every .py file reachable from `entries`, with each package's __init__.
    `forbidden` is passed straight through to imports_of()."""
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
            todo.extend(p for p in imports_of(path, blobs[files[path][1]], names, forbidden)
                       if p not in seen)
    return seen


# ── the shipped Markdown ────────────────────────────────────────────────────

def _anchor(heading: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to -."""
    text = re.sub(r"[`*_\[\]()]", "", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def bundle_text(path: str, text: str, shipped: Set[str], repo_files: Set[str]) -> str:
    """The bundle's copy of a listing Markdown file (see the module doc)."""
    out, pos, open_at = [], 0, None
    for m in _MARKER.finditer(text):
        if m.group(1) == OMIT_START:
            if open_at is not None:
                raise SystemExit(f"{path}: nested {OMIT_START}")
            out.append(text[pos:m.start()])
            open_at = m.end()
            continue
        if open_at is None:
            raise SystemExit(f"{path}: {OMIT_END} without {OMIT_START}")
        heading = _HEADING.search(text[open_at:m.start()])
        if not heading:
            raise SystemExit(f"{path}: an omitted block must start with a heading")
        title = heading.group(1)
        out.append(f"{title}: see [the README on GitHub]({REPO_URL}#{_anchor(title)}).\n")
        pos, open_at = m.end(), None
    if open_at is not None:
        raise SystemExit(f"{path}: {OMIT_START} without {OMIT_END}")
    out.append(text[pos:])
    base = posixpath.dirname(path)

    def link(m: "re.Match[str]") -> str:
        target, frag = m.group(1), m.group(2) or ""
        rel = posixpath.normpath(posixpath.join(base, target))
        if rel in shipped:
            return m.group(0)
        kind = "tree" if rel not in repo_files and any(f.startswith(rel.rstrip("/") + "/")
                                                        for f in repo_files) else "blob"
        return f"]({REPO_URL}/{kind}/main/{rel}{frag})"

    return _LINK.sub(link, "".join(out))


def _shipped_blobs(files: Dict[str, Tuple[str, str]], paths: List[str]) -> Dict[str, bytes]:
    """Contents of the transformed files, as the bundle holds them."""
    shipped, names = set(paths), set(files)
    raw = read_blobs(files[p][1] for p in TRANSFORMED if p in shipped)
    return {p: bundle_text(p, raw[files[p][1]].decode("utf-8"), shipped, names).encode("utf-8")
            for p in TRANSFORMED if p in shipped}


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
    picked |= {p for p in files if any(fnmatch.fnmatch(p, pat) for pat in REPO_DATA)}
    picked |= {p for p in files
               if p.startswith(CODE_TREES) and not p.endswith(".py") and classify_data(p)}
    return sorted(picked)


def version_at(ref: str) -> str:
    spec = ":.claude-plugin/plugin.json" if ref == INDEX else f"{ref}:.claude-plugin/plugin.json"
    return json.loads(_git("show", spec))["version"]


def tree_sha(ref: str) -> str:
    """The git tree of the bundle built from ref. Same files, same sha."""
    files = ls_tree(ref)
    paths = bundle_paths(files)
    entries = {p: files[p] for p in paths}
    for p, data in _shipped_blobs(files, paths).items():
        sha = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=ROOT, input=data,
                             capture_output=True, check=True).stdout.decode().strip()
        entries[p] = ("100644", sha)
    lines = "".join(f"{entries[p][0]} {entries[p][1]}\t{p}\n" for p in paths)
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
    changed = _shipped_blobs(files, paths)
    for p in paths:
        mode, sha = files[p]
        dest = out / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(changed.get(p, blobs[sha]))
        if mode == "100755":
            dest.chmod(0o755)
    return paths


def _rev(name: str) -> Optional[str]:
    out = subprocess.run(["git", "rev-parse", "--verify", "-q", f"{name}^{{commit}}"], cwd=ROOT,
                         capture_output=True, text=True)
    return out.stdout.strip() or None


def _publish_guard(ref: str) -> None:
    """Shared by publish() and research_publish()."""
    if ref == INDEX:
        raise SystemExit("publish needs a commit (a tag or main), not the index")
    # Inside a git hook, git exports GIT_DIR / GIT_INDEX_FILE for the repo
    # being committed. A publish there writes refs into whichever repo those
    # name, not the one in the working directory (2026-11-13: a test's
    # throwaway clone wrote a `plugin` branch into the real repo that way).
    hooked = sorted(k for k in HOOK_ENV if k in os.environ)
    if hooked:
        raise SystemExit(f"publish refused: {', '.join(hooked)} is set (a git hook or a "
                         f"nested git command); run it from a plain shell")


def _do_publish(branch: str, tree: str, version: str, source: str, push: bool, label: str) -> str:
    """One commit onto `branch` whose tree is `tree`, fast-forward only.
    Shared by publish() (BRANCH, label "plugin bundle") and research_publish()
    (RESEARCH_BRANCH, label "research plugin bundle")."""
    if push:
        subprocess.run(["git", "fetch", "-q", "origin", branch], cwd=ROOT, capture_output=True)
    parent = _rev(f"refs/heads/{branch}") or _rev(f"refs/remotes/origin/{branch}")
    remote = _rev(f"refs/remotes/origin/{branch}")
    if parent and remote and parent != remote:
        merged = subprocess.run(["git", "merge-base", "--is-ancestor", remote, parent], cwd=ROOT)
        if merged.returncode != 0:
            raise SystemExit(f"local {branch} does not contain origin/{branch}; "
                             f"run: git branch -f {branch} origin/{branch}")
    if parent and _git("rev-parse", f"{parent}^{{tree}}").strip() == tree:
        commit = parent
        if not _rev(f"refs/heads/{branch}"):   # only origin/<branch> had it: track it locally
            _git("update-ref", f"refs/heads/{branch}", commit, "")
        print(f"{branch}: already the bundle of {source[:7]} ({commit[:7]})", file=sys.stderr)
    else:
        msg = (f"{label} {version} from {source[:12]}\n\n"
               f"Built by scripts/build_plugin_bundle.py from {source}.\n"
               f"Do not edit this branch by hand: publish again from main.\n")
        args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
        # commit-tree does not read commit.gpgsign; honor it, so the bundle
        # commit is signed like every other commit (GitHub: Verified).
        sign = subprocess.run(["git", "config", "--bool", "commit.gpgsign"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
        if sign == "true":
            args.insert(1, "-S")
        commit = _git(*args).strip()
        _git("update-ref", f"refs/heads/{branch}", commit, *( [parent] if parent and
             _rev(f"refs/heads/{branch}") else []))
        print(f"{branch}: {commit[:7]} = bundle {version} of {source[:7]}", file=sys.stderr)
    if push:
        _git("push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
        print(f"pushed {branch}", file=sys.stderr)
    return commit


def publish(ref: str, push: bool) -> str:
    """Commit the bundle of ref onto the `plugin` branch. Returns its sha."""
    _publish_guard(ref)
    source = _git("rev-parse", f"{ref}^{{commit}}").strip()
    return _do_publish(BRANCH, tree_sha(ref), version_at(ref), source, push, "plugin bundle")


def _do_check(tree: str, version: str, ref: str, against: str, publish_cmd: str) -> bool:
    """Shared by check() and research_check()."""
    have = _git("rev-parse", f"{against}^{{tree}}").strip()
    if tree == have:
        print(f"ok: {against} holds the bundle of {ref} ({version})")
        return True
    print(f"MISMATCH: the bundle of {ref} is tree {tree[:12]}, {against} has {have[:12]}. "
          f"Run: {publish_cmd}", file=sys.stderr)
    return False


def check(ref: str, against: str) -> bool:
    return _do_check(tree_sha(ref), version_at(ref), ref, against,
                     f"python3 scripts/build_plugin_bundle.py publish --ref {ref} --push")


# ── the research bundle (services/research_mcp/) ───────────────────────────
#
# A second, independent plugin in this same repo: the read-only research MCP
# server. Bundled onto its own branch, `plugin-research`, whose ROOT is a
# self-contained plugin root -- not nested under services/research_mcp/ the
# way the checked-in manifest sits, because Claude Code only copies a plugin
# ENTRY's own source directory into its cache: a marketplace entry whose
# source is the relative path "./services/research_mcp" ships none of
# scripts/, services/intel/, services/strategies/ or contracts/, so the
# launcher it starts cannot import the server at all (see
# code.claude.com/docs/en/plugins/loading#in-place-and-copied-plugins: "Every
# other marketplace plugin: ... Files outside the plugin directory aren't
# copied"). This bundle instead ships, at ITS OWN root, every real-repo-root
# relative file the server's import closure touches (the exact same relative
# layout as a checkout, just a subset of files), so
# ${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-research-mcp.sh keeps working
# whichever root it loads from -- see .claude-plugin/marketplace.json's
# pathiel-research entry, a `github` source pinned to this branch.
RESEARCH_BRANCH = "plugin-research"
RESEARCH_PLUGIN_JSON_SRC = "services/research_mcp/.claude-plugin/plugin.json"
RESEARCH_README_SRC = "services/research_mcp/README.md"
RESEARCH_ENTRY_SCRIPTS = ("scripts/pathiel-research-mcp.py",)
# requirements.lock: the one root lock file (scripts/pathiel-research-mcp.sh
# pip-installs from it, same as scripts/pathiel-mcp.sh does for the trading
# bundle). It is the full repo's lock, not a trimmed one -- same tradeoff the
# trading bundle already makes (ship the one tested lock file, accept the
# extra packages this server does not import, rather than maintain a second
# lock that could drift from what CI actually runs against).
RESEARCH_RUNTIME_FILES = ("scripts/pathiel-research-mcp.sh", "requirements.lock")
# services/intel/data/scheduled_events.json: the one non-.py data file any
# module in the closure actually reads (services/intel/scheduled_events.py's
# DATA_PATH). Everything else DATA_INCLUDE allows for the trading bundle
# (pathiel/agents/unlock_map.json, contracts/*/fixtures) is reachable only
# through this server's own dead trading-code imports (see
# RESEARCH_FORBIDDEN_MODULES) and ships to neither bundle.
RESEARCH_REPO_DATA = ("services/intel/data/scheduled_events.json",)
# Reused byte for byte at the bundle root, same path (no relocation: the
# icon already lives at ".claude-plugin/icon.png", same relative path the
# research bundle needs it at). This plugin's security posture (no key,
# keeps no key, see SECURITY.md) does not apply, so that file does not ship.
RESEARCH_LISTING_FILES = ("LICENSE", ".claude-plugin/icon.png")
# source path -> bundle path. Every other shipped file keeps its repo path.
RESEARCH_PATH_MAP: Dict[str, str] = {
    RESEARCH_PLUGIN_JSON_SRC: ".claude-plugin/plugin.json",
    RESEARCH_README_SRC: "README.md",
}
# services/research_mcp/tests/test_no_trading_imports.py's own
# FORBIDDEN_MODULES (order placement and signing code), duplicated here --
# not imported from the test, since a build script importing a test module
# is backwards -- and pinned a superset of it by
# tests/test_eval_plugin_bundle_research.py::
# test_research_forbidden_modules_cover_the_servers_own_gate. "scripts.
# pathiel-mcp-server" is that test's own documentary entry (not a valid
# dotted module name; closure() never sees it as an import target) and is
# left out here on purpose. "services.accounts" is added on top of that
# test's list: it is credential/key-vault code, not itself order or signing
# code, but a keyless, read-only server (this README: "Never touches a key,
# an account, or an order") must not ship it either, even as dead weight
# reached only through pathiel.client.hl_client.resolve_user_address (a
# function this server never calls -- see levels.py's fetch_mid/fetch_candles,
# which use fetch_all_mids/_http_post instead).
RESEARCH_FORBIDDEN_MODULES: FrozenSet[str] = frozenset({
    "pathiel.agents.bracket", "pathiel.agents.executor", "pathiel.client.exchange",
    "services.strategies.next_trade", "eth_account", "hyperliquid.exchange",
    "services.accounts",
})


def research_bundle_paths(files: Dict[str, Tuple[str, str]]) -> List[str]:
    """The sorted repo (source) paths the research bundle holds. Two of them
    (RESEARCH_PATH_MAP's keys) ship at a different bundle path; see
    research_build()/research_tree_sha()."""
    required = (RESEARCH_PLUGIN_JSON_SRC, RESEARCH_README_SRC) + RESEARCH_LISTING_FILES \
        + RESEARCH_RUNTIME_FILES + RESEARCH_ENTRY_SCRIPTS
    missing = [p for p in required if p not in files]
    if missing:
        raise SystemExit(f"research bundle source is missing required files: {', '.join(missing)}")
    picked: Set[str] = {RESEARCH_PLUGIN_JSON_SRC, RESEARCH_README_SRC} | set(RESEARCH_LISTING_FILES)
    picked |= set(RESEARCH_RUNTIME_FILES)
    picked |= closure(RESEARCH_ENTRY_SCRIPTS, files, RESEARCH_FORBIDDEN_MODULES)
    picked |= {p for p in files if any(fnmatch.fnmatch(p, pat) for pat in RESEARCH_REPO_DATA)}
    return sorted(picked)


def _research_dest(path: str) -> str:
    return RESEARCH_PATH_MAP.get(path, path)


def research_version_at(ref: str) -> str:
    spec = (":" if ref == INDEX else f"{ref}:") + RESEARCH_PLUGIN_JSON_SRC
    return json.loads(_git("show", spec))["version"]


def _research_shipped_blobs(files: Dict[str, Tuple[str, str]], paths: List[str]) -> Dict[str, bytes]:
    """Bundle-dest-path -> content, for the one research file that gets a
    text transform (README.md: its one relative link outside
    services/research_mcp/ would otherwise be dead at the bundle's new root;
    see bundle_text())."""
    if RESEARCH_README_SRC not in paths:
        return {}
    shipped, names = set(paths), set(files)
    raw = read_blobs([files[RESEARCH_README_SRC][1]])
    text = raw[files[RESEARCH_README_SRC][1]].decode("utf-8")
    return {_research_dest(RESEARCH_README_SRC): bundle_text(RESEARCH_README_SRC, text, shipped,
                                                             names).encode("utf-8")}


def research_tree_sha(ref: str) -> str:
    """The git tree of the research bundle built from ref. Same files, same
    sha, with RESEARCH_PATH_MAP's relocations applied."""
    files = ls_tree(ref)
    paths = research_bundle_paths(files)
    entries = {_research_dest(p): files[p] for p in paths}
    for dest, data in _research_shipped_blobs(files, paths).items():
        sha = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=ROOT, input=data,
                             capture_output=True, check=True).stdout.decode().strip()
        entries[dest] = ("100644", sha)
    lines = "".join(f"{entries[d][0]} {entries[d][1]}\t{d}\n" for d in sorted(entries))
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        _git("read-tree", "--empty", env=env)
        _git("update-index", "--index-info", env=env, input_text=lines)
        return _git("write-tree", env=env).strip()


def research_build(ref: str, out: Path) -> List[str]:
    """Write the research bundle's files into an empty directory (relocated
    paths included), exec bits kept. Returns the SOURCE paths, as build()
    does."""
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    files = ls_tree(ref)
    paths = research_bundle_paths(files)
    blobs = read_blobs(files[p][1] for p in paths)
    changed = _research_shipped_blobs(files, paths)
    for p in paths:
        mode, sha = files[p]
        dest_path = _research_dest(p)
        dest = out / dest_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(changed.get(dest_path, blobs[sha]))
        if mode == "100755":
            dest.chmod(0o755)
    return paths


def research_publish(ref: str, push: bool) -> str:
    """Commit the research bundle of ref onto the `plugin-research` branch."""
    _publish_guard(ref)
    source = _git("rev-parse", f"{ref}^{{commit}}").strip()
    return _do_publish(RESEARCH_BRANCH, research_tree_sha(ref), research_version_at(ref), source,
                       push, "research plugin bundle")


def research_check(ref: str, against: str) -> bool:
    return _do_check(research_tree_sha(ref), research_version_at(ref), ref, against,
                     f"python3 scripts/build_plugin_bundle.py publish --plugin research "
                     f"--ref {ref} --push")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("list", "tree", "build", "publish", "check"):
        p = sub.add_parser(name)
        p.add_argument("--ref", default="HEAD")
        p.add_argument("--plugin", choices=("pathiel", "research"), default="pathiel",
                       help="which plugin's bundle to operate on (default: pathiel)")
        if name == "build":
            p.add_argument("out", type=Path)
        if name == "publish":
            p.add_argument("--push", action="store_true")
        if name == "check":
            p.add_argument("--against", default=None)
    a = ap.parse_args(argv)
    research = a.plugin == "research"
    if a.cmd == "check" and a.against is None:
        a.against = f"origin/{RESEARCH_BRANCH if research else BRANCH}"
    if a.cmd == "list":
        paths = research_bundle_paths(ls_tree(a.ref)) if research else bundle_paths(ls_tree(a.ref))
        print("\n".join(paths))
    elif a.cmd == "tree":
        print(research_tree_sha(a.ref) if research else tree_sha(a.ref))
    elif a.cmd == "build":
        paths = research_build(a.ref, a.out) if research else build(a.ref, a.out)
        print(f"{len(paths)} files -> {a.out}", file=sys.stderr)
    elif a.cmd == "publish":
        print(research_publish(a.ref, a.push) if research else publish(a.ref, a.push))
    elif a.cmd == "check":
        ok = research_check(a.ref, a.against) if research else check(a.ref, a.against)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
