/**
 * "Just watch an address": read-only, no key, no signature. One field.
 */

import { useState } from 'react';
import type { Account } from '../lib/contract';
import { watchAddress } from '../lib/api';
import { errorMessage } from '../lib/errors';
import { ADDRESS_HELP, isValidAddress } from '../lib/validate';
import { shortAddress } from '../lib/format';
import { IconCheck, IconSpinner } from './Icons';

export interface WatchViewProps {
  onWatched: (account: Account) => Promise<void> | void;
  onDone: () => void;
}

export function WatchView({ onWatched, onDone }: WatchViewProps) {
  const [address, setAddress] = useState('');
  const [touched, setTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Account | null>(null);

  const addressOk = isValidAddress(address);

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setTouched(true);
    if (!addressOk) return;

    setSubmitting(true);
    setError(null);
    try {
      const account = await watchAddress({ address: address.trim() });
      await onWatched(account);
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
        <p className="pw-step-heading">Now watching</p>
        <p className="pw-step-body">{shortAddress(done.main_address)} — positions and PnL, read-only.</p>
        <button type="button" className="pw-btn pw-btn-primary" onClick={onDone}>Done</button>
      </div>
    );
  }

  return (
    <form className="pw-form" onSubmit={handleSubmit} autoComplete="off">
      <label className="pw-field">
        <span className="pw-field-label">Address to watch</span>
        <input
          type="text"
          inputMode="text"
          placeholder="0x…"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={touched && !addressOk}
          className="pw-input pw-input-mono"
        />
        {touched && !addressOk && <span className="pw-field-error">{ADDRESS_HELP}</span>}
      </label>

      {error && <div className="pw-banner pw-banner-down"><p>{error}</p></div>}

      <button type="submit" className="pw-btn pw-btn-primary" disabled={submitting}>
        {submitting && <IconSpinner size={14} />}
        {submitting ? 'Adding…' : 'Watch this address'}
      </button>
    </form>
  );
}
