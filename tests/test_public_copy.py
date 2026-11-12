"""Gate tests for pathiel's public copy: what a stranger reads before
installing, and everything that copy links one hop away.

Four promises, each pinned here:

1. The README discloses what a public user must know before trading: what it
   does, the three modes, the builder fee and its exact rate, how to approve
   it, how Hyperliquid enforces it, the risk, and the restricted
   jurisdictions (DISCLOSURE_CHECKLIST).
2. No return claim anywhere a public reader lands: public copy and every
   repo doc it links (copy_lint.reach, one hop) have 0 claim hits, and public
   copy links no research doc that carries backtest figures. The live audit
   of 2026-11-12 found no demonstrated edge.
3. The maintainer path (the repo checkout, the repo .mcp.json, a direct
   scripts/pathiel-mcp-server.py launch) runs mainnet with the tracked 20x
   whole-equity config. Every place public copy shows it carries
   MAINTAINER_WARNING.
4. The oil rules doc states the rules the code enforces, with the code's own
   numbers.

The claims and style patterns live in the copy_lint fixture
(tests/conftest.py); this file defines none of its own. Files other units
own (commands/setup.md, docs/BUILDER_FEE.md) are linted when present and
never required to exist.

Offline, no network. The one subprocess is `git grep` on the local index.

    .venv/bin/python -m pytest -q tests/test_public_copy.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_COPY = [
    "README.md",
    "docs/GETTING_STARTED.md",
    "docs/strategy/OIL_RULES.md",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "server.json",
    "smithery.yaml",
    "glama.json",
]
# Owned by other units; linted when they exist.
PUBLIC_COPY_WHEN_PRESENT = ["commands/setup.md", "docs/BUILDER_FEE.md"]

MAINTAINER_WARNING = ("Maintainer path: this runs mainnet with the tracked repo config "
                      "(20x, whole-equity sizing). It is not the public install.")
CLONE_WARNING_WINDOW = 15

PUBLIC_SECTION = "## Public install: read this before you trade"

# (label, marker, phrases the marker's block must hold). A block runs from its
# marker to the next marker, or to the end of the section. Matching is on
# whitespace-normalized text, so line wrapping never matters.
DISCLOSURE_CHECKLIST: List[Tuple[str, str, List[str]]] = [
    ("what it does", "**What it does.**", []),
    ("three modes", "**Three modes.**",
     ["read-only", "testnet (the default)", "PATHIEL_NETWORK=mainnet", "~/.pathiel/.env.local",
      "real money", "kept per network"]),
    ("builder fee", "**Builder fee.**",
     ["0.01% of notional per fill", "0.02% a round trip", "every fill pathiel routes"]),
    ("approval", "**Approving the fee.**",
     ["/pathiel:setup", "](docs/BUILDER_FEE.md)", "Declining is fine", "no builder fee"]),
    ("enforcement", "**How the fee is enforced.**",
     ["rejects any order whose builder fee is above the max you approved",
      "exactly the rate it charges", "0.1%"]),
    ("risk", "**Risk.**",
     ["lose the whole account", "3x", "1% of equity at the stop", "one position",
      "daily loss figure is shown, not enforced", "no demonstrated edge",
      "no return is promised", "not financial advice"]),
    ("restricted jurisdictions", "**Restricted jurisdictions.**",
     ["restricted jurisdictions", "US persons", "Hyperliquid's terms",
      "does not check where you are"]),
]

# A path public copy may name under docs/strategy/. Everything else there is
# research that carries backtest figures.
ALLOWED_STRATEGY_DOC = "OIL_RULES.md"
RESEARCH_DOC_RE = re.compile(r"(?:docs/)?strategy/([\w.-]+\.md)")
BACKTEST_DOC_RE = re.compile(r"BACKTEST_RESULTS[\w.-]*")

OIL_STRATEGY = ROOT / "docs" / "strategy" / "OIL_STRATEGY.md"
OIL_RULES = ROOT / "docs" / "strategy" / "OIL_RULES.md"
OIL_STRATEGY_BANNER = (
    "> Research log, not a performance record. The figures below are backtests over windows "
    "of 12 to 50 days. Live audit, 2026-11-12: 36 graded thesis calls, direction right 32% of "
    "the time; 19 closed trades, average result not distinguishable from zero. No edge is "
    "demonstrated. Nothing here is a forecast. The rules alone are in OIL_RULES.md.")
# Built, not written out: a literal here would be a tracked reference itself.
ROI_DOC = "ROI" + ".md"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def public_copy_files() -> List[str]:
    """PUBLIC_COPY plus the cross-unit files that exist today."""
    return PUBLIC_COPY + [p for p in PUBLIC_COPY_WHEN_PRESENT if (ROOT / p).is_file()]


def section(text: str, heading: str) -> str:
    """The body under `heading` (a full '## ...' line) up to the next heading
    of the same or a higher level. '' when the heading is missing."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading
                     or line.startswith(heading + " ") or line.startswith(heading + ":"))
    except StopIteration:
        return ""
    out: List[str] = []
    for line in lines[start + 1:]:
        m = re.match(r"^(#{1,6}) ", line)
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    return "\n".join(out)


