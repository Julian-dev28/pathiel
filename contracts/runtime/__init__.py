"""Runtime contract: who is running pathiel, on which network, with which builder fee.

Every other unit asks here and nowhere else:

  * install kind: 'operator' (the maintainer's checkout, launched by the repo
    .mcp.json with no env block) or 'public' (the Claude Code plugin, the
    launcher scripts/pathiel-mcp, or the container; they export
    PATHIEL_INSTALL=public before Python starts).
  * network: 'mainnet' or 'testnet'. One network per process.
  * builder: the Hyperliquid builder code attached to public orders.

Stdlib only. Imports nothing from pathiel or services, so any service and any
script can depend on it. Every function reads the environment at call time
(`env` defaults to os.environ), so a test can pass a plain dict.

See README.md for the resolution table.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

CONTRACT_VERSION = "1.1.0"   # 1.1.0 (2026-11-12): mainnet default on public installs

MAINNET_API_URL = "https://api.hyperliquid.xyz"
TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"

ENV_INSTALL = "PATHIEL_INSTALL"
ENV_NETWORK = "PATHIEL_NETWORK"
# Set by the public launcher (scripts/_plugin_env.sh), which always exports
# PATHIEL_NETWORK: "default" means nobody chose the network (mainnet), and
# "invalid" means the env file line was not mainnet or testnet (the launcher
# then runs testnet, the side that cannot lose real money).
ENV_NETWORK_SOURCE = "PATHIEL_NETWORK_SOURCE"
ENV_BUILDER_ADDRESS = "PATHIEL_BUILDER_ADDRESS"
ENV_BUILDER_FEE = "PATHIEL_BUILDER_FEE_TENTHS_BP"

RUNTIME_ENV_VARS = (ENV_INSTALL, ENV_NETWORK, ENV_BUILDER_ADDRESS, ENV_BUILDER_FEE)

# ════════════════════════════════════════════════════════════════════════════
# BUILDER CONFIG POINT
#
# The one place the default builder address lives. Julien's choice
# (2026-11-12): his funded master account.
#   * It links that wallet to the project publicly: anyone can see which
#     address collects the fee on every order a public install routes.
#   * The wallet needs at least 100 USDC of perps account value on Hyperliquid
#     to collect builder fees (the venue's rule; pathiel does not check it).
#   * An order from this same account never carries the builder (attach rule
#     step 4, builder.builder_for_order: self_trade).
#   * Empty means no builder is ever attached, on any install. Everything else
#     works the same.
# PATHIEL_BUILDER_ADDRESS in the environment overrides it (tests, forks); a
# non-empty value that is not an address (e.g. "none") turns the builder off.
# PATHIEL_BUILDER_FEE_TENTHS_BP=0 turns it off too.
# ════════════════════════════════════════════════════════════════════════════
DEFAULT_BUILDER_ADDRESS = "0x2c2e4efF574B3Fd9879AdeF9c5CAC6fD7C916985"

# 10 tenths of a basis point = 1 bp = 0.01% of notional per fill. A tenth of the
# 0.1% perp cap and about a fifth of the base taker fee. Approval asks for this
# exact rate, so a raise needs a new signature from the user.
DEFAULT_BUILDER_FEE_TENTHS_BP = 10
MAX_PERP_BUILDER_FEE_TENTHS_BP = 100   # Hyperliquid perp cap, 0.1%

_NETWORKS = ("mainnet", "testnet")
# PATHIEL_NETWORK_SOURCE values that mean the user did not choose the network.
_NOT_CHOSEN_SOURCES = ("default", "invalid")
_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_DIGITS_RE = re.compile(r"^[0-9]+$")


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def install_kind(env: Optional[Mapping[str, str]] = None) -> str:
    """'public' iff PATHIEL_INSTALL is 'public' (stripped, any case), else 'operator'."""
    raw = _env(env).get(ENV_INSTALL) or ""
    return "public" if str(raw).strip().lower() == "public" else "operator"


def is_public(env: Optional[Mapping[str, str]] = None) -> bool:
    return install_kind(env) == "public"


def _explicit_network(env: Optional[Mapping[str, str]]) -> Optional[str]:
    raw = _env(env).get(ENV_NETWORK) or ""
    value = str(raw).strip().lower()
    return value if value in _NETWORKS else None


def network(env: Optional[Mapping[str, str]] = None) -> str:
    """A valid PATHIEL_NETWORK wins. Otherwise mainnet, on both install kinds
    (Julien, 2026-11-12: a public install trades real money unless the user
    asks for testnet). An invalid value counts as unset. The public launcher
    never hands Python an invalid value: it exports testnet for one."""
    explicit = _explicit_network(env)
    if explicit:
        return explicit
    return "mainnet"


def network_is_explicit(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when someone chose the network: PATHIEL_NETWORK holds a valid value
    and the launcher did not mark it as its own default or its fallback for
    an invalid line."""
    if str(_env(env).get(ENV_NETWORK_SOURCE) or "").strip().lower() in _NOT_CHOSEN_SOURCES:
        return False
    return _explicit_network(env) is not None


