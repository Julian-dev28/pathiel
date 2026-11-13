"""What .dockerignore lets into a Docker build context, decided the way
Docker decides it.

Why this exists: the research pool ships docs/scoreboard/ in the MCP image so
pool_status can show the pool's counts, and .dockerignore excluded `docs/`
whole. A `COPY docs/scoreboard/` then fails the build ("/docs/scoreboard": not
found; reproduced with Docker 29.6.2 on 2026-11-12). The fix is the line
`!docs/scoreboard/` AFTER `docs/`. The older checks (tests/test_plugin.py,
tests/test_deploy_config.py) compare COPY sources with the ignore file's
lines one to one, so they pass on a parent directory that is excluded.

`ignored()` below is Docker's rule: patterns in file order, a pattern matches
a path or any of its parent directories, the LAST match wins, `!` re-includes.
`*` and `?` stop at a slash, `**` does not; a pattern without a slash is tied
to the context root (`*.md` is root markdown only). Gate lane: no Docker, no
network. One opt-in test (PATHIEL_DOCKER_TESTS=1) asks a real Docker for the
same answers, so the rule here cannot drift from the tool.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILES = ("Dockerfile", "Dockerfile.mcp")

# path -> is it in the build context? Every row was confirmed against real
# Docker (BuildKit and the legacy builder, 29.6.2) with the committed file.
CASES = [
    ("docs/scoreboard/scoreboard.json", True),
    ("docs/scoreboard/index.html", True),
    ("docs/scoreboard/scoreboard.js", True),
    ("docs/scoreboard/scoreboard.css", True),
    ("pool/house.json", True),
    ("pool/members/0x2c2e4eff574b3fd9879adef9c5cac6fd7c916985.json", True),
    ("pool/leaves/.gitkeep", True),
    ("README.md", True),
    ("services/pool/venue.py", True),
    ("docs/PRIVACY.md", False),
    ("docs/overhaul/RESEARCH_POOL_2026-11-12.md", False),
    ("docs/strategy/data.json", False),
    ("docs/.nojekyll", False),
    ("CHANGELOG.md", False),
    (".env.local", False),
    (".agent-config.json", False),
    (".state/pool/ledger/0xabc.jsonl", False),
    (".git/config", False),
    ("tests/test_plugin.py", False),
    ("services/pathiel_data_api/app.py", False),
]


def _patterns(text: str) -> List[Tuple[bool, "re.Pattern[str]"]]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keep = line.startswith("!")
        pat = line[1:].strip() if keep else line
        pat = pat.lstrip("/").rstrip("/")
        if pat.startswith("./"):
            pat = pat[2:]
        rx, i = "", 0
        while i < len(pat):
            if pat.startswith("**/", i):
                rx, i = rx + "(?:.*/)?", i + 3
            elif pat.startswith("**", i):
                rx, i = rx + ".*", i + 2
            elif pat[i] == "*":
                rx, i = rx + "[^/]*", i + 1
            elif pat[i] == "?":
                rx, i = rx + "[^/]", i + 1
            else:
                rx, i = rx + re.escape(pat[i]), i + 1
        out.append((keep, re.compile(rx + r"\Z")))
    return out


def ignored(path: str, text: str) -> bool:
    """Is `path` (relative, forward slashes) left OUT of the build context?"""
    parts = path.strip("/").split("/")
    candidates = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]     # the path and its parents
    out = False
    for keep, rx in _patterns(text):
        if any(rx.match(c) for c in candidates):
            out = not keep
    return out


def _copy_sources(dockerfile: str) -> List[str]:
    out = []
    for line in (ROOT / dockerfile).read_text().splitlines():
        line = line.strip()
        if line.startswith("COPY ") and "--from=" not in line:
            out += [tok for tok in line.split()[1:-1] if not tok.startswith("--")]
    return out


def _tracked(prefix: str) -> List[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", prefix],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


@pytest.mark.parametrize("path,included", CASES)
def test_what_the_ignore_file_lets_into_the_context(path, included):
    assert ignored(path, DOCKERIGNORE.read_text()) is (not included), path


def test_the_scoreboard_exception_comes_after_the_docs_exclusion():
    lines = [ln.strip() for ln in DOCKERIGNORE.read_text().splitlines()]
    assert lines.index("!docs/scoreboard/") > lines.index("docs/"), "the last matching pattern wins"
    # the exception is the scoreboard alone: every other docs path stays out
    text = DOCKERIGNORE.read_text()
    for path in _tracked("docs"):
        assert ignored(path, text) is (not path.startswith("docs/scoreboard/")), path


def test_the_rule_can_fail():
    """The same checks on the ignore file as it was before the fix, and with
    the two lines in the wrong order: the scoreboard is left out."""
    text = DOCKERIGNORE.read_text()
    before = text.replace("!docs/scoreboard/\n", "")
    assert before != text
    assert ignored("docs/scoreboard/scoreboard.json", before)
    assert ignored("docs/scoreboard/index.html", before)
    wrong_order = before.replace("docs/\n", "!docs/scoreboard/\ndocs/\n", 1)
    assert ignored("docs/scoreboard/scoreboard.json", wrong_order)
    assert not ignored("pool/house.json", before)


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_every_copy_source_reaches_the_context(dockerfile):
    """A COPY whose source the ignore file removes fails the build (a
    directory) or ships nothing. For every COPY source of both Dockerfiles at
    least one file under it must be in the context. An integrator who adds
    `COPY docs/scoreboard/ ...` or `COPY pool/ ...` is covered by this test
    with no edit here."""
    text = DOCKERIGNORE.read_text()
    for src in _copy_sources(dockerfile):
        rel = src.rstrip("/")
        files = _tracked(rel) if (ROOT / rel).is_dir() else [rel]
        assert files, f"{dockerfile}: COPY {src} names nothing tracked"
        assert any(not ignored(f, text) for f in files), \
            f"{dockerfile}: COPY {src} is removed from the build context by .dockerignore"


@pytest.mark.skipif(os.environ.get("PATHIEL_DOCKER_TESTS") != "1" or shutil.which("docker") is None,
                    reason="opt-in: PATHIEL_DOCKER_TESTS=1 and a running Docker (no network: FROM scratch)")
def test_real_docker_agrees_with_the_rule(tmp_path):
    """Plant one file per CASES row in a throwaway context with the repo's
    .dockerignore, export the context through a FROM scratch build, and
    compare what Docker shipped with what `ignored()` says."""
    ctx, out = tmp_path / "ctx", tmp_path / "out"
    ctx.mkdir()
    shutil.copy(DOCKERIGNORE, ctx / ".dockerignore")
    for path, _ in CASES:
        target = ctx / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")
    dockerfile = tmp_path / "Dockerfile.context"
    dockerfile.write_text("FROM scratch\nCOPY . /ctx/\n")
    subprocess.run(["docker", "buildx", "build", "-q", "-f", str(dockerfile),
                    "--output", f"type=local,dest={out}", str(ctx)],
                   check=True, capture_output=True, timeout=120)
    shipped = {p.relative_to(out / "ctx").as_posix() for p in (out / "ctx").rglob("*") if p.is_file()}
    text = DOCKERIGNORE.read_text()
    for path, included in CASES:
        assert (path in shipped) is included, f"Docker: {path}"
        assert (path in shipped) is (not ignored(path, text)), f"the rule here disagrees with Docker: {path}"


# ── integration: the MCP image ships the pool's data, the dashboard image
# does not (that image cannot run the build) ────────────────────────────────

def test_dockerfile_mcp_copies_pool_and_scoreboard():
    sources = _copy_sources("Dockerfile.mcp")
    assert "pool/" in sources, "Dockerfile.mcp has no COPY pool/ pool/"
    assert "docs/scoreboard/" in sources, "Dockerfile.mcp has no COPY docs/scoreboard/ docs/scoreboard/"


def test_dockerfile_dashboard_never_copies_pool_or_scoreboard_data():
    """Dockerfile (the dashboard image) ships services/pool/'s CODE
    (python3 -m services.pool), never pool/ or docs/scoreboard/: it cannot
    run the scoreboard build (see the COPY services/pool/ comment there)."""
    sources = _copy_sources("Dockerfile")
    assert "pool/" not in sources
    assert "docs/scoreboard/" not in sources


def test_the_scoreboard_files_reach_the_mcp_image():
    text = DOCKERIGNORE.read_text()
    for path in ("docs/scoreboard/scoreboard.json", "docs/scoreboard/index.html"):
        assert (ROOT / path).is_file(), f"{path} is not committed"
        assert ignored(path, text) is False, path


def test_at_least_one_pool_file_reaches_the_mcp_image():
    text = DOCKERIGNORE.read_text()
    tracked = _tracked("pool")
    assert tracked, "no tracked file under pool/"
    reaching = [p for p in tracked if not ignored(p, text)]
    assert reaching, f"every tracked pool/ file is removed by .dockerignore: {tracked}"
