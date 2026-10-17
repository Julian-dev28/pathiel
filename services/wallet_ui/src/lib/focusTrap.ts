/**
 * A small, dependency-free focus trap for the wallet modal.
 *
 * Three jobs, each one a thing a screen-reader or keyboard-only user
 * actually depends on: move focus into the dialog when it opens, keep Tab /
 * Shift+Tab cycling inside it rather than escaping to the page behind, and
 * give focus back to whatever triggered the dialog when it closes. Escape
 * closes the dialog via the caller's `onEscape`.
 *
 * Not a generic a11y library import: the whole behaviour is ~40 lines, this
 * project has no other modal yet, and a library here would bring its own
 * API to learn for less control than writing the four event cases out.
 */

import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

export function useFocusTrap(
  active: boolean,
  containerRef: RefObject<HTMLElement | null>,
  onEscape: () => void,
): void {
  const onEscapeRef = useRef(onEscape);
  onEscapeRef.current = onEscape;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const toFocus = focusableIn(container)[0] ?? container;
    // One tick so the dialog's own content (which may still be mounting its
    // children) is in the DOM before we query it a second time inside.
    const raf = window.requestAnimationFrame(() => toFocus.focus());

    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onEscapeRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = focusableIn(container);
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;

      if (e.shiftKey && current === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && current === last) {
        e.preventDefault();
        first.focus();
      } else if (!container.contains(current)) {
        // Focus drifted outside the dialog (e.g. a programmatic blur) —
        // pull it back in rather than letting Tab escape to the page.
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.cancelAnimationFrame(raf);
      document.removeEventListener('keydown', onKeyDown, true);
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [active, containerRef]);
}
