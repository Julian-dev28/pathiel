/**
 * Hand-mirrored TypeScript types for contracts/accounts/models.py.
 *
 * There is no codegen step from the Pydantic models to this file — the
 * project has no Python-to-TS pipeline and building one for a dozen fields
 * would be more machinery than the problem deserves. Instead this file keeps
 * CONTRACT_VERSION in lockstep with the Python constant, and
 * tests/test_contract_sync.py reads both source files as text and fails the
 * instant they drift, which is the deterministic guard that makes hand-
 * mirroring safe: a human (or model) edits one side, the gate test catches a
 * forgotten edit to the other before it reaches a commit.
 *
 * Keep every field name, optionality and literal union here identical to
 * contracts/accounts/models.py. If you add a field there, add it here in the
 * same change and re-read that file's docstring for the route it belongs to.
 */

// Bump together with contracts/accounts/models.py's CONTRACT_VERSION.
export const CONTRACT_VERSION = '1.0.0';

// 0x-prefixed, 40 hex chars. The server stores it lowercase; this file does
// not assume that — see lib/validate.ts for the pattern used to check one.
export type Address = string;

export type AccountMode = 'trade' | 'watch';
export type AccountSource = 'wallet_sign' | 'api_key' | 'watch' | 'env';

export interface Account {
  id: string;
  label: string;
  main_address: Address;
  agent_address: Address | null;
  mode: AccountMode;
  source: AccountSource;
  is_active: boolean;
  created_at: string;
  verified_at: string | null;
  agent_name: string | null;
}

/** The EIP-712 payload, exactly as HyperliquidTransaction:ApproveAgent needs
 * it: {domain, types, primaryType, message}. Typed as unknown-ish on purpose
 * — this bundle's entire safety property is that it forwards this object to
 * the wallet without reading, reordering or reconstructing any of it. See
 * lib/api.ts signTypedDataArgs(). */
export interface TypedData {
  domain: Record<string, unknown>;
  types: Record<string, Array<{ name: string; type: string }>>;
  primaryType: string;
  message: Record<string, unknown>;
}

export interface AgentPrepareIn {
  main_address: Address;
  label?: string;
  /** The chain the wallet is on now. Hyperliquid accepts a signature from any
   *  chain, so the server builds the approval for this one: no network switch. */
  signature_chain_id?: number;
}

export interface AgentPrepareOut {
  request_id: string;
  agent_address: Address;
  typed_data: TypedData;
  expires_at: string;
}

export interface AgentConfirmIn {
  request_id: string;
  signature: string;
}

export interface ApiKeyImportIn {
  main_address: Address;
  agent_private_key: string;
  label?: string;
}

export interface WatchIn {
  address: Address;
  label?: string;
}

export interface AccountPatch {
  label?: string;
}

export interface SessionInfo {
  local_mode: boolean;
  address: Address | null;
  active_account_id: string | null;
  accounts_count: number;
  can_trade: boolean;
  needs_onboarding: boolean;
}

export interface ActiveSummary {
  account: Account | null;
  equity_usd: number | null;
  withdrawable_usd: number | null;
  open_positions: number;
  can_trade: boolean;
  blockers: string[];
}
