'use client'

import { useState } from 'react'
import type { HLAccount } from '@/lib/hyperliquid'

interface HLPositionsPanelProps {
  account:   HLAccount | null
  onRefresh: () => void
}

const fmtUSD = (n: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const fmtPrice = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })

export default function HLPositionsPanel({ account, onRefresh }: HLPositionsPanelProps) {
  const [loading, setLoading] = useState(false)

  function handleRefresh() {
    setLoading(true)
    onRefresh()
    setTimeout(() => setLoading(false), 800)
  }

  const pos         = account?.position ?? null
  const isLong      = pos?.side === 'long'
  const pnlColor    = pos && pos.unrealizedPnl >= 0 ? 'var(--green-dark)' : 'var(--pink-dark)'
  const entryValue  = pos ? pos.entryPx * pos.sizeBTC : 0
  const pnlPct      = entryValue > 0 ? (pos!.unrealizedPnl / entryValue) * 100 : 0
  const barPct      = Math.min(100, Math.abs(pnlPct) * 10)

  return (
    <div className="card" style={{ padding: '20px 20px 16px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', display: 'inline-block', animation: 'pulse-dot 2s ease infinite' }} />
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '-0.01em' }}>HL Account</span>
        </div>
        <button onClick={handleRefresh} disabled={loading}
          style={{ background: 'none', border: 'none', cursor: loading ? 'wait' : 'pointer', fontSize: 13, color: 'var(--text-muted)', padding: 0, lineHeight: 1 }}
          title="Refresh">
          <span style={{ display: 'inline-block', animation: loading ? 'spin-slow 0.8s linear infinite' : 'none' }}>↻</span>
        </button>
      </div>

      {/* Total Equity */}
      {account ? (
        <>
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600, marginBottom: 4 }}>
              Total Equity
            </div>
            <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 30, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.03em', lineHeight: 1 }}>
              ${fmtUSD(account.totalEquity)}
            </div>
          </div>

          {/* Breakdown row */}
          <div style={{ display: 'flex', gap: 0, marginBottom: 20, borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', padding: '10px 0' }}>
            {[
              ['Perp margin', `$${fmtUSD(account.equity)}`,   'var(--text-primary)'],
              ['Spot USDC',   `$${fmtUSD(account.spotUSDC)}`, 'var(--blue-dark)'],
            ].map(([label, val, col], idx, arr) => (
              <div key={label} style={{
                flex: 1,
                textAlign: idx === arr.length - 1 ? 'right' : 'left',
              }}>
                <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 3 }}>{label}</div>
                <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: col }}>{val}</div>
              </div>
            ))}
          </div>

          {/* Open Position */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
              Open Position
            </div>

            {pos ? (
              <div>
                {/* Side badge + size + leverage */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center',
                    padding: '4px 12px', borderRadius: 100,
                    background: isLong ? 'var(--green-pale)' : 'var(--pink-pale)',
                    border: `1px solid ${isLong ? 'rgba(46,158,104,0.3)' : 'rgba(190,74,64,0.3)'}`,
                    fontSize: 11, fontWeight: 800,
                    color: isLong ? 'var(--green-dark)' : 'var(--pink-dark)',
                    letterSpacing: '0.05em',
                  }}>
                    {isLong ? 'LONG' : 'SHORT'}
                  </span>
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                    {pos.sizeBTC.toFixed(4)} BTC
                  </span>
                  <span style={{
                    padding: '2px 8px', borderRadius: 6,
                    background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                    fontSize: 10, fontWeight: 700, color: 'var(--text-secondary)',
                    fontFamily: 'var(--font-geist-mono)',
                  }}>
                    {pos.leverage}×
                  </span>
                </div>

                {/* Entry + PnL */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
                  <div>
                    <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 3 }}>Entry Price</div>
                    <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 14, fontWeight: 700, color: 'var(--text-secondary)' }}>
                      {fmtPrice(pos.entryPx)}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 3 }}>Unrealized PnL</div>
                    <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 14, fontWeight: 800, color: pnlColor }}>
                      {pos.unrealizedPnl >= 0 ? '+' : ''}${fmtUSD(pos.unrealizedPnl)}
                    </div>
                  </div>
                </div>

                {/* PnL progress bar */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>PnL %</span>
                    <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, fontWeight: 700, color: pnlColor }}>
                      {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(3)}%
                    </span>
                  </div>
                  <div style={{ height: 5, borderRadius: 3, background: 'var(--bg-secondary)', overflow: 'hidden' }}>
                    <div style={{
                      height: '100%', borderRadius: 3,
                      width: `${barPct}%`,
                      background: pos.unrealizedPnl >= 0 ? 'var(--green)' : 'var(--pink)',
                      transition: 'width 0.6s ease, background 0.4s',
                    }} />
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '20px 0', fontSize: 11, color: 'var(--text-muted)' }}>
                No open position
              </div>
            )}
          </div>
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: '24px 0', fontSize: 11, color: 'var(--text-muted)' }}>
          Connecting…
        </div>
      )}

      {/* Footer strip */}
      <div style={{ marginTop: 8, paddingTop: 10, borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'center', gap: 12, alignItems: 'center' }}>
        {['OpenRouter', 'Hyperliquid', 'Qwen3'].map((item, i, arr) => (
          <span key={item} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.03em' }}>{item}</span>
            {i < arr.length - 1 && <span style={{ fontSize: 9, color: 'var(--border-bright)' }}>·</span>}
          </span>
        ))}
      </div>

    </div>
  )
}
