"""The research plugin bundle (scripts/build_plugin_bundle.py --plugin
research): what a marketplace install of `pathiel-research` actually gets.

Mirrors tests/test_plugin_bundle.py's checks for the trading bundle, scoped
to the research server. The bug this bundle exists to fix: services/
research_mcp/.claude-plugin/plugin.json used to start its server with
"${CLAUDE_PLUGIN_ROOT}/../../scripts/pathiel-research-mcp.sh", and the
marketplace entry's source was the relative path "./services/research_mcp".
Claude Code only copies a relative-path entry's OWN directory into the
plugin cache (code.claude.com/docs/en/plugins/loading#in-place-and-copied-
plugins: "Every other marketplace plugin: ... Files outside the plugin
directory aren't copied"), so a real (non-local) marketplace install got
services/research_mcp/ alone -- no scripts/, services/intel/, services/
strategies/ or contracts/ -- and the launcher could not import the server
at all. The fix: build a SELF-CONTAINED bundle (this file's `built` fixture)
whose root holds every file the server's import closure touches, at their
real repo-root-relative paths, and point the marketplace entry at that
bundle's branch instead (a `github` source with `ref: "plugin-research"`;
see .claude-plugin/marketplace.json and tests/test_plugin.py).

    .venv/bin/python -m pytest -q tests/test_plugin_bundle_research.py
"""
from __future__ import annotations

import ast
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
HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("build_plugin_bundle", SCRIPT)
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

# The trading bundle's own test module: reused for its git-plumbing test
# helpers (_clean_env, _git, _refs, _repo_state), not duplicated.
_tspec = importlib.util.spec_from_file_location("_research_bundle_shared", HERE / "test_plugin_bundle.py")
T = importlib.util.module_from_spec(_tspec)
_tspec.loader.exec_module(T)

# services/research_mcp/tests/test_no_trading_imports.py's own FORBIDDEN_MODULES:
# the canonical, server-side gate this bundle must still satisfy.
_gspec = importlib.util.spec_from_file_location(
    "_research_no_trading_gate", ROOT / "services" / "research_mcp" / "tests" / "test_no_trading_imports.py")
GATE = importlib.util.module_from_spec(_gspec)
_gspec.loader.exec_module(GATE)


@pytest.fixture(scope="module")
def files():
    return B.ls_tree(B.INDEX)


@pytest.fixture(scope="module")
def paths(files):
    return B.research_bundle_paths(files)


@pytest.fixture(scope="module")
def built(tmp_path_factory, paths):
    out = tmp_path_factory.mktemp("research_bundle") / "pathiel-research"
    assert B.research_build(B.INDEX, out) == paths
    return out


# ── the forbidden-module cut ────────────────────────────────────────────────

def test_research_forbidden_modules_cover_the_servers_own_static_gate():
    """RESEARCH_FORBIDDEN_MODULES must be a superset of the server's own
    canonical list (the one documentary, non-dotted entry, "scripts.
    pathiel-mcp-server", excepted -- closure() never sees it as an import
    target). A drift here (someone trims the build script's list without
    noticing the server's gate grew) fails right here, not in a shipped
    bundle."""
    # "scripts.pathiel-mcp-server" is that module's own documentary entry
    # (not a valid dotted module name -- it says so in its own comment);
    # closure() never sees it as an import target, so it is not expected
    # here either.
    canonical = set(GATE.FORBIDDEN_MODULES) - {"scripts.pathiel-mcp-server"}
    assert canonical <= B.RESEARCH_FORBIDDEN_MODULES, canonical - B.RESEARCH_FORBIDDEN_MODULES


def test_banned_trading_and_credential_files_are_absent_from_the_bundle(paths):
    banned_prefixes = ("pathiel/agents/bracket.py", "pathiel/agents/executor.py",
                      "pathiel/client/exchange.py", "pathiel/client/builder.py",
                      "pathiel/client/account_snapshot.py", "services/strategies/next_trade.py",
                      "services/accounts/")
    hits = [p for p in paths if any(p == b or p.startswith(b) for b in banned_prefixes)]
    assert hits == []


def test_no_shipped_research_file_statically_imports_a_forbidden_module(built, paths):
    """services/research_mcp/tests/test_no_trading_imports.py's own static
    technique, run over the server's own files INSIDE the built bundle (byte
    for byte the same files; this proves the bundling step did not change
    that gate's answer)."""
    offenders = []
    for p in paths:
        if not (p.startswith("services/research_mcp/") and p.endswith(".py")):
            continue
        tree = ast.parse((built / p).read_bytes(), filename=p)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if GATE._is_forbidden(n):
                    offenders.append(f"{p}: {n}")
    assert offenders == []