def disclosure_results(readme: str) -> List[Tuple[str, bool, List[str]]]:
    """(label, ok, missing phrases) per checklist entry."""
    body = _flat(section(readme, PUBLIC_SECTION))
    positions = [(label, marker, body.find(marker), phrases)
                 for label, marker, phrases in DISCLOSURE_CHECKLIST]
    found = sorted(p for _, _, p, _ in positions if p >= 0)
    results = []
    for label, marker, pos, phrases in positions:
        if pos < 0:
            results.append((label, False, [marker]))
            continue
        later = [p for p in found if p > pos]
        block = body[pos:later[0] if later else len(body)]
        missing = [ph for ph in phrases if ph not in block]
        if label == "what it does":
            sentences = re.findall(r"[.!?](?:\s|$)", block[len(marker):].strip())
            if len(sentences) != 2:
                missing.append(f"two sentences (found {len(sentences)})")
        results.append((label, not missing, missing))
    return results


def research_doc_mentions(rel: str, copy_lint) -> List[str]:
    """Research docs a public file names or links: any docs/strategy doc but
    OIL_RULES.md, any BACKTEST_RESULTS doc, and any linked repo doc under
    docs/strategy or named like a backtest report."""
    text = _read(rel)
    hits = [f"{rel}: names strategy/{m.group(1)}" for m in RESEARCH_DOC_RE.finditer(text)
            if m.group(1) != ALLOWED_STRATEGY_DOC]
    hits += [f"{rel}: names {m.group(0)}" for m in BACKTEST_DOC_RE.finditer(text)]
    if rel.endswith(".md"):
        for doc in copy_lint.linked_docs(ROOT / rel):
            r = doc.relative_to(ROOT).as_posix()
            if (r.startswith("docs/strategy/") and doc.name != ALLOWED_STRATEGY_DOC) \
                    or "BACKTEST_RESULTS" in r or re.search(r"_TEST_\d{4}-\d\d-\d\d\.md$", r):
                hits.append(f"{rel}: links {r}")
    return sorted(set(hits))


def unwarned_clone_lines(rel: str) -> List[str]:
    """Lines with 'git clone' that have no MAINTAINER_WARNING in the
    CLONE_WARNING_WINDOW lines above them."""
    lines = _read(rel).splitlines()
    bad = []
    for i, line in enumerate(lines):
        if "git clone" not in line:
            continue
        window = lines[max(0, i - CLONE_WARNING_WINDOW):i]
        if not any(MAINTAINER_WARNING in w for w in window):
            bad.append(f"{rel}:{i + 1}: {line.strip()}")
    return bad


