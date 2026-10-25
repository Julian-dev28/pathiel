"""Guard tests that keep the 2026-10-17 cleanup sweep from silently drifting
back.

Four independent guards, each catching a class of leftover the sweep found
and removed:

  1. a tracked binary (pathiel_data.db: a live sqlite file with a real
     api_keys row, committed by accident) or an oversized tracked file.
  2. a vendored IDE-skill copy (.qwen/.forge/.agents: 66 files duplicating a
     skill already installed via the Claude Code plugin system).
  3. a scripts/*.py file nothing runs, imports, schedules, or documents
     (scripts/install_launchd_jobs.py: superseded by scripts/scheduler.py,
     left behind for months).
  4. a pathiel/agents/*.py module unreachable from any real entry point.

All four are static analysis over the working tree — no network, no
subprocess beyond `git ls-files`, no LLM. Deterministic and fast by
construction; see the timing note at the bottom of this file if that ever
stops being true.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> List[str]:
    """Every path git tracks at the current HEAD+index, repo-root-relative.

    `cwd=ROOT` is enough here — unlike tests/test_preflight_secrets.py, this
    is never run against a second, nested repo, so there is no inherited
    GIT_DIR to be wrong about: whatever GIT_DIR this process has (ambient, or
    none) already correctly names ROOT's own git dir.
    """
    proc = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True)
    return [line for line in proc.stdout.splitlines() if line]


# ── 1. no tracked binaries, nothing oversized ───────────────────────────────

# 5MB catches an accidentally committed model weight, archive, or media
# file. (Until 2026-10-25 the committed wallet.js bundle, ~3.1MB, was the
# one legitimate big file; it is gone with services/wallet_ui.)
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024

# Nothing tracked today needs to be binary (checked 2026-10-17: every
# extension in `git ls-files` is text — .py/.md/.json/.yaml/.html/.js/...).
# A real binary asset added later (a favicon, a font) should go through Git
# LFS or cloud storage with a pointer per CLAUDE.md, not land here — if that
# ever becomes a real, deliberate exception, widen this set, don't delete
# the test.
BINARY_ALLOWLIST: Set[str] = set()


def test_no_tracked_file_exceeds_size_ceiling():
    oversized = []
    for rel in _tracked_files():
        path = ROOT / rel
        if not path.is_file():
            continue  # a tracked path can be a submodule gitlink; skip those
        size = path.stat().st_size
        if size > MAX_TRACKED_FILE_BYTES:
            oversized.append(f"{rel} ({size:,} bytes)")
    assert not oversized, (
        "tracked file(s) over the "
        f"{MAX_TRACKED_FILE_BYTES:,}-byte ceiling — use Git LFS/cloud storage "
        f"with a pointer instead (CLAUDE.md), or raise the ceiling here with "
        f"a reason if this is a deliberate, checked build artifact:\n  "
        + "\n  ".join(oversized)
    )


def test_no_tracked_file_is_binary():
    """A NUL byte in the first 8KB is git's own heuristic for "this file is
    binary" (core.attributes / `git diff` use the same signal). Catches a
    small binary that a size ceiling alone would miss — pathiel_data.db was
    only 45KB."""
    binaries = []
    for rel in _tracked_files():
        if rel in BINARY_ALLOWLIST:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as fh:
                chunk = fh.read(8192)
        except OSError:
            continue
        if b"\x00" in chunk:
            binaries.append(rel)
    assert not binaries, (
        "tracked binary file(s) — this repo bans committed binaries "
        "(CLAUDE.md: \"never commit binaries, compiled outputs, or model "
        "weights\"); use Git LFS or cloud storage with a pointer, or add to "
        "BINARY_ALLOWLIST in this test with a reason:\n  " + "\n  ".join(binaries)
    )


# ── 2. no vendored IDE-skill copies ─────────────────────────────────────────

VENDORED_SKILL_DIR_PREFIXES = (".qwen/", ".forge/", ".agents/")


def test_no_vendored_ide_skill_directories():
    """.qwen/.forge/.agents were three byte-identical copies of the caveman
    skill (66 tracked files, removed 2026-10-17 — see
    archive/vendored-caveman-skills-2026-10-17), vendored for IDE agents
    nothing in this repo's tooling uses. The skill is available properly
    through Claude Code's own plugin system; re-adding a vendored copy here
    is always a regression, never a deliberate choice this test should
    special-case."""
    offenders = [rel for rel in _tracked_files()
                 if rel.startswith(VENDORED_SKILL_DIR_PREFIXES)]
    assert not offenders, (
        "vendored IDE-skill files are back — see "
        "archive/vendored-caveman-skills-2026-10-17 for why they were "
        "removed:\n  " + "\n  ".join(offenders[:10])
        + ("\n  ..." if len(offenders) > 10 else "")
    )


# ── 3. every scripts/*.py is reachable by SOME path ─────────────────────────

SCRIPTS_DIR = ROOT / "scripts"

# Helper modules meant to be imported, never run on their own — `scripts/foo`
# imports them, so they are covered by guard 3's "imported by" path, but a
# leading underscore is the existing naming convention for "not an entry
# point" (scripts/_state_env.py) and is excluded from
# needing its OWN justification beyond being importable.
def _is_private_helper(name: str) -> bool:
    return name.startswith("_")


def _all_markdown_text() -> str:
    """Every tracked *.md file's content, concatenated once. Cheap: this
    repo's markdown totals well under 2MB."""
    parts = []
    for rel in _tracked_files():
        if rel.endswith(".md"):
            try:
                parts.append((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(parts)


def _all_source_text(dirs: tuple[str, ...]) -> str:
    parts = []
    for rel in _tracked_files():
        if rel.endswith((".py", ".sh", ".yml", ".yaml")) and rel.startswith(dirs):
            try:
                parts.append((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(parts)


def test_every_script_is_imported_scheduled_or_documented():
    """Every scripts/*.py must be justified by at least one of:

      - imported by name from another tracked .py file (scripts/<name> or
        scripts.<name>) — covers helper modules and scripts that call each
        other;
      - named in scripts/scheduler.py or scripts/restart.sh — the two
        places a process actually gets launched;
      - named in some tracked .md file (README.md, DEPLOY.md, a
        services/*/README.md, ...) — the documented "run by hand when
        needed" tools (DEPLOY.md's own words);
      - named in a CI workflow;
      - has a dedicated tests/test_<name>.py, or is referenced by name from
        inside tests/.

    scripts/install_launchd_jobs.py (removed 2026-10-17) satisfied NONE of
    these — superseded by scheduler.py, it had drifted out of every one of
    these five paths for months before anyone noticed.
    """
    all_py_text = _all_source_text(("pathiel/", "scripts/", "services/", "tests/"))
    md_text = _all_markdown_text()
    ci_text = _all_source_text((".github/",))

    unjustified = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        name = path.stem
        if name == "__init__" or _is_private_helper(name):
            continue
        filename = path.name
        justified = (
            # imported by (dotted or path form), excluding the trivial
            # self-match every file has with its own filename
            f"scripts.{name}" in all_py_text.replace(f"scripts/{filename}", "")
            or f"scripts/{filename}" in all_py_text.replace(f"scripts/{filename}", "", 1)
            or f"import {name}" in all_py_text
            or filename in md_text
            or filename in ci_text
            or (ROOT / "tests" / f"test_{name}.py").exists()
            or f"scripts.{name}" in _all_source_text(("tests/",))
            or f"scripts/{filename}" in _all_source_text(("tests/",))
        )
        if not justified:
            unjustified.append(filename)

    assert not unjustified, (
        "scripts/*.py with no import, scheduler/restart.sh entry, doc "
        "citation, CI reference, or dedicated test — dead code or missing "
        "documentation, pick one and fix it:\n  " + "\n  ".join(unjustified)
    )


# ── 4. every pathiel/agents/*.py is reachable from a real entry point ──────

AGENTS_DIR = ROOT / "pathiel" / "agents"

# Owned by other overhaul agents (see docs/overhaul/SWARM_RULES.md section
# 6) — out of scope for this guard, which only answers for cleanup's own
# pathiel/agents/** ownership.
NOT_CLEANUP_OWNED = {"hyperfeed.py"}

ENTRY_POINT_FILES = (
    ROOT / "pathiel" / "server.py",
    ROOT / "pathiel" / "dashboard.py",
    ROOT / "pathiel" / "__main__.py",
    ROOT / "scripts" / "pathiel-mcp-server.py",   # Claude's trading surface (bracket)
    # 2026-10-23 (wave 2): the autonomous scan/research/execute engine
    # (scripts/trading_loop.py, pathiel/agents/executor.py, risk_gates.py,
    # sizing.py) is retired, so neither the script nor its NOT_CLEANUP_OWNED
    # exemptions apply any more. book_params.py lost its one direct
    # pathiel/agents/*.py importer (executor.py) in the same deletion — it is
    # still genuinely live, just reached from outside pathiel/agents/ now
    # (services/risk/books.py's main_engine_spec and services/risk/migrate.py's
    # legacy-key migration), so those real callers join the BFS roots below
    # rather than being left as a false "unreachable" positive — the same
    # class of bug the NOT_CLEANUP_OWNED tracing fix (see the test's own
    # docstring) existed to catch when executor.py was still the one doing
    # the reaching. (news_catalyst.py and its scripts/news.py CLI, the other
    # pair this note used to cover, were deleted 2026-10-25: services/intel
    # replaced them.)
    ROOT / "services" / "risk" / "books.py",
    ROOT / "services" / "risk" / "migrate.py",
)


def _pathiel_agents_imports(source: str) -> Set[str]:
    """Every `pathiel.agents.<name>` this source statically imports, found
    by walking the AST — catches imports nested inside a function/if-branch
    that a plain substring scan would too, but without the false positives
    a dotted-name substring scan gets from prose/comments mentioning the
    same module name."""
    found: Set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("pathiel.agents."):
                found.add(node.module.rsplit(".", 1)[-1])
            elif node.module == "pathiel.agents":
                for alias in node.names:
                    found.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pathiel.agents."):
                    found.add(alias.name.rsplit(".", 1)[-1])
    return found


def _all_agent_module_names() -> Dict[str, Path]:
    """Every pathiel/agents/*.py, including NOT_CLEANUP_OWNED — used only to
    TRACE imports (see test below), never to decide what must be reachable."""
    return {p.stem: p for p in AGENTS_DIR.glob("*.py") if p.name != "__init__.py"}


def _agent_module_names() -> Dict[str, Path]:
    return {name: path for name, path in _all_agent_module_names().items()
            if path.name not in NOT_CLEANUP_OWNED}


def test_every_cleanup_owned_agent_module_is_reachable():
    modules = _agent_module_names()
    # Trace through EVERY agent module's source, including NOT_CLEANUP_OWNED
    # (executor.py, sizing.py, risk_gates.py, hyperfeed.py): those files are
    # out of scope for WHICH modules they themselves must be, but a cleanup-
    # owned module they import (e.g. executor.py -> book_params.py,
    # shadow_ledger.py) is genuinely reachable and must not be flagged dead
    # just because the entry point's import of it is one hop further than
    # this BFS used to look. Not tracing into them was the actual bug: it
    # made book_params.py/shadow_ledger.py look unreachable from 2026-10-23
    # onward even though pathiel.agents.executor.maybe_execute calls both on
    # every live trade — the headless-desk cleanup's own deletions (killing
    # scripts/autonomous_cycle.py, the one file that imported book_params.py
    # directly) were the first change to expose it.
    all_sources: Dict[str, str] = {}
    for name, path in _all_agent_module_names().items():
        try:
            all_sources[name] = path.read_text(encoding="utf-8")
        except OSError:
            all_sources[name] = ""

    # BFS from every entry point's direct pathiel.agents.* imports, following
    # agent-to-agent imports transitively (e.g. shadow_ledger imports
    # atomic_io: atomic_io is reachable because shadow_ledger is).
    reachable: Set[str] = set()
    frontier: Set[str] = set()
    for entry in ENTRY_POINT_FILES:
        if entry.exists():
            frontier |= _pathiel_agents_imports(entry.read_text(encoding="utf-8"))

    while frontier:
        reachable |= frontier
        next_frontier: Set[str] = set()
        for name in frontier:
            if name in all_sources:
                next_frontier |= _pathiel_agents_imports(all_sources[name])
        frontier = next_frontier - reachable

    unreachable = sorted(set(modules) - reachable)
    assert not unreachable, (
        "pathiel/agents/*.py module(s) not imported, directly or "
        "transitively, by any of "
        f"{[str(e.relative_to(ROOT)) for e in ENTRY_POINT_FILES]}: "
        f"{unreachable} — either wire it in, or it is a leftover recorder "
        "for a killed strategy and should be removed with its tests."
    )


# Timing note: as of 2026-10-17, this whole file (4 tests, ~920 tracked
# files, no subprocess beyond one `git ls-files` call, reused across all
# four) runs in well under 0.2s. If this file ever creeps toward the gate
# suite's 2s budget, the fix is memoizing `_tracked_files()` at module scope
# — not relaxing a guard.
