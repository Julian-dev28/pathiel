'use client'

import React, { useState, useRef, useEffect, useCallback } from 'react'
import Header from '@/components/Header'
import { useHLTick } from '@/hooks/useHLTick'
import type { HLAccount } from '@/lib/hyperliquid'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Msg {
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done'
  autoExecuted?: boolean
}

// ── Tool display ───────────────────────────────────────────────────────────────

const HL_TOOLS: Record<string, string> = {
  get_all_mids:           'Fetching BTC price',
  get_l2_book:            'Checking order book',
  get_clearinghouse_state:'Checking positions',
  get_open_orders:        'Fetching open orders',
  get_user_fills:         'Checking trade history',
  get_funding_history:    'Checking funding rates',
  get_candle_snapshot:    'Fetching candles',
  brave_search:           'Searching web',
}

function ToolPill({ name, status }: { name: string; status: 'running' | 'done' }) {
  const label = HL_TOOLS[name] ?? name.replace(/_/g, ' ')
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '5px 14px', borderRadius: 20,
      background: status === 'done' ? 'rgba(74,127,165,0.08)' : 'rgba(74,127,165,0.05)',
      border: `1px solid ${status === 'done' ? 'rgba(74,127,165,0.35)' : 'rgba(74,127,165,0.18)'}`,
      fontSize: 12, fontWeight: 600,
      color: status === 'done' ? 'var(--blue)' : 'var(--text-muted)',
      transition: 'all 0.3s',
    }}>
      {status === 'running' ? (
        <span style={{ display: 'inline-flex', gap: 3 }}>
          {[0,1,2].map(i => (
            <span key={i} style={{
              width: 4, height: 4, borderRadius: '50%', background: 'var(--blue)',
              display: 'inline-block',
              animation: `dotbounce 1.2s ease-in-out ${i * 0.2}s infinite`,
            }} />
          ))}
        </span>
      ) : <span>⬡</span>}
      {label}{status === 'running' ? '…' : ' · done'}
    </div>
  )
}

// ── Message renderer ───────────────────────────────────────────────────────────

function BotMsg({ content, autoExecuted }: { content: string; autoExecuted?: boolean }) {
  const lines = content.split('\n').filter(l => l.trim())
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {autoExecuted && (
        <div style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 4,
          padding: '2px 10px', borderRadius: 20, fontSize: 10, fontWeight: 700,
          background: 'rgba(245,158,11,0.10)', color: 'var(--amber)',
          border: '1px solid rgba(245,158,11,0.25)', width: 'fit-content',
          letterSpacing: '0.06em',
        }}>AUTO-EXECUTED</div>
      )}
      {lines.map((line, i) => {
        const clean = line.replace(/^[•\-*]\s*/, '').replace(/\*\*(.*?)\*\*/g, '$1').trim()
        if (!clean) return null
        const isLong  = /\bLONG\b/i.test(clean)
        const isShort = /\bSHORT\b/i.test(clean)
        const isClose = /\bCLOSE\b/i.test(clean)
        const isPass  = /\bPASS\b/i.test(clean)
        const isVerdict = (isLong || isShort || isClose || isPass) && i === 0
        if (isVerdict) {
          const verdict = isLong ? 'LONG' : isShort ? 'SHORT' : isClose ? 'CLOSE' : 'PASS'
          const [vColor, vBg] = isLong  ? ['var(--green-dark)', 'rgba(58,158,114,0.10)']
            : isShort ? ['var(--pink-dark)',  'rgba(224,111,160,0.10)']
            : isClose ? ['var(--blue)',        'rgba(74,127,165,0.10)']
            : ['var(--amber)', 'rgba(212,135,44,0.08)']
          const rest = clean.replace(/^(LONG|SHORT|CLOSE|PASS)\s*[—–\-]?\s*/i, '')
          return (
            <div key={i} style={{
              display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px',
              borderRadius: 12, background: vBg, border: `1px solid ${vColor}40`,
            }}>
              <span style={{
                padding: '2px 10px', borderRadius: 20, background: vColor,
                color: '#fff', fontSize: 11, fontWeight: 800, letterSpacing: '0.04em',
                flexShrink: 0, marginTop: 2,
              }}>{verdict}</span>
              <span style={{ fontSize: 14, lineHeight: 1.5, color: 'var(--text-primary)', fontWeight: 500 }}>{rest}</span>
            </div>
          )
        }
        return (
          <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: 16, lineHeight: 1.4, flexShrink: 0 }}>·</span>
            <span style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--text-secondary)' }}>{clean}</span>
          </div>
        )
      })}
    </div>
  )
}

