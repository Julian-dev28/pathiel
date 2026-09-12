/**
 * RainbowKit's authentication adapter, wired to pathia's own SIWE endpoints.
 *
 * The server (services/auth/api.py) composes the EIP-4361 message itself and
 * verifies domain, nonce and expiry out of the message it is handed back. That
 * is deliberate and it is the property this adapter must not break: whatever
 * the client signs, the server only accepts a message whose domain matches its
 * own and whose nonce it minted and has not yet burned.
 *
 * Which creates one wrinkle worth spelling out, because it looks wrong at a
 * glance. RainbowKit's flow is `getNonce()` then `createMessage({ nonce,
 * address, chainId })`, and `getNonce` is not told the address. pathia's
 * `/auth/nonce` requires one — the message it returns is bound to the account
 * that will sign it. So:
 *
 *   - `getNonce` returns a placeholder and talks to nobody. Minting a real
 *     nonce here would mint one per sign-in attempt and immediately throw it
 *     away, leaving the store to purge them on a timer.
 *   - `createMessage` does the real fetch, against the address RainbowKit has
 *     by then resolved, and returns the server's message verbatim.
 *
 * RainbowKit's nonce is therefore unused, and nothing is weakened by that: the
 * nonce that matters is the one inside the signed message, which the server
 * minted and checks against its own store. See the `nonce` note in
 * services/auth/siwe.py.
 */

import { createAuthenticationAdapter } from '@rainbow-me/rainbowkit';

export type User = {
  address: string;
  display_name?: string | null;
  role?: string;
};

/** Read a FastAPI error body without assuming it is JSON. */
async function detail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    return (body && body.detail) || fallback;
  } catch {
    return fallback;
  }
}

export async function fetchMe(): Promise<User | null> {
  try {
    const res = await fetch('/auth/me');
    if (!res.ok) return null;
    return (await res.json()).user ?? null;
  } catch {
    return null;
  }
}

/**
 * Built with a callback rather than reading a module-level setter, so the React
 * tree owns the "who is signed in" state and this file stays a pure transport.
 */
export function buildAdapter(onAuthenticated: (user: User) => void) {
  return createAuthenticationAdapter({
    // See the note above: deliberately offline.
    getNonce: async () => 'server-issued',

    createMessage: async ({ address }) => {
      const res = await fetch(`/auth/nonce?address=${encodeURIComponent(address)}`);
      if (!res.ok) throw new Error(await detail(res, 'could not start sign-in'));
      const prepared = await res.json();
      if (!prepared.message) throw new Error('server returned no message to sign');
      return prepared.message as string;
    },

    verify: async ({ message, signature }) => {
      const res = await fetch('/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, signature }),
      });
      if (!res.ok) return false;
      const body = await res.json();
      if (body.user) onAuthenticated(body.user);
      return true;
    },

    signOut: async () => {
      try {
        await fetch('/auth/logout', { method: 'POST' });
      } catch {
        /* the cookie is the session; a failed call here still ends the UI's. */
      }
    },
  });
}
