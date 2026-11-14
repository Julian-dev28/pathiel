"""Guards against the three ways Midnight Expert's fixes never reached its
users (midnight-takehome-assignment/ADDENDUM.md and MEMO.md, 2026-11-14):

1. release lag   main is fixed, the marketplace ships an old commit
                 -> scripts/plugin_freshness.py (daily, .github/workflows/freshness.yml)
2. stale content shipped Markdown steers agents to things that no longer exist
                 -> the shipped-content gate below (every commit)
3. dead links    the docs point at retired URLs
                 -> scripts/link_check.py (daily)
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = _load("plugin_freshness")
L = _load("link_check")
DAY = 86400.0
NOW = 1_800_000_000.0


# ── 1. release lag ──────────────────────────────────────────────────────────

def test_nothing_pending_is_fresh():
    assert F.decide([], NOW) == (True, "fresh: the plugin branch ships everything main would")


def test_recent_changes_are_pending_not_stale():
    ok, msg = F.decide([("a" * 40, NOW - 3 * DAY, "fix x", ["scripts/x.py"])], NOW)
    assert ok and msg.startswith("pending: 1")


def test_a_shipped_change_older_than_seven_days_fails():
    ok, msg = F.decide([("b" * 40, NOW - 8 * DAY, "fix y", ["commands/setup.md"]),
                        ("c" * 40, NOW - 1 * DAY, "fix z", ["scripts/x.py"])], NOW)
    assert not ok and "STALE: 2 unreleased" in msg and "bbbbbbb" in msg


def test_a_lock_change_fails_after_two_days():
    """Dependency pins carry security fixes (the msgpack advisory): 2 days."""
    ok, msg = F.decide([("d" * 40, NOW - 3 * DAY, "fix(deps): msgpack", ["requirements.lock"])], NOW)
    assert not ok and "requirements.lock changed" in msg
    ok, _ = F.decide([("d" * 40, NOW - 1 * DAY, "fix(deps): msgpack", ["requirements.lock"])], NOW)
    assert ok


def test_the_published_source_is_read_from_the_plugin_commit():
    subject = F.B._git("log", "-1", "--format=%s", "origin/plugin").strip() if subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "origin/plugin"], cwd=ROOT, capture_output=True).returncode == 0 else ""
    if not subject:
        pytest.skip("no origin/plugin in this checkout")
    assert re.search(r"\bfrom [0-9a-f]{7,40}\b", subject), subject
    assert F.published_source("origin/plugin")


# ── 2. stale shipped content ────────────────────────────────────────────────

def _tool_names():
    _spec = importlib.util.spec_from_file_location("pathiel_mcp_server_fresh", ROOT / "scripts" / "pathiel-mcp-server.py")
    m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(m)
    import contracts.mcp_public as P
    every = {t["name"] for t in m.TOOLS + m.PUBLIC_ONLY_TOOLS} | set(m.ALIASES) | set(P.NAMES)
    return set(P.NAMES), every


def _steering_files():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    out = ["README.md"]
    for f in tracked:
        top = f.split("/")[0]
        if f.endswith(".md") and (top in ("commands", "agents") or (top == "skills" and f.endswith("/SKILL.md"))):
            out.append(f)
    return out


def test_shipped_steering_files_name_only_tools_a_plugin_user_can_see():
    """A model can only call listed tools. The plugin's desk-tick agent named
    account_overview after a plugin install stopped listing it (fixed
    2026-11-14); this keeps every agent-facing file on the listed names."""
    public, every = _tool_names()
    stale = {}
    for f in _steering_files():
        text = (ROOT / f).read_text()
        found = {w for w in re.findall(r"(?<![A-Za-z0-9_./-])([a-z]+_[a-z_]+)(?![A-Za-z0-9_(])", text)
                 if w in every and w not in public}
        if found:
            stale[f] = sorted(found)
    assert stale == {}, stale


def test_no_shipped_file_claims_the_old_tool_count():
    for f in _steering_files():
        assert not re.search(r"\b65 (MCP )?tools\b", (ROOT / f).read_text()), f


# ── 3. links ────────────────────────────────────────────────────────────────

def test_link_extraction_skips_placeholders_and_local_addresses():
    text = ("see https://example.org/a. and <https://x.y/{id}> and http://localhost:8000/ "
            "https://127.0.0.1:9/x https://github.com/Julian-dev28/pathiel/blob/main/README.md)")
    assert L.extract(text) == {"https://example.org/a",
                               "https://github.com/Julian-dev28/pathiel/blob/main/README.md"}


def test_repo_links_are_checked_against_the_tree():
    tracked = {"docs/PRIVACY.md", "docs/strategy/OIL_RULES.md"}
    base = "https://github.com/Julian-dev28/pathiel/blob/main/"
    assert L.repo_path_ok(base + "docs/PRIVACY.md#leaving", tracked)
    assert L.repo_path_ok("https://github.com/Julian-dev28/pathiel/tree/main/docs/strategy", tracked)
    assert not L.repo_path_ok(base + "docs/GONE.md", tracked)


@pytest.mark.parametrize("status,want", [(200, "ok"), (301, "ok"), (403, "unverified"), (429, "unverified"),
                                         (404, "broken"), (410, "broken"), (500, "broken"), (0, "broken")])
def test_link_verdicts(status, want):
    assert L.verdict(status) == want


def test_every_repo_link_in_the_shipped_files_exists():
    """The offline half of the link check, on every commit."""
    tracked = set(subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split())
    bad = []
    for f in L.files():
        for u in L.extract(f.read_text(errors="replace")):
            if L.REPO_PREFIX.match(u) and not L.repo_path_ok(u, tracked):
                bad.append((str(f.relative_to(ROOT)), u))
    assert bad == []


# ── seo_compare: the token goes to the GitHub API only ─────────────────────

@pytest.mark.parametrize("url,sends", [
    ("https://api.github.com/repos/a/b", True),
    ("https://api.github.com/search/repositories?q=x", True),
    ("http://api.github.com/repos/a/b", False),                 # never over plain http
    ("https://evil.example/?u=https://api.github.com/", False),
    ("https://api.github.com.evil.example/repos", False),
    ("https://evil.example/api.github.com", False),
    ("https://glama.ai/mcp/servers/a/b", False),
])
def test_the_github_token_goes_only_to_the_github_api(url, sends):
    assert _load("seo_compare").sends_token(url) is sends
