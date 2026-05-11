'use client'

import React from 'react'

export type WatchlistCardProps = {
  coin: string
  type: 'perp' | 'spot'
  mid: number
  compositeScore: number
  triggers: Array<{ name: string; score: number; reason: string; fired: boolean }>
  status: 'scanning' | 'analyzing' | 'analyzed' | 'blocked' | 'executed'
  blockReason?: string
  lastSeenAt?: number
}

const TRIGGER_COLORS: Record<string, string> = {
  pctMoveSpike: '#BE4A40',
  volumeSpike: '#4A7FA5',
  breakout: '#2E9E68',
  rangeCompression: '#F59E0B',
  trendStrength: '#3C6EA0',
}

export function WatchlistCard({ coin, type, mid, compositeScore, triggers, status, blockReason }: WatchlistCardProps) {
  const fired = triggers.filter(t => t.fired)
  const scoreColor =
    compositeScore >= 85 ? '#FF6B35' :
    compositeScore >= 70 ? '#4A7FA5' :
    compositeScore >= 50 ? '#C2956B' : '#666'
  const statusLabel = status === 'scanning' ? 'scan' :
    status === 'analyzing' ? 'AI' :
    status === 'analyzed' ? 'done' :
    status === 'blocked' ? 'blocked' : 'exec'
  const statusColor = status === 'analyzing' ? '#4A7FA5' :
    status === 'analyzed' ? '#2E9E68' :
    status === 'blocked' ? '#BE4A40' :
    status === 'executed' ? '#2E9E68' : '#888'

  return (
    <div style={{
      padding: '10px 12px',
      borderRadius: 8,
      background: 'var(--bg-card)',
      border: `1px solid ${status === 'analyzing' ? 'rgba(74,127,165,0.4)' : 'var(--border)'}`,
      transition: 'border-color 0.2s',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 800, color: 'var(--text-primary)' }}>
          {coin}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            fontFamily: 'var(--font-geist-mono)',
            fontSize: 11,
            fontWeight: 700,
            color: scoreColor,
          }}>
            {compositeScore}
          </span>
          <span style={{
            fontSize: 9,
            fontWeight: 700,
            color: statusColor,
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
          }}>
            {statusLabel}
          </span>
        </div>
      </div>

      <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', marginBottom: 4 }}>
        ${mid > 1 ? mid.toFixed(2) : mid.toFixed(6)} · {type}
      </div>

      {fired.length > 0 && (
        <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', marginBottom: 3 }}>
          {fired.map(t => (
            <span key={t.name} style={{
              fontSize: 8,
              fontWeight: 700,
              padding: '1px 5px',
              borderRadius: 3,
              background: `${TRIGGER_COLORS[t.name] || '#666'}15`,
              color: TRIGGER_COLORS[t.name] || '#666',
              border: `1px solid ${TRIGGER_COLORS[t.name] || '#666'}30`,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}>
              {t.name.replace(/([A-Z])/g, ' $1').trim().slice(0, 12)}
            </span>
          ))}
        </div>
      )}

      {blockReason && (
        <div style={{
          fontSize: 9,
          color: '#BE4A40',
          fontFamily: 'var(--font-geist-mono)',
          marginTop: 2,
        }}>
          Blocked: {blockReason}
        </div>
      )}
    </div>
  )
}