def test_dynamic_fresh_process_never_loads_a_forbidden_module_from_the_bundle(built):
    """The bundle's own services.research_mcp.server, imported fresh in a
    subprocess whose sys.path is ONLY the bundle directory (so a forbidden
    module could only come from inside the bundle, never the checkout)."""
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(built)!r})\n"
        "from services.research_mcp import server\n"
        f"forbidden = {sorted(B.RESEARCH_FORBIDDEN_MODULES)!r}\n"
        "hits = [m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "print(json.dumps(hits))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                          cwd=str(built))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


# ── self-containment: every path inside ${CLAUDE_PLUGIN_ROOT} ──────────────

def test_every_required_research_file_is_in_the_bundle(paths):
    required = (B.RESEARCH_PLUGIN_JSON_SRC, B.RESEARCH_README_SRC) + B.RESEARCH_LISTING_FILES \
        + B.RESEARCH_RUNTIME_FILES + B.RESEARCH_ENTRY_SCRIPTS
    for p in required:
        assert p in paths, p


def test_no_relocated_or_shipped_path_escapes_the_bundle_root(paths):
    import posixpath
    for p in paths:
        dest = B._research_dest(p)
        assert not dest.startswith("..") and not dest.startswith("/"), dest
        assert posixpath.normpath(dest) == dest, dest   # no "./", no redundant ".."


