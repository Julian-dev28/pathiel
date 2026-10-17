/**
 * Error classification for the connect stepper.
 *
 * Signing and chain-switch failures come from the wallet/viem, not from our
 * own fetch layer, so they are not ApiError instances — they can be a viem
 * BaseError, a raw EIP-1193 provider error ({code, message}), or (rarely) a
 * plain Error from a connector that does its own thing. Rather than depend
 * on exact viem error classes — which differ between a real wallet's own
 * phrasing and a minimal EIP-1193 mock like the one the browser tests
 * inject — this reads the handful of signals every one of those shapes
 * actually sets: a numeric EIP-1193 `code`, and a message string.
 */

import { ApiError } from './api';

interface MaybeProviderError {
  code?: unknown;
  message?: unknown;
  shortMessage?: unknown;
  name?: unknown;
  cause?: unknown;
}

function asRecord(err: unknown): MaybeProviderError {
  return typeof err === 'object' && err !== null ? (err as MaybeProviderError) : {};
}

function messageOf(err: unknown): string {
  const e = asRecord(err);
  const parts = [e.shortMessage, e.message, e.name]
    .filter((v): v is string => typeof v === 'string');
  return parts.join(' ').toLowerCase();
}

/** EIP-1193 code 4001, or any of the phrasings wallets use for "user closed
 * the signature request" — MetaMask, Rabby, OKX and viem's own wrapper all
 * say it slightly differently. */
export function isUserRejection(err: unknown): boolean {
  const e = asRecord(err);
  if (e.code === 4001) return true;
  if (typeof e.cause === 'object' && e.cause !== null && (e.cause as MaybeProviderError).code === 4001) {
    return true;
  }
  const msg = messageOf(err);
  return msg.includes('user rejected') || msg.includes('user denied') || msg.includes('userrejectedrequest');
}

/** Wagmi/viem throw a distinct, recognizable error when the connector can't
 * switch chains at all (vs. the user rejecting the switch prompt, which is
 * also a 4001 and handled by isUserRejection). */
export function isChainMismatch(err: unknown): boolean {
  const msg = messageOf(err);
  return msg.includes('chain mismatch') || msg.includes('does not match the target chain');
}

/** Only ApiError carries a server detail string, and only the confirm route
 * can tell us a request expired — the server is the single source of truth
 * for "has this request_id's ttl passed", not the client clock. */
export function isExpired(err: unknown): boolean {
  return err instanceof ApiError && err.detail.toLowerCase().includes('expir');
}

export function isNetworkError(err: unknown): boolean {
  return err instanceof ApiError && err.kind === 'network';
}

/** What to put on screen. ApiError.detail is already written for a human
 * (see services/accounts' route handlers); everything else gets a generic,
 * calm fallback rather than a raw wallet error string. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (isUserRejection(err)) return 'You closed the signature request.';
  const e = asRecord(err);
  if (typeof e.shortMessage === 'string') return e.shortMessage;
  if (typeof e.message === 'string') return e.message;
  return 'Something went wrong. Try again.';
}
