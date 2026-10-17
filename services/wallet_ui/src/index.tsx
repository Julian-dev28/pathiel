/**
 * Mount point, and the bridge back to the vanilla page.
 *
 * This bundle is ~380 KB over the wire and the dashboard's own JS is ~30 KB,
 * so it is NOT loaded on page load. `pathiel/static/pathiel.js` injects it
 * the first time someone actually needs it, then calls
 * `window.PathielWallet.open(view)` (SWARM_RULES section 10) with one of
 * "choose" | "connect" | "apikey" | "watch".
 *
 * `window.PathielWallet` is assigned synchronously below, in the same tick
 * as `createRoot().render()` — but React has not committed yet, so
 * WalletRoot has not subscribed to lib/bridge's mailbox. A caller invoking
 * `.open()` in that window (the realistic case: the loader's `onload` fires
 * the instant this script finishes evaluating, then calls `.open()`
 * immediately) is not dropped: `requestOpen` remembers the most recent
 * request until something is listening, and WalletRoot's mount effect
 * delivers it the moment it subscribes. See lib/bridge.ts for the previous
 * version's click-and-retry approach and why this replaced it — that one
 * existed only because it had to drive RainbowKit's OWN modal via a
 * simulated click from outside React; this one calls into React directly.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@rainbow-me/rainbowkit/styles.css';
import './theme.css';
import { App } from './App';
import { requestOpen, type WalletView } from './lib/bridge';

declare global {
  interface Window {
    PathielWallet?: {
      open: (view: WalletView) => void;
      /** @deprecated kept as a harmless alias of open('choose') while
       * pathiel.js still calls it; remove once onboarding's rewrite ships. */
      openConnectModal?: () => void;
    };
  }
}

const MOUNT_ID = 'wallet-connect-root';

function mount(): void {
  const node = document.getElementById(MOUNT_ID);
  // Every dashboard page carries the masthead, but a page without one is not
  // an error worth a console trace — the island simply has nothing to do.
  if (!node) return;

  createRoot(node).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  window.PathielWallet = {
    open: requestOpen,
    openConnectModal: () => requestOpen('choose'),
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mount, { once: true });
} else {
  mount();
}
