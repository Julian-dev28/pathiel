/**
 * Everything that is not "which chains/wallets are configured" (that part
 * lives in App.tsx's providers). This owns session/account/summary state,
 * the modal's open view, and is where window.PathielWallet.open(view)
 * actually lands — via lib/bridge's mailbox, subscribed on mount.
 */

import { useCallback, useEffect, useState } from 'react';
import { registerOpenListener, type WalletView } from '../lib/bridge';
import { getActiveSummary, listAccounts, getSession, refreshSession } from '../lib/api';
import type { Account, ActiveSummary, SessionInfo } from '../lib/contract';
import { AccountChip } from './AccountChip';
import { WalletModal } from './WalletModal';
import { ChooseView } from './ChooseView';
import { ConnectStepper } from './ConnectStepper';
import { ApiKeyView } from './ApiKeyView';
import { WatchView } from './WatchView';

function titleFor(view: WalletView): string {
  switch (view) {
    case 'choose': return 'Trading wallet';
    case 'connect': return 'Connect your Hyperliquid wallet';
    case 'apikey': return 'Use an API key';
    case 'watch': return 'Just watch an address';
  }
}

export function WalletRoot() {
  // Possibly stale reads: this is read-model state for the header, not the
  // source of truth. The server (and, after any mutation, refreshSession's
  // broadcast) is that; see lib/api.ts.
  const [, setSessionInfo] = useState<SessionInfo | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [summary, setSummary] = useState<ActiveSummary | null>(null);
  const [view, setView] = useState<WalletView | null>(null);

  const load = useCallback(async (): Promise<void> => {
    const [session, accs] = await Promise.all([getSession(), listAccounts()]);
    setSessionInfo(session);
    setAccounts(accs);
    setSummary(accs.some((a) => a.is_active) ? await getActiveSummary() : null);
  }, []);

  useEffect(() => {
    load().catch(() => {
      // No accounts yet, or the API is not reachable — either way the
      // header falls back to the "Connect" button below, which is the
      // right state when nothing is known.
    });
  }, [load]);

  useEffect(() => registerOpenListener(setView), []);

  const syncAfterChange = useCallback(async (): Promise<void> => {
    await refreshSession(); // dispatches pathiel:account-changed
    await load();
  }, [load]);

  const closeModal = useCallback(() => setView(null), []);
  const goToChoose = useCallback(() => setView('choose'), []);

  const activeAccount = accounts.find((a) => a.is_active) ?? null;

  return (
    <>
      {accounts.length === 0 ? (
        <button
          type="button"
          className="pw-connect-btn"
          onClick={() => setView('choose')}
          title="Connect a Hyperliquid wallet to trade"
        >
          Connect
        </button>
      ) : (
        <AccountChip
          accounts={accounts}
          activeAccount={activeAccount}
          equityUsd={summary?.equity_usd ?? null}
          onOpenChooser={() => setView('choose')}
          onChange={syncAfterChange}
        />
      )}

      {view && (
        <WalletModal
          titleId="pw-modal-title"
          title={titleFor(view)}
          onClose={closeModal}
          onBack={view === 'choose' ? undefined : goToChoose}
        >
          {view === 'choose' && <ChooseView onPick={(v) => setView(v)} />}
          {view === 'connect' && <ConnectStepper onAuthorized={syncAfterChange} onDone={closeModal} />}
          {view === 'apikey' && <ApiKeyView onImported={syncAfterChange} onDone={closeModal} />}
          {view === 'watch' && <WatchView onWatched={syncAfterChange} onDone={closeModal} />}
        </WalletModal>
      )}
    </>
  );
}
