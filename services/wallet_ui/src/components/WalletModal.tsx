/**
 * The "Trading wallet" modal shell: overlay, dialog frame, title, optional
 * Back, Close, and a focus trap. Every view (choose / connect / apikey /
 * watch) renders as this shell's children — the shell owns none of their
 * state, only the things every view needs identically: how to get out.
 */

import { useRef } from 'react';
import { useFocusTrap } from '../lib/focusTrap';
import { IconClose } from './Icons';

export interface WalletModalProps {
  titleId: string;
  title: string;
  onClose: () => void;
  onBack?: () => void;
  children: React.ReactNode;
}

export function WalletModal({ titleId, title, onClose, onBack, children }: WalletModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(true, dialogRef, onClose);

  return (
    <div className="pw-overlay" onMouseDown={(e) => {
      // Click-outside-to-close: only when the mousedown started on the
      // overlay itself, not when a drag/selection that started inside the
      // dialog happens to end up over the overlay's padding.
      if (e.target === e.currentTarget) onClose();
    }}>
      <div
        ref={dialogRef}
        className="pw-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="pw-dialog-head">
          {onBack ? (
            <button type="button" className="pw-back" onClick={onBack} aria-label="Back">
              ← Back
            </button>
          ) : (
            <span />
          )}
          <h2 id={titleId} className="pw-dialog-title">{title}</h2>
          <button type="button" className="pw-icon-btn pw-close" onClick={onClose} aria-label="Close">
            <IconClose />
          </button>
        </div>
        <div className="pw-dialog-body">{children}</div>
      </div>
    </div>
  );
}
