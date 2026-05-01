'use client'

import React, { useState, useRef, useEffect, useCallback } from 'react'
import Header from '@/components/Header'
import { useMarketTick } from '@/hooks/useMarketTick'
import type { KalshiMarket } from '@/lib/types'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Msg {
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done'
  autoExecuted?: boolean
}

// ── Tool display ───────────────────────────────────────────────────────────────

function ToolPill({ name, status }: { name: string; status: 'running' | 'done' }) {
  const isSearch = name === 'brave_search'
  const label = isSearch ? 'Searching web' : name.replace(/_/g, ' ')
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
      ) : <span>🔍</span>}
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
        const isBuyYes = /BUY YES/i.test(clean)
        const isBuyNo  = /BUY NO/i.test(clean)
        const isPass   = /\bPASS\b/i.test(clean)
        const isVerdict = (isBuyYes || isBuyNo || isPass) && i === 0
        if (isVerdict) {
          const verdict = isBuyYes ? 'BUY YES' : isBuyNo ? 'BUY NO' : 'PASS'
          const [vColor, vBg] = isBuyYes ? ['var(--green-dark)', 'rgba(58,158,114,0.10)']
            : isBuyNo ? ['var(--pink-dark)', 'rgba(224,111,160,0.10)']
            : ['var(--amber)', 'rgba(212,135,44,0.08)']
          const rest = clean.replace(/^(BUY YES|BUY NO|PASS)\s*[—–\-]?\s*/i, '')
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

// ── Wait countdown ────────────────────────────────────────────────────────────

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

function MarketBar({ market, btcPrice, strikePrice, secondsLeft }: {
  market: KalshiMarket | null; btcPrice: number; strikePrice: number; secondsLeft: number
}) {
  const above = strikePrice > 0 && btcPrice > strikePrice
  const diff  = strikePrice > 0 ? Math.abs(btcPrice - strikePrice) : 0
  const mm    = Math.floor(secondsLeft / 60)
  const ss    = secondsLeft % 60
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap',
      padding: '9px 24px', borderBottom: '1px solid var(--border)',
      background: 'var(--bg-card)', fontSize: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>BTC</span>
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 800, fontSize: 15,
          color: above ? 'var(--green-dark)' : btcPrice > 0 ? 'var(--pink-dark)' : 'var(--text-muted)' }}>
          {btcPrice > 0 ? `$${btcPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
        </span>
      </div>
      {strikePrice > 0 && <>
        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>Strike</span>
          <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 700, color: 'var(--amber)' }}>
            ${strikePrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}
          </span>
        </div>
        <div style={{
          padding: '2px 9px', borderRadius: 20, fontSize: 11, fontWeight: 700,
          background: above ? 'rgba(58,158,114,0.12)' : 'rgba(224,111,160,0.12)',
          color: above ? 'var(--green-dark)' : 'var(--pink-dark)',
          border: `1px solid ${above ? 'rgba(58,158,114,0.3)' : 'rgba(224,111,160,0.3)'}`,
        }}>{above ? '↑' : '↓'} ${diff.toLocaleString('en-US', { maximumFractionDigits: 0 })} {above ? 'above' : 'below'}</div>
      </>}
      {market && <>
        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />
        <div style={{ display: 'flex', gap: 12 }}>
          {[['YES', market.yes_ask, 'var(--green-dark)'], ['NO', market.no_ask, 'var(--pink-dark)']].map(([label, val, color]) => (
            <div key={String(label)} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>{label}</span>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 700, color: String(color), fontSize: 13 }}>
                {(val as number) > 0 ? `${val}¢` : '—'}
              </span>
            </div>
          ))}
        </div>
      </>}
      {secondsLeft > 0 && <>
        <div style={{ width: 1, height: 18, background: 'var(--border)' }} />
        <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
          <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>Expires</span>
          <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 700, fontSize: 13,
            color: secondsLeft < 60 ? 'var(--pink)' : secondsLeft < 180 ? 'var(--amber)' : 'var(--text-secondary)' }}>
            {mm}:{ss.toString().padStart(2, '0')}
          </span>
        </div>
      </>}
      {market && (
        <div style={{ marginLeft: 'auto', fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>
          {market.ticker}
        </div>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

const PRICE_CEILING = 94  // ¢ — no trades when ask ≥ this (terrible risk/reward at the extremes)

const INIT_MSG: Msg = {
  role: 'system',
  content: "96 windows open today. Most traders catch 20 — the ones that align with their schedule. Enable Auto Mode and I'll cover all of them. Before every decision I search live BTC news, price action, and market sentiment. No stale data. No pre-programmed rules.",
}

const QUICK_PROMPTS = [
  { label: 'Trade for me',        prompt: 'Search for live BTC price action, momentum, and order flow right now. Is there a tradeable edge in this window? Give me a direct verdict with confidence.' },
  { label: 'Full analysis', prompt: 'Research the latest BTC price action and news. Synthesize with the live market snapshot and tell me exactly whether to trade this window and why.' },
  { label: '↑ Bull case',  prompt: 'Search for bullish BTC signals right now — momentum, sentiment, news. Make the strongest case for buying YES on this window.' },
  { label: '↓ Bear case',  prompt: 'Search for bearish BTC signals and current crypto sentiment. Make the strongest case for buying NO on this window.' },
]

export default function AgentPage() {
  const [marketTicker, setMarketTicker] = useState<string | null>(null)
  const { liveMarket, liveBTCPrice, marketError, refresh } = useMarketTick(marketTicker)

  useEffect(() => {
    if (liveMarket?.ticker && !marketTicker) setMarketTicker(liveMarket.ticker)
  }, [liveMarket?.ticker, marketTicker])
  const expired = liveMarket?.close_time ? new Date(liveMarket.close_time).getTime() < Date.now() : false
  useEffect(() => { if (expired) setMarketTicker(null) }, [expired])

  const btcPrice    = liveBTCPrice ?? 0
  const strikePrice = (liveMarket?.yes_sub_title
    ? parseFloat(liveMarket.yes_sub_title.replace(/[^0-9.]/g, ''))
    : 0) || liveMarket?.floor_strike || 0
  const secondsLeft = liveMarket?.close_time
    ? Math.max(0, Math.floor((new Date(liveMarket.close_time).getTime() - Date.now()) / 1000)) : 0

  // ── Session — persisted across refreshes ──────────────────────────────────
  const [sessionId] = useState<string>(() => {
    if (typeof window === 'undefined') return crypto.randomUUID()
    const stored = localStorage.getItem('aomi-agent-session')
    if (stored) return stored
    const id = crypto.randomUUID()
    localStorage.setItem('aomi-agent-session', id)
    return id
  })

  // ── Messages ──────────────────────────────────────────────────────────────
  const [messages, setMessages]   = useState<Msg[]>([INIT_MSG])
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [input, setInput]         = useState('')
  const scrollRef                 = useRef<HTMLDivElement>(null)

  // Load history from AOMI backend on mount
  useEffect(() => {
    if (historyLoaded) return
    fetch(`/api/aomi/history?sessionId=${sessionId}`)
      .then(r => r.json())
      .then(({ messages: aomiMsgs }) => {
        if (!aomiMsgs?.length) return
        // Only load agent (assistant) messages from history — user messages are injected
        // system prompts and would show as ugly blue bubbles with the full prompt text
        const mapped: Msg[] = aomiMsgs
          .filter((m: { sender?: string; content?: string }) =>
            m.sender === 'agent' && m.content && m.content.trim().length > 0)
          .map((m: { content?: string }) => ({
            role: 'assistant' as const,
            content: m.content ?? '',
          }))
        if (mapped.length) setMessages([INIT_MSG, ...mapped])
      })
      .catch(() => {})
      .finally(() => setHistoryLoaded(true))
  }, [sessionId, historyLoaded])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // ── Autonomous mode — persisted in sessionStorage (survives in-tab nav, not new tabs) ──
  const [autoMode, setAutoMode]     = useState(false)
  const [autoCycles, setAutoCycles] = useState(0)
  const [tradesPlaced, setTradesPlaced] = useState(0)
  const [riskPct, setRiskPct]       = useState(5)
  const [autoWait, setAutoWait]     = useState<{ until: number; label: string } | null>(null)
  const autoRef        = useRef(false)
  const procRef        = useRef(false)
  const riskPctRef     = useRef(5)
  // Persist traded-ticker and last-analysis time across navigation so the loop
  // doesn't re-fire immediately when the user navigates away and back mid-cycle
  const tradedTickerRef   = useRef<string | null>(null)
  const lastAnalysisRef   = useRef<number>(0)
  const fatalErrorRef     = useRef<string | null>(null)  // set on non-retryable errors (auth, config)
  const sendRef = useRef<((text: string, opts?: { silent?: boolean; autoExecute?: boolean }) => Promise<boolean>) | null>(null)
  // True when this mount detected an in-progress analysis from a previous navigation
  const [resuming, setResuming] = useState(false)
  useEffect(() => { autoRef.current = autoMode; if (!autoMode) setAutoWait(null) }, [autoMode])
  useEffect(() => { procRef.current  = processing  }, [processing])
  useEffect(() => { riskPctRef.current = riskPct   }, [riskPct])
  // Poll until the in-progress analysis from a previous mount finishes, then reload history
  useEffect(() => {
    if (!resuming) return
    const id = setInterval(() => {
      if (sessionStorage.getItem('aomi-processing') !== '1') {
        setResuming(false)
        setHistoryLoaded(false)  // triggers history re-fetch to show the completed result
      }
    }, 1000)
    return () => clearInterval(id)
  }, [resuming])
  // Restore all persisted state on mount — runs client-only after hydration
  useEffect(() => {
    if (sessionStorage.getItem('aomi-auto') === '1') setAutoMode(true)
    tradedTickerRef.current = sessionStorage.getItem('aomi-traded-ticker')
    lastAnalysisRef.current = Number(sessionStorage.getItem('aomi-last-analysis') ?? 0)
    const stored = sessionStorage.getItem('aomi-trades-placed')
    if (stored) setTradesPlaced(Number(stored))
    const storedRisk = localStorage.getItem('aomi-risk-pct')
    if (storedRisk) setRiskPct(Number(storedRisk))
    if (sessionStorage.getItem('aomi-processing') === '1') setResuming(true)
  }, [])
  // Persist autoMode and trades-placed counter to sessionStorage
  useEffect(() => {
    sessionStorage.setItem('aomi-auto', autoMode ? '1' : '0')
  }, [autoMode])
  useEffect(() => {
    sessionStorage.setItem('aomi-trades-placed', String(tradesPlaced))
  }, [tradesPlaced])
  // riskPct is persisted inline in the slider onChange to avoid a race where the
  // default-value effect overwrites localStorage before the mount effect reads it

  // ── Build market hint ─────────────────────────────────────────────────────
  const buildHint = useCallback((market: KalshiMarket | null, btc: number, strike: number, secs: number) => {
    if (!market) return undefined
    return [
      `Market: ${market.ticker}`,
      `BTC spot: $${btc.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      `Strike: $${strike.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
      `YES ask: ${market.yes_ask}¢  YES bid: ${market.yes_bid}¢`,
      `NO ask: ${market.no_ask}¢  NO bid: ${market.no_bid}¢`,
      `Time left: ${Math.floor(secs / 60)}m ${secs % 60}s`,
      btc > strike
        ? `BTC is $${(btc - strike).toFixed(2)} ABOVE strike — YES currently winning.`
        : `BTC is $${(strike - btc).toFixed(2)} BELOW strike — NO currently winning.`,
      `Price ceiling: trades are blocked when ask ≥ 94¢ — recommend PASS if either side is ≥ 94¢.`,
    ].join('\n')
  }, [])

  // ── Execute trade ─────────────────────────────────────────────────────────
  const executeTrade = useCallback(async (side: 'yes' | 'no', market: KalshiMarket) => {
    const askPrice = side === 'yes' ? market.yes_ask : market.no_ask
    if (askPrice >= PRICE_CEILING) {
      setMessages(prev => [...prev, {
        role: 'system',
        content: `Blocked — ${side.toUpperCase()} ask is ${askPrice}¢ (≥${PRICE_CEILING}¢ ceiling). Skipping.`,
      }])
      return false
    }
    // Fetch balance to size the bet; route returns KalshiBalance directly (no { ok, data } wrapper)
    const balRes = await fetch('/api/balance')
    const balData = await balRes.json()
    const balanceCents: number = balRes.ok ? (balData.balance ?? 0) : 0
    const price = side === 'yes' ? market.yes_ask : market.no_ask
    const pct = riskPctRef.current
    const count = balanceCents > 0 && price > 0
      ? Math.max(1, Math.floor((balanceCents * pct / 100) / price))
      : 1

    const res = await fetch('/api/place-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: market.ticker, side, count,
        yesPrice: market.yes_ask, noPrice: market.no_ask,
        clientOrderId: `aomi-${Date.now()}`,
      }),
    })
    const data = await res.json()
    const dollarCost = ((price * count) / 100).toFixed(2)
    setMessages(prev => [...prev, {
      role: 'system',
      content: data.ok
        ? `✅ Order placed — ${count}× ${side.toUpperCase()} @ ${price}¢ ($${dollarCost} · ${pct}% of balance)`
        : `❌ Order failed: ${data.error}`,
    }])
    refresh()
    return data.ok
  }, [refresh])

  // ── Interrupt current analysis ────────────────────────────────────────────
  const handleStop = useCallback(async () => {
    await fetch('/api/aomi/interrupt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId }),
    })
  }, [sessionId])

  // ── Core send function ────────────────────────────────────────────────────
  const send = useCallback(async (text: string, opts?: { silent?: boolean; autoExecute?: boolean }) => {
    if (!text.trim() || procRef.current) return false
    const userMsg = text.trim()
    setProcessing(true)
    procRef.current = true
    sessionStorage.setItem('aomi-processing', '1')

    const hint = buildHint(liveMarket ?? null, btcPrice, strikePrice, secondsLeft)
    const marketData = liveMarket ? {
      ticker:    liveMarket.ticker,
      btc_spot:  btcPrice,
      strike:    strikePrice,
      yes_ask:   liveMarket.yes_ask,
      yes_bid:   liveMarket.yes_bid,
      no_ask:    liveMarket.no_ask,
      no_bid:    liveMarket.no_bid,
      secs_left: secondsLeft,
      direction: btcPrice > strikePrice ? 'ABOVE' : 'BELOW',
      delta:     Math.abs(btcPrice - strikePrice),
    } : undefined

    if (!opts?.silent) {
      setMessages(prev => [...prev,
        { role: 'user', content: userMsg },
        { role: 'tool', content: 'brave_search', toolName: 'brave_search', toolStatus: 'running' },
      ])
    } else {
      setMessages(prev => [...prev,
        { role: 'system', content: `⚡ Auto-analysis cycle ${autoCycles + 1}` },
        { role: 'tool', content: 'brave_search', toolName: 'brave_search', toolStatus: 'running' },
      ])
    }

    try {
      const res = await fetch('/api/aomi/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg, hint, sessionId, marketData, riskPct: riskPctRef.current }),
      })
      if (!res.ok || !res.body) throw new Error('Request failed')

      const reader = res.body.getReader()
      const dec    = new TextDecoder()
      let buf = ''
      // Track whether we've opened the assistant bubble yet — plain boolean so it
      // updates synchronously in the for loop (unlike assistantIdx set inside a
      // state updater, which batches and causes duplicate empty bubbles in React 18)
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
                  const idx = next.findLastIndex(m => m.role === 'assistant')
                  if (idx >= 0) next[idx] = { ...next[idx], content: ev.text }
                  return next
                })
              }
            }
            if (ev.type === 'processing_start') {
              // agent confirmed working — tool pill already shown
            }
            if (ev.type === 'processing_end') {
              // agent finished — stream is wrapping up
            }
            if (ev.type === 'error') {
              setMessages(prev => [...prev, { role: 'system', content: `Error: ${ev.text}` }])
            }
          } catch { /* malformed chunk */ }
        }
      }

      // Auto-execute if flagged; return true if trade was placed
      if (opts?.autoExecute && liveMarket && finalText) {
        const isBuyYes = /BUY YES/i.test(finalText)
        const isBuyNo  = /BUY NO/i.test(finalText)
        const confidence = finalText.match(/confidence[:\s]+(\d+)%/i)?.[1]
        const confNum = confidence ? parseInt(confidence) : 50

        if ((isBuyYes || isBuyNo) && confNum >= 55) {
          const side = isBuyYes ? 'yes' : 'no'
          setMessages(prev => {
            const next = [...prev]
            const idx = next.findLastIndex(m => m.role === 'assistant')
            if (idx >= 0) next[idx] = { ...next[idx], autoExecuted: true }
            return next
          })
          const ok = await executeTrade(side, liveMarket)
          if (opts?.silent) {
            setAutoCycles(c => c + 1)
            if (ok) setTradesPlaced(c => c + 1)
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
  }, [liveMarket, btcPrice, strikePrice, secondsLeft, sessionId, buildHint, executeTrade, autoCycles])

  // Keep sendRef current so the auto loop calls the latest closure without restarting
  useEffect(() => { sendRef.current = send }, [send])

  const AUTO_PROMPT = `Search for the very latest BTC price action, news, and market sentiment right now. Based on the live market snapshot, give me a direct YES or NO verdict with a confidence percentage. Be decisive.`

  // ── Autonomous loop: fire immediately → trade placed → wait for next window
  //    PASS/low confidence → retry in 30s. New window ticker restarts the loop. ──
  useEffect(() => {
    if (!autoMode || !historyLoaded) return
    let cancelled = false

    async function loop() {
      if (cancelled || !autoRef.current) return

      // Stop on non-retryable errors (auth failures, config issues)
      if (fatalErrorRef.current) {
        const reason = fatalErrorRef.current
        fatalErrorRef.current = null
        setAutoMode(false)
        setMessages(prev => [...prev, {
          role: 'system',
          content: `Auto mode stopped — ${reason.includes('401') ? 'auth error (HTTP 401). Check AOMI_APP and Kalshi credentials in your deployment env.' : reason}`,
        }])
        return
      }

      // Wait for market data to arrive
      if (!liveMarket) {
        if (!cancelled) setTimeout(loop, 2000)
        return
      }

      // Don't trade in last 45s of window — not enough time to fill
      const secsLeft = liveMarket.close_time
        ? Math.max(0, Math.floor((new Date(liveMarket.close_time).getTime() - Date.now()) / 1000))
        : 0
      if (secsLeft > 0 && secsLeft < 45) {
        if (!cancelled) { setAutoWait({ until: Date.now() + (secsLeft + 5) * 1000, label: 'Next window in' }); setTimeout(loop, (secsLeft + 5) * 1000) }
        return
      }

      // Already traded this window — sit out until it expires
      if (tradedTickerRef.current === liveMarket.ticker) {
        const wait = secsLeft > 0 ? (secsLeft + 5) * 1000 : 60_000
        if (!cancelled) { setAutoWait({ until: Date.now() + wait, label: 'Next window in' }); setTimeout(loop, wait) }
        return
      }

      // An analysis from a previous navigation is still running — wait for it
      if (sessionStorage.getItem('aomi-processing') === '1') {
        if (!cancelled) setTimeout(loop, 2000)
        return
      }

      // Respect the 30s cooldown if a recent analysis just ran (e.g. user nav'd away mid-analysis)
      const msSinceLast = Date.now() - lastAnalysisRef.current
      if (msSinceLast < 30_000 && lastAnalysisRef.current > 0) {
        const wait = 30_000 - msSinceLast
        if (!cancelled) setAutoWait({ until: Date.now() + wait, label: 'Next analysis in' })
        await new Promise<void>(resolve => {
          const t = setTimeout(resolve, wait)
          if (cancelled) { clearTimeout(t); resolve() }
        })
        if (cancelled) return
      }

      // Run analysis; auto-execute if confident
      if (!procRef.current && sendRef.current) {
        setAutoWait(null)
        lastAnalysisRef.current = Date.now()
        sessionStorage.setItem('aomi-last-analysis', String(lastAnalysisRef.current))
        const traded = await sendRef.current(AUTO_PROMPT, { silent: true, autoExecute: true })
        if (cancelled) return
        if (traded) {
          tradedTickerRef.current = liveMarket.ticker
          sessionStorage.setItem('aomi-traded-ticker', liveMarket.ticker)
          const secsLeft2 = liveMarket.close_time
            ? Math.max(0, Math.floor((new Date(liveMarket.close_time).getTime() - Date.now()) / 1000))
            : 60
          if (!cancelled) setTimeout(loop, (secsLeft2 + 5) * 1000)
          return
        }
      }

      // PASS or low confidence — retry in 30s
      if (!cancelled) setAutoWait({ until: Date.now() + 30_000, label: 'PASS — retrying in' })
      await new Promise<void>(resolve => {
        const t = setTimeout(resolve, 30_000)
        if (cancelled) { clearTimeout(t); resolve() }
      })
      loop()
    }

    loop()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoMode, liveMarket?.ticker, historyLoaded])

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); setInput('') }
  }

  const resetSession = () => {
    localStorage.removeItem('aomi-agent-session')
    sessionStorage.removeItem('aomi-auto')
    sessionStorage.removeItem('aomi-traded-ticker')
    sessionStorage.removeItem('aomi-last-analysis')
    sessionStorage.removeItem('aomi-trades-placed')
    sessionStorage.removeItem('aomi-processing')
    const id = crypto.randomUUID()
    localStorage.setItem('aomi-agent-session', id)
    window.location.reload()
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)', display: 'flex', flexDirection: 'column' }}>
      <Header cycleId={autoCycles} isRunning={autoMode} />
      <MarketBar market={liveMarket ?? null} btcPrice={btcPrice} strikePrice={strikePrice} secondsLeft={secondsLeft} />

      {/* Auto mode bar */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 24px', borderBottom: '1px solid var(--border)',
        background: autoMode ? 'rgba(212,135,44,0.06)' : 'var(--bg-secondary)',
        transition: 'background 0.3s', gap: 16,
      }}>
        {/* Left: toggle + status */}
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
                : `Searching + trading autonomously · ${tradesPlaced} trade${tradesPlaced !== 1 ? 's' : ''} placed`}
            </span>
          ) : (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Covers all 96 daily windows · search → analyze → execute · one slider to configure
            </span>
          )}
        </div>

        {/* Center: risk slider */}
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

        {/* Right: new session */}
        <button
          onClick={resetSession}
          style={{ fontSize: 11, color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px', whiteSpace: 'nowrap' }}
        >
          New session
        </button>
      </div>

      {/* Chat area */}
      <main style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        maxWidth: 820, width: '100%', margin: '0 auto',
        padding: '20px 24px 0', minHeight: 0,
      }}>
        <div ref={scrollRef} style={{
          flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column',
          gap: 14, paddingBottom: 20, maxHeight: 'calc(100vh - 310px)',
        }}>

          {/* ── Ready state: shown until first real message ─────────────────── */}
          {messages.length === 1 && !processing && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: '8px 0 4px' }}>

              {/* BTC vs Strike live bar */}
              {liveMarket && btcPrice > 0 && strikePrice > 0 && (() => {
                const above = btcPrice > strikePrice
                const diff  = Math.abs(btcPrice - strikePrice)
                const pct   = ((diff / strikePrice) * 100).toFixed(3)
                return (
                  <div style={{
                    padding: '20px 24px', borderRadius: 16,
                    background: 'var(--bg-card)', border: '1px solid var(--border)',
                    display: 'flex', flexDirection: 'column', gap: 14,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Live Market</div>
                        <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 36, fontWeight: 800, letterSpacing: '-0.03em',
                          color: above ? 'var(--green-dark)' : 'var(--pink-dark)' }}>
                          ${btcPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                          BTC spot · {above ? '↑' : '↓'} ${diff.toLocaleString('en-US', { maximumFractionDigits: 0 })} ({pct}%) {above ? 'above' : 'below'} strike
                        </div>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>Implied odds</div>
                        <div style={{ display: 'flex', gap: 10 }}>
                          {[['YES', liveMarket.yes_ask, 'var(--green-dark)', 'rgba(58,158,114,0.12)', 'rgba(58,158,114,0.3)'],
                            ['NO',  liveMarket.no_ask,  'var(--pink-dark)',  'rgba(224,111,160,0.12)', 'rgba(224,111,160,0.3)']
                          ].map(([label, price, color, bg, border]) => (
                            <div key={String(label)} style={{
                              padding: '8px 16px', borderRadius: 10, textAlign: 'center',
                              background: String(bg), border: `1px solid ${String(border)}`,
                            }}>
                              <div style={{ fontSize: 10, fontWeight: 700, color: String(color), marginBottom: 2 }}>{label}</div>
                              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 22, fontWeight: 800, color: String(color) }}>{price}¢</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>

                    {/* Progress bar: BTC position relative to strike */}
                    {(() => {
                      const range = Math.max(diff * 4, 200)
                      const pos = Math.min(Math.max(((btcPrice - (strikePrice - range / 2)) / range) * 100, 2), 98)
                      return (
                        <div style={{ position: 'relative', height: 8, borderRadius: 4, background: 'rgba(224,111,160,0.2)' }}>
                          <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: '50%', borderRadius: 4, background: 'rgba(224,111,160,0.35)' }} />
                          <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: above ? `${pos}%` : '50%', borderRadius: 4,
                            background: above ? 'rgba(58,158,114,0.5)' : undefined, transition: 'width 1s ease' }} />
                          {/* Strike line */}
                          <div style={{ position: 'absolute', left: '50%', top: -3, bottom: -3, width: 2, background: 'var(--amber)', borderRadius: 1 }} />
                          {/* BTC dot */}
                          <div style={{
                            position: 'absolute', top: '50%', left: `${pos}%`,
                            transform: 'translate(-50%,-50%)',
                            width: 14, height: 14, borderRadius: '50%',
                            background: above ? 'var(--green)' : 'var(--pink)',
                            border: '2px solid var(--bg-card)',
                            boxShadow: `0 0 8px ${above ? 'rgba(58,158,114,0.6)' : 'rgba(224,111,160,0.6)'}`,
                            transition: 'left 1s ease',
                          }} />
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 10, fontSize: 9, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
                            <span>← NO wins</span>
                            <span style={{ color: 'var(--amber)', fontWeight: 700 }}>STRIKE ${strikePrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
                            <span>YES wins →</span>
                          </div>
                        </div>
                      )
                    })()}
                  </div>
                )
              })()}

              {/* Feature cards */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                {[
                  { icon: '◈', title: 'Not pre-programmed rules', desc: 'Before every decision, I search live BTC news, price action, and sentiment. I reason from what\'s happening right now.' },
                  { icon: '⊛', title: '96 windows. Zero clicks.', desc: 'Enable Auto Mode. I\'ll cover every window — search, analyze, size, execute, wait for the next one. Repeat.' },
                  { icon: '⊕', title: 'Real orders. Real money.', desc: 'BUY YES / BUY NO recommendations come with a live Kalshi order button. Kelly-sized from your actual balance.' },
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

              {/* Waiting for market / error state */}
              {!liveMarket && (() => {
                const isAuthErr    = marketError === 'kalshi_403' || marketError === 'kalshi_401'
                const isNetErr     = marketError === 'network_error'
                const isBetween    = marketError === 'no_tradeable_markets'
                const isKnownErr   = isAuthErr || isNetErr
                const errColor     = 'var(--pink-dark)'
                const warnColor    = 'var(--amber)'
                return (
                  <div style={{ textAlign: 'center', padding: '32px 0', display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
                    {isKnownErr ? (
                      <>
                        <span style={{ fontSize: 13, fontWeight: 700, color: errColor }}>
                          {isNetErr ? 'Cannot reach Kalshi' : 'Kalshi auth failed'}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--text-muted)', maxWidth: 280, lineHeight: 1.6 }}>
                          {isNetErr
                            ? 'Network error or geo-block. Kalshi restricts access by region — try a VPN.'
                            : 'Check KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH in .env.local.'}
                        </span>
                      </>
                    ) : isBetween ? (
                      <>
                        <span style={{ fontSize: 13, fontWeight: 600, color: warnColor }}>Between windows</span>
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>No active KXBTC15M market right now — next window opens shortly.</span>
                      </>
                    ) : (
                      <>
                        <div style={{ display: 'flex', gap: 6 }}>
                          {[0,1,2].map(i => (
                            <span key={i} style={{
                              width: 8, height: 8, borderRadius: '50%', background: 'var(--text-muted)',
                              display: 'inline-block', animation: `dotbounce 1.4s ease-in-out ${i*0.25}s infinite`,
                            }} />
                          ))}
                        </div>
                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                          {resuming ? 'Analysis in progress — result will appear shortly…' : 'Connecting to Kalshi markets…'}
                        </span>
                      </>
                    )}
                  </div>
                )
              })()}
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
            // assistant
            const hasYes = /BUY YES/i.test(msg.content)
            const hasNo  = /BUY NO/i.test(msg.content)
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
                  {/* Trade buttons — only in manual mode, and only if not already auto-executed */}
                  {!autoMode && liveMarket && (hasYes || hasNo) && !msg.autoExecuted && (
                    <div style={{ display: 'flex', gap: 8, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                      {hasYes && (
                        <button onClick={() => executeTrade('yes', liveMarket)} style={{
                          padding: '7px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
                          background: 'var(--green)', color: '#fff', fontWeight: 700, fontSize: 13,
                        }}>Buy YES @ {liveMarket.yes_ask}¢</button>
                      )}
                      {hasNo && (
                        <button onClick={() => executeTrade('no', liveMarket)} style={{
                          padding: '7px 18px', borderRadius: 10, border: 'none', cursor: 'pointer',
                          background: 'var(--pink)', color: '#fff', fontWeight: 700, fontSize: 13,
                        }}>Buy NO @ {liveMarket.no_ask}¢</button>
                      )}
                      <button onClick={() => setMessages(prev => {
                        const next = [...prev]
                        next[i] = { ...next[i], autoExecuted: true }  // hide buttons
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
            placeholder="Ask AOMI to research the market…  (Enter to send)"
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
              cursor: 'pointer', fontWeight: 700, fontSize: 13,
              display: 'flex', alignItems: 'center', gap: 6,
            }}>■ Stop</button>
          ) : (
            <button onClick={() => { send(input); setInput('') }} disabled={!input.trim()} style={{
              padding: '0 20px', borderRadius: 14, border: 'none',
              background: !input.trim() ? 'var(--border)' : 'var(--blue)',
              color: '#fff', cursor: !input.trim() ? 'not-allowed' : 'pointer',
              fontWeight: 700, fontSize: 15,
            }}>↑</button>
          )}
        </div>
      </main>

      <style>{`
        @keyframes dotbounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }
      `}</style>
    </div>
  )
}