// ── Wait countdown ─────────────────────────────────────────────────────────────

function WaitCountdown({ label, until }: { label: string; until: number }) {
  const [secs, setSecs] = useState(() => Math.max(0, Math.ceil((until - Date.now()) / 1000)))
  useEffect(() => {
    const id = setInterval(() => setSecs(Math.max(0, Math.ceil((until - Date.now()) / 1000))), 500)
    return () => clearInterval(id)
  }, [until])
  const m = Math.floor(secs / 60)
  const s = secs % 60
  const display = m > 0 ? `${m}m ${s}s` : `${s}s`
  return <>{label} <span style={{ fontFamily: 'var(--font-geist-mono)' }}>{display}</span></>
}

// ── Market bar ─────────────────────────────────────────────────────────────────

function MarketBar({ btcPrice, account }: { btcPrice: number | null; account: HLAccount | null }) {
  const pos         = account?.position ?? null
  const equity      = account?.totalEquity ?? null
  const pnlPos      = pos && pos.unrealizedPnl >= 0

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap',
      padding: '9px 24px', borderBottom: '1px solid var(--border)',
      background: 'var(--bg-card)', fontSize: 12,
    }}>
      {/* BTC price */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>BTC-PERP</span>
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 800, fontSize: 15, color: 'var(--text-primary)' }}>
          {btcPrice ? `$${btcPrice.toLocaleString('en-US', { maximumFractionDigits: 1 })}` : '—'}
        </span>
      </div>

      {/* Account equity */}
      {equity !== null && <>
        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>Equity</span>
          <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 700, color: 'var(--amber)' }}>
            ${equity.toFixed(2)}
          </span>
        </div>
      </>}

      {/* Position */}
      <div style={{ width: 1, height: 18, background: 'var(--border)' }} />
      {pos ? (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <div style={{
            padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
            background: pos.side === 'long' ? 'rgba(58,158,114,0.12)' : 'rgba(224,111,160,0.12)',
            color: pos.side === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)',
            border: `1px solid ${pos.side === 'long' ? 'rgba(58,158,114,0.3)' : 'rgba(224,111,160,0.3)'}`,
          }}>{pos.side === 'long' ? '↑ LONG' : '↓ SHORT'}</div>
          <span style={{ fontFamily: 'var(--font-geist-mono)', color: 'var(--text-secondary)', fontWeight: 600 }}>
            {pos.sizeBTC.toFixed(4)} BTC @ ${pos.entryPx.toLocaleString('en-US', { maximumFractionDigits: 0 })}
          </span>
          <span style={{
            fontFamily: 'var(--font-geist-mono)', fontWeight: 700, fontSize: 12,
            color: pnlPos ? 'var(--green-dark)' : 'var(--pink-dark)',
          }}>
            {pnlPos ? '+' : ''}{pos.unrealizedPnl.toFixed(2)}
          </span>
        </div>
      ) : (
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.04em' }}>FLAT</span>
      )}

      <div style={{ marginLeft: 'auto', fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>
        Hyperliquid
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

const INIT_MSG: Msg = {
  role: 'system',
  content: "BTC-PERP runs 24/7. Before every decision I pull live data from Hyperliquid — price, order book, your position — then search live BTC news and momentum. Enable Auto Mode and I'll analyze continuously: query → search → reason → execute. No stale data. No pre-programmed rules.",
}

const QUICK_PROMPTS = [
  { label: 'Trade for me',  prompt: 'Get live BTC price and order book from Hyperliquid. Check my current position and PnL. Look at the last 5–10 minutes of price action and candles. Give me a LONG / SHORT / CLOSE / PASS verdict right now based on the current momentum. Be decisive.' },
  { label: 'Should I flip', prompt: 'Check my current position on Hyperliquid. Get the live order book and recent candles. Is the current trend still intact or is it reversing? Should I hold, close, or flip direction? Give a direct verdict.' },
  { label: '↑ Long case',   prompt: 'Check live BTC price and order book. Is there a near-term bullish setup right now — green momentum, bid pressure, buyers stepping in? Give me the case for LONG with confidence.' },
  { label: '↓ Short case',  prompt: 'Check live BTC price and order book. Is there a near-term bearish setup — red candles, ask pressure, sellers dominating? Give me the case for SHORT with confidence.' },
]

const AUTO_PROMPT = `Get live BTC price and order book depth from Hyperliquid. Check my current position and unrealized PnL. Look at recent candles for momentum direction across the 5m to 4h timeframe. Give a verdict: LONG (enter/add long), SHORT (enter/flip short), CLOSE (exit current position), or PASS (no edge). Be decisive — 60%+ confidence is enough to act.`

export default function AgentPage() {
  const { btcPrice, account, refreshAccount } = useHLTick()

  // ── Session ──────────────────────────────────────────────────────────────
  const [sessionId] = useState<string>(() => {
    if (typeof window === 'undefined') return crypto.randomUUID()
    const env = window.location.hostname === 'localhost' ? 'local' : 'prod'
    const key = `aomi-agent-session-${env}`
    const stored = localStorage.getItem(key)
    if (stored) return stored
    const id = crypto.randomUUID()
    localStorage.setItem(key, id)
    return id
  })

  // ── Messages ──────────────────────────────────────────────────────────────
  const [messages, setMessages]     = useState<Msg[]>([INIT_MSG])
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [input, setInput]           = useState('')
  const scrollRef                   = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (historyLoaded) return
    fetch(`/api/aomi/history?sessionId=${sessionId}`)
      .then(r => r.json())
      .then(({ messages: aomiMsgs }) => {
        if (!aomiMsgs?.length) return
        const mapped: Msg[] = aomiMsgs
          .filter((m: { sender?: string; content?: string }) =>
            m.sender === 'agent' && m.content && m.content.trim().length > 0)
          .map((m: { content?: string }) => ({ role: 'assistant' as const, content: m.content ?? '' }))
        if (mapped.length) setMessages([INIT_MSG, ...mapped])
      })
      .catch(() => {})
      .finally(() => setHistoryLoaded(true))
  }, [sessionId, historyLoaded])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // ── Autonomous mode ───────────────────────────────────────────────────────
  const [autoMode, setAutoMode]         = useState(false)
  const [autoCycles, setAutoCycles]     = useState(0)
  const [tradesPlaced, setTradesPlaced] = useState(0)
  const [riskPct, setRiskPct]           = useState(5)
  const [autoWait, setAutoWait]         = useState<{ until: number; label: string } | null>(null)
  const autoRef      = useRef(false)
  const procRef      = useRef(false)
  const riskPctRef   = useRef(5)
  const lastAnalysisRef = useRef<number>(0)
  const lastTradedRef   = useRef<number>(0)    // timestamp of last trade — wait 5min before re-entering
  const fatalErrorRef   = useRef<string | null>(null)
  const sendRef = useRef<((text: string, opts?: { silent?: boolean; autoExecute?: boolean }) => Promise<boolean>) | null>(null)
  const [resuming, setResuming] = useState(false)

  useEffect(() => { autoRef.current = autoMode; if (!autoMode) setAutoWait(null) }, [autoMode])
  useEffect(() => { procRef.current = processing }, [processing])
  useEffect(() => { riskPctRef.current = riskPct }, [riskPct])

  useEffect(() => {
    if (!resuming) return
    const id = setInterval(() => {
      if (sessionStorage.getItem('aomi-processing') !== '1') {
        setResuming(false)
        setHistoryLoaded(false)
      }
    }, 1000)
    return () => clearInterval(id)
  }, [resuming])

  useEffect(() => {
    if (sessionStorage.getItem('aomi-auto') === '1') setAutoMode(true)
    lastAnalysisRef.current = Number(sessionStorage.getItem('aomi-last-analysis') ?? 0)
    lastTradedRef.current   = Number(sessionStorage.getItem('aomi-last-traded')   ?? 0)
    const stored = sessionStorage.getItem('aomi-trades-placed')
    if (stored) setTradesPlaced(Number(stored))
    const storedRisk = localStorage.getItem('aomi-risk-pct')
    if (storedRisk) setRiskPct(Number(storedRisk))
    if (sessionStorage.getItem('aomi-processing') === '1') setResuming(true)
  }, [])

  useEffect(() => { sessionStorage.setItem('aomi-auto', autoMode ? '1' : '0') }, [autoMode])
  useEffect(() => { sessionStorage.setItem('aomi-trades-placed', String(tradesPlaced)) }, [tradesPlaced])

  // ── Build market hint ─────────────────────────────────────────────────────
  const buildHint = useCallback((price: number | null, acct: HLAccount | null) => {
    if (!price) return undefined
    const pos = acct?.position
    return [
      `BTC-PERP mid price: $${price.toLocaleString('en-US', { maximumFractionDigits: 1 })}`,
      `Master account (NEXT_PUBLIC_HL_MASTER): ${process.env.NEXT_PUBLIC_HL_MASTER ?? 'see env'} — this is the unified account holding all funds. Use this address for get_clearinghouse_state, NOT the API wallet.`,
      `Available trading capital: $${(acct?.totalEquity ?? 0).toFixed(2)} (spot USDC is auto-transferred to perp margin on order execution — NEVER treat $0 perp equity as a blocker, use totalEquity)`,
      pos
        ? `Current position: ${pos.side.toUpperCase()} ${pos.sizeBTC.toFixed(4)} BTC @ $${pos.entryPx.toLocaleString('en-US', { maximumFractionDigits: 0 })} · unrealized PnL: ${pos.unrealizedPnl >= 0 ? '+' : ''}${pos.unrealizedPnl.toFixed(2)}`
        : 'Current position: FLAT (no open BTC-PERP position)',
    ].join('\n')
  }, [])

  // ── Close position ────────────────────────────────────────────────────────
  const closePosition = useCallback(async () => {
    const res  = await fetch('/api/hl/close-position', { method: 'POST' })
    const data = await res.json() as { ok: boolean; sizeBTC?: number; midPrice?: number; error?: string }
    setMessages(prev => [...prev, {
      role: 'system',
      content: data.ok
        ? `✅ Position closed — ${(data.sizeBTC ?? 0).toFixed(5)} BTC @ $${(data.midPrice ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
        : `❌ Close failed: ${data.error}`,
    }])
    refreshAccount()
    return data.ok
  }, [refreshAccount])

  // ── Execute trade ─────────────────────────────────────────────────────────
  const executeTrade = useCallback(async (side: 'long' | 'short') => {
    const res = await fetch('/api/hl/place-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ side, riskPct: riskPctRef.current }),
    })
    const data = await res.json() as { ok: boolean; orderId?: string; error?: string; sizeBTC?: number; midPrice?: number; leverage?: number }
    const dir  = side === 'long' ? '↑ LONG' : '↓ SHORT'
    setMessages(prev => [...prev, {
      role: 'system',
      content: data.ok
        ? `✅ ${dir} order placed — ${(data.sizeBTC ?? 0).toFixed(5)} BTC @ $${(data.midPrice ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · ${data.leverage ?? 5}× · ${riskPctRef.current}% risk`
        : `❌ Order failed: ${data.error}`,
    }])
    refreshAccount()
    return data.ok
  }, [refreshAccount])

  // ── Interrupt ─────────────────────────────────────────────────────────────
  const handleStop = useCallback(async () => {
    await fetch('/api/aomi/interrupt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId }),
    })
  }, [sessionId])

  // ── Core send ─────────────────────────────────────────────────────────────
  const send = useCallback(async (text: string, opts?: { silent?: boolean; autoExecute?: boolean }) => {
    if (!text.trim() || procRef.current) return false
    setProcessing(true)
    procRef.current = true
    sessionStorage.setItem('aomi-processing', '1')

    const hint       = buildHint(btcPrice, account)
    const marketData = btcPrice ? {
      btc_price: btcPrice,
      equity:    account?.equity ?? 0,
      position:  account?.position ?? null,
    } : undefined

    if (!opts?.silent) {
      setMessages(prev => [...prev,
        { role: 'user', content: text.trim() },
      ])
    } else {
      setMessages(prev => [...prev,
        { role: 'system', content: `⚡ Auto-analysis cycle ${autoCycles + 1}` },
      ])
    }

    try {
      const res = await fetch('/api/aomi/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text.trim(), hint, sessionId, marketData, riskPct: riskPctRef.current }),
      })
      if (!res.ok || !res.body) throw new Error('Request failed')

      const reader = res.body.getReader()
      const dec    = new TextDecoder()
      let buf = ''
      let assistantStarted = false
      let finalText = ''

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
            if (ev.type === 'tool') {
              setMessages(prev => {
                const next = [...prev]
                const last = next.findLastIndex(m => m.role === 'tool')
                if (last >= 0) {
                  next[last] = { ...next[last], toolName: ev.name, toolStatus: ev.status }
                } else {
                  next.push({ role: 'tool', content: ev.name, toolName: ev.name, toolStatus: ev.status })
                }
                return next
              })
            }
            if (ev.type === 'message') {
              finalText = ev.text
              if (!assistantStarted) {
                assistantStarted = true
                setMessages(prev => [
                  ...prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
                  { role: 'assistant', content: ev.text },
                ])
              } else {
                setMessages(prev => {
                  const next = [...prev]
                  const idx  = next.findLastIndex(m => m.role === 'assistant')
                  if (idx >= 0) next[idx] = { ...next[idx], content: ev.text }
                  return next
                })
              }
            }
            if (ev.type === 'error') {
              setMessages(prev => [...prev, { role: 'system', content: `Error: ${ev.text}` }])
            }
          } catch { /* malformed chunk */ }
        }
      }

      if (opts?.autoExecute && finalText) {
        // Verdict must appear at the START of the response (first non-empty line)
        const firstLine = finalText.split('\n').find(l => l.trim())?.trim() ?? ''
        const isLong  = /^LONG\b/i.test(firstLine)
        const isShort = /^SHORT\b/i.test(firstLine)
        const isClose = /^CLOSE\b/i.test(firstLine)
        // Match only explicit confidence label, not random percentages in the analysis
        const confMatch = finalText.match(/confidence[^:]*:\s*(\d+)%/i)
        const confNum   = confMatch ? parseInt(confMatch[1]) : 0

        const markAutoExecuted = () => setMessages(prev => {
          const next = [...prev]
          const idx  = next.findLastIndex(m => m.role === 'assistant')
          if (idx >= 0) next[idx] = { ...next[idx], autoExecuted: true }
          return next
        })

        // CLOSE — exit current position regardless of confidence
        if (isClose && !isLong && !isShort) {
          markAutoExecuted()
          const ok = await closePosition()
          if (opts?.silent && ok) {
            setAutoCycles(c => c + 1)
            lastTradedRef.current = 0  // reset so next analysis can re-enter immediately
            sessionStorage.setItem('aomi-last-traded', '0')
          }
          return ok
        }

        // LONG / SHORT — enter or flip at 60%+ confidence
        if ((isLong || isShort) && confNum >= 60) {
          const side = isLong ? 'long' : 'short'
          markAutoExecuted()
          const ok = await executeTrade(side)
          if (opts?.silent) {
            setAutoCycles(c => c + 1)
            if (ok) {
              setTradesPlaced(c => c + 1)
              lastTradedRef.current = Date.now()
              sessionStorage.setItem('aomi-last-traded', String(lastTradedRef.current))
            }
          }
          return ok
        }
      }

      if (opts?.silent) setAutoCycles(c => c + 1)
      return false

    } catch (err) {
      const errText = String(err)
      const isFatal = errText.includes('401') || errText.toLowerCase().includes('unauthorized')
      if (isFatal) fatalErrorRef.current = errText
      setMessages(prev => [
        ...prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
        { role: 'system', content: `Error: ${errText}` },
      ])
      return false
    } finally {
      sessionStorage.removeItem('aomi-processing')
      setProcessing(false)
      procRef.current = false
    }
  }, [btcPrice, account, sessionId, buildHint, executeTrade, autoCycles])

  useEffect(() => { sendRef.current = send }, [send])

  // ── Autonomous loop ───────────────────────────────────────────────────────
  // Analyze every 60s. After opening a position, hold for 2 min before re-evaluating.
  useEffect(() => {
    if (!autoMode || !historyLoaded) return
    let cancelled = false

    async function loop() {
      if (cancelled || !autoRef.current) return

      if (fatalErrorRef.current) {
        const reason = fatalErrorRef.current
        fatalErrorRef.current = null
        setAutoMode(false)
        setMessages(prev => [...prev, {
          role: 'system',
          content: `Auto mode stopped — ${reason.includes('401') ? 'auth error (HTTP 401).' : reason}`,
        }])
        return
      }

      if (sessionStorage.getItem('aomi-processing') === '1') {
        if (!cancelled) setTimeout(loop, 2000)
        return
      }

      // After opening a position, wait 2 min before re-evaluating (give the trade room)
      const msSinceTrade = Date.now() - lastTradedRef.current
      if (lastTradedRef.current > 0 && msSinceTrade < 120_000) {
        const wait = 120_000 - msSinceTrade
        if (!cancelled) setAutoWait({ until: Date.now() + wait, label: 'Holding position — next check in' })
        await new Promise<void>(resolve => {
          const t = setTimeout(resolve, wait)
          if (cancelled) { clearTimeout(t); resolve() }
        })
        if (cancelled) return
      }

      // 60s minimum between analyses
      const msSinceLast = Date.now() - lastAnalysisRef.current
      if (msSinceLast < 60_000 && lastAnalysisRef.current > 0) {
        const wait = 60_000 - msSinceLast
        if (!cancelled) setAutoWait({ until: Date.now() + wait, label: 'Next analysis in' })
        await new Promise<void>(resolve => {
          const t = setTimeout(resolve, wait)
          if (cancelled) { clearTimeout(t); resolve() }
        })
        if (cancelled) return
      }

      if (!procRef.current && sendRef.current) {
        setAutoWait(null)
        lastAnalysisRef.current = Date.now()
        sessionStorage.setItem('aomi-last-analysis', String(lastAnalysisRef.current))
        const traded = await sendRef.current(AUTO_PROMPT, { silent: true, autoExecute: true })
        if (cancelled) return
        if (traded) {
          // Wait 2 min after opening a position before re-evaluating
          const wait = 120_000
          if (!cancelled) { setAutoWait({ until: Date.now() + wait, label: 'Holding — next check in' }); setTimeout(loop, wait) }
          return
        }
      }

      // PASS / low confidence — retry in 60s
      if (!cancelled) setAutoWait({ until: Date.now() + 60_000, label: 'PASS — retrying in' })
      await new Promise<void>(resolve => {
        const t = setTimeout(resolve, 60_000)
        if (cancelled) { clearTimeout(t); resolve() }
      })
      loop()
    }

    loop()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoMode, historyLoaded])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); setInput('') }
  }

  const resetSession = () => {
    const env = window.location.hostname === 'localhost' ? 'local' : 'prod'
    localStorage.removeItem(`aomi-agent-session-${env}`)
    sessionStorage.removeItem('aomi-auto')
    sessionStorage.removeItem('aomi-last-analysis')
    sessionStorage.removeItem('aomi-last-traded')
    sessionStorage.removeItem('aomi-trades-placed')
    sessionStorage.removeItem('aomi-processing')
    const id = crypto.randomUUID()
    localStorage.setItem(`aomi-agent-session-${env}`, id)
    window.location.reload()
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)', display: 'flex', flexDirection: 'column' }}>
      <Header cycleId={autoCycles} isRunning={autoMode} />
      <MarketBar btcPrice={btcPrice} account={account} />

      {/* Auto mode bar */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 24px', borderBottom: '1px solid var(--border)',
        background: autoMode ? 'rgba(212,135,44,0.06)' : 'var(--bg-secondary)',
        transition: 'background 0.3s', gap: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            onClick={() => setAutoMode(m => !m)}
            disabled={processing && !autoMode}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '5px 16px', borderRadius: 20, cursor: processing && !autoMode ? 'not-allowed' : 'pointer',
              fontWeight: 700, fontSize: 12, transition: 'all 0.2s', border: 'none',
              background: autoMode ? 'var(--amber)' : 'var(--bg-card)',
              color: autoMode ? '#fff' : 'var(--text-muted)',
              outline: autoMode ? '1px solid transparent' : '1px solid var(--border)',
              boxShadow: autoMode ? '0 0 12px rgba(212,135,44,0.4)' : 'none',
            }}
          >
            {autoMode
              ? <><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#fff', animation: 'pulse-live 1s infinite', display: 'inline-block' }} /> AUTO ON</>
              : '⚡ Enable Auto Mode'}
          </button>
          {autoMode ? (
            <span style={{ fontSize: 11, color: autoWait && !processing ? 'var(--text-muted)' : 'var(--amber)', fontWeight: 600 }}>
              {autoWait && !processing
                ? <WaitCountdown label={autoWait.label} until={autoWait.until} />
                : `Querying Hyperliquid + searching · ${tradesPlaced} trade${tradesPlaced !== 1 ? 's' : ''} placed`}
            </span>
          ) : (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Continuous 24/7 loop · live HL data + search → analyze → execute
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, maxWidth: 340 }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600, whiteSpace: 'nowrap' }}>Risk per trade</span>
          <input
            type="range" min={1} max={50} value={riskPct}
            onChange={e => { const v = Number(e.target.value); setRiskPct(v); localStorage.setItem('aomi-risk-pct', String(v)) }}
            style={{ flex: 1, accentColor: 'var(--amber)', cursor: 'pointer' }}
          />
          <span style={{
            fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 800, minWidth: 40, textAlign: 'right',
            color: riskPct > 20 ? 'var(--pink-dark)' : riskPct > 10 ? 'var(--amber)' : 'var(--green-dark)',
          }}>{riskPct}%</span>
        </div>

        <button
          onClick={resetSession}
          style={{ fontSize: 11, color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px', whiteSpace: 'nowrap' }}
        >New session</button>
      </div>

      {/* Chat area */}
      <main style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        maxWidth: 820, width: '100%', margin: '0 auto',
        padding: '20px 24px 0', minHeight: 0,
      }}>
        <div ref={scrollRef} style={{
          flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column',
          gap: 14, paddingBottom: 20, maxHeight: 'calc(100vh - 290px)',
        }}>

          {/* Ready state */}
          {messages.length === 1 && !processing && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: '8px 0 4px' }}>

              {/* Live BTC card */}
              {btcPrice && (
                <div style={{
                  padding: '20px 24px', borderRadius: 16,
                  background: 'var(--bg-card)', border: '1px solid var(--border)',
                  display: 'flex', flexDirection: 'column', gap: 14,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Live Market</div>
                      <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 36, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--text-primary)' }}>
                        ${btcPrice.toLocaleString('en-US', { maximumFractionDigits: 1 })}
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                        BTC-PERP · Hyperliquid · {account ? `$${account.totalEquity.toFixed(2)} total equity` : 'loading account…'}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Position</div>
                      {account?.position ? (
                        <div style={{
                          padding: '8px 16px', borderRadius: 10, textAlign: 'center',
                          background: account.position.side === 'long' ? 'rgba(58,158,114,0.12)' : 'rgba(224,111,160,0.12)',
                          border: `1px solid ${account.position.side === 'long' ? 'rgba(58,158,114,0.3)' : 'rgba(224,111,160,0.3)'}`,
                        }}>
                          <div style={{ fontSize: 10, fontWeight: 700, color: account.position.side === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)', marginBottom: 2 }}>
                            {account.position.side.toUpperCase()}
                          </div>
                          <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 18, fontWeight: 800, color: account.position.side === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)' }}>
                            {account.position.sizeBTC.toFixed(4)}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>BTC</div>
                        </div>
                      ) : (
                        <div style={{
                          padding: '8px 16px', borderRadius: 10, textAlign: 'center',
                          background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                        }}>
                          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)' }}>FLAT</div>
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>no position</div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Feature cards */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                {[
                  { icon: '⬡', title: 'Live Hyperliquid data', desc: 'Pulls BTC price, order book depth, and your position state from Hyperliquid before every decision. No stale data.' },
                  { icon: '⊛', title: 'Continuous 24/7 loop', desc: 'Enable Auto Mode. I analyze every 90 seconds — Hyperliquid data + web search → verdict → execute → hold → repeat.' },
                  { icon: '⊕', title: 'Real orders. Real money.', desc: 'LONG / SHORT verdicts trigger live Hyperliquid perp orders. Kelly-sized from your actual equity.' },
                ].map(({ icon, title, desc }) => (
                  <div key={title} style={{
                    padding: '16px', borderRadius: 12,
                    background: 'var(--bg-card)', border: '1px solid var(--border)',
                  }}>
                    <div style={{ fontSize: 20, marginBottom: 8 }}>{icon}</div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>{title}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.5 }}>{desc}</div>
                  </div>
                ))}
              </div>

              {!btcPrice && (
                <div style={{ textAlign: 'center', padding: '32px 0', display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {[0,1,2].map(i => (
                      <span key={i} style={{
                        width: 8, height: 8, borderRadius: '50%', background: 'var(--text-muted)',
                        display: 'inline-block', animation: `dotbounce 1.4s ease-in-out ${i*0.25}s infinite`,
                      }} />
                    ))}
                  </div>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {resuming ? 'Analysis in progress — result will appear shortly…' : 'Connecting to Hyperliquid…'}
                  </span>
                </div>
              )}
            </div>
          )}

          {messages.map((msg, i) => {
            if (msg.role === 'tool') {
              return (
                <div key={i} style={{ display: 'flex', justifyContent: 'center' }}>
                  <ToolPill name={msg.toolName!} status={msg.toolStatus!} />
                </div>
              )
            }
            if (msg.role === 'system') {
              return (
                <div key={i} style={{ textAlign: 'center' }}>
                  <span style={{
                    display: 'inline-block', fontSize: 11, color: 'var(--text-muted)',
                    padding: '5px 14px', background: 'var(--bg-secondary)',
                    borderRadius: 20, border: '1px solid var(--border)',
                  }}>{msg.content}</span>
                </div>
              )
            }
            if (msg.role === 'user') {
              return (
                <div key={i} style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <div style={{
                    maxWidth: '72%', padding: '10px 16px', fontSize: 14, lineHeight: 1.5,
                    borderRadius: '18px 18px 4px 18px',
                    background: 'var(--blue)', color: '#fff', fontWeight: 500,
                  }}>{msg.content}</div>
                </div>
              )
            }
            const hasLong  = /\bLONG\b/i.test(msg.content)
            const hasShort = /\bSHORT\b/i.test(msg.content)
            const hasClose = /\bCLOSE\b/i.test(msg.content)
            return (
              <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                <div style={{
                  width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
                  background: 'linear-gradient(135deg, var(--blue), var(--green))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 12, fontWeight: 800, color: '#fff', marginTop: 2,
                }}>A</div>
                <div style={{
                  flex: 1, padding: '14px 16px', borderRadius: '4px 18px 18px 18px',
                  background: 'var(--bg-card)', border: '1px solid var(--border)',
                }}>
                  <BotMsg content={msg.content} autoExecuted={msg.autoExecuted} />
                  {!autoMode && (hasLong || hasShort || hasClose) && !msg.autoExecuted && (
                    <div style={{ display: 'flex', gap: 8, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                      {hasLong && (
                        <button onClick={() => executeTrade('long')} style={{
                          padding: '7px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
                          background: 'var(--green)', color: '#fff', fontWeight: 700, fontSize: 13,
                        }}>↑ Go Long</button>
                      )}
                      {hasShort && (
                        <button onClick={() => executeTrade('short')} style={{
                          padding: '7px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
                          background: 'var(--pink)', color: '#fff', fontWeight: 700, fontSize: 13,
                        }}>↓ Go Short</button>
                      )}
                      {hasClose && (
                        <button onClick={() => closePosition()} style={{
                          padding: '7px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
                          background: 'var(--amber)', color: '#fff', fontWeight: 700, fontSize: 13,
                        }}>✕ Close Position</button>
                      )}
                      <button onClick={() => setMessages(prev => {
                        const next = [...prev]
                        next[i] = { ...next[i], autoExecuted: true }
                        return next
                      })} style={{
                        padding: '7px 14px', borderRadius: 10, cursor: 'pointer',
                        background: 'transparent', border: '1px solid var(--border)',
                        color: 'var(--text-muted)', fontSize: 12, fontWeight: 600,
                      }}>Skip</button>
                    </div>
                  )}
                </div>
              </div>
            )
          })}

          {processing && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <div style={{
                width: 30, height: 30, borderRadius: '50%', flexShrink: 0,
                background: 'linear-gradient(135deg, var(--blue), var(--green))',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 12, fontWeight: 800, color: '#fff',
              }}>A</div>
              <div style={{ padding: '14px 16px', borderRadius: '4px 18px 18px 18px', background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', gap: 4 }}>
                  {[0,1,2].map(i => (
                    <span key={i} style={{
                      width: 6, height: 6, borderRadius: '50%', background: 'var(--text-muted)',
                      display: 'inline-block', animation: `dotbounce 1.2s ease-in-out ${i*0.2}s infinite`,
                    }} />
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Quick prompts */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '10px 0 8px' }}>
          {QUICK_PROMPTS.map(({ label, prompt }) => (
            <button key={label} onClick={() => send(prompt)} disabled={processing} style={{
              padding: '5px 13px', borderRadius: 20, fontSize: 12, fontWeight: 600,
              border: '1px solid var(--border)', background: 'var(--bg-card)',
              color: 'var(--text-secondary)', cursor: processing ? 'not-allowed' : 'pointer',
              opacity: processing ? 0.5 : 1, transition: 'all 0.15s',
            }}>{label}</button>
          ))}
        </div>

        {/* Input */}
        <div style={{ display: 'flex', gap: 8, padding: '8px 0 20px', borderTop: '1px solid var(--border)' }}>
          <textarea
            value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey}
            placeholder="Ask AOMI to analyze the market…  (Enter to send)"
            disabled={processing} rows={2}
            style={{
              flex: 1, padding: '11px 15px', borderRadius: 14,
              border: '1px solid var(--border)', background: 'var(--bg-card)',
              fontSize: 14, lineHeight: 1.5, resize: 'none', outline: 'none',
              color: 'var(--text-primary)', fontFamily: 'inherit',
            }}
          />
          {processing ? (
            <button onClick={handleStop} style={{
              padding: '0 20px', borderRadius: 14, border: 'none',
              background: 'var(--pink-dark)', color: '#fff',
              fontWeight: 700, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap',
            }}>Stop</button>
          ) : (
            <button onClick={() => { send(input); setInput('') }} disabled={!input.trim()} style={{
              padding: '0 20px', borderRadius: 14, border: 'none',
              background: input.trim() ? 'var(--blue)' : 'var(--bg-card)',
              color: input.trim() ? '#fff' : 'var(--text-muted)',
              fontWeight: 700, fontSize: 13, cursor: input.trim() ? 'pointer' : 'default',
              outline: input.trim() ? 'none' : '1px solid var(--border)',
              transition: 'all 0.15s',
            }}>Send</button>
          )}
        </div>
      </main>
    </div>
  )
}
