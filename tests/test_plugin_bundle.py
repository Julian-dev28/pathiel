"""The plugin bundle (scripts/build_plugin_bundle.py): what the directory sees.

The Claude plugin directory reads every file in the folder it is given. These
tests pin that the bundle holds what the plugin runs and nothing it flagged
on the whole-repo submission of 2026-11-13: no CLAUDE.md (ROOT_CLAUDE_MD), no
repo .mcp.json (DECLARED_IN_TWO_PLACES, SERVER_START_LINE_NOT_ACCEPTED), no
.claude/ (LOCAL_CODE_FILE_TYPE), no workflows, VPS scripts, docs or fixtures
(RUNTIME_FETCH_EXEC); and that the server starts and lists the same tools
from the bundle alone as from the checkout.
"""
from __future__ import annotations

import ast
import fnmatch
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_plugin_bundle.py"

_spec = importlib.util.spec_from_file_location("build_plugin_bundle", SCRIPT)
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

# Download-and-run shapes the directory's RUNTIME_FETCH_EXEC check is about.
FETCH_EXEC = re.compile(
    r"(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"
    r"|\b(iex|Invoke-Expression)\b"
    r"|\bsh\s+-c\s+[\"']?\$\((curl|wget)\b"
    r"|\bbash\s+<\(\s*(curl|wget)\b",
    re.IGNORECASE)


# The staged files, not HEAD: the pre-commit gate runs before the commit it
# checks exists. In CI and a clean checkout the index is HEAD.
@pytest.fixture(scope="module")
def files():
    return B.ls_tree(B.INDEX)


@pytest.fixture(scope="module")
def paths(files):
    return B.bundle_paths(files)


@pytest.fixture(scope="module")
def built(tmp_path_factory, paths):
    out = tmp_path_factory.mktemp("bundle") / "pathiel"
    assert B.build(B.INDEX, out) == paths
    return out


# ── what is in, what is out ─────────────────────────────────────────────────

# ── checks, shared with tests/test_eval_plugin_bundle.py ────────────────────

BANNED_EXACT = {"CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".lsp.json", "hooks/hooks.json",
                ".claude-plugin/marketplace.json", "Dockerfile", "Dockerfile.mcp",
                "DEPLOY.md", "CONTRIBUTING.md", "docker-entrypoint.sh", "vercel.json",
                "scripts/seo_rank.py", ".agent-config.json", ".env.local.example"}
BANNED_PREFIX = (".claude/", ".github/", "docs/", "tests/", "k8s/", "api/",
                 "scripts/vps/", "scripts/hooks/")

# services/strategies/evals/ is "paid or slow eval lanes" by default
# (DATA_EXCLUDE's */evals/* in build_plugin_bundle.py says the same for the
# non-.py files under it) -- except loss_audit.py (2026-11-13): its pure
# trade-reconstruction functions are the one eval module a shipped runtime
# path needs, since services/pool/grade.py imports it to grade a pool
# member's trades with the exact harness the project runs on its own
# (docs/overhaul/RESEARCH_POOL_2026-11-12.md). It carries no import of
# stop_cap_test or anything else under evals/ (that chain moved to the
# CLI-only services/strategies/evals/loss_audit_cli.py, never reached from
# an entry script), so letting it ship does not reopen the door to the rest
# of the backtest/eval chain. Its own package __init__.py (empty) ships
# alongside it, same as every other package's __init__.py.
EVALS_ALLOWED = frozenset({"services/strategies/evals/__init__.py",
                           "services/strategies/evals/loss_audit.py"})


def flagged_paths(paths) -> list:
    """Bundle paths the 2026-11-13 directory review flagged on the repo root."""
    return [p for p in paths if p in BANNED_EXACT
            or (p.startswith(BANNED_PREFIX) and p not in B.LISTING_FILES
                and not any(fnmatch.fnmatch(p, pat) for pat in B.REPO_DATA))
            or "/tests/" in p
            or ("/evals/" in p and p not in EVALS_ALLOWED)
            or ("/fixtures/" in p and not p.startswith("contracts/"))]


def fetch_exec_hits(root: Path, paths) -> list:
    hits = []
    for p in paths:
        data = (root / p).read_bytes()
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if FETCH_EXEC.search(line):
                hits.append(f"{p}:{n}: {line.strip()[:120]}")
    return hits


def shell_files(root: Path, paths) -> list:
    return [p for p in paths if p.endswith(".sh") or
            (root / p).read_bytes().startswith(b"#!/usr/bin/env bash")]


