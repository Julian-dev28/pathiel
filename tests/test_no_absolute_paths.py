"""The regression test for the bug that kept CI red for weeks.

50 tracked files carried `REPO = "/Users/julian_dev/Documents/code/pathia"`.
The suite was green on the one machine where that path existed and red on every
push. Nothing caught it, so this is the thing that catches it now.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_no_absolute_paths.py"

_spec = importlib.util.spec_from_file_location("check_no_absolute_paths", SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_the_repo_has_no_machine_bound_absolute_paths():
    assert checker.main() == 0


def test_the_pattern_catches_the_exact_bug_that_shipped():
    """The literal that was in W-X2_xs_widening.py:74."""
    line = 'REPO = "/Users/julian_dev/Documents/code/pathia"'
    assert checker.PATTERN.search(line) is not None


def test_the_pattern_catches_a_linux_home_too():
    assert checker.PATTERN.search('P = "/home/runner/work/thing"') is not None


def test_the_pattern_leaves_machine_independent_paths_alone():
    """/tmp and /usr/local exist on any box — flagging them would make the
    check noisy enough that someone turns it off."""
    for ok in ('X = "/tmp/pathia/state.json"',
               'BIN = "/usr/local/bin/claude"',
               'p = Path(__file__).resolve().parents[1]'):
        assert checker.PATTERN.search(ok) is None, ok


def test_ci_runs_the_check_before_the_suite():
    """A guard that only exists locally is not a guard. It must be wired into
    the workflow, and BEFORE pytest so the failure names the real cause."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "check_no_absolute_paths.py" in ci
    assert ci.index("check_no_absolute_paths.py") < ci.index("run: pytest")


