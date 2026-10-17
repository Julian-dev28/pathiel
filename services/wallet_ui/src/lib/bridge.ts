/**
 * The imperative side of window.PathielWallet.open(view) (SWARM_RULES
 * section 10). index.tsx assigns `window.PathielWallet` synchronously, in
 * the same tick that calls `createRoot().render()` — but React has not
 * committed yet, so the component that owns modal state has not registered
 * itself. A caller that invokes `.open()` in that window (realistically:
 * `onload` firing right after the bundle finishes evaluating, which is
 * exactly when the loader in pathiel.js calls it) must not be dropped.
 *
 * So this is a one-slot mailbox rather than a direct call: `requestOpen`
 * delivers to the listener if one is registered, otherwise remembers the
 * most recent request; `registerOpenListener` (called from the root
 * component's mount effect) delivers anything pending the moment it
 * subscribes. No click-and-retry polling, no role=dialog queries — the
 * previous version of this file needed both because it was driving
 * RainbowKit's own modal from outside React. This one calls into React
 * directly, so the only race is "was anyone listening yet", which a mailbox
 * resolves exactly once, in order.
 */

export type WalletView = 'choose' | 'connect' | 'apikey' | 'watch';

type Listener = (view: WalletView) => void;

let listener: Listener | null = null;
let pending: WalletView | null = null;

export function requestOpen(view: WalletView): void {
  if (listener) {
    listener(view);
  } else {
    pending = view;
  }
}

/** Returns an unsubscribe function. Delivers a pending request immediately
 * if `requestOpen` was called before anything subscribed. */
export function registerOpenListener(fn: Listener): () => void {
  listener = fn;
  if (pending) {
    const view = pending;
    pending = null;
    fn(view);
  }
  return () => {
    if (listener === fn) listener = null;
  };
}
