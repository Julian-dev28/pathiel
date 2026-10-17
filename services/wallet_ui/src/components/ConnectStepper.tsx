/**
 * "Connect your Hyperliquid wallet": two steps, one decision each.
 *
 * Step 1 connects a browser wallet via RainbowKit's own picker (wagmi's
 * EIP-6963 discovery — see services/wallet_ui/README.md for why that
 * replaced a direct window.ethereum call). Step 2 signs Hyperliquid's
 * ApproveAgent payload, exactly as contracts/accounts/models.py's
 * AgentPrepareOut hands it over: this component never reads apart or
 * reconstructs `typed_data` — it spreads the four fields straight into
 * wagmi's signTypedData. That is the property the browser tests in
 * tests/test_wallet_account_flow_browser.py verify server-side, by recovering
 * the signer from the ORIGINAL stored payload and checking it matches.
 *
 * There is no third, "approve builder" step. Per the task: we skip the
 * builder-fee step entirely, so there is nothing there to approve.
 */

import { useEffect, useRef, useState } from 'react';
import { useAccount, useDisconnect, useSignTypedData, useSwitchChain } from 'wagmi';
import { useConnectModal } from '@rainbow-me/rainbowkit';
import type { Account, AgentPrepareOut } from '../lib/contract';
import { activateAccount, confirmAgent, prepareAgent } from '../lib/api';
import { errorMessage, isChainMismatch, isExpired, isUserRejection } from '../lib/errors';
import { shortAddress } from '../lib/format';
import { IconCheck, IconSpinner } from './Icons';

type Phase = 'idle' | 'preparing' | 'need-chain-switch' | 'switching' | 'signing' | 'confirming';

export interface ConnectStepperProps {
  /** Called once the agent key is approved (and, where possible, activated).
   * May itself fail (a refresh call can still error) without undoing the
   * approval, which is why this component treats it as best-effort — see
   * finishWithAccount below. */
  onAuthorized: (account: Account) => Promise<void> | void;
  /** The success panel's "Done" button. */
  onDone: () => void;
}

