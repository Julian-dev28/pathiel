"""The dynamic wall: pool data feeds grading only, and never reaches a
thesis prompt.

`contracts/pool/tests/test_thesis_boundary.py` proves this statically (who
may import the pool, what the thesis side may never name, what the pool
may never import). This file proves it dynamically: it plants a canary
address and a canary token in every place the pool writes, under a throwaway
state dir and a throwaway repo root, then builds the oil desk's thesis
prompts exactly the way production builds them - from the same contract
fixtures `services/thesis`'s own tests use - and asserts the canary is in
none of them. It also asserts that building a thesis prompt never touches
anything under the pool's own state directory.

No network, no LLM call: prompts are built here, never sent anywhere.

Named `tests/test_pool_*`, so it may import `services.pool` and
`contracts.pool` (`contracts/pool/tests/test_thesis_boundary.py`'s own
importer allowlist).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

import contracts.pool as pool
from services.pool import paths as pool_paths

CANARY_TOKEN = "POOLCANARY7731"
# 40 lowercase-hex characters that are never a real account, built from a
# repeating hex-valid word so a human skimming a failure sees it at once.
CANARY_ADDRESS = "0x" + "cafe" * 10

FORBIDDEN_SUBSTRINGS = (CANARY_TOKEN, CANARY_ADDRESS, "scoreboard", "research pool", "pool/")


def _canary_hits(text: str) -> List[str]:
    low = text.lower()
    return [needle for needle in FORBIDDEN_SUBSTRINGS if needle.lower() in low]


def _snapshot(root: Path) -> Dict[str, bytes]:
    """Every file under `root`, relative path -> bytes. {} when the
    directory does not exist (nothing to compare is still a valid state)."""
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _plant(repo: Path) -> None:
    """One canary-laden file in every place the design doc
    (docs/overhaul/RESEARCH_POOL_2026-11-12.md, 'Data flow') says the pool
    writes to. Content does not need to verify against the contract - the
    build itself is another unit's code, not yet in this worktree - it only
    needs to be the kind of thing a real build would have written, with the
    canary token and address somewhere inside it."""
    # <state>/pool/ledger/<address>.jsonl
    ledger = Path(pool_paths.ledger_path(CANARY_ADDRESS))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({
        "address": CANARY_ADDRESS, "coin": "xyz:BRENTOIL", "side": "long",
        "r_mult": 0.42, "note": CANARY_TOKEN,
    }) + "\n", encoding="ascii")

    # <state>/pool/outbox/members/<address>.json
    outbox_member = Path(pool_paths.outbox_root()) / pool.MEMBERS_SUBDIR / pool.statement_filename(CANARY_ADDRESS)
    outbox_member.parent.mkdir(parents=True, exist_ok=True)
    outbox_doc = {
        "schema": pool.FILE_SCHEMA, "kind": "join", "network": pool.NETWORK,
        "address": CANARY_ADDRESS, "signer": CANARY_ADDRESS,
        "joined_at": "2026-11-12T00:00:00Z", "canary": CANARY_TOKEN,
    }
    outbox_member.write_text(json.dumps(outbox_doc, indent=2) + "\n", encoding="ascii")

    # <repo>/pool/members/<address>.json
    repo_member = Path(pool.member_path(pool_paths.repo_pool_root(), CANARY_ADDRESS))
    repo_member.parent.mkdir(parents=True, exist_ok=True)
    repo_member.write_text(json.dumps(outbox_doc, indent=2) + "\n", encoding="ascii")

    # <repo>/docs/scoreboard/scoreboard.json
    board = pool.empty_scoreboard(pool.ms_from_iso("2026-11-12T00:00:00Z"))
    board["members"] = [{
        "address": CANARY_ADDRESS, "short": pool.short_address(CANARY_ADDRESS),
        "explorer": pool.explorer_url(CANARY_ADDRESS), "kind": "member", "signer_role": "master",
        "since": "2026-11-12", "closed_trades": 1, "n": 1, "wins": 1, "win_rate": 1.0,
        "win_lo": 0.0, "win_hi": 1.0, "mean_r": 0.42, "r_lo": 0.0, "r_hi": 1.0, "p": 0.5,
        "label": "NOISE", "luck_median": 50.0, "luck_n": 1, "note": CANARY_TOKEN,
    }]
    board_path = Path(pool_paths.scoreboard_json())
    board_path.parent.mkdir(parents=True, exist_ok=True)
    board_path.write_text(pool.dump_scoreboard(board), encoding="ascii")


@pytest.fixture
def canary_repo(tmp_path, monkeypatch):
    state, repo = tmp_path / "state", tmp_path / "repo"
    state.mkdir()
    repo.mkdir()
    monkeypatch.setenv("PATHIEL_STATE_DIR", str(state))
    monkeypatch.setenv("PATHIEL_POOL_REPO_ROOT", str(repo))
    monkeypatch.chdir(repo)
    _plant(repo)
    return {"state": state, "repo": repo}


def _built_prompts(monkeypatch) -> List[str]:
    """Every prompt text production can build for the oil desk: the system
    prompt, and the user prompt in full and incremental mode, with the
    SCORECARD section off and on (PATHIEL_THESIS_SCORECARD) - the same
    fixtures services/thesis/tests/conftest.py loads
    (contracts.strategies.fixtures._build), the same functions production
    calls (services.thesis.prompt.build_system_prompt / build_user_prompt),
    the same scorecard builder the live pipeline calls
    (services.strategies.pipeline._build_scorecard_text)."""
    from contracts.strategies.fixtures import _build as oil_fixture
    from services.strategies import pipeline as strategies_pipeline
    from services.thesis import prompt as thesis_prompt

    spec, analytics, intel, prev = (
        oil_fixture.spec, oil_fixture.analytics, oil_fixture.intel, oil_fixture.thesis)

    texts = [thesis_prompt.build_system_prompt(spec)]
    for scorecard_on in (False, True):
        if scorecard_on:
            monkeypatch.setenv("PATHIEL_THESIS_SCORECARD", "1")
        else:
            monkeypatch.delenv("PATHIEL_THESIS_SCORECARD", raising=False)
        scorecard_text = strategies_pipeline._build_scorecard_text("oil")
        if scorecard_text:
            texts.append(scorecard_text)
        for incremental in (False, True):
            text, _ = thesis_prompt.build_user_prompt(
                spec, analytics, intel, prev, incremental=incremental, scorecard_text=scorecard_text)
            texts.append(text)
    return texts


def test_the_plant_actually_wrote_every_canary_location(canary_repo):
    state, repo = canary_repo["state"], canary_repo["repo"]
    paths = [
        Path(pool_paths.ledger_path(CANARY_ADDRESS)),
        Path(pool_paths.outbox_root()) / pool.MEMBERS_SUBDIR / pool.statement_filename(CANARY_ADDRESS),
        Path(pool.member_path(pool_paths.repo_pool_root(), CANARY_ADDRESS)),
        Path(pool_paths.scoreboard_json()),
    ]
    for path in paths:
        assert path.is_file(), path
        assert CANARY_TOKEN in path.read_text(encoding="ascii"), path
    assert str(state) in str(paths[0]) and str(state) in str(paths[1])
    assert str(repo) in str(paths[2]) and str(repo) in str(paths[3])


def test_pool_data_never_reaches_a_thesis_prompt(canary_repo, monkeypatch):
    pool_state_dir = canary_repo["state"] / pool.STATE_SUBDIR
    before = _snapshot(pool_state_dir)
    assert before, "the plant did not write anything under <state>/pool"

    prompts = _built_prompts(monkeypatch)
    assert len(prompts) >= 5, "expected the system prompt, two user prompts and a scorecard text"

    hits = {i: _canary_hits(text) for i, text in enumerate(prompts) if _canary_hits(text)}
    assert hits == {}, hits

    after = _snapshot(pool_state_dir)
    assert after == before, "building a thesis prompt changed something under <state>/pool"


def test_the_canary_check_can_fail():
    """Proof the detector above is not vacuous: each forbidden substring is
    independently catchable, case included, and a clean prompt passes."""
    assert _canary_hits("an ordinary thesis prompt about oil and Brent") == []
    assert _canary_hits(f"oops, {CANARY_TOKEN} leaked into the prompt") == [CANARY_TOKEN]
    assert _canary_hits(f"the address {CANARY_ADDRESS} showed up") == [CANARY_ADDRESS]
    assert _canary_hits("check the SCOREBOARD for today's call") == ["scoreboard"]
    assert _canary_hits("the Research Pool numbers say otherwise") == ["research pool"]
    assert _canary_hits("see pool/members for the list") == ["pool/"]


def test_the_canary_check_catches_a_poisoned_real_prompt(canary_repo, monkeypatch):
    """The same assertion the main test runs, deliberately tripped on one
    real prompt, so the main test's clean result is not an accident of a
    detector that never fires."""
    prompts = _built_prompts(monkeypatch)
    poisoned = prompts[0] + f"\n{CANARY_TOKEN}"
    assert _canary_hits(poisoned) == [CANARY_TOKEN]
