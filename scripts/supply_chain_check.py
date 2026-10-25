#!/usr/bin/env python3
"""Supply-chain checks for the one Node tree (services/wallet_ui) and the
Python lock, as code instead of a checklist.

Written for the 2026-10-25 review (docs/security/SUPPLY_CHAIN_2026-10-25.md)
against the 2025-2026 npm compromises: the Qix maintainer phish that put a
wallet drainer into chalk/debug (Sept 2025), the Shai-Hulud install-hook
worms (Sept and Nov 2025), and the autumn 2026 wave (MALFEX, tensorlake,
keyv/cacheable). Each check names the gap it closes.

    python scripts/supply_chain_check.py            # offline, every check
    python scripts/supply_chain_check.py --online   # + OSV advisories for what ships

Offline mode is deterministic, needs no network and no node_modules, and is
what tests/test_supply_chain.py runs on every commit. --online asks OSV
(api.osv.dev) about every npm package that lands in pathiel/static/wallet.js
and every pin in requirements.lock; the nightly workflow runs it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WALLET_UI = ROOT / "services" / "wallet_ui"
LOCK = WALLET_UI / "package-lock.json"
PACKAGE_JSON = WALLET_UI / "package.json"
NPMRC = WALLET_UI / ".npmrc"
MANIFEST = WALLET_UI / "bundle.manifest.json"
STATIC = ROOT / "pathiel" / "static"
WORKFLOWS = ROOT / ".github" / "workflows"
PY_LOCK = ROOT / "requirements.lock"

REGISTRY = "https://registry.npmjs.org/"

# Packages in the tree that declare an install/postinstall script. None of
# them may run (`ignore-scripts=true` in .npmrc, `--ignore-scripts` in CI);
# the list exists so a NEW package with a hook fails review instead of
# arriving silently. Why each is safe to skip:
#   esbuild         binary comes from the optional @esbuild/<platform> package
#   bufferutil      optional native speedup for `ws` in Node; never bundled
#   utf-8-validate  same, for `ws`
#   keccak          native build of a hash the bundle gets from @noble/hashes
KNOWN_INSTALL_SCRIPTS = {"esbuild", "bufferutil", "utf-8-validate", "keccak"}

# Published indicators from the Sept 2025 Qix/chalk/debug browser drainer, the
# one 2025-2026 npm payload that targeted wallet code running in a browser,
# i.e. exactly what pathiel/static/wallet.js is. Source: the maintainer's
# incident thread https://github.com/chalk/chalk/issues/656 (2025-09-08).
# Only names a legitimate bundle has no reason to contain are listed.
BUNDLE_IOCS = (
    "stealthProxyControl",
    "checkethereumw",
    "newdlocal",
    "runmask",
    "0xfc4a4858bafef54d1b1d7697bfb5c52f4c166976",   # attacker ETH receiver
)

# File names the Shai-Hulud worms (2025-09, 2025-11) dropped into repos and
# packages. Sources: CISA alert 2025-09-23; Microsoft guidance 2025-12-09.
WORM_FILENAMES = re.compile(
    r"(^|/)(setup_bun\.js|bun_environment\.js|shai-hulud(-workflow)?\.ya?ml|"
    r"formatter_[^/]*\.ya?ml|truffleSecrets\.json|actionsSecrets\.json)$")

_SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")


def _lock() -> dict:
    return json.loads(LOCK.read_text())


def check_lockfile_integrity() -> list[str]:
    """Every locked package has a sha512 integrity hash and comes from the
    public registry. npm ci refuses a tarball whose bytes do not match, so a
    lockfile entry without one is a package npm would install unverified."""
    problems = []
    lock = _lock()
    if lock.get("lockfileVersion", 0) < 3:
        problems.append(f"package-lock.json lockfileVersion {lock.get('lockfileVersion')} < 3")
    for path, entry in lock["packages"].items():
        if not path or entry.get("link"):
            continue
        if not str(entry.get("integrity", "")).startswith("sha512-"):
            problems.append(f"{path}: no sha512 integrity")
        if not str(entry.get("resolved", "")).startswith(REGISTRY):
            problems.append(f"{path}: resolved outside {REGISTRY}: {entry.get('resolved')!r}")
    return problems


def check_install_scripts() -> list[str]:
    """No package outside KNOWN_INSTALL_SCRIPTS declares an install hook, and
    hooks are off for every install."""
    problems = []
    for path, entry in _lock()["packages"].items():
        if entry.get("hasInstallScript"):
            name = path.split("node_modules/")[-1]
            if name not in KNOWN_INSTALL_SCRIPTS:
                problems.append(f"{path}@{entry.get('version')}: new package with an install "
                                f"script; review it, then add it to KNOWN_INSTALL_SCRIPTS")
    npmrc = NPMRC.read_text() if NPMRC.exists() else ""
    if not re.search(r"(?m)^ignore-scripts\s*=\s*true\s*$", npmrc):
        problems.append("services/wallet_ui/.npmrc does not set ignore-scripts=true")
    return problems


def check_package_json_exact() -> list[str]:
    """package.json names exact versions, the same ones the lockfile holds.
    A caret range lets `npm install` (not `npm ci`) take any newer release
    on the day a hijacked one is live, which is the whole Qix attack window."""
    problems = []
    pj = json.loads(PACKAGE_JSON.read_text())
    pkgs = _lock()["packages"]
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (pj.get(section) or {}).items():
            if not re.fullmatch(r"\d+\.\d+\.\d+", spec):
                problems.append(f"package.json {section}.{name} = {spec!r} is a range, not a version")
                continue
            locked = (pkgs.get(f"node_modules/{name}") or {}).get("version")
            if locked != spec:
                problems.append(f"package.json {name}@{spec} but package-lock.json has {locked}")
    return problems


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


def check_bundle_manifest() -> list[str]:
    """The committed bundle matches the sha256 its build recorded, and every
    package the manifest says ships is a version the lockfile pins."""
    problems = []
    manifest = json.loads(MANIFEST.read_text())
    import hashlib
    for artifact in manifest.get("artifacts", []):
        want = (manifest.get("artifactSha256") or {}).get(artifact)
        got = hashlib.sha256((STATIC / artifact).read_bytes()).hexdigest()
        if want != got:
            problems.append(f"pathiel/static/{artifact}: sha256 {got[:12]} != manifest {str(want)[:12]}")
    locked = {(p.split("node_modules/")[-1], e.get("version"))
              for p, e in _lock()["packages"].items() if p}
    shipped = manifest.get("bundledPackages") or []
    if not shipped:
        problems.append("bundle.manifest.json lists no bundledPackages; run `npm run build`")
    for spec in shipped:
        name, _, version = spec.rpartition("@")
        if (name, version) not in locked:
            problems.append(f"bundled {spec} is not a version package-lock.json pins")
    return problems


def check_bundle_iocs() -> list[str]:
    problems = []
    for artifact in ("wallet.js", "wallet.css"):
        text = (STATIC / artifact).read_text(errors="ignore").lower()
        for ioc in BUNDLE_IOCS:
            if ioc.lower() in text:
                problems.append(f"pathiel/static/{artifact} contains drainer indicator {ioc!r}")
    return problems


def check_worm_artifacts() -> list[str]:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.splitlines()
    return [f"tracked file matches a Shai-Hulud artifact name: {f}"
            for f in files if WORM_FILENAMES.search(f)]


def check_no_registry_tokens() -> list[str]:
    """No tracked .npmrc carries a registry token."""
    problems = []
    for rc in [ROOT / ".npmrc", NPMRC]:
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
    check_lockfile_integrity,
    check_install_scripts,
    check_package_json_exact,
    check_workflows,
    check_bundle_manifest,
    check_bundle_iocs,
    check_worm_artifacts,
    check_no_registry_tokens,
    check_python_lock_hashes,
)


def osv_findings() -> list[str]:
    """OSV advisories for every npm package that ships in the bundle and every
    Python pin. Network; nightly lane only."""
    import httpx
    manifest = json.loads(MANIFEST.read_text())
    queries = []
    for spec in manifest.get("bundledPackages") or []:
        name, _, version = spec.rpartition("@")
        queries.append({"package": {"name": name, "ecosystem": "npm"}, "version": version})
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
        print(f"{'FAIL' if found else 'ok  '}  osv advisories on shipped npm packages and Python pins")
        problems += found
    for p in problems:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
