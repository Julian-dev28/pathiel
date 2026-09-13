"""Gate tests for the Vercel entrypoint, `api/index.py`.

These are source-level assertions rather than an import of the module, and that
is deliberate: importing it mutates `os.environ` (HOME, four data paths, two
feature flags) and pulls in the whole FastAPI app, which would leak into every
other test in the suite.

What they defend:

  - The credential guard. `PATHIA_PUBLIC_DASHBOARD=1` opens every account
    endpoint to anonymous readers. That is fine over invented data and a
    breach over real data, so the guard that separates the two cannot regress.
  - Statement order. Three separate module-scope side effects in the pathia
    package read the environment at import time — `Path.home()` in
    `client/universe.py` (which mkdirs, and crashed the first deploy with
    `OSError: [Errno 30] Read-only file system`), and the path constants in
    `session_log.py` and `config_store.py`. An import that drifts above the
    environment setup breaks the deploy at boot, and the only place that shows
    up is production.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[3] / "api" / "index.py"


@pytest.fixture(scope="module")
def source() -> str:
    return ENTRYPOINT.read_text()


@pytest.fixture(scope="module")
def tree(source: str) -> ast.Module:
    return ast.parse(source)


def _lineno_of_pathia_import(tree: ast.Module) -> int:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pathia"):
            return node.lineno
    pytest.fail("api/index.py never imports pathia — it cannot serve the dashboard")


def _lineno_of_env_assignment(tree: ast.Module, key: str) -> int:
    """Line of `os.environ[key] = ...`, or the fail if it never happens."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "environ"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == key):
                return node.lineno
    pytest.fail(f"api/index.py never sets os.environ[{key!r}]")


def test_entrypoint_exists():
    assert ENTRYPOINT.is_file()


class TestCredentialGuard:
    def test_names_every_credential_that_would_matter(self, source: str):
        for key in ("HYPERLIQUID_PRIVATE_KEY", "PRIVATE_KEY_HEX",
                    "HYPERLIQUID_ACCOUNT_ADDRESS", "PATHIA_OPERATOR_TOKEN"):
            assert key in source, f"credential guard does not check {key}"

    def test_treats_a_local_env_file_as_a_credential(self, source: str):
        """`pathia/server.py` loads `.env.local` into the environment on import,
        so a guard that only reads os.environ passes on a dev box and then
        boots the public demo against real keys."""
        assert ".env.local" in source

    def test_raises_rather_than_warning(self, tree: ast.Module):
        assert any(isinstance(n, ast.Raise) for n in ast.walk(tree)), (
            "the credential guard must abort the boot, not log and continue")

    def test_guard_runs_before_the_dashboard_is_imported(self, tree: ast.Module):
        raise_line = min(n.lineno for n in ast.walk(tree) if isinstance(n, ast.Raise))
        assert raise_line < _lineno_of_pathia_import(tree)


class TestEnvironmentOrder:
    @pytest.mark.parametrize("key", [
        "HOME",
        "PATHIA_PUBLIC_DASHBOARD",
        "PATHIA_DASHBOARD_READONLY",
    ])
    def test_set_before_pathia_is_imported(self, tree: ast.Module, key: str):
        assert _lineno_of_env_assignment(tree, key) < _lineno_of_pathia_import(tree), (
            f"{key} is set after pathia is imported; the module-scope constants "
            f"that read it are already frozen by then")

    def test_home_is_redirected_somewhere_writable(self, source: str):
        """Regression: the first deploy died at import with
        `OSError: [Errno 30] Read-only file system: '/home/sbx_user1051'`
        because `pathia/client/universe.py` mkdirs `Path.home()/.pathia`."""
        assert "gettempdir()" in source
        assert 'os.environ["HOME"]' in source

    def test_the_demo_is_mounted_behind_a_prefix(self, source: str):
        """The demo used to BE the deployment: the entrypoint materialized
        synthetic data into the environment and the whole app served it. Live is
        the landing page now, and the demo is a sub-application under /demo with
        its own dashboard instance — so the front-end toggle is a path, and no
        request can put the live view into a state where it serves generated
        numbers. See services/demo/router.py.
        """
        assert "build_demo_app" in source
        assert 'app.mount("/demo"' in source

    def test_the_entrypoint_no_longer_makes_the_whole_app_demo(self, source: str):
        """The regression that matters in the other direction."""
        assert "for _key, _path in materialize" not in source


class TestReadOnly:
    def test_write_path_is_closed(self, source: str):
        assert 'os.environ["PATHIA_DASHBOARD_READONLY"] = "1"' in source

    def test_public_read_is_open(self, source: str):
        """Without this the demo renders a sign-in prompt and no data."""
        assert 'os.environ["PATHIA_PUBLIC_DASHBOARD"] = "1"' in source


class TestSiweDomain:
    """The domain inside the signed message has to be the domain being served.

    services/auth takes it from config, never from the Host header, because an
    attacker controls Host and a domain derived from it asserts nothing. The
    default is "localhost:8000", so an entrypoint that does not set it makes
    the wallet show "localhost:8000 wants you to sign in" on a vercel.app page
    — the exact shape of a phishing prompt, teaching the user to ignore the one
    field that makes SIWE worth anything.
    """

    def test_the_entrypoint_sets_the_auth_domain(self, source: str):
        assert "PATHIA_AUTH_DOMAIN" in source

    def test_it_takes_the_domain_from_the_platform(self, source: str):
        """Not a literal anyone has to remember to update."""
        assert "VERCEL_PROJECT_PRODUCTION_URL" in source or "VERCEL_URL" in source

    def test_the_domain_is_set_before_pathia_is_imported(self, tree: ast.Module):
        """services/auth/api.py reads it per request, but the URI default is
        built from it, so ordering stays part of this file's contract."""
        setdefault_lines = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "setdefault"
            and any(isinstance(a, ast.Constant) and a.value == "PATHIA_AUTH_DOMAIN"
                    for a in n.args)
        ]
        assert setdefault_lines, "PATHIA_AUTH_DOMAIN is never set"
        assert min(setdefault_lines) < _lineno_of_pathia_import(tree)


class TestWritableState:
    """Everything this process writes has to go somewhere writable.

    /var/task is read-only on Vercel and the defaults point at it: HOME for the
    universe cache, PATHIA_STATE_DIR for the auth database. Both have now broken
    a deploy, and neither failed anywhere near the thing the user was doing —
    the second surfaced in the wallet as "Error preparing message, please
    retry!" while the actual exception was
    `sqlite3.OperationalError: unable to open database file`.
    """

    def test_a_writable_state_dir_is_set(self, source: str):
        """services/auth builds `<PATHIA_STATE_DIR>/auth.db`, defaulting to "."."""
        assert "PATHIA_STATE_DIR" in source
        assert "gettempdir()" in source

    def test_it_is_set_before_pathia_is_imported(self, tree: ast.Module):
        """deps.get_store() constructs AuthStore lazily, but nothing guarantees
        the first call happens after import, so ordering is the contract."""
        lines = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "setdefault"
                 and any(isinstance(a, ast.Constant) and a.value == "PATHIA_STATE_DIR"
                         for a in n.args)]
        assert lines, "PATHIA_STATE_DIR is never set"
        assert min(lines) < _lineno_of_pathia_import(tree)

    def test_every_directory_it_names_is_created(self, source: str):
        """Setting the variable is not enough; sqlite will not mkdir for you."""
        assert source.count("os.makedirs(") >= 2
