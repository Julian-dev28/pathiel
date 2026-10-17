/**
 * Client-side validation, mirroring contracts/accounts/models.py exactly.
 *
 * These patterns are copied character-for-character from the Python side
 * (_ADDR, ApiKeyImportIn.agent_private_key, AgentConfirmIn.signature) so a
 * bad paste is rejected before a network round trip, not after. They are
 * NOT the source of truth — the server validates again with Pydantic and
 * would reject anything that slipped past a bug here — but matching them
 * exactly means the user sees one error message, not two different ones for
 * the same bad input depending on which side caught it.
 *
 * tests/test_contract_sync.py checks these literal strings against
 * contracts/accounts/models.py so the two cannot drift silently.
 */

// contracts/accounts/models.py: _ADDR = r"^0x[0-9a-fA-F]{40}$"
export const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

// contracts/accounts/models.py: ApiKeyImportIn.agent_private_key pattern
export const PRIVATE_KEY_RE = /^(0x)?[0-9a-fA-F]{64}$/;

export function isValidAddress(value: string): boolean {
  return ADDRESS_RE.test(value.trim());
}

export function isValidPrivateKey(value: string): boolean {
  return PRIVATE_KEY_RE.test(value.trim());
}

export const ADDRESS_HELP = 'Enter a valid wallet address (0x followed by 40 hex characters).';
export const PRIVATE_KEY_HELP = 'Enter a valid private key (64 hex characters, with or without 0x).';