def tracked_roi_references() -> List[str]:
    """Tracked files (the git index) naming the gitignored projections file,
    other than .gitignore itself."""
    out = subprocess.run(["git", "-C", str(ROOT), "grep", "-l", "--fixed-strings", ROI_DOC],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode in (0, 1), out.stderr
    return [f for f in out.stdout.splitlines() if f.strip() and f.strip() != ".gitignore"]


def backticked_repo_paths(text: str) -> List[str]:
    """Inline code spans that look like a repo path: a slash, no spaces, no
    URL scheme, not home- or root-anchored, not a coin like xyz:CL."""
    no_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    paths = []
    for span in re.findall(r"`([^`\n]+)`", no_fences):
        if "/" not in span or " " in span or "://" in span or ":" in span:
            continue
        if span.startswith(("/", "~", "$")):
            continue
        paths.append(span)
    return paths


# ── 1. disclosure ───────────────────────────────────────────────────────────

def test_readme_disclosure_checklist():
    readme = _read("README.md")
    assert section(readme, PUBLIC_SECTION), f"README has no '{PUBLIC_SECTION}' section"
    results = disclosure_results(readme)
    assert len(results) == 7
    failed = {label: missing for label, ok, missing in results if not ok}
    assert not failed, f"README disclosure gaps: {failed}"
    flat = _flat(readme)
    for must in ("0.01%", "US persons", "not financial advice"):
        assert must in flat, must
    assert re.search(r"\]\(docs/BUILDER_FEE\.md\)", readme), "no relative link to docs/BUILDER_FEE.md"


def test_public_section_sits_directly_after_read_this_first():
    headings = [line for line in _read("README.md").splitlines() if line.startswith("## ")]
    i = headings.index("## Read this first")
    assert headings[i + 1] == PUBLIC_SECTION, headings[:i + 3]


def test_disclosure_check_fails_on_a_stripped_readme():
    """The checklist is only proof if it can fail: drop the jurisdiction
    paragraph and the fee rate, and both must be reported."""
    readme = _read("README.md")
    stripped = readme.replace("**Restricted jurisdictions.**", "**Elsewhere.**")
    stripped = stripped.replace("0.01% of", "a small fee of")
    failed = {label for label, ok, _ in disclosure_results(stripped) if not ok}
    assert {"restricted jurisdictions", "builder fee"} <= failed, failed


def test_fee_rate_in_readme_matches_the_runtime_default():
    from contracts import runtime

    fee = runtime.DEFAULT_BUILDER_FEE_TENTHS_BP
    per_fill = runtime.fee_rate_str(fee)
    round_trip = runtime.fee_rate_str(2 * fee)
    cap = runtime.fee_rate_str(runtime.MAX_PERP_BUILDER_FEE_TENTHS_BP)
    flat = _flat(_read("README.md"))
    assert f"{per_fill} of notional per fill" in flat
    assert f"{round_trip} a round trip" in flat
    assert f"capped at {cap}" in flat or f"cap is {cap}" in flat or f"at {cap}" in flat
    # Every other public file that states a builder fee rate states this one.
    for rel in public_copy_files():
        for m in re.finditer(r"(\d+(?:\.\d+)?%) builder fee", _flat(_read(rel))):
            assert m.group(1) == per_fill, f"{rel} states a {m.group(1)} builder fee"


# ── 2. claims ───────────────────────────────────────────────────────────────

def test_reach_set_has_no_performance_claims(copy_lint):
    files = copy_lint.reach([ROOT / p for p in public_copy_files()])
    assert len(files) >= len(PUBLIC_COPY), "reach lost a seed"
    hits: Dict[str, List[str]] = {}
    for path in files:
        found = copy_lint.claim_hits(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            hits[path.relative_to(ROOT).as_posix()] = found
    assert not hits, f"return claims a public reader can reach: {hits}"


def test_public_copy_links_no_research_docs(copy_lint):
    hits = [h for rel in public_copy_files() for h in research_doc_mentions(rel, copy_lint)]
    assert not hits, hits
    for rel in ("README.md", "docs/GETTING_STARTED.md"):
        text = _read(rel)
        assert "OIL_RULES.md" in text, f"{rel} should point at the clean rules doc"


def test_no_em_dash_or_banned_word_in_public_copy(copy_lint):
    hits = {rel: copy_lint.style_hits(_read(rel)) for rel in public_copy_files()}
    hits = {k: v for k, v in hits.items() if v}
    assert not hits, hits


def test_no_tracked_file_references_roi_md():
    assert tracked_roi_references() == []


def test_oil_strategy_has_the_banner_and_no_roi_section():
    text = OIL_STRATEGY.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("# ")
    body = [line for line in lines[1:] if line.strip()]
    assert body[0] == OIL_STRATEGY_BANNER, body[0]
    assert not re.search(r"^## ROI\b", text, re.MULTILINE)
    assert ROI_DOC not in text
    summary = section(text, "## Backtest summary")
    assert summary.strip(), "the ROI section's replacement is missing"
    assert len([line for line in summary.splitlines() if line.strip()]) <= 6
    assert "2816df7" in summary
    assert not re.search(r"\d+(\.\d+)?\s?%", summary), "no return rates in the summary"
    assert "$" not in summary, "no dollar paths in the summary"


def test_oil_strategy_keeps_the_weekend_pins():
    """services/strategies/tests/test_weekend.py reads these two strings."""
    text = OIL_STRATEGY.read_text(encoding="utf-8")
    assert "**Live: weekend flat.**" in text and "5 of 8" in text


def test_oil_rules_matches_the_code_minimum_r_and_its_paths_exist(copy_lint):
    from pathiel.agents import bracket
    from services.strategies import event_blackout, next_trade, weekend

    text = OIL_RULES.read_text(encoding="utf-8")
    flat = _flat(text)
    assert f"{bracket.DEFAULT_MIN_R:g}R" in flat
    assert f"{bracket.TARGET_OFFSET_FRAC * 100:g}% of price" in flat
    assert "half the distance to liquidation" in flat and bracket.DEFAULT_MAX_STOP_FRAC_OF_LIQ == 0.5
    assert f"{next_trade.BRENT_STOP_BUFFER:g} beyond the 24h range" in flat
    assert f"{next_trade.ATR_BUFFER_MULT:g} times the 1h ATR14" in flat
    assert f"{next_trade.STOP_BUFFER_FLOOR_FRAC * 100:g}% of the mid" in flat
    close_day, close_min = weekend.WEEKEND_CLOSE_NY
    open_day, open_min = weekend.WEEKEND_OPEN_NY
    assert (close_day, open_day) == (4, 6), "the doc says Friday to Sunday"
    assert f"Friday {close_min // 60:02d}:{close_min % 60:02d} to Sunday " \
           f"{open_min // 60:02d}:{open_min % 60:02d} New York" in flat
    assert f"{event_blackout.BEFORE_H:g} hours before to {event_blackout.AFTER_H:g} hours after" in flat
    due = (ROOT / "scripts" / "thesis_due.py").read_text(encoding="utf-8")
    move = float(re.search(r"^MOVE_FRAC\s*=\s*([0-9.]+)", due, re.MULTILINE).group(1))
    assert f"moved {move * 100:g}%" in flat

    assert "## What is not known" in text
    for fact in ("36 thesis calls", "32% of the time", "19 closed trades",
                 "cannot be told apart from zero", "no demonstrated edge"):
        assert fact in flat, fact

    assert copy_lint.claim_hits(text) == []
    assert copy_lint.style_hits(text) == []
    paths = backticked_repo_paths(text)
    assert paths, "the rules doc should name the files that enforce each rule"
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, f"OIL_RULES.md names paths that do not exist: {missing}"


# ── 3. maintainer path ──────────────────────────────────────────────────────

def test_every_clone_instruction_carries_the_maintainer_warning():
    bad = unwarned_clone_lines("README.md") + unwarned_clone_lines("docs/GETTING_STARTED.md")
    assert not bad, bad
    assert "git clone" in _read("docs/GETTING_STARTED.md"), "the maintainer checkout block is gone"


def test_direct_server_launch_is_marked_maintainer_only():
    for rel in public_copy_files():
        text = _read(rel)
        if "pathiel-mcp-server.py" in text:
            assert MAINTAINER_WARNING in text, f"{rel} shows the direct server without the warning"
    readme = _read("README.md")
    for heading in ("## MCP server", "## Running it",
                    "## Risk: the live profile and the rules place_bracket enforces"):
        assert MAINTAINER_WARNING in section(readme, heading), heading
    assert "can register the same server" not in readme
    mcp = _flat(section(readme, "## MCP server"))
    assert "scripts/pathiel-mcp`" in mcp and "maintainer path" in mcp


def test_readme_no_longer_claims_mode_off_or_an_edge():
    readme = _read("README.md")
    for gone in ("The edge is the thesis", "mode OFF", "+6.03R", "can register the same server",
                 "beat the market target"):
        assert gone not in _flat(readme), gone


def test_readme_keeps_tool_count_and_getting_started_link():
    readme = _read("README.md")
    assert "60 tools over stdio" in readme
    assert "docs/GETTING_STARTED.md" in readme
    for tool in ("place_bracket", "bracket_sweep", "close_position", "set_leverage",
                 "cancel_order", "connected_wallets", "wallet_account", "desk_brief"):
        assert f"`{tool}`" in readme, tool


@pytest.mark.parametrize("rel", PUBLIC_COPY)
def test_every_public_copy_file_exists(rel):
    assert (ROOT / rel).is_file(), rel
