"""Gate tests: deterministic, offline, <2s. No browser, no build step.

services/wallet_ui/src/lib/contract.ts and lib/validate.ts hand-mirror
contracts/accounts/models.py because there is no Python-to-TS codegen in
this project (see contract.ts's own docstring for why one isn't worth
building here). Hand-mirroring is only safe if something catches drift the
instant one side changes without the other — that is this file's entire
job. It reads both sides as plain text and compares the literal strings; it
does not import or execute either the TS or the Python beyond what
contracts/accounts/tests/test_contract.py already does for the Python side
alone.

Also guards the one security property that must never regress silently:
services/wallet_ui never writes to localStorage/sessionStorage and never
logs to the console, anywhere in its source — see ApiKeyView.tsx's
docstring for why (the API wallet private key passes through this bundle).
"""
from __future__ import annotations

import re
from pathlib import Path

WALLET_SRC = Path(__file__).resolve().parents[1] / "src"
MODELS_PY = Path(__file__).resolve().parents[3] / "contracts" / "accounts" / "models.py"


def _models_text() -> str:
    assert MODELS_PY.is_file(), f"contracts/accounts/models.py not found at {MODELS_PY}"
    return MODELS_PY.read_text()


def _ts_text(relpath: str) -> str:
    path = WALLET_SRC / relpath
    assert path.is_file(), f"{path} not found"
    return path.read_text()


def test_contract_version_matches_python():
    py_match = re.search(r'CONTRACT_VERSION\s*=\s*"([^"]+)"', _models_text())
    ts_match = re.search(r"CONTRACT_VERSION\s*=\s*'([^']+)'", _ts_text("lib/contract.ts"))
    assert py_match, "CONTRACT_VERSION not found in contracts/accounts/models.py"
    assert ts_match, "CONTRACT_VERSION not found in services/wallet_ui/src/lib/contract.ts"
    assert py_match.group(1) == ts_match.group(1), (
        f"CONTRACT_VERSION drifted: python={py_match.group(1)!r} ts={ts_match.group(1)!r}. "
        f"Bump both together."
    )


def test_address_regex_matches_python():
    py_match = re.search(r'_ADDR\s*=\s*r"(\^[^"]+\$)"', _models_text())
    ts_match = re.search(r"ADDRESS_RE\s*=\s*/(\^[^/]+\$)/", _ts_text("lib/validate.ts"))
    assert py_match, "_ADDR pattern not found in contracts/accounts/models.py"
    assert ts_match, "ADDRESS_RE not found in services/wallet_ui/src/lib/validate.ts"
    assert py_match.group(1) == ts_match.group(1), (
        f"address pattern drifted: python={py_match.group(1)!r} ts={ts_match.group(1)!r}"
    )


def test_private_key_regex_matches_python():
    py_match = re.search(r'agent_private_key:\s*str\s*=\s*Field\(pattern=r"(\^[^"]+\$)"\)', _models_text())
    ts_match = re.search(r"PRIVATE_KEY_RE\s*=\s*/(\^[^/]+\$)/", _ts_text("lib/validate.ts"))
    assert py_match, "ApiKeyImportIn.agent_private_key pattern not found in contracts/accounts/models.py"
    assert ts_match, "PRIVATE_KEY_RE not found in services/wallet_ui/src/lib/validate.ts"
    assert py_match.group(1) == ts_match.group(1), (
        f"private key pattern drifted: python={py_match.group(1)!r} ts={ts_match.group(1)!r}"
    )


def test_signature_regex_matches_python():
    py_match = re.search(r'signature:\s*str\s*=\s*Field\(pattern=r"(\^[^"]+\$)"\)', _models_text())
    assert py_match, "AgentConfirmIn.signature pattern not found in contracts/accounts/models.py"
    # The bundle never constructs or validates a signature string itself — it
    # only relays whatever wagmi's signTypedDataAsync returns straight to
    # confirmAgent() (lib/api.ts) — so there is no TS-side regex to compare
    # against. This test exists so a future signature-shape change is at
    # least noticed here, with an explicit reason it is not mirrored.
    assert py_match.group(1) == r"^0x[0-9a-fA-F]{130}$"


def test_private_key_never_written_to_storage_or_logged():
    offenders: list[str] = []
    for path in WALLET_SRC.rglob("*"):
        if path.suffix not in (".ts", ".tsx"):
            continue
        text = path.read_text()
        for pattern, label in (
            (r"localStorage\s*[.\[]", "localStorage"),
            (r"sessionStorage\s*[.\[]", "sessionStorage"),
            (r"console\.\w+\(", "console.*("),
        ):
            if re.search(pattern, text):
                offenders.append(f"{path.relative_to(WALLET_SRC)}: {label}")
    assert not offenders, (
        "found storage/console usage in services/wallet_ui/src — the API wallet "
        f"private key must never reach either: {offenders}"
    )
