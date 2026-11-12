"""Nothing a public plugin user receives, runs, or has sent to their model
depends on the operator's machine, wallet, name or 20x profile, and nothing
the plugin ships links a doc that carries return figures.

SHIPPED is what the Claude Code plugin hands a stranger: the tracked files
under skills/, agents/, commands/ and .claude-plugin/, plus the three launcher
scripts. The desk-tick skill and agent there are GENERATED from the
operator's .claude/ copies by scripts/build_plugin_skill.py; the generator's
rules strip the operator numbers and research-doc pointers, and these tests
fail when a later edit to the operator source slips one through.

Limits: the MACHINE_BOUND scan reads files, not Python string literals in the
server; the persona check covers services/thesis build_system_prompt only.
"""
from __future__ import annotations

import importlib.util
import sys
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SHIPPED_DIRS = ("skills/", "agents/", "commands/", ".claude-plugin/")
SHIPPED_SCRIPTS = ("scripts/_plugin_env.sh", "scripts/pathiel-mcp", "scripts/pathiel-py")

MACHINE_BOUND = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"/(?:Users)/[A-Za-z0-9_.-]+/",   # grouped so the path checker reads a regex, not a path
    r"/(?:home)/[A-Za-z0-9_.-]+/",
    r"localhost:8000",
    r"127\.0\.0\.1:8000",
    r"julian_dev",
    r"0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985",   # the operator's trading wallet
))
OPERATOR_PERSONA = (re.compile(r"\bJulien\b"), re.compile(r"\bADHD\b"))

GENERATED = ("agents/desk-tick.md", "skills/desk-tick/SKILL.md")
OPERATOR_SOURCES = (".claude/agents/desk-tick.md", ".claude/skills/desk-tick/SKILL.md")
OPERATOR_NUMBERS = (
    re.compile(r"\b(20|15)x\b"),
    re.compile(re.escape("--lev 20")),
    re.compile(re.escape("oil 90%, metals 10%")),
)
RESEARCH_DOCS = (re.compile(r"docs/strategy/"), re.compile(r"docs/overhaul/BACKTEST_RESULTS"))


