#!/usr/bin/env python3
"""Is this pathiel install set up, and what will it do? One line per check.

    "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/public_status.py
    "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/public_status.py --online   # also read the fee approval
    "${CLAUDE_PLUGIN_ROOT}/scripts/pathiel-py" scripts/public_status.py --json

Each line is 'PASS|WARN|FAIL <name>: <message>'. Exit 1 on any FAIL, else 0.
A keyless install is all PASS or WARN: read-only mode is a valid setup.

Names only, never a value: no line carries a key, and every line goes through
a redaction pass (the same idea as scripts/preflight_secrets.py) before it is
printed, so a bug in a check still cannot leak one.

Offline by default: no venue call, no background thread. --online makes one
read of the builder fee approval (maxBuilderFee, info endpoint only), waiting
at most a few seconds. Nothing here signs or places anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(1, str(ROOT / "scripts"))

# The env file first: contracts.runtime and every pathiel module read the
# environment, and some freeze it at import.
import _state_env  # noqa: E402

ENV_FILE_LOADED = _state_env.load_env_local(str(ROOT))

import contracts.runtime as runtime  # noqa: E402

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_KEY_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_HEX_KEY_IN_TEXT = re.compile(r"(0x)?[0-9a-fA-F]{64}")
_SECRET_NAME = re.compile(r"(PRIVATE_KEY|SECRET|TOKEN|PASSWORD|API_KEY)", re.IGNORECASE)

TRADING_READ_ONLY = "read-only (no key)"
TRADING_TESTNET = "testnet trading"
TRADING_MAINNET = "MAINNET trading: real money"


@dataclass
class Check:
    status: str      # PASS | WARN | FAIL
    name: str
    message: str

    def line(self) -> str:
        return f"{self.status} {self.name}: {self.message}"


# ── redaction ─────────────────────────────────────────────────────────────────

def _declared_secret_names() -> List[str]:
    """The names scripts/preflight_secrets.py declares, when it imports."""
    try:
        import preflight_secrets  # scripts/ is on sys.path
        return [spec.name for spec in preflight_secrets.REGISTRY]
    except Exception:   # noqa: BLE001 - redaction still runs on the patterns below
        return []


def redaction_values(env: Mapping[str, str]) -> List[str]:
    """Every value that must never appear in output: declared secrets, any
    variable whose name says key/secret/token/password, and any value shaped
    like a private key, whatever its name."""
    declared = set(_declared_secret_names())
    out: List[str] = []
    for name, value in env.items():
        v = (value or "").strip()
        if len(v) < 4:
            continue
        if name in declared or _SECRET_NAME.search(name) or _KEY_RE.match(v):
            out.append(v)
    # Longest first, so a value that contains another is replaced whole.
    return sorted(set(out), key=len, reverse=True)


def redact(text: str, secret_values: Sequence[str]) -> str:
    for v in secret_values:
        if v:
            text = text.replace(v, "[REDACTED]")
    # Anything still shaped like a 64-hex private key goes too.
    return _HEX_KEY_IN_TEXT.sub("[REDACTED]", text)


# ── checks ────────────────────────────────────────────────────────────────────

def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def check_install(env: Mapping[str, str]) -> Check:
    if runtime.is_public(env):
        return Check("PASS", "install", "public (the plugin, launcher or container path)")
    return Check("WARN", "install",
                 "operator (the maintainer's checkout: mainnet and the tracked config). "
                 "Run this through scripts/pathiel-py for the public path")


def network_explicit(env: Mapping[str, str]) -> bool:
    """True when the user chose the network. The launcher always exports
    PATHIEL_NETWORK, so its PATHIEL_NETWORK_SOURCE ('default' when nothing
    was set) decides; without the launcher, contracts.runtime does."""
    if (env.get("PATHIEL_NETWORK_SOURCE") or "").strip() == "default":
        return False
    return runtime.network_is_explicit(env)


def check_network(env: Mapping[str, str]) -> Check:
    net = runtime.network(env)
    how = "set by PATHIEL_NETWORK" if network_explicit(env) else "the default"
    if net == "mainnet":
        return Check("WARN", "network", f"mainnet ({how}): orders use real money")
    return Check("PASS", "network", f"testnet ({how}): test funds only")


def check_state_dir(env: Mapping[str, str]) -> Check:
    path = _get(env, "PATHIEL_STATE_DIR")
    if not path:
        return Check("WARN", "state dir", "PATHIEL_STATE_DIR is not set; state goes to the code "
                                          "directory. Run through scripts/pathiel-py")
    net = runtime.network(env)
    has_testnet = "testnet" in path.lower()
    if net == "testnet" and not has_testnet:
        return Check("WARN", "state dir", f"{path} is used for testnet but does not say testnet; "
                                          "keep testnet state apart from mainnet state")
    if net == "mainnet" and has_testnet:
        return Check("WARN", "state dir", f"{path} is used for mainnet but says testnet")
    p = Path(path)
    if p.is_dir() and os.access(p, os.W_OK):
        return Check("PASS", "state dir", f"{path} ({net})")
    if not p.exists():
        return Check("WARN", "state dir", f"{path} does not exist yet; the launcher creates it")
    return Check("FAIL", "state dir", f"{path} is not a writable directory")


def _profile_summary(profile: Any) -> str:
    def num(value: Any, unit: str) -> str:
        return f"{value:g}{unit}" if isinstance(value, (int, float)) else "from the preset"
    positions = profile.max_open_positions
    pos = (f"{positions} position{'s' if positions != 1 else ''} at a time"
           if positions else "positions from equity")
    return (f"preset {profile.preset}, max leverage {num(profile.max_leverage, 'x')}, "
            f"risk {num(profile.risk_per_trade_pct, '%')} a trade, {pos}")


def _looser_than_public(profile: Any) -> bool:
    from services.risk.presets import PUBLIC_DEFAULT_PROFILE as P
    lev, risk, pos = profile.max_leverage, profile.risk_per_trade_pct, profile.max_open_positions
    return (lev is None or lev > P.max_leverage or risk is None or risk > P.risk_per_trade_pct
            or pos is None or pos > P.max_open_positions)


def check_config(env: Mapping[str, str]) -> Check:
    try:
        from pathiel.agents import config_store
        path = Path(config_store.CONFIG_PATH)
        repo_file = ROOT / ".agent-config.json"
        if runtime.is_public(env) and os.path.realpath(path) == os.path.realpath(repo_file):
            return Check("FAIL", "config", "a public install is reading the repo's .agent-config.json "
                                           "(the maintainer's 20x config); set "
                                           "PATHIEL_AGENT_CONFIG_FILE or run through scripts/pathiel-py")
        profile = config_store.read_risk_profile()
        summary = _profile_summary(profile)
        if not path.is_file():
            return Check("WARN", "config", f"{path} does not exist; using {summary}. "
                                           "scripts/pathiel-mcp --bootstrap writes it")
        if runtime.is_public(env) and _looser_than_public(profile):
            return Check("WARN", "config", f"{path}: {summary}. That is looser than the public "
                                           "default (3x, 1% a trade, one position)")
        return Check("PASS", "config", f"{path}: {summary}")
    except Exception as e:   # noqa: BLE001
        return Check("FAIL", "config", f"cannot read the config: {type(e).__name__}: {e}")


def check_key_present(env: Mapping[str, str]) -> Check:
    key = _get(env, "HYPERLIQUID_PRIVATE_KEY")
    if not key:
        return Check("WARN", "key", "HYPERLIQUID_PRIVATE_KEY is not set: read-only mode. "
                                    "Every read tool works; order tools refuse")
    if not _KEY_RE.match(key):
        return Check("FAIL", "key", "HYPERLIQUID_PRIVATE_KEY is set but is not 64 hex characters "
                                    "(with or without 0x)")
    return Check("PASS", "key", "HYPERLIQUID_PRIVATE_KEY is set (value not shown)")


def check_addresses(env: Mapping[str, str]) -> List[Check]:
    has_key = bool(_get(env, "HYPERLIQUID_PRIVATE_KEY"))
    out: List[Check] = []
    for name in ("HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_MASTER_ADDRESS"):
        value = _get(env, name)
        if not value:
            status = "FAIL" if has_key else "WARN"
            why = "a key is set, so this is needed" if has_key else "fine for read-only mode"
            out.append(Check(status, "address", f"{name} is not set ({why})"))
        elif not _ADDRESS_RE.match(value):
            out.append(Check("FAIL", "address", f"{name} is not 0x plus 40 hex characters"))
        else:
            out.append(Check("PASS", "address", f"{name} is a well-formed address"))
    return out


def _key_address(key: str) -> Optional[str]:
    """The address the key signs as, or None when it cannot be derived here."""
    if not _KEY_RE.match(key):
        return None
    try:
        from eth_account import Account
        return str(Account.from_key(key if key.startswith("0x") else "0x" + key).address).lower()
    except Exception:   # noqa: BLE001 - not installed, or a key the library refuses
        return None


def check_agent_not_master(env: Mapping[str, str]) -> Check:
    key = _get(env, "HYPERLIQUID_PRIVATE_KEY")
    wallet = _get(env, "HYPERLIQUID_WALLET_ADDRESS").lower()
    master = _get(env, "HYPERLIQUID_MASTER_ADDRESS").lower()
    if not key:
        return Check("PASS", "agent key", "no key set; nothing can sign")
    if wallet and master and wallet == master:
        return Check("FAIL", "agent key", "HYPERLIQUID_WALLET_ADDRESS equals HYPERLIQUID_MASTER_ADDRESS: "
                                          "the signing key would be your main account. Create an API "
                                          "wallet in the Hyperliquid app (More, API) and use its key")
    signer = _key_address(key)
    if signer and master and signer == master:
        return Check("FAIL", "agent key", "HYPERLIQUID_PRIVATE_KEY is your main account's key. Use an "
                                          "API wallet key instead; the main key can withdraw funds")
    if signer and wallet and signer != wallet:
        return Check("WARN", "agent key", "HYPERLIQUID_PRIVATE_KEY does not belong to "
                                          "HYPERLIQUID_WALLET_ADDRESS; check that the two match")
    if not master:
        return Check("WARN", "agent key", "HYPERLIQUID_MASTER_ADDRESS is not set, so this cannot "
                                          "confirm the key is an API wallet")
    return Check("PASS", "agent key", "the signing key is an API wallet, not the main account")


def check_master_key_absent(env: Mapping[str, str]) -> Check:
    present = bool(_get(env, "HYPERLIQUID_MASTER_PRIVATE_KEY"))
    if not present:
        return Check("PASS", "master key", "HYPERLIQUID_MASTER_PRIVATE_KEY is not set")
    if runtime.is_public(env):
        return Check("FAIL", "master key", "HYPERLIQUID_MASTER_PRIVATE_KEY is set: remove it from the "
                                           "env file; pathiel never needs it there")
    return Check("WARN", "master key", "HYPERLIQUID_MASTER_PRIVATE_KEY is set (operator install; "
                                       "scripts/treasury.py reads it)")


def trading_mode(env: Mapping[str, str]) -> str:
    if not _get(env, "HYPERLIQUID_PRIVATE_KEY"):
        return TRADING_READ_ONLY
    return TRADING_MAINNET if runtime.network(env) == "mainnet" else TRADING_TESTNET


def check_trading_mode(env: Mapping[str, str]) -> Check:
    mode = trading_mode(env)
    if mode == TRADING_TESTNET:
        return Check("PASS", "trading", mode)
    return Check("WARN", "trading", mode)


def _account(env: Mapping[str, str]) -> str:
    """The account orders trade for: the main account when the signer is a
    separate API wallet, else the signer (pathiel.client.exchange.HL_ACCOUNT)."""
    wallet = _get(env, "HYPERLIQUID_WALLET_ADDRESS")
    master = _get(env, "HYPERLIQUID_MASTER_ADDRESS")
    return master if (master and wallet and master.lower() != wallet.lower()) else wallet


def check_builder(env: Mapping[str, str], online: bool) -> Check:
    address = runtime.builder_address(env)
    fee = runtime.builder_fee_tenths_bp(env)
    rate = runtime.fee_rate_str(fee)
    if not runtime.is_public(env):
        return Check("PASS", "builder fee", "operator install: no builder fee on any order")
    if not address or fee <= 0:
        return Check("PASS", "builder fee", "no builder address configured: orders carry no builder fee")
    head = f"{rate} of notional per fill to {address}, charged only after you approve it"
    if not online:
        return Check("PASS", "builder fee", f"{head}. Approval not read (add --online)")
    try:
        from pathiel.client.builder import builder_status
        status = builder_status(_account(env) or None, refresh=True)
    except Exception as e:   # noqa: BLE001
        return Check("WARN", "builder fee", f"{head}. Could not read the approval: {type(e).__name__}")
    approved = status.get("approved_tenths_bp")
    approved_txt = (f"approved up to {runtime.fee_rate_str(approved)}"
                    if isinstance(approved, int) else "approval unknown")
    level = "PASS" if status.get("attaches") else "WARN"
    return Check(level, "builder fee", f"{head}. {approved_txt}: {status.get('reason')}")


def check_claude_cli() -> Check:
    if shutil.which("claude"):
        return Check("PASS", "claude CLI", "on PATH (desk_refresh writes theses with it)")
    return Check("WARN", "claude CLI", "not on PATH: desk_refresh cannot write a new thesis; "
                                       "every other tool works")


def run_checks(env: Mapping[str, str], online: bool = False) -> List[Check]:
    checks = [check_install(env), check_network(env), check_state_dir(env), check_config(env),
              check_key_present(env)]
    checks += check_addresses(env)
    checks += [check_agent_not_master(env), check_master_key_absent(env), check_trading_mode(env),
               check_builder(env, online), check_claude_cli()]
    return checks


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="pathiel install status (names only, never a value)")
    ap.add_argument("--online", action="store_true",
                    help="also read the builder fee approval from Hyperliquid (info endpoint only)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    env: Dict[str, str] = dict(os.environ)
    secrets = redaction_values(env)
    checks = run_checks(env, online=args.online)
    failed = any(c.status == "FAIL" for c in checks)

    if args.as_json:
        payload = {
            "ok": not failed,
            "install": runtime.install_kind(env),
            "network": runtime.network(env),
            "network_explicit": network_explicit(env),
            "trading_mode": trading_mode(env),
            "env_file": ENV_FILE_LOADED,
            "checks": [asdict(c) for c in checks],
        }
        print(redact(json.dumps(payload, indent=2), secrets))
    else:
        for c in checks:
            print(redact(c.line(), secrets))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
