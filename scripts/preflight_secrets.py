#!/usr/bin/env python3
"""Preflight secrets check — run BEFORE the app starts, refuse to start when
secrets handling is wrong.

Local dev survives a plaintext `.env.local` on a laptop nobody else touches.
A deployed service does not: the same file pattern on Fly/k8s means the
process env, the image layers, and (if someone ever runs `git add -A`) the
repo history all become plausible leak paths for a real-money Hyperliquid key.
This script is the gate — it is deterministic, offline, and exits non-zero
the moment anything is wrong, with a precise message naming what and where.

Checks (see CHECKS below for the authoritative list):
  1. every REQUIRED secret is present and non-empty (some conditionally, e.g.
     OPENROUTER_API_KEY only when AI_BRAIN_PROVIDER resolves to "openrouter")
  2. the Hyperliquid trading key is an AGENT wallet, not the MASTER wallet —
     HYPERLIQUID_WALLET_ADDRESS must differ from HYPERLIQUID_MASTER_ADDRESS.
     This is the single highest-value check here: an agent wallet cannot
     withdraw funds, a master key can. A leaked agent key is an annoyance;
     a leaked master key is a robbery.
  3. HYPERLIQUID_MASTER_PRIVATE_KEY (the actual master signing key, used only
     by scripts/treasury.py for manual transfers) is never present when this
     runs in a deployed context (Fly/k8s) — it must stay laptop-only.
  4. no known secret VALUE appears anywhere in the git index (`git grep
     --cached`, which inspects staged blobs, not the working tree — this is
     what actually catches an accidental `git add .env.local`).
  5. `.env.local` and its siblings (.env, .env.old, .env.local.old) are
     gitignored AND not tracked.
  6. those same files are not group/other readable or writable (0600, not
     the 0644 a plain `touch`/editor save leaves behind).

Every finding is printed by NAME, never by VALUE. `_redact()` is applied to
every line this script prints, as defense in depth against a check
accidentally interpolating a value into its own message.

Usage:
    python scripts/preflight_secrets.py [--repo-root PATH] [--env-file PATH] [--deploy]

Exit codes:
    0  all checks passed (WARN/SKIP findings do not fail the run)
    1  one or more checks failed
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Values shorter than this are almost always placeholders ("", "0", "changeme")
# rather than real secrets — scanning for them against the git index would be
# noise (a short common substring matches all kinds of unrelated files).
MIN_SECRET_SCAN_LEN = 8

# Env-like files we expect to exist locally and never in the repo.
ENV_FILE_CANDIDATES: Tuple[str, ...] = (".env.local", ".env", ".env.old", ".env.local.old")

# Signals that this process is running inside an already-deployed container,
# not on the operator's laptop. Both are set automatically by the respective
# platform — nothing in this repo has to opt in for the signal to be honest.
_DEPLOY_ENV_SIGNALS: Tuple[str, ...] = ("FLY_APP_NAME", "KUBERNETES_SERVICE_HOST")


# ── Secret registry ──────────────────────────────────────────────────────────
# The authoritative list of secret-ish env vars this codebase reads. Keep in
# sync with docs/SECRETS.md (which carries the full inventory: purpose, module,
# provisioning, rotation) — this table only needs what the checks act on.
#
# required:
#   "always"       — preflight fails if missing/empty, unconditionally
#   "openrouter"   — required only when AI_BRAIN_PROVIDER resolves to "openrouter"
#   "optional"     — feature degrades gracefully without it (documented per-var
#                    in SECRETS.md); preflight never fails on absence
#   "local_only"   — must NEVER be present in a deployed environment; checked
#                    by check_master_key_not_deployed(), not the required-set check
@dataclass(frozen=True)
class SecretSpec:
    name: str
    purpose: str
    required: str  # "always" | "openrouter" | "optional" | "local_only"


REGISTRY: Tuple[SecretSpec, ...] = (
    SecretSpec("HYPERLIQUID_WALLET_ADDRESS", "agent/API wallet address that signs orders", "always"),
    SecretSpec("HYPERLIQUID_PRIVATE_KEY", "agent/API wallet private key — signs orders", "always"),
    SecretSpec("HYPERLIQUID_MASTER_ADDRESS", "master account public address (funds live here)", "always"),
    SecretSpec("HERMES_OPERATOR_TOKEN", "bearer token gating the dashboard operator surface", "always"),
    SecretSpec("OPENROUTER_API_KEY", "OpenRouter API key for the default AI brain provider", "openrouter"),
    SecretSpec("HYPERLIQUID_MASTER_PRIVATE_KEY", "master account private key — treasury transfers only", "local_only"),
    SecretSpec("BRAVE_API_KEY", "Brave Search API key — news context for research (optional)", "optional"),
    SecretSpec("UW_API_KEY", "Unusual Whales API key — options-flow alt-data (optional)", "optional"),
    SecretSpec("HYDROMANCER_API_KEY", "Hydromancer data-plane API key (research/backfill only)", "optional"),
    SecretSpec("POLYMARKET_ADDRESS", "Polymarket funder address (arb — no live order path yet)", "optional"),
    SecretSpec("POLYMARKET_API_KEY", "Polymarket CLOB L2 API key (arb — no live order path yet)", "optional"),
    SecretSpec("POLYMARKET_SECRET", "Polymarket CLOB L2 HMAC secret (arb — no live order path yet)", "optional"),
    SecretSpec("POLYMARKET_PASSPHRASE", "Polymarket CLOB L2 passphrase (arb — no live order path yet)", "optional"),
    SecretSpec("POLYMARKET_PRIVATE_KEY", "Polymarket L1 signing key (arb — no live order path yet)", "optional"),
)


@dataclass
class Finding:
    check: str
    status: str  # "PASS" | "FAIL" | "WARN" | "SKIP"
    name: str
    message: str


# ── env loading ───────────────────────────────────────────────────────────────

def parse_env_file(path: Path) -> Dict[str, str]:
    """KEY=VALUE parser matching services/trend_engine/env.py's loader. Never
    logs or returns anything derived from a value beyond the value itself in
    memory — callers must not print it."""
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def effective_env(env_file: Path, process_env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """What the app will actually see: the file provides values, a real
    process env var of the same name wins (matches every `.setdefault`
    loader in this repo — hermes_trader/server.py, services/trend_engine/env.py,
    scripts/*.py all load the file first, then `setdefault`)."""
    merged = parse_env_file(env_file)
    merged.update(dict(process_env if process_env is not None else os.environ))
    return merged


def is_deploy_context(env: Mapping[str, str], forced: bool = False) -> bool:
    return forced or any(env.get(sig) for sig in _DEPLOY_ENV_SIGNALS)


# ── redaction ─────────────────────────────────────────────────────────────────

def _redact(text: str, secret_values: Sequence[str]) -> str:
    """Defense in depth: strip any known secret VALUE out of a line before it
    is ever printed, even if a check bug tried to put one there. Only values
    long enough to not be common substrings are redacted, matching the git
    scan's own threshold."""
    out = text
    for v in secret_values:
        if v and len(v) >= 4:
            out = out.replace(v, "[REDACTED]")
    return out


def _emit(findings: List[Finding], secret_values: Sequence[str],
          printer=print) -> None:
    for f in findings:
        line = f"[{f.status}] {f.check}: {f.name}: {f.message}"
        printer(_redact(line, secret_values))


# ── checks ────────────────────────────────────────────────────────────────────

def check_required_present(env: Mapping[str, str]) -> List[Finding]:
    findings: List[Finding] = []
    provider = _effective_ai_brain_provider(env)
    for spec in REGISTRY:
        applies = spec.required == "always" or (spec.required == "openrouter" and provider == "openrouter")
        if not applies:
            continue
        val = (env.get(spec.name) or "").strip()
        if val:
            findings.append(Finding("required_present", "PASS", spec.name, "present and non-empty"))
        else:
            reason = "required" if spec.required == "always" else \
                "required because AI_BRAIN_PROVIDER resolves to 'openrouter'"
            findings.append(Finding("required_present", "FAIL", spec.name,
                                     f"missing or empty ({reason}) — {spec.purpose}"))
    return findings


def _effective_ai_brain_provider(env: Mapping[str, str]) -> str:
    """Mirrors hermes_trader/agents/ai_brain.py's _normalise_provider() +
    DEFAULT_AI_BRAIN_PROVIDER. Kept standalone (no project import) so this
    script has zero dependency on the app's import graph — it must still run
    when the app itself is broken."""
    raw = (env.get("AI_BRAIN_PROVIDER") or "").strip().lower().replace("-", "_")
    aliases = {"claude": "claude_cli", "codex": "codex_cli", "open_router": "openrouter"}
    provider = aliases.get(raw, raw)
    if provider in {"openrouter", "claude_cli", "codex_cli"}:
        return provider
    return "openrouter"  # DEFAULT_AI_BRAIN_PROVIDER


def check_master_not_agent(env: Mapping[str, str]) -> List[Finding]:
    """The highest-value check: the trading key must be an agent/API wallet,
    never the master. Mirrors hermes_trader/client/exchange.py's own
    IS_AGENT computation, but preflight treats an unproven relationship as a
    failure rather than a silent single-wallet fallback."""
    wallet = (env.get("HYPERLIQUID_WALLET_ADDRESS") or "").strip()
    master = (env.get("HYPERLIQUID_MASTER_ADDRESS") or "").strip()
    name = "HYPERLIQUID_WALLET_ADDRESS/HYPERLIQUID_MASTER_ADDRESS"
    if not wallet:
        return [Finding("agent_not_master", "SKIP", name,
                         "HYPERLIQUID_WALLET_ADDRESS is not set — covered by required_present")]
    if not master:
        return [Finding("agent_not_master", "FAIL", name,
                         "HYPERLIQUID_MASTER_ADDRESS is not set — cannot prove "
                         "HYPERLIQUID_WALLET_ADDRESS is an agent wallet distinct from the "
                         "master. Set HYPERLIQUID_MASTER_ADDRESS (a public address, not a "
                         "secret) so this check can run.")]
    if wallet.lower() == master.lower():
        return [Finding("agent_not_master", "FAIL", name,
                         "HYPERLIQUID_WALLET_ADDRESS equals HYPERLIQUID_MASTER_ADDRESS — the "
                         "trading key IS the master wallet. A compromised process could "
                         "withdraw funds, not just lose the trade. Create an agent/API wallet "
                         "in the Hyperliquid UI (approveAgent) and point "
                         "HYPERLIQUID_WALLET_ADDRESS/HYPERLIQUID_PRIVATE_KEY at it instead.")]
    return [Finding("agent_not_master", "PASS", name,
                     "trading key is a distinct agent wallet from the master — cannot withdraw")]


def check_master_key_not_deployed(env: Mapping[str, str], deploy_mode: bool) -> List[Finding]:
    """HYPERLIQUID_MASTER_PRIVATE_KEY signs treasury transfers (scripts/treasury.py)
    and can withdraw funds. It has no business in a running service's
    environment — only in the operator's local .env.local for manual CLI use."""
    name = "HYPERLIQUID_MASTER_PRIVATE_KEY"
    present = bool((env.get(name) or "").strip())
    if not present:
        return [Finding("master_key_not_deployed", "PASS", name, "not present in this environment")]
    if deploy_mode:
        return [Finding("master_key_not_deployed", "FAIL", name,
                         "present in a DEPLOYED environment (Fly/k8s signal detected). This key "
                         "can withdraw funds and must stay laptop-only for scripts/treasury.py — "
                         "remove it from the deploy secret store immediately and rotate it.")]
    return [Finding("master_key_not_deployed", "WARN", name,
                     "present locally — fine for scripts/treasury.py, but confirm it is never "
                     "copied into a Fly/k8s secret store alongside the trading key.")]


def _env_files_present(repo_root: Path) -> List[Path]:
    return [repo_root / name for name in ENV_FILE_CANDIDATES if (repo_root / name).exists()]


def check_env_files_gitignored(repo_root: Path) -> List[Finding]:
    findings: List[Finding] = []
    if not (repo_root / ".git").exists():
        return [Finding("env_gitignored", "SKIP", "-", f"{repo_root} is not a git repository")]
    for path in _env_files_present(repo_root):
        rel = path.relative_to(repo_root)
        proc = subprocess.run(["git", "check-ignore", "-q", str(rel)],
                               cwd=repo_root, capture_output=True)
        if proc.returncode == 0:
            findings.append(Finding("env_gitignored", "PASS", str(rel), "gitignored"))
        else:
            findings.append(Finding("env_gitignored", "FAIL", str(rel),
                                     "NOT covered by .gitignore — add it before touching git in this tree"))
    if not findings:
        findings.append(Finding("env_gitignored", "PASS", "-", "no env files present to check"))
    return findings


def check_env_files_not_tracked(repo_root: Path) -> List[Finding]:
    findings: List[Finding] = []
    if not (repo_root / ".git").exists():
        return [Finding("env_not_tracked", "SKIP", "-", f"{repo_root} is not a git repository")]
    for path in _env_files_present(repo_root):
        rel = path.relative_to(repo_root)
        proc = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)],
                               cwd=repo_root, capture_output=True)
        if proc.returncode == 0:
            findings.append(Finding("env_not_tracked", "FAIL", str(rel),
                                     "TRACKED by git — this file has secrets and must never be "
                                     "committed. `git rm --cached` it, then rotate every secret "
                                     "it held."))
        else:
            findings.append(Finding("env_not_tracked", "PASS", str(rel), "not tracked"))
    if not findings:
        findings.append(Finding("env_not_tracked", "PASS", "-", "no env files present to check"))
    return findings


def check_env_file_permissions(repo_root: Path) -> List[Finding]:
    findings: List[Finding] = []
    for path in _env_files_present(repo_root):
        rel = path.relative_to(repo_root)
        mode = stat.S_IMODE(path.stat().st_mode)
        # Any group/other bit (read, write, or execute) means this file is
        # readable by more than its owner. 0600 has none of these bits set.
        if mode & 0o077:
            findings.append(Finding("env_file_permissions", "FAIL", str(rel),
                                     f"mode {oct(mode)} is readable/writable beyond the owner — "
                                     f"run `chmod 600 {rel}`"))
        else:
            findings.append(Finding("env_file_permissions", "PASS", str(rel), f"mode {oct(mode)}"))
    if not findings:
        findings.append(Finding("env_file_permissions", "PASS", "-", "no env files present to check"))
    return findings


def check_git_index_for_secrets(repo_root: Path, secrets: Mapping[str, str]) -> List[Finding]:
    """Scans the git INDEX (staged blobs), not the working tree — this is
    what catches a secret that was `git add`ed and then the working copy
    edited or reverted, which a working-tree grep would miss entirely."""
    if not (repo_root / ".git").exists():
        return [Finding("git_index_scan", "SKIP", "-", f"{repo_root} is not a git repository")]
    try:
        subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo_root,
                        capture_output=True, check=True)
    except Exception:
        return [Finding("git_index_scan", "SKIP", "-", "git not available or repo has no HEAD")]

    findings: List[Finding] = []
    for name, value in secrets.items():
        if not value or len(value) < MIN_SECRET_SCAN_LEN:
            continue
        pattern_file = None
        try:
            fd, pattern_file = tempfile.mkstemp(prefix="preflight-secret-", text=True)
            os.chmod(pattern_file, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(value + "\n")
            # -l: filenames only. Never let the matched LINE (which contains
            # the secret) reach this process's stdout.
            proc = subprocess.run(
                ["git", "grep", "--cached", "--fixed-strings", "-l", "-f", pattern_file],
                cwd=repo_root, capture_output=True, text=True,
            )
            if proc.returncode == 0:
                paths = [p for p in proc.stdout.splitlines() if p]
                findings.append(Finding("git_index_scan", "FAIL", name,
                                         f"value found in tracked file(s) in the git index: "
                                         f"{', '.join(paths)} — rotate {name} now and purge it "
                                         f"from history"))
            elif proc.returncode not in (0, 1):
                findings.append(Finding("git_index_scan", "WARN", name,
                                         f"git grep exited {proc.returncode}; could not verify "
                                         f"({(proc.stderr or '').strip()[:120]})"))
        finally:
            if pattern_file and os.path.exists(pattern_file):
                os.remove(pattern_file)
    if not findings:
        findings.append(Finding("git_index_scan", "PASS", "-",
                                 f"no scanned secret value (>= {MIN_SECRET_SCAN_LEN} chars) "
                                 f"found in the git index"))
    return findings


# ── orchestration ─────────────────────────────────────────────────────────────

def run_all(repo_root: Path, env: Mapping[str, str], deploy_mode: bool) -> List[Finding]:
    findings: List[Finding] = []
    findings += check_required_present(env)
    findings += check_master_not_agent(env)
    findings += check_master_key_not_deployed(env, deploy_mode)
    findings += check_env_files_gitignored(repo_root)
    findings += check_env_files_not_tracked(repo_root)
    findings += check_env_file_permissions(repo_root)
    scan_values = {spec.name: (env.get(spec.name) or "") for spec in REGISTRY}
    findings += check_git_index_for_secrets(repo_root, scan_values)
    return findings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--env-file", type=Path, default=None,
                         help="defaults to <repo-root>/.env.local")
    parser.add_argument("--deploy", action="store_true",
                         help="force deploy-mode checks even without a platform signal")
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root
    env_file: Path = args.env_file if args.env_file is not None else repo_root / ".env.local"

    env = effective_env(env_file)
    deploy_mode = is_deploy_context(env, forced=args.deploy)
    findings = run_all(repo_root, env, deploy_mode)

    secret_values = [v for v in env.values() if v and len(v) >= 4]

    print(f"preflight_secrets: repo={repo_root} env_file={env_file} "
          f"deploy_mode={deploy_mode}")
    _emit(findings, secret_values)

    failures = [f for f in findings if f.status == "FAIL"]
    warnings = [f for f in findings if f.status == "WARN"]
    if failures:
        print(f"PREFLIGHT: FAIL ({len(failures)} failing check(s), "
              f"{len(warnings)} warning(s)) — do not start")
        return 1
    print(f"PREFLIGHT: PASS ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