def shell_eval_hits(root: Path, paths) -> list:
    hits = []
    for p in shell_files(root, paths):
        for n, ln in enumerate((root / p).read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"(^|[;&|\s])eval\b", ln.split("#", 1)[0]):
                hits.append(f"{p}:{n}")
    return hits


def unclassified_data(files) -> list:
    return [p for p in files if p.startswith(B.CODE_TREES) and not p.endswith(".py")
            and B.classify_data(p) is None]


def test_nothing_the_directory_flagged_on_the_repo_root_is_in_the_bundle(paths):
    assert flagged_paths(paths) == []


def test_evals_in_the_bundle_are_exactly_the_allowed_loss_audit_module(paths):
    """Regression (2026-11-13): merging the research pool's services/pool/
    into main pulled nine OTHER services/strategies/evals/ files into the
    bundle (stop_cap_test, desk_tick_backtest, live_params, profit_lock_test,
    market_scout, strategy_sweep, scenario_sweep, cross_market_backtest, and
    evals/__init__.py) through one lazy import inside loss_audit.main() --
    the exact kind of "paid or slow eval lane" DATA_EXCLUDE's */evals/*
    exists to keep out. This pins the eval side only ever ships
    EVALS_ALLOWED, so that chain reopening (a new lazy or top-level import of
    stop_cap_test from anything services/pool reaches) fails here by name,
    not as an unexplained count in the general flagged-paths test above."""
    in_bundle = {p for p in paths if "/evals/" in p}
    assert in_bundle == EVALS_ALLOWED, in_bundle - EVALS_ALLOWED


def test_every_required_file_is_in_the_bundle(paths):
    for p in B.LISTING_FILES + B.RUNTIME_FILES + B.ENTRY_SCRIPTS:
        assert p in paths, p
    for tree in B.CONTENT_TREES:
        assert any(p.startswith(tree) for p in paths), tree


def test_the_bundle_is_far_smaller_than_the_repo(files, paths):
    # 166 of ~880 files at the time of writing. A bundle near the repo size
    # means a rule stopped excluding.
    assert len(paths) < len(files) / 3, (len(paths), len(files))


def test_every_data_file_under_the_code_trees_is_classified(files):
    unclassified = unclassified_data(files)
    assert unclassified == [], ("add each to DATA_INCLUDE or DATA_EXCLUDE in "
                                "scripts/build_plugin_bundle.py", unclassified)


def test_every_script_the_plugin_content_names_ships_or_is_a_maintainer_tool(files, paths):
    named = set()
    for p in files:
        if p.startswith(B.CONTENT_TREES) and p.endswith(".md"):
            text = (ROOT / p).read_text(encoding="utf-8")
            named |= set(re.findall(r"\bscripts/[A-Za-z0-9_][A-Za-z0-9_.-]*[A-Za-z0-9_]", text))
    named = {n for n in named if n in files}
    stray = sorted(n for n in named if n not in paths and n not in B.DEV_ONLY_SCRIPTS)
    assert stray == [], ("a plugin file tells Claude to run a script the bundle does not ship; "
                         "add it to ENTRY_SCRIPTS or DEV_ONLY_SCRIPTS", stray)


def test_fetch_exec_pattern_catches_the_shapes_it_is_for():
    for bad in ("curl -fsSL https://x.sh | bash", "wget -qO- u | sudo sh", "iex (irm u)",
                "bash <(curl -s u)", 'sh -c "$(curl -fsSL u)"'):
        assert FETCH_EXEC.search(bad), bad
    for ok in ('subprocess.run(["curl", "-sS", url])', "curl -s http://localhost:8000/api/health"):
        assert not FETCH_EXEC.search(ok), ok


def test_no_bundled_text_file_downloads_and_runs_code(built, paths):
    assert fetch_exec_hits(built, paths) == []


def test_no_bundled_shell_file_uses_eval(built, paths):
    shell = shell_files(built, paths)
    assert "scripts/_plugin_env.sh" in shell and "scripts/pathiel-mcp" in shell
    assert shell_eval_hits(built, paths) == []