export function ConnectStepper({ onAuthorized, onDone }: ConnectStepperProps) {
  const { address, isConnected, chainId } = useAccount();
  const { disconnect } = useDisconnect();
  const { openConnectModal } = useConnectModal();
  const { signTypedDataAsync } = useSignTypedData();
  const { switchChainAsync } = useSwitchChain();

  const [wantsReconnect, setWantsReconnect] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [prepared, setPrepared] = useState<AgentPrepareOut | null>(null);
  const [pendingChainId, setPendingChainId] = useState<number | null>(null);
  const [error, setError] = useState<{ message: string; retry: () => void } | null>(null);
  const [doneAccount, setDoneAccount] = useState<Account | null>(null);
  const [doneNote, setDoneNote] = useState<string | null>(null);
  const retriedExpiryRef = useRef(false);

  // "Change wallet": disconnect, then reopen RainbowKit's picker the instant
  // it is actually connectable again (openConnectModal is undefined while
  // still connected — see @rainbow-me/rainbowkit's useConnectModal).
  useEffect(() => {
    if (wantsReconnect && !isConnected && openConnectModal) {
      setWantsReconnect(false);
      openConnectModal();
    }
  }, [wantsReconnect, isConnected, openConnectModal]);

  async function signAndConfirm(current: AgentPrepareOut): Promise<void> {
    setPhase('signing');
    const signature = await signTypedDataAsync({
      domain: current.typed_data.domain,
      types: current.typed_data.types,
      primaryType: current.typed_data.primaryType,
      message: current.typed_data.message,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    setPhase('confirming');
    let account: Account;
    try {
      account = await confirmAgent({ request_id: current.request_id, signature });
    } catch (err) {
      if (isExpired(err) && !retriedExpiryRef.current) {
        retriedExpiryRef.current = true;
        await attemptApprove();
        return;
      }
      throw err;
    }
    retriedExpiryRef.current = false;
    await finishWithAccount(account);
  }

  async function finishWithAccount(account: Account): Promise<void> {
    try {
      const finalAccount = account.is_active ? account : await activateAccount(account.id);
      await onAuthorized(finalAccount);
      setPhase('idle');
      setDoneAccount(finalAccount);
    } catch (err) {
      // The agent key is already approved server-side. Losing the follow-up
      // activate/refresh call is a lesser problem than the signing flow
      // itself, so land on success regardless rather than routing a signed
      // approval back through the retry UI.
      setPhase('idle');
      setDoneAccount(account);
      setDoneNote(errorMessage(err));
    }
  }

  async function attemptApprove(): Promise<void> {
    setPhase('preparing');
    // Local, not the `prepared` state: setPrepared() below does not update
    // this function's view of that state until the next render, and the
    // catch block needs the record from THIS attempt, not last render's.
    let current: AgentPrepareOut | null = null;
    try {
      current = await prepareAgent({ main_address: address as string, label: 'Main' });
      setPrepared(current);

      const targetChainId = Number(current.typed_data.domain.chainId);
      if (Number.isFinite(targetChainId) && chainId !== targetChainId) {
        setPendingChainId(targetChainId);
        setPhase('need-chain-switch');
        return;
      }
      await signAndConfirm(current);
    } catch (err) {
      setPhase('idle');
      if (isChainMismatch(err) && current) {
        setPendingChainId(Number(current.typed_data.domain.chainId));
        setPhase('need-chain-switch');
        return;
      }
      setError({
        message: isUserRejection(err) ? 'You closed the signature request.' : errorMessage(err),
        retry: handleApprove,
      });
    }
  }

  function handleApprove(): void {
    retriedExpiryRef.current = false;
    setError(null);
    void attemptApprove();
  }

  async function handleSwitchChain(): Promise<void> {
    if (pendingChainId == null || !prepared) return;
    setPhase('switching');
    setError(null);
    try {
      await switchChainAsync({ chainId: pendingChainId });
      setPendingChainId(null);
      await signAndConfirm(prepared);
    } catch (err) {
      setPhase('idle');
      setError({ message: errorMessage(err), retry: handleApprove });
    }
  }

  function handleChangeWallet(): void {
    setWantsReconnect(true);
    setPrepared(null);
    setError(null);
    disconnect();
  }

  if (doneAccount) {
    return (
      <div className="pw-step pw-step-done">
        <div className="pw-done-badge"><IconCheck size={28} /></div>
        <p className="pw-step-heading">Trading key authorized</p>
        <p className="pw-step-body">
          {shortAddress(doneAccount.main_address)} can now trade on pathiel. This key can
          never withdraw your funds.
        </p>
        {doneNote && <p className="pw-inline-warn">{doneNote}</p>}
        <button type="button" className="pw-btn pw-btn-primary" onClick={onDone}>Done</button>
      </div>
    );
  }

  const busy = phase === 'preparing' || phase === 'signing' || phase === 'confirming' || phase === 'switching';

  return (
    <div className="pw-stepper">
      <section className="pw-step" aria-current={!isConnected}>
        <p className="pw-step-label">Step 1</p>
        <p className="pw-step-heading">Connect Wallet</p>
        {isConnected && address ? (
          <div className="pw-step-row">
            <span className="pw-connected-addr">{shortAddress(address)}</span>
            <span className="pw-tag pw-tag-up">Connected</span>
            <button type="button" className="pw-link-btn" onClick={handleChangeWallet} disabled={busy}>
              Change wallet
            </button>
          </div>
        ) : (
          <>
            <p className="pw-step-body">Pick the wallet that holds your trading funds.</p>
            <button
              type="button"
              className="pw-btn pw-btn-primary"
              onClick={() => openConnectModal?.()}
              disabled={!openConnectModal}
            >
              Connect wallet
            </button>
          </>
        )}
      </section>

      <section className="pw-step" aria-current={isConnected} aria-disabled={!isConnected}>
        <p className="pw-step-label">Step 2</p>
        <p className="pw-step-heading">Authorize a trading key</p>
        <p className="pw-step-body">Generate a Hyperliquid API key for trading.</p>
        <p className="pw-step-fine">This key can place trades. It can never withdraw your funds.</p>

        {phase === 'need-chain-switch' && (
          <div className="pw-banner pw-banner-amber">
            <p>Your wallet is on the wrong network for this signature.</p>
            <button type="button" className="pw-btn" onClick={handleSwitchChain}>Switch network</button>
          </div>
        )}

        {error && (
          <div className="pw-banner pw-banner-down">
            <p>{error.message}</p>
            <button type="button" className="pw-btn" onClick={error.retry}>Try again</button>
          </div>
        )}

        {!error && phase !== 'need-chain-switch' && (
          <button
            type="button"
            className="pw-btn pw-btn-primary"
            onClick={handleApprove}
            disabled={!isConnected || busy}
          >
            {busy && <IconSpinner size={14} />}
            {phase === 'preparing' && 'Preparing…'}
            {phase === 'signing' && 'Waiting for your wallet…'}
            {phase === 'confirming' && 'Confirming…'}
            {phase === 'idle' && 'Approve'}
          </button>
        )}
      </section>
    </div>
  );
}