def _tracked(*prefixes: str) -> list:
    out = subprocess.run(["git", "ls-files", "-z", "--", *prefixes], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return sorted(p for p in out.split("\0") if p)


def shipped_files() -> list:
    files = _tracked(*SHIPPED_DIRS)
    files += [p for p in SHIPPED_SCRIPTS if (ROOT / p).is_file()]
    return sorted(set(files))


def machine_bound_hits(paths) -> list:
    hits = []
    for rel in paths:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for rx in MACHINE_BOUND:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{rel}:{line}: {m.group(0)}")
    return hits


def pattern_hits(paths, patterns) -> list:
    hits = []
    for rel in paths:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for rx in patterns:
            hits += [f"{rel}: {m.group(0)!r}" for m in rx.finditer(text)]
    return hits


def load_generator():
    spec = importlib.util.spec_from_file_location("build_plugin_skill_probe",
                                                  ROOT / "scripts" / "build_plugin_skill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def public_thesis_prompt(monkeypatch) -> str:
    from contracts.strategies.fixtures import _build
    from services.thesis import prompt
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    return prompt.build_system_prompt(_build.spec)


def persona_hits(text: str) -> list:
    return [m.group(0) for rx in OPERATOR_PERSONA for m in rx.finditer(text)]


def reach_claim_hits(copy_lint):
    seeds = _tracked("skills/", "agents/", "commands/")
    reach = copy_lint.reach(ROOT / s for s in seeds)
    out = {}
    for path in reach:
        hits = copy_lint.claim_hits(path.read_text(encoding="utf-8", errors="replace"))
        if hits:
            out[path.relative_to(ROOT).as_posix()] = hits
    return out, reach


# ── tests ───────────────────────────────────────────────────────────────────

def test_shipped_set_is_what_the_plugin_ships():
    files = shipped_files()
    for rel in GENERATED + ("commands/setup.md", ".claude-plugin/plugin.json",
                            "skills/pathiel-agent/SKILL.md") + SHIPPED_SCRIPTS:
        assert rel in files, rel


def test_no_machine_bound_strings_in_shipped_files():
    assert machine_bound_hits(shipped_files()) == []


def test_the_machine_bound_scan_catches_a_planted_path():
    text = ("cd /Users/someone/code && open http://localhost:8000/\n"
            "ssh box:/home/ops/pathiel  127.0.0.1:8000  julian_dev\n"
            "wallet 0x2C2E4EFF574B3FD9879ADEF9C5CAC6FD7C916985\n")
    hits = [m.group(0) for rx in MACHINE_BOUND for m in rx.finditer(text)]
    assert len(hits) == 6


def test_generated_tick_files_carry_no_operator_leverage_or_shares():
    assert pattern_hits(GENERATED, OPERATOR_NUMBERS) == []


def test_generated_tick_files_reference_no_research_docs():
    assert pattern_hits(GENERATED, RESEARCH_DOCS) == []


def test_generated_tick_files_point_at_risk_limits():
    agent = (ROOT / "agents" / "desk-tick.md").read_text(encoding="utf-8")
    flat = " ".join(agent.split())
    assert "`max_leverage` that `risk_limits` reports (a fresh install is 3x" in flat
    assert "`place_bracket` caps and fits it by itself" in flat
    assert "scripts/oil_levels.py --check ENTRY STOP TP --lev L`" in flat
    assert "scripts/oil_levels.py --coin COIN --next SIDE --thesis-entry ENTRY --risk-lev L`" in flat
    # one definition of L per oil_levels.py call that takes it (--check, --next)
    assert flat.count("L is the `max_leverage` that `risk_limits` reports") == 2
    assert "where L is the `max_leverage` that `risk_limits` reports" in flat
    assert "On testnet a tool says plainly when a coin is not tradable there" in flat


def test_generator_output_is_current():
    gen = load_generator()
    for out, text in gen.render_all():
        assert out.read_text(encoding="utf-8") == text, f"rerun scripts/build_plugin_skill.py ({out})"
    proc = subprocess.run([sys.executable,
                           str(ROOT / "scripts" / "build_plugin_skill.py"), "--check"],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_operator_tick_sources_are_untouched(tmp_path, monkeypatch):
    """The generator reads the operator's .claude/ copies and never writes
    them. Every real output sits outside .claude/, and a full write run on a
    copy of the tree (ROOT and JOBS pointed at tmp_path) writes the two plugin
    copies and leaves both sources byte-identical. The sources stay the
    operator's to edit, so this pins the generator's behaviour, not their
    bytes (this unit left them identical to 2816df7; see its commit)."""
    gen = load_generator()
    assert sorted(s.relative_to(ROOT).as_posix() for s, _o, _r in gen.JOBS) == sorted(OPERATOR_SOURCES)
    assert sorted(o.relative_to(ROOT).as_posix() for _s, o, _r in gen.JOBS) == sorted(GENERATED)
    jobs = []
    for src, out, rules in gen.JOBS:
        copy = tmp_path / src.relative_to(ROOT)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(src.read_bytes())
        jobs.append((copy, tmp_path / out.relative_to(ROOT), rules))
    before = {s: s.read_bytes() for s, _o, _r in jobs}
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "JOBS", tuple(jobs))
    assert gen.main([]) == 0
    assert {s: s.read_bytes() for s, _o, _r in jobs} == before
    for _src, out, _rules in jobs:
        assert out.read_text(encoding="utf-8") == (ROOT / out.relative_to(tmp_path)).read_text(encoding="utf-8")
    assert gen.main(["--check"]) == 0


def test_pathiel_agent_skill_names_the_public_layout():
    text = (ROOT / "skills" / "pathiel-agent" / "SKILL.md").read_text(encoding="utf-8")
    head = text[:text.index("## How a trade happens now")]
    for needle in ("~/.pathiel", "testnet", "PATHIEL_NETWORK=mainnet", "`risk_limits`"):
        assert needle in head, needle
    assert "60 tools" in text


def test_public_thesis_prompt_has_no_operator_name(monkeypatch):
    text = public_thesis_prompt(monkeypatch)
    assert "Oil desk" in text
    assert persona_hits(text) == []


def test_shipped_reach_set_has_no_performance_claims(copy_lint):
    hits, reach = reach_claim_hits(copy_lint)
    rels = {p.relative_to(ROOT).as_posix() for p in reach}
    assert set(GENERATED) <= rels
    assert hits == {}


@pytest.mark.parametrize("planted", [
    "Run with --lev 20 on Brent.",
    "Leverage 20x since October.",
    "See docs/strategy/OIL_STRATEGY.md for the numbers.",
])
def test_the_tick_checks_catch_a_planted_line(planted):
    hits = [m.group(0) for rx in OPERATOR_NUMBERS + RESEARCH_DOCS for m in rx.finditer(planted)]
    assert hits