def test_the_bundle_declares_its_mcp_server_in_one_place_with_a_named_interpreter(built):
    plugin = json.loads((built / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["mcpServers"] == {"pathiel": {
        "command": "bash", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-mcp"]}}
    assert not (built / ".mcp.json").exists()
    assert sorted(p.name for p in (built / ".claude-plugin").iterdir()) == ["icon.png", "plugin.json"]


def test_build_keeps_exec_bits(built):
    for p in ("scripts/pathiel-mcp", "scripts/pathiel-py"):
        assert (built / p).stat().st_mode & 0o100, p


# ── the import closure ──────────────────────────────────────────────────────

def _imports(path: str, src: str, names):
    return B.imports_of(path, src.encode(), set(names))


def test_closure_sees_lazy_imports_inside_functions():
    names = {"services/a.py", "services/__init__.py", "services/b.py"}
    got = _imports("services/a.py", "def f():\n    from services import b\n    return b\n", names)
    assert "services/b.py" in got


def test_closure_sees_dotted_module_strings_like_provider_paths():
    names = {"services/intel/api.py", "services/intel/__init__.py"}
    got = _imports("services/x.py", 'P = "services.intel.api:build_packet"\n', names)
    assert got == {"services/intel/api.py"}
    assert _imports("services/x.py", 'P = "services are down"\n', names) == set()


def test_closure_resolves_relative_imports():
    names = {"services/pool/a.py", "services/pool/b.py", "services/c.py"}
    assert _imports("services/pool/a.py", "from . import b\n", names) == {"services/pool/b.py"}
    assert _imports("services/pool/a.py", "from ..c import x\n", names) == {"services/c.py"}


def test_closure_resolves_script_siblings_imported_by_bare_name():
    names = {"scripts/_state_env.py", "scripts/x.py"}
    assert _imports("scripts/x.py", "import _state_env\n", names) == {"scripts/_state_env.py"}


def test_every_first_party_import_in_the_bundle_resolves_inside_it(built, paths):
    """Independent of the closure code: parse every bundled module and check
    each pathiel/services/contracts import names a bundled file."""
    shipped = set(paths)
    missing = []
    for p in paths:
        if not p.endswith(".py"):
            continue
        for node in ast.walk(ast.parse((built / p).read_bytes())):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                mods = [node.module]
            for m in mods:
                if m.split(".")[0] in B.PACKAGE_ROOTS:
                    base = m.replace(".", "/")
                    namespace = any(s.startswith(base + "/") for s in shipped)  # no __init__.py
                    if f"{base}.py" not in shipped and f"{base}/__init__.py" not in shipped \
                            and not namespace:
                        missing.append(f"{p}: {m}")
    assert missing == []


# ── it runs ─────────────────────────────────────────────────────────────────

def _tools(server: Path, public_env) -> list:
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    proc = subprocess.run([sys.executable, str(server)],
                          input="".join(json.dumps(m) + "\n" for m in msgs),
                          capture_output=True, text=True, timeout=90, env=public_env(),
                          cwd=str(public_env.env_file.parent))
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = {json.loads(ln).get("id"): json.loads(ln) for ln in proc.stdout.splitlines()}
    return sorted(t["name"] for t in out[2]["result"]["tools"])


def test_the_server_lists_the_same_tools_from_the_bundle_alone(built, public_env):
    from_bundle = _tools(built / "scripts" / "pathiel-mcp-server.py", public_env)
    from_checkout = _tools(ROOT / "scripts" / "pathiel-mcp-server.py", public_env)
    assert from_bundle == from_checkout and len(from_bundle) >= 60


def test_every_bundled_module_imports_from_the_bundle_alone(built, paths, public_env):
    """A lazy import the closure missed would only fail when a user calls that
    tool; importing every shipped module up front catches it here."""
    mods = [p[:-3].replace("/", ".").removesuffix(".__init__") for p in paths
            if p.endswith(".py") and p.startswith(B.CODE_TREES)]
    code = ("import importlib, sys\nsys.path.insert(0, sys.argv[1])\nbad = []\n"
            "for m in sys.argv[2:]:\n"
            "    try:\n        importlib.import_module(m)\n"
            "    except Exception as e:\n        bad.append(f'{m}: {type(e).__name__}: {e}')\n"
            "print('\\n'.join(bad))\n")
    proc = subprocess.run([sys.executable, "-c", code, str(built), *mods], capture_output=True,
                          text=True, timeout=120, env=public_env(), cwd=str(built))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "", proc.stdout


# ── publish and check, on a throwaway clone ─────────────────────────────────

def _clean_env() -> dict:
    """The environment without GIT_*: inside the pre-commit hook git exports
    GIT_INDEX_FILE (and GIT_DIR in a worktree), which would point the clone's
    git at the real repo's index."""
    import os
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=_clean_env()).stdout.strip()


def _refs(repo: Path) -> str:
    return subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=repo,
                          capture_output=True, text=True, env=_clean_env()).stdout


def _repo_state(repo: Path) -> str:
    """Every ref and the repo-local config: what a leaked GIT_DIR could change."""
    config = subprocess.run(["git", "config", "--local", "--list"], cwd=repo,
                            capture_output=True, text=True, env=_clean_env()).stdout
    return _refs(repo) + "\n--config--\n" + config


@pytest.fixture
def real_refs_unchanged():
    """No test here may move a ref of the real repo or touch its config, hook
    or no hook. Regression, 2026-11-13: under the pre-commit hook the clone
    test's `git config user.email` landed in the real .git/config, and 13
    commits went out as "bundle test" (GitHub: Unverified)."""
    before = _repo_state(ROOT)
    yield
    assert _repo_state(ROOT) == before, "a bundle test changed a ref or the config of the real repo"


@pytest.mark.parametrize("var", B.HOOK_ENV)
def test_publish_refuses_inside_a_git_hook(tmp_path, var, real_refs_unchanged):
    """Regression, 2026-11-13: run by the pre-commit hook, the publish test's
    throwaway clone inherited GIT_DIR and wrote a `plugin` branch into the
    real repo. Point the variable at a decoy repo: nothing may land there."""
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=_clean_env())
    env = dict(_clean_env(), **{var: str(decoy / ".git") if var != "GIT_INDEX_FILE"
                                else str(decoy / ".git" / "index")})
    proc = subprocess.run([sys.executable, str(SCRIPT), "publish", "--ref", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0 and "publish refused" in proc.stderr and var in proc.stderr
    assert _refs(decoy) == ""


def test_publish_is_idempotent_fast_forward_and_check_catches_drift(tmp_path, real_refs_unchanged):
    clone = tmp_path / "clone"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(clone)],
                   check=True, env=_clean_env())
    _git(clone, "checkout", "-q", "--detach", head)
    (clone / "scripts").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, clone / "scripts" / "build_plugin_bundle.py")  # this version, uncommitted
    run = [sys.executable, str(clone / "scripts" / "build_plugin_bundle.py")]

    def bundle(*args):
        return subprocess.run(run + list(args), cwd=clone, capture_output=True, text=True,
                              env=_clean_env())

    # The clone may already see origin/plugin (the real repo publishes); with
    # or without it, publish must leave the local branch at the bundle.
    if _git(clone, "branch", "-r", "--list", "origin/plugin"):
        assert bundle("publish").stdout.strip() == _git(clone, "rev-parse", "plugin")
        _git(clone, "branch", "-D", "plugin")
        _git(clone, "update-ref", "-d", "refs/remotes/origin/plugin")
    first = bundle("publish").stdout.strip()
    assert first and _git(clone, "rev-parse", "plugin") == first
    assert _git(clone, "rev-parse", "plugin^{tree}") == bundle("tree").stdout.strip()
    assert bundle("publish").stdout.strip() == first          # same source, no new commit
    assert bundle("check", "--against", "plugin").returncode == 0

    # A change to a bundled file: check fails until a new publish, which
    # stacks on the old bundle (a fast-forward, never a rewrite).
    readme = clone / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    _git(clone, "commit", "-q", "-am", "change a bundled file")
    stale = bundle("check", "--against", "plugin")
    assert stale.returncode == 1 and "MISMATCH" in stale.stderr
    second = bundle("publish").stdout.strip()
    assert second != first and _git(clone, "rev-parse", f"{second}^") == first
    assert bundle("check", "--against", "plugin").returncode == 0

    # A change outside the bundle (the maintainer's CLAUDE.md) changes nothing.
    t = clone / "CLAUDE.md"
    t.write_text(t.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    _git(clone, "commit", "-q", "-am", "change an unbundled file")
    assert bundle("check", "--against", "plugin").returncode == 0
    assert bundle("publish").stdout.strip() == second


def test_the_release_workflow_refuses_a_version_whose_bundle_is_not_published():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "python3 scripts/build_plugin_bundle.py check --ref HEAD --against origin/plugin" in wf
    assert "refs/heads/plugin:refs/remotes/origin/plugin" in wf


# ── the shipped Markdown ────────────────────────────────────────────────────

def _bt(text, shipped=("README.md", "LICENSE"), repo=("README.md", "LICENSE", "docs/A.md")):
    return B.bundle_text("README.md", text, set(shipped), set(repo))


def test_an_omitted_block_becomes_one_line_to_its_section_on_github():
    text = ("a\n<!-- bundle:omit -->\n### Other MCP clients\n\n```bash\ndocker run x\n```\n"
            "<!-- /bundle:omit -->\nb\n")
    assert _bt(text) == ("a\nOther MCP clients: see [the README on GitHub]"
                         "(https://github.com/Julian-dev28/pathiel#other-mcp-clients).\nb\n")


def test_a_marker_named_in_prose_is_not_a_marker():
    text = "Blocks marked `<!-- bundle:omit -->` are dropped. So is `<!-- /bundle:omit -->`.\n"
    assert _bt(text) == text


@pytest.mark.parametrize("bad, why", [
    ("<!-- bundle:omit -->\n## X\n", "without"),
    ("x\n<!-- /bundle:omit -->\n", "without"),
    ("<!-- bundle:omit -->\n## X\n<!-- bundle:omit -->\n<!-- /bundle:omit -->\n", "nested"),
    ("<!-- bundle:omit -->\nno heading\n<!-- /bundle:omit -->\n", "heading"),
])
def test_a_malformed_omit_block_stops_the_build(bad, why):
    with pytest.raises(SystemExit, match=why):
        _bt(bad)


def test_links_to_files_the_bundle_lacks_point_at_github():
    text = "[L](LICENSE) [A](docs/A.md#x) [D](docs/) [W](https://x.y) [H](#top)"
    assert _bt(text) == ("[L](LICENSE) [A](https://github.com/Julian-dev28/pathiel/blob/main/docs/A.md#x) "
                         "[D](https://github.com/Julian-dev28/pathiel/tree/main/docs) [W](https://x.y) [H](#top)")


def test_the_root_readme_marks_the_container_and_maintainer_blocks():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.count(B.OMIT_START) == text.count(B.OMIT_END) == 2
    for block in text.split(B.OMIT_START)[1:]:
        body = block.split(B.OMIT_END)[0]
        assert "docker run" in body or "git clone" in body


def test_the_bundle_readme_has_no_install_commands_and_no_dead_links(built, paths):
    shipped = set(paths)
    for p in B.TRANSFORMED:
        text = (built / p).read_text(encoding="utf-8")
        assert not B._MARKER.search(text), p
        for m in re.finditer(r"\]\((?!https?://|mailto:|#)([^)\s#]+)", text):
            assert m.group(1) in shipped, f"{p}: dead link {m.group(1)}"
    readme = (built / "README.md").read_text(encoding="utf-8")
    for needle in ("docker run", "git clone", "docker build"):
        assert needle not in readme, needle


def test_the_bundle_readme_keeps_every_disclosure(built):
    spec = importlib.util.spec_from_file_location("_bundle_public_copy", ROOT / "tests" / "test_public_copy.py")
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    results = copy.disclosure_results((built / "README.md").read_text(encoding="utf-8"))
    assert [label for label, ok, _ in results if not ok] == []
    assert len(results) == len(copy.DISCLOSURE_CHECKLIST)


def _call(server: Path, public_env, name: str, args: dict) -> dict:
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": name, "arguments": args}}]
    proc = subprocess.run([sys.executable, str(server)],
                          input="".join(json.dumps(m) + "\n" for m in msgs),
                          capture_output=True, text=True, timeout=90, env=public_env(),
                          cwd=str(public_env.env_file.parent))
    assert proc.returncode == 0 and "Traceback" not in proc.stderr, proc.stderr[-2000:]
    out = {json.loads(ln).get("id"): json.loads(ln) for ln in proc.stdout.splitlines()}
    return out[2]["result"]


def test_the_pool_data_ships_and_pool_status_answers_the_same_from_the_bundle(built, paths, public_env):
    for p in ("pool/house.json", "docs/scoreboard/scoreboard.json"):
        assert p in paths, p
    assert not any(p.startswith("docs/scoreboard/") and not p.endswith(".json") for p in paths)
    got = _call(built / "scripts" / "pathiel-mcp-server.py", public_env, "pool_status", {})
    want = _call(ROOT / "scripts" / "pathiel-mcp-server.py", public_env, "pool_status", {})
    assert got == want and not got.get("isError"), got


def test_publish_signs_the_bundle_commit_when_git_is_set_to_sign():
    """commit-tree ignores commit.gpgsign; the bundle commit came out unsigned
    (GitHub: Unverified) until publish passed -S itself (2026-11-13)."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"commit.gpgsign"' in src and 'args.insert(1, "-S")' in src
