"""No venue call bypasses the network resolver.

contracts/runtime resolves mainnet or testnet once per process, and every
venue URL must come from it. A hardcoded 'api.hyperliquid.xyz' anywhere else
would read (or sign on) mainnet from a testnet install. This scans every
tracked non-test .py and shell file for the literal mainnet or testnet API
host in code (comments, docstrings and other bare string statements do not
count) and fails on any file outside ALLOWED.

ALLOWED is the runtime contract itself plus the operator's research and data
tools that stay on mainnet by design. A signer on the list must refuse to run
off the operator's mainnet path (SIGNERS_ON_ALLOWLIST).
"""
from __future__ import annotations

import ast
import io
import subprocess
import tokenize
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]

VENUE_HOSTS = ("api.hyperliquid.xyz", "api.hyperliquid-testnet.xyz")

ALLOWED = {
    "contracts/runtime/__init__.py",
    "scripts/treasury.py",
    "services/strategies/backtest/data.py",
    "services/strategies/evals/loss_audit.py",
    "services/pathiel_data_api/app/config.py",
}
SIGNERS_ON_ALLOWLIST = {"scripts/treasury.py"}
GUARD = "_refuse_unless_operator_mainnet"


def _is_test_file(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return ("tests" in parts[:-1] or name == "conftest.py"
            or name.startswith("test_") or name.endswith("_test.py"))


def _is_shell(path: Path) -> bool:
    if path.suffix == ".sh":
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as fh:
            first = fh.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and (b"sh" in first.split(b"/")[-1])


def candidate_files() -> List[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    files = []
    for rel in out.split("\0"):
        if not rel or _is_test_file(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        if path.suffix == ".py" or _is_shell(path):
            files.append(rel)
    return sorted(files)


def _python_code_strings(source: str) -> List[str]:
    """Every string literal that is code: f-string parts and bytes included,
    docstrings and other bare string statements left out. Comments never
    reach the AST."""
    tree = ast.parse(source)
    skip = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, (str, bytes))):
            skip.add(id(node.value))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in skip:
            if isinstance(node.value, str):
                found.append(node.value)
            elif isinstance(node.value, bytes):
                found.append(node.value.decode("utf-8", "replace"))
    return found


def _python_code_strings_fallback(source: str) -> List[str]:
    """For a file ast cannot parse: STRING tokens, comments already dropped by
    the tokenizer. Docstrings are not told apart here, so it errs strict."""
    return [tok.string for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type == tokenize.STRING]


def _shell_code(source: str) -> str:
    kept = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        cut = line.find(" #")
        kept.append(line if cut < 0 else line[:cut])
    return "\n".join(kept)


def hardcoded_host(path: Path) -> Optional[str]:
    """The first venue host the file's code names, else None."""
    source = path.read_text(encoding="utf-8", errors="replace")
    if not any(host in source for host in VENUE_HOSTS):
        return None
    if path.suffix == ".py":
        try:
            chunks = _python_code_strings(source)
        except SyntaxError:
            chunks = _python_code_strings_fallback(source)
    else:
        chunks = [_shell_code(source)]
    for chunk in chunks:
        for host in VENUE_HOSTS:
            if host in chunk:
                return host
    return None


def offenders(files) -> List[str]:
    return [f"{rel}: {host}" for rel in files
            if rel not in ALLOWED and (host := hardcoded_host(ROOT / rel)) is not None]


# ── tests ───────────────────────────────────────────────────────────────────

def test_the_scan_covers_the_venue_clients():
    files = set(candidate_files())
    for rel in ("pathiel/client/hl_client.py", "services/intel/sources/venue.py",
                "scripts/oil_levels.py", "scripts/pathiel-mcp-server.py", "scripts/_plugin_env.sh",
                "scripts/pathiel-mcp", "contracts/runtime/__init__.py"):
        assert rel in files, rel
    assert not any(_is_test_file(f) for f in files)


def test_only_allowlisted_files_hardcode_a_venue_url():
    assert offenders(candidate_files()) == []


def test_the_check_catches_a_planted_url(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text(
        '"""Talks to https://api.hyperliquid.xyz/info (docstring: ignored)."""\n'
        "# https://api.hyperliquid.xyz in a comment: ignored\n"
        "BASE = 'https://api.hyperliquid.xyz'\n", encoding="utf-8")
    assert hardcoded_host(planted) == "api.hyperliquid.xyz"

    fstring = tmp_path / "fstring.py"
    fstring.write_text("def url(p):\n    return f'https://api.hyperliquid-testnet.xyz/{p}'\n",
                       encoding="utf-8")
    assert hardcoded_host(fstring) == "api.hyperliquid-testnet.xyz"

    quiet = tmp_path / "quiet.py"
    quiet.write_text('"""See https://api.hyperliquid.xyz/info."""\n'
                     "# api.hyperliquid.xyz\nX = 1\n", encoding="utf-8")
    assert hardcoded_host(quiet) is None

    shell = tmp_path / "planted.sh"
    shell.write_text("#!/bin/bash\n# api.hyperliquid.xyz is fine here\n"
                     "curl https://api.hyperliquid.xyz/info  # and here\n", encoding="utf-8")
    assert hardcoded_host(shell) == "api.hyperliquid.xyz"


def test_today_the_venue_module_mentions_the_host_only_in_its_docstring():
    """services/intel/sources/venue.py keeps its 2026-10-23 verification note
    in the docstring; the scan must read past it and find no code use."""
    path = ROOT / "services" / "intel" / "sources" / "venue.py"
    assert "api.hyperliquid.xyz" in path.read_text(encoding="utf-8")
    assert hardcoded_host(path) is None


def test_allowlisted_files_still_exist():
    tracked = set(candidate_files())
    assert ALLOWED <= tracked, sorted(ALLOWED - tracked)
    assert SIGNERS_ON_ALLOWLIST <= ALLOWED


def test_allowlisted_signers_are_guarded():
    for rel in sorted(SIGNERS_ON_ALLOWLIST):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        defined = any(isinstance(n, ast.FunctionDef) and n.name == GUARD for n in ast.walk(tree))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == GUARD]
        assert defined, f"{rel} does not define {GUARD}"
        assert len(calls) >= 2, f"{rel} calls {GUARD} {len(calls)} time(s); main() and the signer need it"
