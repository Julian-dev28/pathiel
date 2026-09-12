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

    def test_demo_data_is_materialized_before_the_import(self, tree: ast.Module):
        call_lines = [n.lineno for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id == "materialize"]
        assert call_lines, "api/index.py never calls materialize()"
        assert min(call_lines) < _lineno_of_pathia_import(tree)


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
