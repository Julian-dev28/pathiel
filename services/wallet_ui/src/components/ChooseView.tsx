/**
 * The modal's home view: three ways in, per contracts/accounts/models.py's
 * docstring (wallet_sign / api_key / watch). One decision, three cards, no
 * nesting — Julien's ask was one clear next step at a time, so this does not
 * reproduce Hyperdash's "pick a card, then pick a tab" structure; each card
 * goes straight to its own flow.
 */

import type { WalletView } from '../lib/bridge';

export interface ChooseViewProps {
  onPick: (view: Exclude<WalletView, 'choose'>) => void;
}

export function ChooseView({ onPick }: ChooseViewProps) {
  return (
    <div className="pw-choose">
      <button type="button" className="pw-card pw-card-recommended" onClick={() => onPick('connect')}>
        <span className="pw-card-badge">Recommended</span>
        <span className="pw-card-title">Connect your Hyperliquid wallet</span>
        <span className="pw-card-body">
          Sign once to authorize a trading key. Your funds stay in your wallet — this
          key can trade but can never withdraw.
        </span>
      </button>

      <button type="button" className="pw-card" onClick={() => onPick('apikey')}>
        <span className="pw-card-title">Use an API key</span>
        <span className="pw-card-body">Paste an API wallet key you already created on Hyperliquid.</span>
      </button>

      <button type="button" className="pw-card" onClick={() => onPick('watch')}>
        <span className="pw-card-title">Just watch an address</span>
        <span className="pw-card-body">Follow a wallet's positions and PnL. Read-only, no trading.</span>
      </button>

      <p className="pw-note">
        New to Hyperliquid?{' '}
        <a href="https://app.hyperliquid.xyz" target="_blank" rel="noopener noreferrer">
          Create an account at app.hyperliquid.xyz
        </a>
        , then come back.
      </p>
      <p className="pw-fine">
        pathiel never custodies your funds. We skip the builder-fee step, so your trades
        carry no added fee.
      </p>
    </div>
  );
}
