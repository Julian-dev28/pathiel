/**
 * "Use an API key": paste a main address plus an API wallet private key
 * created on Hyperliquid's own site. The one security property that matters
 * here: the raw key never touches localStorage, sessionStorage or a log —
 * it lives in this component's state for as long as it takes to type it and
 * is cleared the instant the submit request is sent, success or failure.
 */

import { useState } from 'react';
import type { Account } from '../lib/contract';
import { importApiKey } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { ADDRESS_HELP, PRIVATE_KEY_HELP, isValidAddress, isValidPrivateKey } from '../lib/validate';
import { shortAddress } from '../lib/format';
import { IconCheck, IconSpinner } from './Icons';

export interface ApiKeyViewProps {
  onImported: (account: Account) => Promise<void> | void;
  onDone: () => void;
}

export function ApiKeyView({ onImported, onDone }: ApiKeyViewProps) {
  const [mainAddress, setMainAddress] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Account | null>(null);

  const addressOk = isValidAddress(mainAddress);
  const keyOk = isValidPrivateKey(privateKey);

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setTouched(true);
    if (!addressOk || !keyOk) return;

    setSubmitting(true);
    setError(null);
    const key = privateKey;
    // Clear it from component state now, before the request even resolves —
    // whatever happens next, the key does not linger in memory any longer
    // than it has to.
    setPrivateKey('');
    try {
      const account = await importApiKey({ main_address: mainAddress.trim(), agent_private_key: key });
      await onImported(account);
      setDone(account);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="pw-step pw-step-done">
        <div className="pw-done-badge"><IconCheck size={28} /></div>
        <p className="pw-step-heading">Account added</p>
        <p className="pw-step-body">{shortAddress(done.main_address)} is ready to trade.</p>
        <button type="button" className="pw-btn pw-btn-primary" onClick={onDone}>Done</button>
      </div>
    );
  }

  return (
    <form className="pw-form" onSubmit={handleSubmit} autoComplete="off">
      <label className="pw-field">
        <span className="pw-field-label">Main wallet address</span>
        <input
          type="text"
          inputMode="text"
          placeholder="0x…"
          value={mainAddress}
          onChange={(e) => setMainAddress(e.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={touched && !addressOk}
          className="pw-input pw-input-mono"
        />
        {touched && !addressOk && <span className="pw-field-error">{ADDRESS_HELP}</span>}
      </label>

      <label className="pw-field">
        <span className="pw-field-label">API wallet private key</span>
        <input
          type="password"
          placeholder="0x…"
          value={privateKey}
          onChange={(e) => setPrivateKey(e.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={touched && !keyOk}
          autoComplete="off"
          className="pw-input pw-input-mono"
        />
        {touched && !keyOk && <span className="pw-field-error">{PRIVATE_KEY_HELP}</span>}
      </label>

      <a className="pw-note" href="https://app.hyperliquid.xyz/API" target="_blank" rel="noopener noreferrer">
        Get a key at Hyperliquid's API page
      </a>

      {error && <div className="pw-banner pw-banner-down"><p>{error}</p></div>}

      <button type="submit" className="pw-btn pw-btn-primary" disabled={submitting}>
        {submitting && <IconSpinner size={14} />}
        {submitting ? 'Adding…' : 'Add account'}
      </button>
    </form>
  );
}
