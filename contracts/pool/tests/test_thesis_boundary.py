"""Pool data feeds GRADING ONLY. It never reaches a thesis prompt or a tick
decision.

Why: SCORECARD_TEST_2026-10-31 put the desk's own graded record into the
thesis prompt and the money result got worse. The pool is a bigger graded
record, so the same rule holds, enforced here instead of in a comment.

Three static walls, checked on every commit:

  1. importers   only the pool itself, its tests and the MCP server's tool
                 registration may import services.pool or contracts.pool
  2. thesis side the code that builds a thesis or runs a tick (services/thesis,
                 services/llm, services/intel, services/strategies, the
                 desk-tick skill and agent files) never names a pool path, a
                 pool tool or the scoreboard
  3. pool side   services/pool and contracts/pool never import the thesis,
                 llm or intel services and never read a theses directory

Files are the tracked ones plus untracked ones git does not ignore, so a
builder's new file is checked before it is ever committed. Offline; the one
subprocess is `git ls-files`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[3]

# 1. who may import the pool
POOL_IMPORT = re.compile(
    r"\b(?:services|contracts)\.pool\b|from\s+(?:services|contracts)\s+import\s+[^\n#]*\bpool\b")
ALLOWED_IMPORTER_PREFIXES = ("services/pool/", "contracts/pool/", "tests/test_pool_",
                             "tests/test_eval_pool_")
ALLOWED_IMPORTER_FILES = {"scripts/pathiel-mcp-server.py"}

# 2. what the thesis side may never name
THESIS_SIDE_DIRS = ("services/thesis/", "services/llm/", "services/intel/", "services/strategies/")
TICK_FILES = (".claude/agents/desk-tick.md", "agents/desk-tick.md",
              ".claude/skills/desk-tick/SKILL.md", "skills/desk-tick/SKILL.md")
POOL_TOKENS = re.compile(
    r"scoreboard|research[ _-]pool|pool/members|pool/leaves|pool/house|pool_status|pool_report|"
    r"pool_join|pool_leave|PATHIEL_POOL|\b(?:services|contracts)\.pool\b|"
    r"from\s+(?:services|contracts)\s+import\s+[^\n#]*\bpool\b", re.IGNORECASE)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".j2", ".tmpl"}

# 3. what the pool may never import or read
THESIS_IMPORT = re.compile(
    r"\bservices\.(?:thesis|llm|intel)\b|from\s+services\s+import\s+[^\n#]*\b(?:thesis|llm|intel)\b")
THESES_READ = re.compile(r"theses_dir|read_theses|load_journaled_theses|strategies[/\\]+\w+[/\\]+theses")


def _files() -> List[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return sorted({line for line in out.stdout.splitlines() if line and (ROOT / line).is_file()})


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def _is_test(rel: str) -> bool:
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def pool_importers(files: List[str]) -> List[str]:
    return [rel for rel in files if rel.endswith(".py") and POOL_IMPORT.search(_read(rel))]


def thesis_side_hits(files: List[str]) -> List[str]:
    hits = []
    for rel in files:
        on_side = rel in TICK_FILES or (rel.startswith(THESIS_SIDE_DIRS)
                                        and Path(rel).suffix in TEXT_SUFFIXES)
        if not on_side:
            continue
        for i, line in enumerate(_read(rel).splitlines(), 1):
            m = POOL_TOKENS.search(line)
            if m:
                hits.append(f"{rel}:{i}: {m.group(0)}")
    return hits


def pool_side_hits(files: List[str]) -> List[str]:
    hits = []
    for rel in files:
        if not rel.endswith(".py") or not rel.startswith(("services/pool/", "contracts/pool/")):
            continue
        if _is_test(rel):
            continue
        for i, line in enumerate(_read(rel).splitlines(), 1):
            for rx in (THESIS_IMPORT, THESES_READ):
                m = rx.search(line)
                if m:
                    hits.append(f"{rel}:{i}: {m.group(0)}")
    return hits


def test_only_the_pool_its_tests_and_the_mcp_server_import_the_pool():
    bad = [rel for rel in pool_importers(_files())
           if not rel.startswith(ALLOWED_IMPORTER_PREFIXES) and rel not in ALLOWED_IMPORTER_FILES]
    assert not bad, f"pool imported from outside the allowlist (pool data feeds grading only): {bad}"


def test_the_thesis_side_never_names_the_pool():
    hits = thesis_side_hits(_files())
    assert not hits, f"the thesis or tick path names pool data: {hits}"


def test_the_pool_never_imports_the_thesis_side_or_reads_theses():
    hits = pool_side_hits(_files())
    assert not hits, f"the pool reaches into the thesis side: {hits}"


def test_the_walls_can_fail(tmp_path, monkeypatch):
    """Each scanner is only proof if it catches a planted breach."""
    import sys
    mod = sys.modules[__name__]
    (tmp_path / "services" / "thesis").mkdir(parents=True)
    (tmp_path / "services" / "pool").mkdir(parents=True)
    (tmp_path / "services" / "thesis" / "prompt.py").write_text(
        "from contracts import pool\nBOARD = 'docs/scoreboard/scoreboard.json'\n")
    (tmp_path / "services" / "thesis" / "notes.md").write_text("read the Research Pool rows\n")
    (tmp_path / "services" / "pool" / "grade.py").write_text(
        "from services.thesis import prompt\nD = theses_dir('oil')\n")
    (tmp_path / "services" / "pool" / "ok.py").write_text("import contracts.pool as pool\n")
    files = ["services/thesis/prompt.py", "services/thesis/notes.md", "services/pool/grade.py",
             "services/pool/ok.py"]
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert pool_importers(files) == ["services/thesis/prompt.py", "services/pool/ok.py"]
    assert len(thesis_side_hits(files)) == 3
    assert len(pool_side_hits(files)) == 2