def test_the_bundle_declares_one_mcp_server_with_args_fully_inside_claude_plugin_root(built):
    plugin = json.loads((built / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["mcpServers"] == {"pathiel-research": {
        "command": "bash", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-research-mcp.sh"]}}
    assert ".." not in json.dumps(plugin)
    assert not (built / ".mcp.json").exists()
    assert sorted(p.name for p in (built / ".claude-plugin").iterdir()) == ["icon.png", "plugin.json"]


def test_build_keeps_the_launcher_executable(built):
    assert (built / "scripts" / "pathiel-research-mcp.sh").stat().st_mode & 0o100


def test_the_research_bundle_is_far_smaller_than_the_repo_and_has_no_tests_or_evals(files, paths):
    assert len(paths) < len(files) / 3, (len(paths), len(files))
    assert not any("/tests/" in p or "/evals/" in p for p in paths)
    assert not any(p.startswith(("tests/", "CLAUDE.md", ".github/", "docs/")) for p in paths)


# ── the import closure resolves, and the server runs from the bundle alone ──

def test_every_first_party_import_in_the_research_bundle_resolves_inside_it(built, paths):
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
                if m.split(".")[0] in ("pathiel", "services", "contracts"):
                    base = m.replace(".", "/")
                    namespace = any(s.startswith(base + "/") for s in shipped)
                    if f"{base}.py" not in shipped and f"{base}/__init__.py" not in shipped \
                            and not namespace and not B._is_forbidden(m, B.RESEARCH_FORBIDDEN_MODULES):
                        missing.append(f"{p}: {m}")
    assert missing == []


def _tools(server: Path, public_env) -> list:
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    proc = subprocess.run([sys.executable, str(server)],
                          input="".join(json.dumps(m) + "\n" for m in msgs),
                          capture_output=True, text=True, timeout=90, env=public_env(),
                          cwd=str(server.parent.parent))
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = {json.loads(ln).get("id"): json.loads(ln) for ln in proc.stdout.splitlines()}
    return sorted(t["name"] for t in out[2]["result"]["tools"])


def test_the_research_server_lists_the_same_six_tools_from_the_bundle_alone(built, public_env):
    from_bundle = _tools(built / "scripts" / "pathiel-research-mcp.py", public_env)
    from_checkout = _tools(ROOT / "scripts" / "pathiel-research-mcp.py", public_env)
    assert from_bundle == from_checkout and len(from_bundle) == 6
    assert from_bundle == ["events", "fred_series", "headlines", "levels", "prediction_odds",
                           "research_brief"]


def test_every_bundled_research_module_imports_from_the_bundle_alone(built, paths, public_env):
    mods = [p[:-3].replace("/", ".").removesuffix(".__init__") for p in paths
            if p.endswith(".py") and p.startswith(("pathiel/", "services/", "contracts/"))]
    code = ("import importlib, sys\nsys.path.insert(0, sys.argv[1])\nbad = []\n"
            "for m in sys.argv[2:]:\n"
            "    try:\n        importlib.import_module(m)\n"
            "    except Exception as e:\n        bad.append(f'{m}: {type(e).__name__}: {e}')\n"
            "print('\\n'.join(bad))\n")
    proc = subprocess.run([sys.executable, "-c", code, str(built), *mods], capture_output=True,
                          text=True, timeout=120, env=public_env(), cwd=str(built))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "", proc.stdout


# ── the shipped README ──────────────────────────────────────────────────────

def test_the_research_readme_has_no_install_commands_and_no_dead_links(built, paths):
    shipped = set(paths) | {B._research_dest(p) for p in paths}
    text = (built / "README.md").read_text(encoding="utf-8")
    for needle in ("docker run", "docker build", "git clone"):
        assert needle not in text, needle
    for m in re.finditer(r"\]\((?!https?://|mailto:|#)([^)\s#]+)", text):
        assert m.group(1) in shipped, f"dead link: {m.group(1)}"


def test_the_research_readme_still_documents_the_install_command(built):
    text = (built / "README.md").read_text(encoding="utf-8")
    assert "/plugin marketplace add Julian-dev28/pathiel" in text
    assert "/plugin install pathiel-research@pathiel" in text


# ── publish, on a throwaway clone (never the real repo's refs) ─────────────

@pytest.fixture
def real_refs_unchanged():
    before = T._repo_state(ROOT)
    yield
    assert T._repo_state(ROOT) == before, "a research bundle test changed a ref or config of the real repo"


@pytest.mark.parametrize("var", B.HOOK_ENV)
def test_research_publish_refuses_inside_a_git_hook(tmp_path, var, real_refs_unchanged):
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True, env=T._clean_env())
    env = dict(T._clean_env(), **{var: str(decoy / ".git") if var != "GIT_INDEX_FILE"
                                  else str(decoy / ".git" / "index")})
    proc = subprocess.run([sys.executable, str(SCRIPT), "publish", "--plugin", "research",
                           "--ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, env=env)
    assert proc.returncode != 0 and "publish refused" in proc.stderr and var in proc.stderr
    assert T._refs(decoy) == ""


def test_research_publish_is_idempotent_fast_forward_and_check_catches_drift(tmp_path, real_refs_unchanged):
    clone = tmp_path / "clone"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(clone)],
                   check=True, env=T._clean_env())
    T._git(clone, "checkout", "-q", "--detach", head)
    (clone / "scripts").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, clone / "scripts" / "build_plugin_bundle.py")   # this version, uncommitted
    run = [sys.executable, str(clone / "scripts" / "build_plugin_bundle.py")]

    def bundle(*args):
        return subprocess.run(run + list(args) + ["--plugin", "research"], cwd=clone,
                              capture_output=True, text=True, env=T._clean_env())

    if T._git(clone, "branch", "-r", "--list", "origin/plugin-research"):
        assert bundle("publish").stdout.strip() == T._git(clone, "rev-parse", "plugin-research")
        T._git(clone, "branch", "-D", "plugin-research")
        T._git(clone, "update-ref", "-d", "refs/remotes/origin/plugin-research")
    first = bundle("publish").stdout.strip()
    assert first and T._git(clone, "rev-parse", "plugin-research") == first
    assert T._git(clone, "rev-parse", "plugin-research^{tree}") == bundle("tree").stdout.strip()
    assert bundle("publish").stdout.strip() == first
    assert bundle("check", "--against", "plugin-research").returncode == 0

    readme = clone / "services" / "research_mcp" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    T._git(clone, "commit", "-q", "-am", "change a bundled research file")
    stale = bundle("check", "--against", "plugin-research")
    assert stale.returncode == 1 and "MISMATCH" in stale.stderr
    second = bundle("publish").stdout.strip()
    assert second != first and T._git(clone, "rev-parse", f"{second}^") == first
    assert bundle("check", "--against", "plugin-research").returncode == 0

    t = clone / "CLAUDE.md"
    t.write_text(t.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    T._git(clone, "commit", "-q", "-am", "change an unbundled file")
    assert bundle("check", "--against", "plugin-research").returncode == 0
    assert bundle("publish").stdout.strip() == second
