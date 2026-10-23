"""Accounts contract, v1: wallet connection and account management.

One flow replaces sign-in. Connecting a Hyperliquid account is the identity:
the user proves ownership while authorising a trading key, so there is no
separate "Sign in" step (modelled on Hyperdash's Terminal Wallet).

Three ways in:
  wallet_sign  connect a browser wallet, sign Hyperliquid's approveAgent once.
               The server generates the agent (API) key; it can trade but can
               never withdraw. The user's main key never leaves their wallet.
  api_key      paste the main address + an API wallet private key created on
               app.hyperliquid.xyz/API. Verified against Hyperliquid before use.
  watch        paste any address. Read-only: dashboards and desks, no orders.
  env          legacy .env.local keys, shown read-only so old setups keep working.

Owned by services/accounts (registry, key vault, approval, HTTP routes).
services/auth issues the session after a successful connect.
services/wallet_ui renders the modal; pathiel templates render onboarding.

Routes (services/accounts/api.py):
  GET    /api/session                       -> SessionInfo
  GET    /api/accounts                      -> List[Account]
  POST   /api/accounts/agent/prepare        -> AgentPrepareOut   body: AgentPrepareIn
  POST   /api/accounts/agent/confirm        -> Account           body: AgentConfirmIn
  POST   /api/accounts/import               -> Account           body: ApiKeyImportIn
  POST   /api/accounts/watch                -> Account           body: WatchIn
  POST   /api/accounts/{account_id}/activate -> Account
  PATCH  /api/accounts/{account_id}         -> Account           body: AccountPatch
  DELETE /api/accounts/{account_id}         -> {"ok": true}
  GET    /api/accounts/active/summary       -> ActiveSummary
  GET    /api/accounts/{account_id}/positions -> {"user", "accounts": [...], "any_naked"}
                                                (services/accounts/positions.py; not modeled
                                                here since it mirrors scripts/watch_wallet.py's
                                                CLI shape, not a strict contract type)

Private keys are never returned by any route, never logged, never written
unencrypted. Bump CONTRACT_VERSION on a breaking change.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0.0"

Address = str  # 0x-prefixed, 40 hex chars, stored lowercase
AccountMode = Literal["trade", "watch"]
AccountSource = Literal["wallet_sign", "api_key", "watch", "env"]

_ADDR = r"^0x[0-9a-fA-F]{40}$"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Account(_M):
    id: str
    label: str = Field(max_length=48)
    main_address: Address = Field(pattern=_ADDR)       # the account that holds funds
    agent_address: Optional[Address] = Field(default=None, pattern=_ADDR)  # signs orders
    mode: AccountMode
    source: AccountSource
    is_active: bool = False
    created_at: datetime
    verified_at: Optional[datetime] = None             # last time HL confirmed the agent
    agent_name: Optional[str] = None                   # name used in approveAgent


class AgentPrepareIn(_M):
    main_address: Address = Field(pattern=_ADDR)
    label: str = Field(default="Main", max_length=48)
    # The chain the wallet is on right now. Hyperliquid accepts a user-signed
    # action from ANY chain (the SDK: "signatureChainId is the chain used by
    # the wallet to sign and can be any chain"), so signing on the wallet's
    # current chain means the user never has to switch networks. None means
    # Arbitrum One (42161), the chain Hyperliquid deposits already use.
    signature_chain_id: Optional[int] = Field(default=None, ge=1, le=2**53 - 1)


class AgentPrepareOut(_M):
    request_id: str
    agent_address: Address = Field(pattern=_ADDR)
    # EIP-712 payload for eth_signTypedData_v4, exactly as the wallet must sign it:
    # {"domain": ..., "types": ..., "primaryType": ..., "message": ...}
    typed_data: Dict[str, Any]
    expires_at: datetime


class AgentConfirmIn(_M):
    request_id: str
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


class ApiKeyImportIn(_M):
    main_address: Address = Field(pattern=_ADDR)
    agent_private_key: str = Field(pattern=r"^(0x)?[0-9a-fA-F]{64}$")
    label: str = Field(default="Main", max_length=48)


class WatchIn(_M):
    address: Address = Field(pattern=_ADDR)
    label: str = Field(default="Watching", max_length=48)


class AccountPatch(_M):
    label: Optional[str] = Field(default=None, max_length=48)


class SessionInfo(_M):
    # local_mode: request came from this machine; no session is needed at all.
    local_mode: bool
    address: Optional[Address] = None                  # who the session belongs to
    active_account_id: Optional[str] = None
    accounts_count: int = 0
    can_trade: bool = False                            # active account is mode=trade and verified
    needs_onboarding: bool = True                      # no account connected yet


class ActiveSummary(_M):
    account: Optional[Account] = None
    equity_usd: Optional[float] = None
    withdrawable_usd: Optional[float] = None
    open_positions: int = 0
    can_trade: bool = False
    blockers: List[str] = []                           # plain-English reasons trading is off
