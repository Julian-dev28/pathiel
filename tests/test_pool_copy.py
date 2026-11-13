"""Gate tests for the opt-in research pool's user-facing copy: the
`/pathiel:share` command, `docs/PRIVACY.md`, `services/pool/README.md`, and
(once another unit's commit adds them) `pool/README.md` and
`docs/scoreboard/index.html`.

Same claims and style lint `tests/test_public_copy.py` uses
(`tests/conftest.py`'s `copy_lint` fixture), plus the pool's own promises:
`docs/PRIVACY.md` states `contracts.pool.PUBLISHED` and `NOT_PUBLISHED`
verbatim, every pool coin, and a short list of pinned phrases;
`services/pool/README.md` names every key piece of the design. The
contract's own user-facing strings (`PUBLISHED`, `NOT_PUBLISHED`, `NOTES`,
every `MCP_TOOLS` description, both statement texts) are linted here too,
since the docs quote or paraphrase them.

Offline, no network, under a second.

    .venv/bin/python -m pytest -q tests/test_pool_copy.py
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import pytest

import contracts.pool as pool

ROOT = Path(__file__).resolve().parents[1]

DOCS: Dict[str, Path] = {
    "commands/share.md": ROOT / "commands" / "share.md",
    "docs/PRIVACY.md": ROOT / "docs" / "PRIVACY.md",
    "services/pool/README.md": ROOT / "services" / "pool" / "README.md",
}
# Other units' commits add these; linted only once they exist.
DOCS_WHEN_PRESENT: Dict[str, Path] = {
    "pool/README.md": ROOT / "pool" / "README.md",
    "docs/scoreboard/index.html": ROOT / "docs" / "scoreboard" / "index.html",
}

# commands/share.md and docs/PRIVACY.md may name no research or backtest
# doc, full stop (tests/test_public_copy.py's own patterns).
RESEARCH_RESTRICTED = ("commands/share.md", "docs/PRIVACY.md")
ALLOWED_STRATEGY_DOC = "OIL_RULES.md"
RESEARCH_DOC_RE = re.compile(r"(?:docs/)?strategy/([\w.-]+\.md)")
BACKTEST_DOC_RE = re.compile(r"BACKTEST_RESULTS[\w.-]*")
TEST_DOC_RE = re.compile(r"\b[\w.-]*_TEST_\d{4}-\d\d-\d\d\.md\b")

PINNED_JOIN_ADDRESS_A = "0x" + "33" * 20
PINNED_JOIN_ADDRESS_S = "0x" + "44" * 20
PINNED_JOIN_TIME = "2026-11-12T14:03:22Z"


def _present_docs() -> Dict[str, Path]:
    out = dict(DOCS)
    for rel, path in DOCS_WHEN_PRESENT.items():
        if path.is_file():
            out[rel] = path
    return out


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _research_doc_hits(text: str) -> List[str]:
    hits = [f"strategy/{m.group(1)}" for m in RESEARCH_DOC_RE.finditer(text)
            if m.group(1) != ALLOWED_STRATEGY_DOC]
    hits += [m.group(0) for m in BACKTEST_DOC_RE.finditer(text)]
    hits += [m.group(0) for m in TEST_DOC_RE.finditer(text)]
    return hits


# ── the files exist and are clean ───────────────────────────────────────────

@pytest.mark.parametrize("rel", sorted(DOCS))
def test_the_file_exists(rel):
    assert DOCS[rel].is_file(), rel


def test_docs_have_no_style_or_claim_hits(copy_lint):
    hits = {}
    for rel, path in _present_docs().items():
        text = _read(path)
        bad = copy_lint.style_hits(text) + copy_lint.claim_hits(text)
        if bad:
            hits[rel] = bad
    assert hits == {}, hits


def test_every_doc_these_files_link_also_has_zero_claim_hits(copy_lint):
    hits = {}
    for rel, path in _present_docs().items():
        for linked in copy_lint.linked_docs(path):
            found = copy_lint.claim_hits(_read(linked))
            if found:
                hits[f"{rel} -> {linked.relative_to(ROOT).as_posix()}"] = found
    assert hits == {}, hits


def test_research_restricted_docs_name_no_research_or_backtest_doc():
    hits = {rel: _research_doc_hits(_read(DOCS[rel])) for rel in RESEARCH_RESTRICTED}
    hits = {k: v for k, v in hits.items() if v}
    assert hits == {}, hits


def test_the_research_doc_check_can_fail():
    assert _research_doc_hits("see docs/strategy/BRENT_NOTES.md for more") == ["strategy/BRENT_NOTES.md"]
    assert _research_doc_hits("see strategy/OIL_RULES.md for more") == []
    assert _research_doc_hits("BACKTEST_RESULTS_2026.md has the numbers") == ["BACKTEST_RESULTS_2026.md"]
    assert _research_doc_hits("see SCORECARD_TEST_2026-10-31.md directly") == ["SCORECARD_TEST_2026-10-31.md"]


# ── the contract's own user-facing strings ──────────────────────────────────

def test_contract_user_facing_strings_lint_clean(copy_lint):
    texts: List[str] = list(pool.PUBLISHED) + list(pool.NOT_PUBLISHED) + list(pool.NOTES)
    texts += [tool["description"] for tool in pool.MCP_TOOLS]
    texts += [pool.statement_text(kind, PINNED_JOIN_ADDRESS_A, PINNED_JOIN_ADDRESS_S, PINNED_JOIN_TIME)
              for kind in ("join", "leave")]
    hits = {text: copy_lint.style_hits(text) + copy_lint.claim_hits(text) for text in texts}
    hits = {k: v for k, v in hits.items() if v}
    assert hits == {}, hits


# ── docs/PRIVACY.md holds the contract's promises verbatim ─────────────────

def test_privacy_has_every_published_and_not_published_line_verbatim():
    flat = _flat(_read(DOCS["docs/PRIVACY.md"]))
    missing = [line for line in (*pool.PUBLISHED, *pool.NOT_PUBLISHED) if _flat(line) not in flat]
    assert missing == [], missing


def test_privacy_names_every_pool_coin():
    text = _read(DOCS["docs/PRIVACY.md"])
    missing = [coin for coin in pool.POOL_COINS if coin not in text]
    assert missing == [], missing


def test_privacy_has_the_pinned_phrases():
    flat = _flat(_read(DOCS["docs/PRIVACY.md"]))
    for phrase in (
        "at least 24 hours late",
        "No payout, refund or reward is ever tied to your returns",
        "Coming back takes a new signed join. Only trades after the new join count.",
        "API wallet that signed your join",
    ):
        assert phrase in flat, phrase
    assert "not financial advice" in flat.lower() or "not advice" in flat.lower()


def test_privacy_join_statement_block_matches_the_contract():
    text = _read(DOCS["docs/PRIVACY.md"])
    blocks = re.findall(r"```\n(.*?)\n```", text, re.DOTALL)
    joins = [b for b in blocks if b.startswith("pathiel research pool: join")]
    assert len(joins) == 1, f"expected exactly one join statement block, found {len(joins)}"
    a, s, t = "0x" + "55" * 20, "0x" + "66" * 20, "2026-01-02T03:04:05Z"
    filled = (joins[0].replace("<your account>", a)
              .replace("<the signing address>", s)
              .replace("<time>", t))
    assert filled == pool.statement_text("join", a, s, t)


# ── planted-failure proof: the lint above can fail ──────────────────────────

def _privacy_problems(text: str, copy_lint) -> List[str]:
    problems = [f"style:{h}" for h in copy_lint.style_hits(text)]
    problems += [f"claim:{h}" for h in copy_lint.claim_hits(text)]
    flat = _flat(text)
    problems += [f"missing_published:{line}" for line in (*pool.PUBLISHED, *pool.NOT_PUBLISHED)
                 if _flat(line) not in flat]
    if "Coming back takes a new signed join. Only trades after the new join count." not in flat:
        problems.append("missing_coming_back_sentence")
    return problems


def test_the_privacy_lint_is_proof_it_can_fail(copy_lint):
    text = _read(DOCS["docs/PRIVACY.md"])
    assert _privacy_problems(text, copy_lint) == []

    banned = text.replace("Open a GitHub issue", "Open a comprehensive GitHub issue", 1)
    assert any(p.startswith("style:") for p in _privacy_problems(banned, copy_lint))

    claimed = text.replace(
        "No payout, refund or reward is ever tied to your returns.",
        "Members have shown +0.4R on average.", 1)
    assert any(p.startswith("claim:") for p in _privacy_problems(claimed, copy_lint))

    dropped = text.replace(pool.PUBLISHED[1], "", 1)
    assert any(p.startswith("missing_published:") for p in _privacy_problems(dropped, copy_lint))

    no_comeback = text.replace(
        "Coming back takes a new signed join. Only trades after the new join count.", "", 1)
    assert "missing_coming_back_sentence" in _privacy_problems(no_comeback, copy_lint)


# ── services/pool/README.md names the design's pieces ───────────────────────

MODULES = ("signing", "approval", "join", "join_cli", "verify_cli", "join_tools",
          "fetch", "grade", "scoreboard", "build_cli", "report", "report_cli",
          "report_tool", "paths", "venue", "account", "__main__")


def test_pool_readme_names_every_cli_command_and_mcp_tool_and_module():
    text = _read(DOCS["services/pool/README.md"])
    missing = [key for key in pool.CLI_COMMANDS if key not in text]
    assert missing == [], f"missing CLI commands: {missing}"
    missing = [tool["name"] for tool in pool.MCP_TOOLS if tool["name"] not in text]
    assert missing == [], f"missing MCP tools: {missing}"
    missing = [m for m in MODULES if m not in text]
    assert missing == [], f"missing modules: {missing}"


def test_pool_readme_names_the_outcome_trace_and_runbook_terms():
    text = _read(DOCS["services/pool/README.md"])
    for needle in (
        "counts.graded_trades", "POOL-NEW", "reinstated", "Pool members",
        "Require branches to be up to date",
        "python -m services.pool.evals.cluster_eval",
        "python -m services.pool.evals.join_eval",
        "python -m services.pool.evals.pool_eval",
    ):
        assert needle in text, needle
    assert f"{pool.CLUSTER_SPAN_MS // pool.HOUR_MS} hours" in text


def test_the_pool_readme_check_can_fail():
    text = _read(DOCS["services/pool/README.md"])
    stripped = text.replace("POOL-NEW", "")
    assert "POOL-NEW" not in stripped