def api_url(env: Optional[Mapping[str, str]] = None) -> str:
    return TESTNET_API_URL if network(env) == "testnet" else MAINNET_API_URL


def info_url(env: Optional[Mapping[str, str]] = None) -> str:
    return api_url(env) + "/info"


def is_mainnet(env: Optional[Mapping[str, str]] = None) -> bool:
    return network(env) == "mainnet"


def builder_address(env: Optional[Mapping[str, str]] = None) -> str:
    """The builder address, lowercase. The env override wins when non-empty.
    Anything that is not 0x plus 40 hex characters gives '' (no builder)."""
    raw = str(_env(env).get(ENV_BUILDER_ADDRESS) or "").strip()
    value = (raw or DEFAULT_BUILDER_ADDRESS or "").strip().lower()
    return value if _ADDRESS_RE.match(value) else ""


def builder_fee_tenths_bp(env: Optional[Mapping[str, str]] = None) -> int:
    """The builder fee in tenths of a basis point. The env override wins when
    non-empty. A non-integer, negative or over-cap value gives 0 (no builder)."""
    raw = str(_env(env).get(ENV_BUILDER_FEE) or "").strip()
    if not raw:
        value = int(DEFAULT_BUILDER_FEE_TENTHS_BP)
    elif _DIGITS_RE.match(raw):          # no sign, no '_', no decimals
        value = int(raw, 10)
    else:
        return 0
    if value < 0 or value > MAX_PERP_BUILDER_FEE_TENTHS_BP:
        return 0
    return value


def fee_rate_str(fee_tenths_bp: int) -> str:
    """Exact decimal percent: 1 -> '0.001%', 10 -> '0.01%', 100 -> '0.1%', 0 -> '0%'.

    This is also the string the approval action signs (max_fee_rate)."""
    pct = (Decimal(int(fee_tenths_bp)) / Decimal(1000)).normalize()
    if pct == 0:
        return "0%"
    return f"{format(pct, 'f')}%"


def describe(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    fee = builder_fee_tenths_bp(env)
    return {
        "install": install_kind(env),
        "network": network(env),
        "network_explicit": network_is_explicit(env),
        "api_url": api_url(env),
        "builder": {
            "address": builder_address(env),
            "fee_tenths_bp": fee,
            "fee_rate": fee_rate_str(fee),
        },
    }


__all__ = [
    "CONTRACT_VERSION", "MAINNET_API_URL", "TESTNET_API_URL",
    "ENV_INSTALL", "ENV_NETWORK", "ENV_NETWORK_SOURCE", "ENV_BUILDER_ADDRESS", "ENV_BUILDER_FEE", "RUNTIME_ENV_VARS",
    "DEFAULT_BUILDER_ADDRESS", "DEFAULT_BUILDER_FEE_TENTHS_BP", "MAX_PERP_BUILDER_FEE_TENTHS_BP",
    "install_kind", "is_public", "network", "network_is_explicit", "api_url", "info_url",
    "is_mainnet", "builder_address", "builder_fee_tenths_bp", "fee_rate_str", "describe",
]
