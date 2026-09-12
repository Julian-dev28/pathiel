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
const OPEN_DEADLINE_MS = 8_000;

function openModal(node: HTMLElement, deadline = Date.now() + OPEN_DEADLINE_MS): void {
  if (document.querySelector('[role="dialog"]')) return;
  const button = node.querySelector('button');
  if (button) button.click();
  if (Date.now() < deadline) {
    // requestAnimationFrame rather than a timer: this waits on React to paint,
    // which is what rAF is scheduled against. A deadline rather than a frame
    // count, because frame rate varies and what is being bounded is the user's
    // patience, not the renderer's.
    requestAnimationFrame(() => openModal(node, deadline));
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
