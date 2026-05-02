'use client'

import { useState, useCallback, useRef } from 'react'
import Header from '@/components/Header'
import HLMarketCard from '@/components/HLMarketCard'
import HLPriceChart from '@/components/HLPriceChart'
import HLPositionsPanel from '@/components/HLPositionsPanel'
import { useHLTick } from '@/hooks/useHLTick'
import type { HLAccount } from '@/lib/hyperliquid'

function buildHint(price: number | null, acct: HLAccount | null): string | undefined {
  if (!price) return undefined
  const pos = acct?.position
  return [
    `BTC-PERP mid price: $${price.toLocaleString('en-US', { maximumFractionDigits: 1 })}`,
    `Master account ${process.env.NEXT_PUBLIC_HL_MASTER ?? ''} holds all funds — use this address for get_clearinghouse_state (NOT the API wallet which has $0).`,
    `Account equity: perp $${(acct?.equity ?? 0).toFixed(2)}, spot USDC $${(acct?.spotUSDC ?? 0).toFixed(2)}, total available $${(acct?.totalEquity ?? 0).toFixed(2)}`,
    pos
      ? `Current position: ${pos.side.toUpperCase()} ${pos.sizeBTC.toFixed(4)} BTC @ $${pos.entryPx.toLocaleString('en-US', { maximumFractionDigits: 0 })} · unrealized PnL: ${pos.unrealizedPnl >= 0 ? '+' : ''}${pos.unrealizedPnl.toFixed(2)}`
      : 'Current position: FLAT (no open BTC-PERP position)',
  ].join('\n')
}

function AnalysisLine({ text }: { text: string }) {
  const clean = text.replace(/^[•\-*]\s*/, '').replace(/\*\*(.*?)\*\*/g, '$1').trim()
  if (!clean) return null
  const isLong  = /\bLONG\b/i.test(clean)
  const isShort = /\bSHORT\b/i.test(clean)
  const isClose = /\bCLOSE\b/i.test(clean)
  const isPass  = /\bPASS\b/i.test(clean)
  const isVerdict = isLong || isShort || isClose || isPass
  if (isVerdict) {
    const verdict = isLong ? 'LONG' : isShort ? 'SHORT' : isClose ? 'CLOSE' : 'PASS'
    const [vColor, vBg] = isLong  ? ['var(--green-dark)', 'rgba(58,158,114,0.10)']
      : isShort ? ['var(--pink-dark)',  'rgba(224,111,160,0.10)']
      : isClose ? ['var(--blue)',       'rgba(74,127,165,0.10)']
      : ['var(--amber)', 'rgba(212,135,44,0.08)']
    const rest = clean.replace(/^(LONG|SHORT|CLOSE|PASS)\s*[—–\-]?\s*/i, '')
    return (
      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px',
        borderRadius: 12, background: vBg, border: `1px solid ${vColor}40`, marginBottom: 6,
      }}>
        <span style={{
          padding: '2px 10px', borderRadius: 20, background: vColor,
          color: '#fff', fontSize: 11, fontWeight: 800, letterSpacing: '0.04em',
          flexShrink: 0, marginTop: 2,
        }}>{verdict}</span>
        <span style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text-primary)', fontWeight: 500 }}>{rest}</span>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
      <span style={{ color: 'var(--text-muted)', fontSize: 16, lineHeight: 1.4, flexShrink: 0 }}>·</span>
      <span style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-secondary)' }}>{clean}</span>
    </div>
  )
}

