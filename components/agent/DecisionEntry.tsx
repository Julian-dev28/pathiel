'use client'

import React, { useState } from 'react'

export type DecisionEntryProps = {
  coin: string
  verdict: string
  confidence: number
  reasoning: string
  side?: 'long' | 'short' | null
  entryPx?: number
  stopPx?: number
  tpPx?: number
  newsContext?: string
  blockedBy?: string[]
  executed?: boolean
  createdAt: number
  gateResults?: Record<string, { pass: boolean; reason?: string }>
}

const VERDICT_COLORS: Record<string, string> = {
  PASS: '#C2956B',
  LONG: '#2E9E68',
  SHORT: '#BE4A40',
  CLOSE: '#3C6EA0',
}

export function DecisionEntry({
  coin, verdict, confidence, reasoning, side, entryPx, stopPx, tpPx,
  newsContext, blockedBy, executed, createdAt, gateResults,
}: DecisionEntryProps) {
  const [expanded, setExpanded] = useState(false)
  const color = VERDICT_COLORS[verdict] ?? '#888'
  const timeStr = new Date(createdAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  const bullets = reasoning.split('\n').filter(l => l.trim()).slice(0, 4)

  return (
    <div style={{
      padding: '10px 14px',
      borderRadius: 8,
      background: executed ? `${color}08` : 'var(--bg-card)',
      border: `1px solid ${color}25`,
      cursor: 'pointer',
      transition: 'border-color 0.15s',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontFamily: 'var(--font-geist-mono)',
            fontSize: 11,
            fontWeight: 800,
            color,
            letterSpacing: '0.03em',
          }}>
            {verdict}
          </span>
          <span style={{
            fontFamily: 'var(--font-geist-mono)',
            fontSize: 11,
            fontWeight: 700,
            color: 'var(--text-primary)',
          }}>
            {coin}
          </span>
          {side && (
            <span style={{
              fontSize: 9,
              fontWeight: 700,
              padding: '1px 4px',
              borderRadius: 3,
              background: side === 'long' ? 'rgba(46,158,104,0.1)' : 'rgba(190,74,64,0.1)',
              color: side === 'long' ? '#2E9E68' : '#BE4A40',
              textTransform: 'uppercase',
            }}>
              {side}
            </span>
          )}
          {executed && (
            <span style={{
              fontSize: 8,
              fontWeight: 700,
              padding: '1px 4px',
              borderRadius: 3,
              background: 'rgba(46,158,104,0.1)',
              color: '#2E9E68',
              border: '1px solid rgba(46,158,104,0.25)',
              letterSpacing: '0.04em',
            }}>
              LIVE
            </span>
          )}
          {blockedBy && blockedBy.length > 0 && (
            <span style={{
              fontSize: 8,
              fontWeight: 700,
              padding: '1px 4px',
              borderRadius: 3,
              background: 'rgba(190,74,64,0.1)',
              color: '#BE4A40',
              letterSpacing: '0.04em',
            }}>
              BLOCKED
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontFamily: 'var(--font-geist-mono)',
            fontSize: 10,
            color: 'var(--text-muted)',
          }}>
            {confidence > 0 ? `${Math.round(confidence * 100)}%` : ''}
          </span>
          <span style={{
            fontFamily: 'var(--font-geist-mono)',
            fontSize: 9,
            color: 'var(--text-muted)',
          }}>
            {timeStr}
          </span>
        </div>
      </div>

      {bullets.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {bullets.map((line, i) => {
            const clean = line.replace(/^[•\-*]\s*/, '').replace(/\*\*(.*?)\*\*/g, '$1').trim()
            if (!clean) return null
            return (
              <div key={i} style={{ display: 'flex', gap: 6, marginTop: i > 0 ? 2 : 0 }}>
                <span style={{ color: 'var(--text-muted)', fontSize: 12, lineHeight: 1.4, flexShrink: 0 }}>·</span>
                <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                  {clean}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {entryPx && stopPx && tpPx && (
        <div style={{
          fontSize: 9,
          fontFamily: 'var(--font-geist-mono)',
          color: 'var(--text-muted)',
          marginTop: 6,
        }}>
          Entry: ${entryPx.toFixed(2)} · SL: ${stopPx.toFixed(2)} · TP: ${tpPx.toFixed(2)}
        </div>
      )}

      {(expanded || blockedBy?.length) && blockedBy && blockedBy.length > 0 && (
        <div style={{
          fontSize: 9,
          fontFamily: 'var(--font-geist-mono)',
          color: '#BE4A40',
          marginTop: 6,
        }}>
          Gates failed: {blockedBy.join('; ')}
        </div>
      )}

      {(expanded || gateResults) && gateResults && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
            Gate Results
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
            {Object.entries(gateResults).map(([key, result]) => (
              <span key={key} style={{
                fontSize: 8,
                fontFamily: 'var(--font-geist-mono)',
                padding: '1px 4px',
                borderRadius: 3,
                background: result.pass ? 'rgba(46,158,104,0.08)' : 'rgba(190,74,64,0.08)',
                color: result.pass ? '#2E9E68' : '#BE4A40',
                border: `1px solid ${result.pass ? 'rgba(46,158,104,0.15)' : 'rgba(190,74,64,0.15)'}`,
              }}>
                {key}: {result.pass ? 'OK' : (result.reason ?? 'fail')}
              </span>
            ))}
          </div>
        </div>
      )}

      <div
        onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
        style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 6, cursor: 'pointer' }}
      >
        {expanded ? '▾ Collapse' : '▸ Expand details'}
      </div>

      {expanded && reasoning && (
        <div style={{
          marginTop: 8,
          padding: '8px 10px',
          background: 'rgba(0,0,0,0.2)',
          borderRadius: 6,
          fontSize: 11,
          fontFamily: 'var(--font-geist-mono)',
          color: 'var(--text-secondary)',
          whiteSpace: 'pre-wrap',
          lineHeight: 1.5,
        }}>
          {reasoning}
        </div>
      )}
    </div>
  )
}
