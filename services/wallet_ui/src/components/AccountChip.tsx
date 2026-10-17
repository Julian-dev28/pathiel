/**
 * The masthead's connected-state control: who's active, a Trade/Watch badge,
 * equity, and a dropdown to switch, add, rename, remove or disconnect.
 *
 * "Disconnect" ends this browser's wagmi/RainbowKit wallet session only — it
 * does not remove the account record. Once a trading key is approved,
 * trading happens through that server-held agent key, not through a live
 * wallet connection, so disconnecting here just means "ask me to pick a
 * wallet again next time I need to sign something" (e.g. authorizing a
 * second account). Removing an account is a separate, explicit action under
 * Manage, with its own inline confirm.
 */

import { useEffect, useRef, useState } from 'react';
import { useAccount, useDisconnect } from 'wagmi';
import type { Account } from '../lib/contract';
import { activateAccount, patchAccount, removeAccount } from '../lib/api';
import { formatUsd, modeLabel, shortAddress } from '../lib/format';
import { IconChevronDown, IconPencil, IconTrash } from './Icons';

export interface AccountChipProps {
  accounts: Account[];
  activeAccount: Account | null;
  equityUsd: number | null;
  onOpenChooser: () => void;
  onChange: () => Promise<void>;
}

export function AccountChip({ accounts, activeAccount, equityUsd, onOpenChooser, onChange }: AccountChipProps) {
  const { isConnected } = useAccount();
  const { disconnect } = useDisconnect();

  const [open, setOpen] = useState(false);
  const [managing, setManaging] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e: MouseEvent): void {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close();
    }
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === 'Escape') {
        e.stopPropagation();
        close();
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const items = rootRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled)');
        if (!items || items.length === 0) return;
        const list = Array.from(items);
        const idx = list.indexOf(document.activeElement as HTMLElement);
        const next = e.key === 'ArrowDown' ? (idx + 1) % list.length : (idx - 1 + list.length) % list.length;
        e.preventDefault();
        list[next]?.focus();
      }
    }
    document.addEventListener('mousedown', onDocMouseDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  function close(): void {
    setOpen(false);
    setManaging(false);
    setEditingId(null);
    setRemovingId(null);
    triggerRef.current?.focus();
  }

  async function doSwitch(acc: Account): Promise<void> {
    if (acc.is_active || busy) return;
    setBusy(true);
    try {
      await activateAccount(acc.id);
      await onChange();
      // Switching is a complete, singular action — close and show the
      // result, rather than leaving the menu open the way "Manage" and a
      // rename deliberately do (those invite a second action right after).
      close();
    } finally {
      setBusy(false);
    }
  }

  async function commitRename(id: string): Promise<void> {
    const label = editValue.trim();
    setEditingId(null);
    const original = accounts.find((a) => a.id === id)?.label;
    if (!label || label === original) return;
    setBusy(true);
    try {
      await patchAccount(id, { label });
      await onChange();
    } finally {
      setBusy(false);
    }
  }

  async function doRemove(id: string): Promise<void> {
    setRemovingId(null);
    setBusy(true);
    try {
      await removeAccount(id);
      await onChange();
    } finally {
      setBusy(false);
    }
  }

  const label = activeAccount?.label ?? (accounts.length > 0 ? 'Select account' : 'Account');

  return (
    <div className="pw-chip-root" ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className="pw-chip"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="pw-chip-label">{label}</span>
        {activeAccount && (
          <>
            <span className="pw-chip-addr">{shortAddress(activeAccount.main_address)}</span>
            <span className={`pw-tag ${activeAccount.mode === 'trade' ? 'pw-tag-up' : 'pw-tag-mut'}`}>
              {modeLabel(activeAccount.mode)}
            </span>
            <span className="pw-chip-equity">{formatUsd(equityUsd)}</span>
          </>
        )}
        <IconChevronDown size={14} />
      </button>

      {open && (
        <div className="pw-menu" role="menu" aria-label="Accounts">
          {accounts.map((acc) => (
            <div className="pw-menu-row" key={acc.id}>
              {editingId === acc.id ? (
                <input
                  autoFocus
                  value={editValue}
                  maxLength={48}
                  className="pw-input pw-input-inline"
                  aria-label={`Rename ${acc.label}`}
                  onChange={(e) => setEditValue(e.target.value)}
                  onBlur={() => void commitRename(acc.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); void commitRename(acc.id); }
                    if (e.key === 'Escape') { e.preventDefault(); setEditingId(null); }
                  }}
                />
              ) : removingId === acc.id ? (
                <div className="pw-row-confirm">
                  <span>Remove {acc.label}?</span>
                  <button type="button" className="pw-btn-danger pw-btn-sm" onClick={() => void doRemove(acc.id)}>
                    Yes, remove
                  </button>
                  <button type="button" className="pw-btn pw-btn-sm" onClick={() => setRemovingId(null)}>
                    Cancel
                  </button>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    role="menuitem"
                    className={`pw-menu-item ${acc.is_active ? 'pw-menu-item-active' : ''}`}
                    disabled={acc.is_active || busy}
                    onClick={() => void doSwitch(acc)}
                  >
                    <span className="pw-menu-item-label">{acc.label}</span>
                    <span className="pw-menu-item-addr">{shortAddress(acc.main_address)}</span>
                    <span className={`pw-tag ${acc.mode === 'trade' ? 'pw-tag-up' : 'pw-tag-mut'}`}>
                      {modeLabel(acc.mode)}
                    </span>
                  </button>
                  {managing && (
                    <span className="pw-row-actions">
                      <button type="button" className="pw-icon-btn" aria-label={`Rename ${acc.label}`}
                        onClick={() => { setEditingId(acc.id); setEditValue(acc.label); }}>
                        <IconPencil size={14} />
                      </button>
                      <button type="button" className="pw-icon-btn" aria-label={`Remove ${acc.label}`}
                        onClick={() => setRemovingId(acc.id)}>
                        <IconTrash size={14} />
                      </button>
                    </span>
                  )}
                </>
              )}
            </div>
          ))}

          <div className="pw-menu-sep" role="separator" />

          <button type="button" role="menuitem" className="pw-menu-item pw-menu-item-plain"
            onClick={() => { close(); onOpenChooser(); }}>
            + Add account
          </button>
          <button type="button" role="menuitemcheckbox" aria-checked={managing}
            className="pw-menu-item pw-menu-item-plain" onClick={() => setManaging((m) => !m)}>
            {managing ? 'Done managing' : 'Manage accounts'}
          </button>
          {isConnected && (
            <button type="button" role="menuitem" className="pw-menu-item pw-menu-item-plain"
              onClick={() => { disconnect(); close(); }}>
              Disconnect
              <span className="pw-menu-item-sub">Ends this browser's wallet connection</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