export default function DashboardPage() {
  const { btcPrice, priceHistory, account, refreshAccount } = useHLTick()

  const [analysisText, setAnalysisText] = useState<string | null>(null)
  const [analyzing,    setAnalyzing]    = useState(false)
  const [analysisErr,  setAnalysisErr]  = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const runAnalysis = useCallback(async () => {
    if (analyzing) return
    setAnalyzing(true)
    setAnalysisErr(null)
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    const hint = buildHint(btcPrice, account)
    const sessionId = typeof window !== 'undefined'
      ? (localStorage.getItem('aomi-dashboard-session') ?? (() => {
          const id = crypto.randomUUID()
          localStorage.setItem('aomi-dashboard-session', id)
          return id
        })())
      : crypto.randomUUID()
    const prompt = `Check live BTC price and order book on Hyperliquid. Check my current position. Search for the latest BTC price action and market sentiment. Give me a direct LONG / SHORT / PASS verdict with confidence and 3-4 bullet points of reasoning.`

    try {
      const res = await fetch('/api/aomi/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: prompt,
          hint,
          sessionId,
          marketData: btcPrice ? { btc_price: btcPrice, equity: account?.equity ?? 0, position: account?.position ?? null } : undefined,
          riskPct: 5,
        }),
        signal: ctrl.signal,
      })

      if (!res.ok || !res.body) {
        if (res.status === 401) throw new Error('Auth error (401) — check API key')
        throw new Error(`Request failed (${res.status})`)
      }

      const reader = res.body.getReader()
      const dec    = new TextDecoder()
      let buf  = ''
      let text = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() ?? ''
        for (const part of parts) {
          if (!part.startsWith('data: ')) continue
          try {
            const ev = JSON.parse(part.slice(6))
            if (ev.type === 'message') {
              text = ev.text
              setAnalysisText(text)
            }
            if (ev.type === 'error') throw new Error(ev.text)
          } catch (e) {
            if ((e as Error).message !== 'JSON parse') throw e
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      setAnalysisErr(String(err))
    } finally {
      setAnalyzing(false)
    }
  }, [btcPrice, account, analyzing])

  const pos         = account?.position ?? null
  const entryPrice  = pos?.entryPx
  const posSide     = pos?.side

  const analysisLines = analysisText
    ? analysisText.split('\n').filter(l => l.trim())
    : []

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)', display: 'flex', flexDirection: 'column' }}>
      <Header cycleId={0} isRunning={false} />

      <main style={{
        flex: 1,
        display: 'grid',
        gridTemplateColumns: '280px 1fr 260px',
        gap: 12,
        padding: '16px 20px',
        alignItems: 'start',
        minHeight: 0,
      }}>
        {/* Left column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <HLMarketCard btcPrice={btcPrice} account={account} onRefresh={refreshAccount} />
        </div>

        {/* Center column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <HLPriceChart
            priceHistory={priceHistory}
            currentPrice={btcPrice ?? 0}
            entryPrice={entryPrice}
            positionSide={posSide}
          />

          {/* AOMI Analysis panel */}
          <div className="card" style={{ padding: '18px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{
                  width: 24, height: 24, borderRadius: '50%', flexShrink: 0,
                  background: 'linear-gradient(135deg, var(--blue), var(--green))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 11, fontWeight: 800, color: '#fff',
                }}>A</div>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                  AOMI Analysis
                </span>
              </div>
              <button
                onClick={runAnalysis}
                disabled={analyzing || !btcPrice}
                style={{
                  padding: '5px 16px', borderRadius: 20, fontSize: 12, fontWeight: 700,
                  border: 'none', cursor: analyzing || !btcPrice ? 'not-allowed' : 'pointer',
                  background: analyzing ? 'var(--bg-secondary)' : 'var(--blue)',
                  color: analyzing ? 'var(--text-muted)' : '#fff',
                  transition: 'all 0.15s',
                  outline: analyzing ? '1px solid var(--border)' : 'none',
                }}
              >
                {analyzing ? 'Analyzing…' : 'Analyze'}
              </button>
            </div>

            {analyzing && !analysisText && (
              <div style={{ display: 'flex', gap: 5, padding: '8px 0' }}>
                {[0, 1, 2].map(i => (
                  <span key={i} style={{
                    width: 6, height: 6, borderRadius: '50%', background: 'var(--text-muted)',
                    display: 'inline-block',
                    animation: `dotbounce 1.2s ease-in-out ${i * 0.2}s infinite`,
                  }} />
                ))}
              </div>
            )}

            {analysisErr && (
              <div style={{
                padding: '10px 14px', borderRadius: 10,
                background: 'rgba(224,111,160,0.06)', border: '1px solid rgba(224,111,160,0.2)',
                fontSize: 12, color: 'var(--pink-dark)', lineHeight: 1.5,
              }}>
                {analysisErr}
              </div>
            )}

            {!analysisText && !analyzing && !analysisErr && (
              <div style={{ padding: '12px 0', fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
                Click <strong>Analyze</strong> to get a live AOMI market verdict — Hyperliquid data + web search + reasoning.
              </div>
            )}

            {analysisText && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {analysisLines.map((line, i) => (
                  <AnalysisLine key={i} text={line} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <HLPositionsPanel account={account} onRefresh={refreshAccount} />
        </div>
      </main>
    </div>
  )
}
