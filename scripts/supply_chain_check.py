#!/usr/bin/env python3
"""Supply-chain checks for the Python lock, the workflows and the repo
itself, as code instead of a checklist.

Written for the 2026-10-25 review (docs/security/SUPPLY_CHAIN_2026-10-25.md)
against the 2025-2026 npm compromises: the Qix maintainer phish that put a
wallet drainer into chalk/debug (Sept 2025), the Shai-Hulud install-hook
worms (Sept and Nov 2025), and the autumn 2026 wave (MALFEX, tensorlake,
keyv/cacheable). Each check names the gap it closes.

    python scripts/supply_chain_check.py            # offline, every check
    python scripts/supply_chain_check.py --online   # + OSV advisories on the pins

Offline mode is deterministic, needs no network, and is what
tests/test_supply_chain.py runs on every commit. --online asks OSV
(api.osv.dev) about every pin in requirements.lock; the nightly workflow
runs it. The npm half (lockfile integrity, install-script allow-list, exact
pins, bundle manifest and drainer indicators) left with services/wallet_ui
on 2026-10-25; docs/WALLET_UI_RESTORE.md says where it is.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
PY_LOCK = ROOT / "requirements.lock"

# File names the Shai-Hulud worms (2025-09, 2025-11) dropped into repos and
# packages. Sources: CISA alert 2025-09-23; Microsoft guidance 2025-12-09.
WORM_FILENAMES = re.compile(
    r"(^|/)(setup_bun\.js|bun_environment\.js|shai-hulud(-workflow)?\.ya?ml|"
    r"formatter_[^/]*\.ya?ml|truffleSecrets\.json|actionsSecrets\.json)$")

_SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.y*ml"))


def check_workflows() -> list[str]:
    """CI installs npm with --ignore-scripts, pins every action to a commit
    SHA, declares least-privilege permissions, and never runs untrusted PR
    code with secrets (pull_request_target)."""
    problems = []
    for wf in _workflow_files():
        text = wf.read_text()
        rel = wf.relative_to(ROOT)
        for line_no, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            m = re.search(r"\buses:\s*(\S+)", code)
            if m and not m.group(1).startswith("./") and not _SHA_PIN.match(m.group(1)):
                problems.append(f"{rel}:{line_no}: action not pinned to a commit SHA: {m.group(1)}")
            if re.search(r"\bnpm\s+(ci|install|i)\b", code) and "--ignore-scripts" not in code:
                problems.append(f"{rel}:{line_no}: npm install without --ignore-scripts")
            if re.search(r"\bpip install\b(?!.*(-r requirements\.lock|--no-deps))", code):
                problems.append(f"{rel}:{line_no}: pip install outside the hashed lock")
        if not re.search(r"(?m)^permissions:", text):
            problems.append(f"{rel}: no top-level permissions block")
        if re.search(r"(?m)^\s*\w[\w-]*:\s*write\s*$", text):
            problems.append(f"{rel}: grants a write permission")
        if "pull_request_target" in text:
            problems.append(f"{rel}: uses pull_request_target")
    return problems


def check_worm_artifacts() -> list[str]:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.splitlines()
    return [f"tracked file matches a Shai-Hulud artifact name: {f}"
            for f in files if WORM_FILENAMES.search(f)]


def check_no_registry_tokens() -> list[str]:
    """No tracked .npmrc carries a registry token."""
    problems = []
    for rc in [ROOT / ".npmrc"]:
        if rc.exists() and re.search(r"_authToken|_auth\s*=|//.*:_password", rc.read_text()):
            problems.append(f"{rc.relative_to(ROOT)} carries a registry credential")
    return problems


def python_pins() -> dict[str, str]:
    pins = {}
    for line in PY_LOCK.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line)
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def check_python_lock_hashes() -> list[str]:
    """Every pin in requirements.lock carries --hash lines, so pip installs
    it in hash-checking mode."""
    problems = []
    lines = PY_LOCK.read_text().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^[A-Za-z0-9_.\-]+==", line):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if "--hash=sha256:" not in nxt:
                problems.append(f"requirements.lock:{i + 1}: {line.split()[0]} has no --hash")
    if not python_pins():
        problems.append("requirements.lock has no pins")
    return problems


OFFLINE_CHECKS = (
    check_workflows,
    check_worm_artifacts,
    check_no_registry_tokens,
    check_python_lock_hashes,
)


def osv_findings() -> list[str]:
    """OSV advisories for every Python pin. Network; nightly lane only."""
    import httpx
    queries = []
    for name, version in python_pins().items():
        queries.append({"package": {"name": name, "ecosystem": "PyPI"}, "version": version})
    resp = httpx.post("https://api.osv.dev/v1/querybatch", json={"queries": queries}, timeout=60)
    resp.raise_for_status()
    out = []
    for q, result in zip(queries, resp.json().get("results", [])):
        for vuln in result.get("vulns") or []:
            out.append(f"{q['package']['ecosystem']} {q['package']['name']}@{q['version']}: "
                       f"{vuln['id']} https://osv.dev/vulnerability/{vuln['id']}")
    return out


def main(argv: list[str]) -> int:
    problems = []
    for check in OFFLINE_CHECKS:
        found = check()
        print(f"{'FAIL' if found else 'ok  '}  {check.__name__}")
        problems += found
    if "--online" in argv:
        found = osv_findings()
        print(f"{'FAIL' if found else 'ok  '}  osv advisories on the Python pins")
        problems += found
    for p in problems:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
