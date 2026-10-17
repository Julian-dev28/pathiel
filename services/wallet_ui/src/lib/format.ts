/** Small, pure formatting helpers — no DOM, no fetch, easy to read top to bottom. */

/** "0x2C2E...9876" style short address, matching the Hyperdash reference shots. */
export function shortAddress(address: string | null | undefined): string {
  if (!address || address.length < 10) return address ?? '';
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
});

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return USD.format(value);
}

export function modeLabel(mode: 'trade' | 'watch'): string {
  return mode === 'trade' ? 'Trade' : 'Watch';
}