def test_ci_installs_from_the_lockfile():
    """CI passing against a different dependency set than production runs is
    not evidence about production."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "requirements.lock" in ci
    assert (ROOT / "requirements.lock").exists()


def test_the_allowlist_is_only_the_files_that_must_carry_the_pattern():
    """The escape hatch has to stay tiny. Every entry is a file whose job is to
    describe the pattern; anything else in here would be a real offender being
    waved through."""
    assert checker.ALLOWED == {
        ".env.local.example",
        "scripts/check_no_absolute_paths.py",
        "tests/test_no_absolute_paths.py",
    }


def test_every_json_block_in_the_docs_parses():
    """A config sample that does not parse is a config sample someone pastes and
    then debugs. Editing the README broke exactly this on 2026-08-29 by leaving a
    trailing comma behind a removed book."""
    import json
    import re

    for doc in ("README.md", "DEPLOY.md", "docs/SECRETS.md", "docs/LOGGING.md"):
        path = ROOT / doc
        if not path.exists():
            continue
        for i, block in enumerate(re.findall(r"```json\n(.*?)```", path.read_text(), re.S)):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{doc} json block {i} does not parse: {exc}")


def test_the_docs_do_not_describe_deleted_subsystems_as_existing():
    """Documentation that confidently describes something that is gone is worse
    than none — it is the first thing a reader trusts.

    A changelog entry SAYING a thing was removed is correct and must not trip
    this; only prose outside such a section counts. So the check skips any
    section whose heading says the thing was removed.
    """
    import re

    gone = ("polymarket_scout", "pathia/v2/", "xs_momentum_live",
            "extreme_fade_live", "--sample-daemon",
            # Deleted 2026-09-04. The pathia-agent SKILL listed rally_exhaustion
            # and hail_mary_short as current live books days after both were
            # gone, and an operator reading it would have gone looking for
            # config that does not exist.
            #
            # uw_client and its three W-UW hypothesis scripts went 2026-09-06,
            # by operator instruction. They re-ran verdicts for uw_flow_xs, a
            # book deleted 2026-08-29, against a paid API. The finding stays on
            # disk — the doctrine keeps refutations findable so they are not
            # rediscovered — but the code and the key it needed are gone.
            "rally_exhaustion", "hail_mary_short", "uw_client",
            "data_providers", "hydromancer")
    # Every operator-facing doc, not just the two at the root. docs/LOGGING.md
    # documented `logs/polymarket_scout.log` and a `restart.sh sampler` action
    # for months after both were deleted, because this loop did not look at it.
    # skills/ is included for the same reason: the SKILL.md book list went stale
    # precisely because nothing checked it.
    docs = ["README.md", "DEPLOY.md"] + sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md")) + sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "skills").rglob("*.md")) + sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "services").rglob("README.md"))
    for doc in docs:
        text = (ROOT / doc).read_text()
        # drop sections headed "What was removed" / "Removed" and the like
        sections = re.split(r"\n(?=#{1,3} )", text)
        live = "\n".join(sec for sec in sections
                          if not re.match(r"#{1,3} .*(removed|deleted)",
                                          sec.split("\n")[0], re.I))
        for name in gone:
            assert name not in live, (
                f"{doc} describes deleted {name} as if it still exists")


def test_every_script_an_operator_doc_tells_you_to_run_exists():
    """README's quickstart said `python3 scripts/status.py` for months after
    that script was deleted, and DEPLOY.md documented an env var whose only
    consumer (scripts/v2_shadow_loop.py) was gone too. A command in a runbook
    that cannot run is worse than no runbook: it is followed.

    Covers inline code AND fenced blocks — the README one was in a fenced
    block, which an inline-backtick check missed.
    """
    import re

    docs = ["README.md", "DEPLOY.md"] + sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))
    missing = []
    for doc in docs:
        path = ROOT / doc
        if not path.exists():
            continue
        text = path.read_text()
        code = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", text, re.S))
        code += "\n" + "\n".join(re.findall(r"`([^`\n]+)`", text))
        for ref in set(re.findall(r"(?:^|[\s(])((?:scripts|skills)/[\w./-]+\.(?:py|sh))",
                                  code, re.M)):
            if not (ROOT / ref).exists():
                missing.append(f"{doc}: {ref}")
    assert not missing, ("operator docs point at scripts that do not exist:\n"
                         + "\n".join(sorted(missing)))


def test_every_restart_action_an_operator_doc_names_is_real():
    """`restart.sh sampler` was documented in docs/LOGGING.md long after the
    action was removed."""
    import re
    import subprocess

    usage = subprocess.run(["bash", str(ROOT / "scripts" / "restart.sh"), "--bogus"],
                           capture_output=True, text=True, cwd=ROOT).stderr
    inner = re.search(r"\[([\w|]+)\]", usage)
    assert inner, "restart.sh no longer prints a usage line to parse"
    actions = set(inner.group(1).split("|"))

    docs = ["README.md", "DEPLOY.md"] + sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))
    bad = []
    for doc in docs:
        path = ROOT / doc
        if not path.exists():
            continue
        text = path.read_text()
        code = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", text, re.S))
        code += "\n" + "\n".join(re.findall(r"`([^`\n]+)`", text))
        for act in set(re.findall(r"restart\.sh[ \t]+(\w+)", code)):
            if act not in actions:
                bad.append(f"{doc}: restart.sh {act}")
    assert not bad, ("docs name restart.sh actions that do not exist:\n"
                     + "\n".join(sorted(bad)))


def test_the_mcp_tool_count_in_the_docs_matches_the_server():
    """The skill quotes "99 tools (52 implemented + 47 stubs)" in three places.
    A count that drifts from the source is the kind of claim a reader trusts and
    then debugs, and nothing was checking it."""
    import ast
    server = ROOT / "scripts" / "pathia-mcp-server.py"
    tree = ast.parse(server.read_text())
    # AnnAssign as well as Assign: _STUB_TOOL_NAMES carries a type annotation
    # now that it is empty, and an Assign-only walk silently misses it.
    g = {}
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if hasattr(tgt, "id"):
                    g[tgt.id] = n.value
        elif isinstance(n, ast.AnnAssign) and hasattr(n.target, "id"):
            g[n.target.id] = n.value
    tools = {v.value for e in g["TOOLS"].elts for k, v in zip(e.keys, e.values)
             if getattr(k, "value", None) == "name"}
    stubs = {e.value for e in g["_STUB_TOOL_NAMES"].elts}
    advertised, implemented = len(tools), len(tools - stubs)
    assert stubs <= tools, "a stub name is not registered in TOOLS"

    for doc in ("skills/pathia-agent/SKILL.md",
                "skills/pathia-agent/references/mcp-config.md",
                "skills/pathia-agent/references/mcp-server.md",
                "docs/ARCHITECTURE.md"):
        text = (ROOT / doc).read_text()
        if "tools" not in text:
            continue
        assert f"{advertised} tools" in text, (
            f"{doc} does not say '{advertised} tools' — the server advertises "
            f"{advertised} ({implemented} implemented + {len(stubs)} stubs)")
        # The split matters more than the total: it is what tells a reader how
        # much of the surface actually returns data. Only checked where a doc
        # actually quotes it — ARCHITECTURE describes the stub mechanism without
        # giving a count, which is fine.
        if "implemented + " in text:
            assert f"{implemented} implemented" in text, (
                f"{doc} misstates the implemented count; it is {implemented}")


def test_no_stub_shadows_a_capability_the_client_already_has():
    """A stub is worse than a missing tool: an agent reads "not implemented" as
    "this data does not exist here" and goes without it. Six stubs sat on top of
    working pathia.client code until 2026-09-06.

    This pins the six so they cannot silently regress to stubs, and names the
    check to run when adding a new one."""
    import ast
    tree = ast.parse((ROOT / "scripts" / "pathia-mcp-server.py").read_text())
    g = {}
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                # AnnAssign for the annotated empty list; Assign for TOOLS.
                if hasattr(tgt, "id"):
                    g[tgt.id] = n.value
        elif isinstance(n, ast.AnnAssign) and hasattr(n.target, "id"):
            g[n.target.id] = n.value
    stubs = {e.value for e in g["_STUB_TOOL_NAMES"].elts}
    handlers = {n.name[7:] for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("handle_")}
    assert stubs == set(), f"stubs are back: {sorted(stubs)}"
    for name in ("get_coin_price", "get_leverage", "get_funding_history",
                 "get_asset_context", "get_open_interest", "get_predicted_funding",
                 "get_price_impact", "get_max_trade_size", "get_portfolio_status",
                 "get_trade_history", "get_user_orders", "get_api_rate_limits"):
        assert name not in stubs, f"{name} regressed to a stub"
        assert name in handlers, f"{name} advertises no real handler"
