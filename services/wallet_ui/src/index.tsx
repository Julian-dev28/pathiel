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
 * island rendered. Crude, and the only approach that keeps working when
 * RainbowKit reorganises its internals.
 *
 * The button does not exist the instant `createRoot().render()` returns — React
 * commits asynchronously — so this polls briefly rather than assuming it is
 * there. Bounded, because a spin that never finds its target must end.
 */
function clickWhenPresent(node: HTMLElement, attemptsLeft = 40): void {
  const button = node.querySelector('button');
  if (button) {
    button.click();
    return;
  }
  if (attemptsLeft > 0) {
    requestAnimationFrame(() => clickWhenPresent(node, attemptsLeft - 1));
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
    openConnectModal: () => clickWhenPresent(node),
  };

  if (window.__pathiaWalletAutoOpen) {
    window.__pathiaWalletAutoOpen = false;
    clickWhenPresent(node);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mount, { once: true });
} else {
  mount();
}
