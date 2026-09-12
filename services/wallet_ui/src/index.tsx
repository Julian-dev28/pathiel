/**
 * Mount point, and the bridge back to the vanilla page.
 *
 * This bundle is ~380 KB over the wire and the dashboard's own JS is ~30 KB, so
 * it is NOT loaded on page load. `pathia/static/pathia.js` injects it the first
 * time someone actually needs a wallet — a click on the masthead chip, or the
 * sign-in gate appearing after a 401 — and the common case for a signed-in
 * operator never fetches it at all.
 *
 * Because the load is user-initiated, the modal has to open by itself once
 * React is up; nobody clicks twice. The loader sets `__pathiaWalletAutoOpen`
 * before injecting and this file honours it after mount.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@rainbow-me/rainbowkit/styles.css';
import { App } from './App';

declare global {
  interface Window {
    PathiaWallet?: { openConnectModal: () => void };
    __pathiaWalletAutoOpen?: boolean;
  }
}

const MOUNT_ID = 'wallet-connect-root';

/**
 * RainbowKit owns its modal state internally and hands out no imperative handle
 * from outside the React tree, so "open the modal" means clicking the button the
 * island rendered.
 *
 * Clicking once is not enough, and this is the bug that shipped to production
 * before it was caught: `createRoot().render()` returns before React commits, so
 * the button can be in the DOM a frame or two before its onClick is attached.
 * A click in that window lands on a button with no handler, silently does
 * nothing, and the user has to click a second time to get the modal they
 * already asked for.
 *
 * So this retries until the modal actually exists rather than until the button
 * does — the observable end state, not a proxy for it. Bounded, because a spin
 * that never succeeds has to stop.
 */
const OPEN_RETRY_MS = 600;
const OPEN_MAX_ATTEMPTS = 8;

/**
 * Clicking the island's button until the modal is actually open.
 *
 * Two bugs live in this seven-line function, both found in production, and both
 * are about timing rather than logic.
 *
 * The first: `createRoot().render()` returns before React commits, so the
 * button can be in the DOM a frame or two before its onClick is attached. A
 * click in that window silently does nothing and the user has to click again.
 * So this retries rather than clicking once.
 *
 * The second, caused by fixing the first: RainbowKit animates its modal in, so
 * `[role="dialog"]` is not queryable on the very next frame. A
 * requestAnimationFrame retry therefore did not see the dialog it had just
 * opened, clicked again, and toggled it shut — about 480 times over 8 seconds,
 * landing closed. Retries are spaced well past the animation, and there are
 * eight of them rather than a frame count.
 */
function openModal(node: HTMLElement, attemptsLeft = OPEN_MAX_ATTEMPTS): void {
  if (document.querySelector('[role="dialog"]')) return;
  const button = node.querySelector('button');
  if (button) button.click();
  if (attemptsLeft > 1) {
    window.setTimeout(() => openModal(node, attemptsLeft - 1), OPEN_RETRY_MS);
  }
}

function mount(): void {
  const node = document.getElementById(MOUNT_ID);
  // Every dashboard page carries the masthead, but a page without one is not an
  // error worth a console trace — the island simply has nothing to do there.
  if (!node) return;

  createRoot(node).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  window.PathiaWallet = {
    openConnectModal: () => openModal(node),
  };

  if (window.__pathiaWalletAutoOpen) {
    window.__pathiaWalletAutoOpen = false;
    openModal(node);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mount, { once: true });
} else {
  mount();
}
