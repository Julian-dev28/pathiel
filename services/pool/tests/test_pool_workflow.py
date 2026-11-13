""".github/workflows/pool-members.yml: a required check that runs on every
pull request, read only, and judges the merge result. Offline; checked as
text plus scripts/supply_chain_check.py's own rules."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "pool-members.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"

_SHA_RE = re.compile(r"@([0-9a-f]{40})\b")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _doc() -> dict:
    return yaml.safe_load(_text())


def test_workflow_file_exists_and_parses():
    assert WORKFLOW.is_file()
    doc = _doc()
    assert doc["name"] == "Pool members"


def test_runs_on_every_pull_request_with_no_filter():
    text = _text()
    doc = _doc()
    on = doc[True] if True in doc else doc["on"]      # yaml parses bare `on:` as the key "on"
    assert "pull_request" in on
    pr_block = on["pull_request"]
    assert pr_block is None or pr_block == {}
    assert "pull_request_target" not in text
    for forbidden in ("paths:", "paths-ignore:", "branches:"):
        assert forbidden not in text, forbidden


def test_exactly_one_permissions_block_contents_read():
    text = _text()
    assert text.count("permissions:") == 1
    doc = _doc()
    assert doc["permissions"] == {"contents": "read"}


def test_no_write_permission_anywhere():
    assert not re.search(r"(?m)^\s*\w[\w-]*:\s*write\s*$", _text())


def test_job_named_pool_members():
    doc = _doc()
    jobs = doc["jobs"]
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert job["name"] == "Pool members"
    assert job["timeout-minutes"] <= 10
    assert job["runs-on"] == "ubuntu-latest"


def _ci_shas() -> set:
    text = CI.read_text(encoding="utf-8")
    return set(_SHA_RE.findall(text))


def test_every_uses_is_pinned_to_a_sha_ci_also_uses():
    ci_shas = _ci_shas()
    text = _text()
    uses_lines = [line for line in text.splitlines() if re.search(r"\buses:\s*\S+", line)]
    assert uses_lines, "no uses: lines found"
    for line in uses_lines:
        m = re.search(r"\buses:\s*(\S+)", line)
        action = m.group(1)
        sm = _SHA_RE.search(action)
        assert sm, f"not pinned to a commit SHA: {action}"
        assert sm.group(1) in ci_shas, f"{action} uses a SHA ci.yml does not: {action}"


def test_checkout_has_fetch_depth_zero_and_no_ref_override():
    doc = _doc()
    job = next(iter(doc["jobs"].values()))
    checkout = next(s for s in job["steps"] if "checkout" in s.get("uses", ""))
    with_block = checkout.get("with") or {}
    assert with_block.get("fetch-depth") == 0
    assert "ref" not in with_block


def test_runs_verify_pr_base_against_the_base_ref():
    text = _text()
    assert "services.pool verify" in text and "--pr-base" in text
    assert 'origin/$BASE_REF' in text or 'origin/${{' in text
    assert "github.base_ref" in text


def test_supply_chain_check_is_clean_on_this_workflow():
    spec = importlib.util.spec_from_file_location(
        "supply_chain_check", ROOT / "scripts" / "supply_chain_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    problems = [p for p in mod.check_workflows() if "pool-members.yml" in p]
    assert problems == []
