/**
 * Fetch layer for contracts/accounts/models.py's routes. One function per
 * route, typed with contract.ts. Every call is same-origin (`/api/...`), so
 * this works unmodified whether it is mounted in the real pathiel dashboard
 * or in the test harness's fake document (services/wallet_ui/tests).
 */

import type {
  Account,
  AccountPatch,
  ActiveSummary,
  AgentConfirmIn,
  AgentPrepareIn,
  AgentPrepareOut,
  ApiKeyImportIn,
  SessionInfo,
  WatchIn,
} from './contract';

export type ApiErrorKind = 'network' | 'http';

/** Thrown by every function below. `detail` is the FastAPI `{"detail": ...}`
 * message when the server sent one, otherwise a plain description of what
 * went wrong — callers show `.detail` directly, they never need to inspect
 * `.kind` except to decide whether a retry is worth offering automatically. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly detail: string;

  constructor(detail: string, kind: ApiErrorKind, status?: number) {
    super(detail);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

async function readDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === 'string') return body.detail;
    if (body && Array.isArray(body.detail)) {
      // FastAPI's 422 shape: a list of {loc, msg, type}.
      const first = body.detail[0];
      if (first && typeof first.msg === 'string') return first.msg;
    }
  } catch {
    /* not JSON, or no body — fall through to the generic message below. */
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
  } catch {
    // fetch() itself rejected: DNS/connection failure, offline, CORS — there
    // is no HTTP status to read. Indistinguishable from the user's network
    // being down, which is exactly how it should read to them.
    throw new ApiError('Could not reach pathiel. Check your connection and try again.', 'network');
  }

  if (!res.ok) {
    const detail = await readDetail(res, `Request failed (${res.status}).`);
    throw new ApiError(detail, 'http', res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getSession(): Promise<SessionInfo> {
  return request<SessionInfo>('/api/session');
}

export function listAccounts(): Promise<Account[]> {
  return request<Account[]>('/api/accounts');
}

export function prepareAgent(body: AgentPrepareIn): Promise<AgentPrepareOut> {
  return request<AgentPrepareOut>('/api/accounts/agent/prepare', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function confirmAgent(body: AgentConfirmIn): Promise<Account> {
  return request<Account>('/api/accounts/agent/confirm', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function importApiKey(body: ApiKeyImportIn): Promise<Account> {
  return request<Account>('/api/accounts/import', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function watchAddress(body: WatchIn): Promise<Account> {
  return request<Account>('/api/accounts/watch', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function activateAccount(id: string): Promise<Account> {
  return request<Account>(`/api/accounts/${encodeURIComponent(id)}/activate`, { method: 'POST' });
}

export function patchAccount(id: string, body: AccountPatch): Promise<Account> {
  return request<Account>(`/api/accounts/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function removeAccount(id: string): Promise<{ ok: true }> {
  return request<{ ok: true }>(`/api/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function getActiveSummary(): Promise<ActiveSummary> {
  return request<ActiveSummary>('/api/accounts/active/summary');
}

/** `document` receives this after any connect, switch or removal (SWARM_RULES
 * section 10). `detail` is the fresh SessionInfo so a listener never has to
 * make its own follow-up request just to learn what changed. */
export const ACCOUNT_CHANGED_EVENT = 'pathiel:account-changed';

export async function refreshSession(): Promise<SessionInfo> {
  const session = await getSession();
  document.dispatchEvent(new CustomEvent(ACCOUNT_CHANGED_EVENT, { detail: session }));
  return session;
}
