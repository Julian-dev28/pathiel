'use client'

import React, { useState, useRef, useEffect, useCallback } from 'react'
import Header from '@/components/Header'
import { useHLTick } from '@/hooks/useHLTick'
import type { HLAccount } from '@/lib/hyperliquid'

interface Msg {
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  toolName?: string
  toolStatus?: 'running' | 'done'
  autoExecuted?: boolean
  ts?: number
}

interface TradeRecord {
  id:         string
  side:       'long' | 'short'
  sizeBTC:    number
  entryPrice: number
  exitPrice?: number
  pnl?:       number
  openedAt:   number
  closedAt?:  number
}

const HL_TOOLS: Record<string, string> = {
  get_all_mids:            'Fetching price',
  get_l2_book:             'Order book depth',
  get_clearinghouse_state: 'Reading position',
  get_open_orders:         'Open orders',
  get_user_fills:          'Trade history',
  get_funding_history:     'Funding rate',
  get_candle_snapshot:     'Candle data',
  get_meta:                'Exchange info',
  brave_search:            'Web search',   // Brave Search — requires BRAVE_API_KEY
}

function exportTradeLog(trades: TradeRecord[]) {
  const headers = ['id', 'side', 'sizeBTC', 'entryPrice', 'exitPrice', 'pnlUsd', 'pnlPctOnNotional', 'openedAt', 'closedAt', 'holdSeconds', 'status']
  const rows = trades.map(t => {
    const notional = t.entryPrice * t.sizeBTC
    const pnlPct   = t.pnl !== undefined && notional > 0 ? (t.pnl / notional * 100).toFixed(4) : ''
    const holdSec  = t.closedAt ? Math.round((t.closedAt - t.openedAt) / 1000) : ''
    const status   = !t.closedAt ? 'open' : (t.pnl ?? 0) > 0 ? 'win' : 'loss'
    return [
      t.id,
      t.side,
      t.sizeBTC,
      t.entryPrice,
      t.exitPrice ?? '',
      t.pnl?.toFixed(2) ?? '',
      pnlPct,
      new Date(t.openedAt).toISOString(),
      t.closedAt ? new Date(t.closedAt).toISOString() : '',
      holdSec,
      status,
    ]
  })
  const csv = [headers, ...rows].map(r => r.map(v => {
    const s = String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url
  a.download = `aomi-trades-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function WaitCountdown({ until }: { until: number }) {
  const [secs, setSecs] = useState(() => Math.max(0, Math.ceil((until - Date.now()) / 1000)))
  useEffect(() => {
    const id = setInterval(() => setSecs(Math.max(0, Math.ceil((until - Date.now()) / 1000))), 500)
    return () => clearInterval(id)
  }, [until])
  const m = Math.floor(secs / 60), s = secs % 60
  return <span style={{ fontFamily: 'var(--font-geist-mono)' }}>{m > 0 ? `${m}m ${s}s` : `${s}s`}</span>
}

const SCAN_CANDLES = [
  { h: 88, l: 18, o: 28, c: 82, bull: true  },
  { h: 82, l: 44, o: 78, c: 48, bull: false },
  { h: 74, l: 28, o: 46, c: 70, bull: true  },
  { h: 94, l: 52, o: 68, c: 90, bull: true  },
  { h: 86, l: 38, o: 83, c: 52, bull: false },
  { h: 68, l: 32, o: 36, c: 63, bull: true  },
  { h: 78, l: 40, o: 74, c: 44, bull: false },
  { h: 90, l: 55, o: 58, c: 86, bull: true  },
  { h: 84, l: 48, o: 80, c: 60, bull: false },
  { h: 97, l: 62, o: 65, c: 94, bull: true  },
]

function CandleScan({ activeTool }: { activeTool?: string }) {
  const W = 200, H = 72
  const spacing = W / SCAN_CANDLES.length
  const bw = 9
  return (
    <div style={{
      padding: '12px 14px', borderRadius: 10,
      background: 'rgba(74,127,165,0.04)',
      border: '1px solid rgba(74,127,165,0.14)',
    }}>
      <div style={{ position: 'relative', marginBottom: 10, overflow: 'hidden', borderRadius: 6 }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} height={H} style={{ display: 'block' }}>
          {SCAN_CANDLES.map((c, i) => {
            const x     = i * spacing + spacing / 2
            const color = c.bull ? '#2E9E68' : '#BE4A40'
            const yH    = H - (c.h / 100) * H
            const yL    = H - (c.l / 100) * H
            const yO    = H - (c.o / 100) * H
            const yC    = H - (c.c / 100) * H
            const byTop = Math.min(yO, yC)
            const byH   = Math.max(2, Math.abs(yO - yC))
            return (
              <g key={i} style={{
                animation: `bar-rise 0.45s ease-out ${i * 0.055}s both`,
                transformOrigin: `${x}px ${H}px`,
              }}>
                <line x1={x} y1={yH} x2={x} y2={yL} stroke={color} strokeWidth="1" opacity="0.45" />
                <rect x={x - bw / 2} y={byTop} width={bw} height={byH} fill={color} opacity="0.88" rx="1" />
              </g>
            )
          })}
        </svg>
        <div style={{
          position: 'absolute', top: 0, bottom: 0, width: 2,
          background: 'linear-gradient(to bottom, transparent, rgba(74,127,165,0.65), transparent)',
          animation: 'scanLine 2.4s linear infinite',
          pointerEvents: 'none',
        }} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        {[0, 1, 2].map(i => (
          <span key={i} style={{
            width: 4, height: 4, borderRadius: '50%', background: 'var(--blue)',
            display: 'inline-block',
            animation: `dotbounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }} />
        ))}
        <span style={{
          fontFamily: 'var(--font-geist-mono)', fontSize: 11, fontWeight: 700, color: 'var(--blue)',
        }}>
          {activeTool ? (HL_TOOLS[activeTool] ?? activeTool) : 'scanning market'}…
        </span>
      </div>
    </div>
  )
}

const AUTO_PROMPT = `You are evaluating a BTC-PERP swing trade. Robust backtested rule set — profitable on 90/180/365d BTC windows after fees, slippage, and funding (53-62% WR, PF 1.13-1.52). Follow precisely. Call tools in this order:
1. get_clearinghouse_state — CHECK IF IN A POSITION FIRST. Note side, size, entry price, unrealized PnL, time in position.
2. get_candle_snapshot interval="1d" count=24 — daily trend (anchor). Compute mental EMA(8), EMA(21), and EMA(8) slope (now vs 2 bars ago).
3. get_candle_snapshot interval="4h" count=24 — 4h entry timing. Compute mental EMA(20), 14-bar RSI, 14-bar ATR, 20-bar avg volume.
4. get_l2_book — bid vs ask pressure (confirmation, not primary).
5. get_all_mids — confirm current BTC price.
6. get_funding_history — extreme funding (>±0.05%/8h) shifts edge against the crowd.
7. brave_search (only if steps 1-6 are setting up a LONG or SHORT, not for PASS) — query "BTC bitcoin news today" or "<event> <today>". Veto entry on adverse news only (see CLOSE rules).

Trend gate (DAILY, STRICT — no trades during range):
- UP: 1d EMA(8) > EMA(21) AND last daily close > EMA(21) AND EMA(8) slope rising.
- DOWN: 1d EMA(8) < EMA(21) AND last daily close < EMA(21) AND EMA(8) slope falling.
- Otherwise: PASS.

Entry trigger (4h close, must align with daily trend):
- LONG: prior 4h dipped to/below 4h EMA(20), current 4h closed back above EMA(20) AND green AND RSI < 70 AND volume ≥ 80% of 20-bar avg.
- SHORT: mirror.
- Confidence ≥ 60%. No setup → PASS.

News veto (only run when 1-6 already point to LONG/SHORT — don't waste calls on PASS):
- brave_search top results for major adverse catalysts: FOMC/CPI within ~6h, US regulation, exchange hack, mass liquidation cascade.
- If found → downgrade to PASS regardless of TA setup. Note the catalyst in your output.
- Routine commentary / price recap headlines are NOT a veto — only acute event risk.

Brackets are auto-placed on Hyperliquid the moment your entry fills:
- Hard stop at entry ± 3.5 × 4h ATR (wide enough to ride 4h noise without getting wicked out).
- Single TP at entry ± 1.0 × 4h ATR (full position closes when reached).

CLOSE rules (rare — brackets handle 95% of exits):
- Daily trend flips to OPPOSITE direction → CLOSE.
- Clear daily structural break (lower-low on long / higher-high on short) → CLOSE.
- Otherwise PASS. Most trades resolve within hours-to-days via TP1/TP2.

Output format: first line "LONG X%" / "SHORT X%" / "CLOSE X%" / "PASS X%". Then 3-5 bullets: daily EMA(8/21) state + slope, 4h EMA(20) pullback+reclaim, RSI + volume, bracket plan if entering OR position progress if holding.`

const INIT_MSG: Msg = { role: 'system', content: 'Awaiting first cycle…', ts: Date.now() }

// Bump this whenever the strategy / prompt format changes — invalidates cached analysis text
// so users don't see stale "PASS 0%" / old-format bullets after a deploy.
const STRATEGY_VERSION = '2026-05-08-robust-daily-4h-news'

export default function AgentPage() {
  const { btcPrice, account, refreshAccount } = useHLTick()

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

  const [mounted, setMounted]             = useState(false)
  const [messages, setMessages]           = useState<Msg[]>([INIT_MSG])
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [processing, setProcessing]       = useState(false)
  const [editingRisk, setEditingRisk]     = useState(false)
  const scrollRef                         = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (historyLoaded) return
    fetch(`/api/aomi/history?sessionId=${sessionId}`)
      .then(r => r.json())
      .then(({ messages: m }) => {
        if (!m?.length) return
        const mapped: Msg[] = m
          .filter((x: { sender?: string; content?: string }) => x.sender === 'agent' && x.content?.trim())
          .map((x: { content?: string }) => ({ role: 'assistant' as const, content: x.content ?? '', ts: Date.now() }))
        if (mapped.length) setMessages([INIT_MSG, ...mapped])
      })
      .catch(() => {})
      .finally(() => setHistoryLoaded(true))
  }, [sessionId, historyLoaded])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // ── Trade log ─────────────────────────────────────────────────────────────
  const [tradeLog, setTradeLog] = useState<TradeRecord[]>([])
  const openTradeRef  = useRef<TradeRecord | null>(null)
  const posReconciled = useRef(false)
  const sessionPnL    = tradeLog.reduce((s, t) => s + (t.pnl ?? 0), 0)
  const closedTrades  = tradeLog.filter(t => t.closedAt)
  const wins          = closedTrades.filter(t => (t.pnl ?? 0) > 0).length

  // ── Auto mode ─────────────────────────────────────────────────────────────
  const [autoMode, setAutoMode]         = useState(false)
  const [autoCycles, setAutoCycles]     = useState(0)
  const [tradesPlaced, setTradesPlaced] = useState(0)
  const [riskPct, setRiskPct]           = useState(5)
  const [leverage, setLeverage]         = useState(5)
  const [autoWait, setAutoWait]         = useState<{ until: number; label: string } | null>(null)
  const [lastVerdict, setLastVerdict]   = useState<string | null>(null)
  const autoRef         = useRef(false)
  const procRef         = useRef(false)
  const riskPctRef      = useRef(5)
  const leverageRef     = useRef(5)
  const lastAnalysisRef = useRef<number>(0)
  const lastTradedRef   = useRef<number>(0)
  const fatalErrorRef   = useRef<string | null>(null)
  const sendRef         = useRef<((text: string, opts?: { silent?: boolean; autoExecute?: boolean }) => Promise<boolean>) | null>(null)
  const abortRef        = useRef<AbortController | null>(null)
  const [resuming, setResuming]   = useState(false)
  const [threads, setThreads]     = useState<Array<{ session_id: string; title: string }>>([])

  useEffect(() => {
    const pk = process.env.NEXT_PUBLIC_HL_MASTER
    if (!pk) return
    fetch(`/api/aomi/threads?publicKey=${pk}`)
      .then(r => r.json())
      .then(({ threads: t }) => { if (Array.isArray(t) && t.length) setThreads(t) })
      .catch(() => {})
  }, [])

  useEffect(() => { autoRef.current = autoMode; if (!autoMode) setAutoWait(null) }, [autoMode])
  useEffect(() => { procRef.current = processing }, [processing])
  useEffect(() => { riskPctRef.current = riskPct }, [riskPct])
  useEffect(() => { leverageRef.current = leverage }, [leverage])

  useEffect(() => {
    if (!resuming) return
    const id = setInterval(() => {
      if (sessionStorage.getItem('aomi-processing') !== '1') { setResuming(false); setHistoryLoaded(false) }
    }, 1000)
    return () => clearInterval(id)
  }, [resuming])

  useEffect(() => {
    if (sessionStorage.getItem('aomi-auto') === '1') setAutoMode(true)
    lastAnalysisRef.current = Number(sessionStorage.getItem('aomi-last-analysis') ?? 0)
    lastTradedRef.current   = Number(sessionStorage.getItem('aomi-last-traded') ?? 0)
    const stored = sessionStorage.getItem('aomi-trades-placed')
    if (stored) setTradesPlaced(Number(stored))
    const storedCycles = sessionStorage.getItem('aomi-auto-cycles')
    if (storedCycles) setAutoCycles(Number(storedCycles))
    const storedVerdict = sessionStorage.getItem('aomi-last-verdict')
    if (storedVerdict) setLastVerdict(storedVerdict)
    const storedTrades = sessionStorage.getItem('aomi-trade-log')
    if (storedTrades) { try { setTradeLog(JSON.parse(storedTrades)) } catch { /* ignore */ } }
    openTradeRef.current = (() => { try { const s = sessionStorage.getItem('aomi-open-trade'); return s ? JSON.parse(s) as TradeRecord : null } catch { return null } })()
    const storedRisk = localStorage.getItem('aomi-risk-pct')
    if (storedRisk) setRiskPct(Number(storedRisk))
    const storedLeverage = localStorage.getItem('aomi-leverage')
    if (storedLeverage) setLeverage(Number(storedLeverage))
    if (sessionStorage.getItem('aomi-processing') === '1') setResuming(true)
    // Invalidate cached analysis text when the strategy version bumps (prevents stale prompt-format display after deploys)
    const storedVersion = sessionStorage.getItem('aomi-strategy-version')
    if (storedVersion !== STRATEGY_VERSION) {
      sessionStorage.removeItem('aomi-last-analysis-text')
      sessionStorage.removeItem('aomi-last-verdict')
      sessionStorage.setItem('aomi-strategy-version', STRATEGY_VERSION)
    } else {
      const storedText = sessionStorage.getItem('aomi-last-analysis-text')
      if (storedText) setMessages([INIT_MSG, { role: 'assistant', content: storedText, ts: Date.now() }])
    }
    setMounted(true)
  }, [])

  useEffect(() => { if (!mounted) return; sessionStorage.setItem('aomi-auto', autoMode ? '1' : '0') }, [autoMode, mounted])
  useEffect(() => { if (!mounted) return; sessionStorage.setItem('aomi-trades-placed', String(tradesPlaced)) }, [tradesPlaced, mounted])
  useEffect(() => { if (!mounted || !lastVerdict) return; sessionStorage.setItem('aomi-last-verdict', lastVerdict) }, [lastVerdict, mounted])
  useEffect(() => { if (!mounted) return; sessionStorage.setItem('aomi-auto-cycles', String(autoCycles)) }, [autoCycles, mounted])
  useEffect(() => { if (!mounted) return; sessionStorage.setItem('aomi-trade-log', JSON.stringify(tradeLog)) }, [tradeLog, mounted])

  // Reconcile trade log from sessionStorage ref or live position
  useEffect(() => {
    if (posReconciled.current) return

    // openTradeRef restored from sessionStorage but tradeLog state is empty — sync them
    const openTrade = openTradeRef.current
    if (openTrade && tradeLog.length === 0) {
      posReconciled.current = true
      setTradeLog([openTrade])
      return
    }

    if (tradeLog.length > 0) { posReconciled.current = true; return }

    // Nothing in local state — bootstrap from live Hyperliquid position
    if (!account?.position) return
    posReconciled.current = true
    const p = account.position
    const r: TradeRecord = { id: crypto.randomUUID(), side: p.side, sizeBTC: p.sizeBTC, entryPrice: p.entryPx, openedAt: Date.now() }
    openTradeRef.current = r
    sessionStorage.setItem('aomi-open-trade', JSON.stringify(r))
    setTradeLog([r])
  }, [account, tradeLog])

  // If HL reports flat but we still have an Open trade locally, it closed externally (TP/SL or manual). Mark it closed.
  useEffect(() => {
    if (!account || !btcPrice) return
    if (account.position) return
    const open = openTradeRef.current
    if (!open) return
    if (Date.now() - open.openedAt < 30_000) return // grace window for HL data lag after entry
    const exitPrice = btcPrice
    const pnl = open.side === 'long'
      ? (exitPrice - open.entryPrice) * open.sizeBTC
      : (open.entryPrice - exitPrice) * open.sizeBTC
    setTradeLog(prev => prev.map(t => t.id === open.id ? { ...open, exitPrice, pnl, closedAt: Date.now() } : t))
    openTradeRef.current = null
    sessionStorage.removeItem('aomi-open-trade')
    sessionStorage.setItem('aomi-last-traded', '0')
    lastTradedRef.current = 0
  }, [account, btcPrice])

  const buildHint = useCallback((price: number | null, acct: HLAccount | null) => {
    if (!price) return undefined
    const pos = acct?.position
    return [
      `BTC-PERP mid price: $${price.toLocaleString('en-US', { maximumFractionDigits: 1 })}`,
      `Master account: ${process.env.NEXT_PUBLIC_HL_MASTER ?? 'see env'} — use for get_clearinghouse_state.`,
      `Available capital: $${(acct?.spotUSDC ?? 0).toFixed(2)} spot USDC (auto-transfers to perp on execution — never treat $0 perp equity as a blocker)`,
      pos
        ? `Position: ${pos.side.toUpperCase()} ${pos.sizeBTC.toFixed(4)} BTC @ $${pos.entryPx.toLocaleString('en-US', { maximumFractionDigits: 0 })} · PnL: ${pos.unrealizedPnl >= 0 ? '+' : ''}${pos.unrealizedPnl.toFixed(2)}`
        : 'Position: FLAT',
    ].join('\n')
  }, [])

  const closePosition = useCallback(async () => {
    const res  = await fetch('/api/hl/close-position', { method: 'POST' })
    const data = await res.json() as { ok: boolean; sizeBTC?: number; midPrice?: number; error?: string }
    if (data.ok && openTradeRef.current) {
      const open = openTradeRef.current
      const exitPrice = data.midPrice ?? 0
      const pnl = open.side === 'long' ? (exitPrice - open.entryPrice) * open.sizeBTC : (open.entryPrice - exitPrice) * open.sizeBTC
      setTradeLog(prev => prev.map(t => t.id === open.id ? { ...open, exitPrice, pnl, closedAt: Date.now() } : t))
      openTradeRef.current = null
      sessionStorage.removeItem('aomi-open-trade')
    }
    setMessages(prev => [...prev, { role: 'system', ts: Date.now(), content: data.ok ? `✅ Closed ${(data.sizeBTC ?? 0).toFixed(5)} BTC @ $${(data.midPrice ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : `❌ Close failed: ${data.error}` }])
    refreshAccount()
    return data.ok
  }, [refreshAccount])

  const executeTrade = useCallback(async (side: 'long' | 'short') => {
    const res  = await fetch('/api/hl/place-order', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ side, riskPct: riskPctRef.current, leverage: leverageRef.current }),
    })
    const data = await res.json() as {
      ok: boolean; error?: string; sizeBTC?: number; midPrice?: number; leverage?: number
      brackets?: { ok: boolean; sl?: string; tp1?: string; tp2?: string; atr: number; error?: string }
    }
    if (data.ok) {
      const record: TradeRecord = { id: crypto.randomUUID(), side, sizeBTC: data.sizeBTC ?? 0, entryPrice: data.midPrice ?? 0, openedAt: Date.now() }
      openTradeRef.current = record
      sessionStorage.setItem('aomi-open-trade', JSON.stringify(record))
      setTradeLog(prev => [...prev, record])
    }
    const bracketLine = data.brackets
      ? data.brackets.ok
        ? `\n  brackets: SL ${data.brackets.sl} · TP1 ${data.brackets.tp1} · TP2 ${data.brackets.tp2}  (ATR ${data.brackets.atr.toFixed(0)})`
        : `\n  ⚠ brackets partial: SL ${data.brackets.sl ?? '—'} · TP1 ${data.brackets.tp1 ?? '—'} · TP2 ${data.brackets.tp2 ?? '—'}`
      : ''
    setMessages(prev => [...prev, { role: 'system', ts: Date.now(), content: data.ok ? `✅ ${side === 'long' ? '↑ LONG' : '↓ SHORT'} ${(data.sizeBTC ?? 0).toFixed(5)} BTC @ $${(data.midPrice ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · ${data.leverage ?? 5}× · ${riskPctRef.current}% risk${bracketLine}` : `❌ Order failed: ${data.error}` }])
    refreshAccount()
    return data.ok
  }, [refreshAccount])

  const send = useCallback(async (text: string, opts?: { silent?: boolean; autoExecute?: boolean }) => {
    if (!text.trim() || procRef.current) return false
    setProcessing(true)
    procRef.current = true
    sessionStorage.setItem('aomi-processing', '1')

    const hint       = buildHint(btcPrice, account)
    const marketData = btcPrice ? { btc_price: btcPrice, equity: account?.equity ?? 0, position: account?.position ?? null } : undefined

    if (!opts?.silent) {
      setMessages(prev => [...prev, { role: 'user', content: text.trim(), ts: Date.now() }])
    }

    try {
      const controller = new AbortController()
      abortRef.current = controller
      const res = await fetch('/api/aomi/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text.trim(), hint, sessionId, marketData, riskPct: riskPctRef.current }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error('Request failed')

      const reader = res.body.getReader()
      const dec    = new TextDecoder()
      let buf = '', assistantStarted = false, finalText = ''

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
                if (last >= 0) next[last] = { ...next[last], toolName: ev.name, toolStatus: ev.status }
                else next.push({ role: 'tool', content: ev.name, toolName: ev.name, toolStatus: ev.status, ts: Date.now() })
                return next
              })
            }
            if (ev.type === 'message') {
              finalText = ev.text
              if (!assistantStarted) {
                assistantStarted = true
                setMessages(prev => [
                  ...prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
                  { role: 'assistant', content: ev.text, ts: Date.now() },
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
            if (ev.type === 'error') setMessages(prev => [...prev, { role: 'system', content: `Error: ${ev.text}`, ts: Date.now() }])
          } catch { /* malformed chunk */ }
        }
      }

      if (finalText) sessionStorage.setItem('aomi-last-analysis-text', finalText)

      if (opts?.autoExecute && finalText) {
        const rawLine = finalText.split('\n').find(l => l.trim())?.trim() ?? ''
        const firstLine = rawLine.replace(/^[^a-zA-Z]+/, '').trim()
        const isLong  = /^LONG\b/i.test(firstLine)
        const isShort = /^SHORT\b/i.test(firstLine)
        const isClose = /^CLOSE\b/i.test(firstLine)
        // Match both inline ("PASS 80%") and labeled ("Confidence: 80%") formats
        const inlineMatch   = finalText.match(/^[\s•\-*]*(?:LONG|SHORT|CLOSE|PASS)[\s—–\-]+(\d+)\s*%/im)
        const labeledMatch  = finalText.match(/confidence[^:]*:?\s*(\d+)\s*%/i)
        const confNum       = inlineMatch ? parseInt(inlineMatch[1]) : labeledMatch ? parseInt(labeledMatch[1]) : 0

        const v = isClose ? 'CLOSE' : isLong ? 'LONG' : isShort ? 'SHORT' : 'PASS'
        setLastVerdict(v)

        const markAuto = () => setMessages(prev => {
          const next = [...prev]; const idx = next.findLastIndex(m => m.role === 'assistant')
          if (idx >= 0) next[idx] = { ...next[idx], autoExecuted: true }; return next
        })

        if (isClose && !isLong && !isShort) {
          markAuto()
          const ok = await closePosition()
          if (opts.silent && ok) { setAutoCycles(c => c + 1); lastTradedRef.current = 0; sessionStorage.setItem('aomi-last-traded', '0') }
          return ok
        }
        if ((isLong || isShort) && confNum >= 60) {
          markAuto()
          const ok = await executeTrade(isLong ? 'long' : 'short')
          if (opts.silent) {
            setAutoCycles(c => c + 1)
            if (ok) { setTradesPlaced(c => c + 1); lastTradedRef.current = Date.now(); sessionStorage.setItem('aomi-last-traded', String(lastTradedRef.current)) }
          }
          return ok
        }
      }

      if (opts?.silent) { setAutoCycles(c => c + 1) }
      return false

    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages(prev => [...prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m), { role: 'system', content: '// interrupted', ts: Date.now() }])
        return false
      }
      const errText = String(err)
      if (errText.includes('401') || errText.toLowerCase().includes('unauthorized')) fatalErrorRef.current = errText
      setMessages(prev => [
        ...prev.map(m => m.role === 'tool' && m.toolStatus === 'running' ? { ...m, toolStatus: 'done' as const } : m),
        { role: 'system', content: `Error: ${errText}`, ts: Date.now() },
      ])
      return false
    } finally {
      abortRef.current = null
      sessionStorage.removeItem('aomi-processing')
      setProcessing(false)
      procRef.current = false
    }
  }, [btcPrice, account, sessionId, buildHint, executeTrade, closePosition, autoCycles])

  useEffect(() => { sendRef.current = send }, [send])

  const interruptAgent = useCallback(async () => {
    abortRef.current?.abort()
    try { await fetch('/api/aomi/interrupt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId }) }) } catch { /* ignore */ }
  }, [sessionId])

  const deleteThread = useCallback(async (threadId: string) => {
    setThreads(prev => prev.filter(t => t.session_id !== threadId))
    try { await fetch(`/api/aomi/threads?sessionId=${threadId}`, { method: 'DELETE' }) } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    if (!autoMode || !historyLoaded) return
    let cancelled = false

    async function loop() {
      if (cancelled || !autoRef.current) return
      if (fatalErrorRef.current) {
        const reason = fatalErrorRef.current; fatalErrorRef.current = null
        setAutoMode(false)
        setMessages(prev => [...prev, { role: 'system', content: `Auto stopped — ${reason.includes('401') ? 'auth error.' : reason}`, ts: Date.now() }])
        return
      }
      if (sessionStorage.getItem('aomi-processing') === '1') { if (!cancelled) setTimeout(loop, 2000); return }

      const HOLD_MS = 4 * 3600_000   // 4h cooldown after entering — match entry timeframe
      const SCAN_MS = 1800_000       // 30 min between flat scans — 4h candles only close every 4h

      const msSinceTrade = Date.now() - lastTradedRef.current
      if (lastTradedRef.current > 0 && msSinceTrade < HOLD_MS) {
        const wait = HOLD_MS - msSinceTrade
        if (!cancelled) setAutoWait({ until: Date.now() + wait, label: 'Holding position' })
        await new Promise<void>(resolve => { const t = setTimeout(resolve, wait); if (cancelled) { clearTimeout(t); resolve() } })
        if (cancelled) return
      }

      const msSinceLast = Date.now() - lastAnalysisRef.current
      if (msSinceLast < SCAN_MS && lastAnalysisRef.current > 0) {
        const wait = SCAN_MS - msSinceLast
        if (!cancelled) setAutoWait({ until: Date.now() + wait, label: 'Next analysis' })
        await new Promise<void>(resolve => { const t = setTimeout(resolve, wait); if (cancelled) { clearTimeout(t); resolve() } })
        if (cancelled) return
      }

      if (!procRef.current && sendRef.current) {
        setAutoWait(null)
        lastAnalysisRef.current = Date.now()
        sessionStorage.setItem('aomi-last-analysis', String(lastAnalysisRef.current))
        const traded = await sendRef.current(AUTO_PROMPT, { silent: true, autoExecute: true })
        if (cancelled) return
        if (traded) {
          const wait = HOLD_MS
          if (!cancelled) { setAutoWait({ until: Date.now() + wait, label: 'Holding position' }); setTimeout(loop, wait) }
          return
        }
      }

      if (!cancelled) setAutoWait({ until: Date.now() + SCAN_MS, label: 'Next analysis' })
      await new Promise<void>(resolve => { const t = setTimeout(resolve, SCAN_MS); if (cancelled) { clearTimeout(t); resolve() } })
      loop()
    }

    loop()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoMode, historyLoaded])

  const resetSession = () => {
    const env = window.location.hostname === 'localhost' ? 'local' : 'prod'
    localStorage.removeItem(`aomi-agent-session-${env}`)
    ;['aomi-auto','aomi-last-analysis','aomi-last-traded','aomi-trades-placed','aomi-processing',
      'aomi-last-verdict','aomi-auto-cycles','aomi-trade-log','aomi-last-analysis-text','aomi-open-trade',
    ].forEach(k => sessionStorage.removeItem(k))
    localStorage.setItem(`aomi-agent-session-${env}`, crypto.randomUUID())
    window.location.reload()
  }

  const pos    = account?.position ?? null
  const pnlPos = pos && pos.unrealizedPnl >= 0

  // Status line text
  const statusText = processing
    ? `// ANALYZING · cycle ${autoCycles + 1}`
    : autoMode
      ? autoWait
        ? `// ${autoWait.label.toUpperCase()} · ${Math.max(0, Math.ceil((autoWait.until - Date.now()) / 1000))}s`
        : `// LIVE · ${autoCycles} cycle${autoCycles !== 1 ? 's' : ''}`
      : autoCycles > 0 ? `// IDLE · last run completed` : '// IDLE'

  // Latest assistant message for center display
  const latestAnalysis = [...messages].reverse().find(m => m.role === 'assistant')

  // Derive verdict from content so it survives tab switches even if lastVerdict state lags
  const displayVerdict = lastVerdict ?? (() => {
    if (!latestAnalysis) return null
    const raw = latestAnalysis.content.split('\n').find(l => l.trim())?.trim() ?? ''
    const first = raw.replace(/^[^a-zA-Z]+/, '').trim()
    if (/^CLOSE\b/i.test(first)) return 'CLOSE'
    if (/^LONG\b/i.test(first))  return 'LONG'
    if (/^SHORT\b/i.test(first)) return 'SHORT'
    if (/^PASS\b/i.test(first))  return 'PASS'
    return null
  })()

  const displayConfidence = (() => {
    if (!latestAnalysis) return null
    const inlineMatch  = latestAnalysis.content.match(/^[\s•\-*]*(?:LONG|SHORT|CLOSE|PASS)[\s—–\-]+(\d+)\s*%/im)
    const labeledMatch = latestAnalysis.content.match(/confidence[^:]*:?\s*(\d+)\s*%/i)
    const v = inlineMatch ? parseInt(inlineMatch[1]) : labeledMatch ? parseInt(labeledMatch[1]) : null
    return v == null || isNaN(v) ? null : v
  })()

  const verdictColor = displayVerdict === 'LONG' ? '#2E9E68' : displayVerdict === 'SHORT' ? '#BE4A40' : displayVerdict === 'CLOSE' ? '#3C6EA0' : '#C2956B'
  const verdictBg    = displayVerdict === 'LONG' ? 'rgba(46,158,104,0.08)' : displayVerdict === 'SHORT' ? 'rgba(190,74,64,0.08)' : displayVerdict === 'CLOSE' ? 'rgba(60,110,160,0.08)' : 'rgba(194,149,107,0.08)'
  const activeTool   = processing
    ? [...messages].reverse().find(m => m.role === 'tool' && m.toolStatus === 'running')?.toolName
    : undefined

  return (
    <div style={{ height: '100vh', background: 'var(--bg-primary)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Header cycleId={autoCycles} isRunning={autoMode} />

      <main style={{ flex: 1, display: 'grid', gridTemplateColumns: '260px 1fr 220px', gap: 12, minHeight: 0, overflow: 'hidden', padding: '12px 16px' } as React.CSSProperties}>

        {/* ── LEFT: Agent config ──────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Balance */}
          {account && (() => {
            const pnl     = account.position?.unrealizedPnl ?? 0
            const balance = account.spotUSDC + pnl
            const pnlColor = pnl > 0 ? 'var(--green-dark)' : pnl < 0 ? 'var(--pink-dark)' : 'var(--text-muted)'
            return (
              <div className="card" style={{ padding: '12px 16px' }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Balance</div>
                <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 20, fontWeight: 800, color: 'var(--amber)' }}>${balance.toFixed(2)}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 3, fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                  <span>USDC ${account.spotUSDC.toFixed(2)}</span>
                  {pnl !== 0 && <span style={{ color: pnlColor }}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} PnL</span>}
                  {btcPrice && <span>· BTC ${btcPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>}
                </div>
              </div>
            )
          })()}

          {/* Trade settings: risk + leverage */}
          <div className="card" style={{ padding: '14px 16px' }}>

            {/* Risk */}
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.06em', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>Risk per trade</span>
                {editingRisk
                  ? <span style={{ color: 'var(--blue)', fontSize: 10 }}>editing</span>
                  : <button onClick={() => setEditingRisk(true)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--blue)', fontSize: 10, fontWeight: 600, padding: 0 }}>edit</button>}
              </div>
              {editingRisk && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <input
                    type="range" min={1} max={100} value={riskPct}
                    onChange={e => { const v = Number(e.target.value); setRiskPct(v); localStorage.setItem('aomi-risk-pct', String(v)) }}
                    style={{ flex: 1, accentColor: 'var(--blue)' }}
                  />
                  <button onClick={() => setEditingRisk(false)} style={{ fontSize: 10, fontWeight: 700, color: 'var(--blue)', background: 'none', border: 'none', cursor: 'pointer' }}>done</button>
                </div>
              )}
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 26, fontWeight: 800, letterSpacing: '-0.02em', color: riskPct > 50 ? 'var(--pink-dark)' : riskPct > 25 ? 'var(--amber)' : 'var(--text-primary)' }}>
                {riskPct}%
              </div>
              {account?.spotUSDC ? (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3, fontFamily: 'var(--font-geist-mono)' }}>
                  ≈ ${(account.spotUSDC * riskPct / 100).toFixed(2)} · {leverage}× = ${(account.spotUSDC * riskPct / 100 * leverage).toFixed(2)} notional
                </div>
              ) : null}
            </div>

            <div style={{ height: 1, background: 'var(--border)', margin: '4px 0 12px' }} />

            {/* Leverage */}
            <div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Leverage</div>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {[1, 2, 3, 5, 10, 20].map(lv => (
                  <button
                    key={lv}
                    onClick={() => { setLeverage(lv); localStorage.setItem('aomi-leverage', String(lv)) }}
                    style={{
                      padding: '4px 9px', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer',
                      border: `1px solid ${leverage === lv ? 'rgba(74,127,165,0.5)' : 'var(--border)'}`,
                      background: leverage === lv ? 'rgba(74,127,165,0.12)' : 'var(--bg-secondary)',
                      color: leverage === lv ? 'var(--blue)' : 'var(--text-muted)',
                      transition: 'all 0.15s',
                    }}
                  >{lv}×</button>
                ))}
              </div>
            </div>

          </div>

          {/* Start / Stop */}
          <button
            onClick={() => { if (processing) interruptAgent(); setAutoMode(m => !m) }}
            disabled={processing && !autoMode}
            style={{
              padding: '12px 0', borderRadius: 10, border: 'none',
              cursor: processing && !autoMode ? 'not-allowed' : 'pointer',
              fontWeight: 700, fontSize: 13, letterSpacing: '0.01em',
              background: autoMode ? 'rgba(190,74,64,0.10)' : 'var(--text-primary)',
              color: autoMode ? 'var(--pink-dark)' : 'var(--bg-card)',
              outline: autoMode ? '1px solid rgba(190,74,64,0.3)' : 'none',
              transition: 'all 0.2s',
            }}
          >
            {autoMode ? '⏹ Stop Agent' : '▶ Start Agent'}
          </button>

          <button
            onClick={() => { if (!processing) send(AUTO_PROMPT, { autoExecute: true }) }}
            disabled={processing}
            style={{
              padding: '8px 0', borderRadius: 10,
              border: '1px solid var(--border)', background: 'var(--bg-card)',
              cursor: processing ? 'not-allowed' : 'pointer',
              fontWeight: 600, fontSize: 12, color: processing ? 'var(--text-muted)' : 'var(--text-secondary)',
              opacity: processing ? 0.5 : 1,
            }}
          >↺ Run Once</button>

          <button
            onClick={() => {
              if (processing) return
              // Clear cached analysis state so the fresh cycle's output renders cleanly
              sessionStorage.removeItem('aomi-last-analysis-text')
              sessionStorage.removeItem('aomi-last-verdict')
              setLastVerdict(null)
              setMessages([INIT_MSG])
              send(AUTO_PROMPT, { autoExecute: true })
            }}
            disabled={processing}
            title="Clear cached analysis text + verdict, then run a fresh cycle"
            style={{
              padding: '8px 0', borderRadius: 10,
              border: '1px dashed var(--border)', background: 'transparent',
              cursor: processing ? 'not-allowed' : 'pointer',
              fontWeight: 600, fontSize: 11, color: processing ? 'var(--text-muted)' : 'var(--text-muted)',
              opacity: processing ? 0.5 : 1,
              letterSpacing: '0.03em',
            }}
          >⟲ Clear cache + rerun</button>

          <button
            onClick={resetSession}
            style={{
              padding: '8px 0', borderRadius: 10,
              border: '1px solid var(--border)', background: 'var(--bg-card)',
              cursor: 'pointer', fontWeight: 600, fontSize: 12,
              color: 'var(--text-muted)',
            }}
          >↺ New Session</button>

          {/* AOMI Agent info */}
          <div className="card" style={{ padding: '14px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Open Trader</span>
              <span style={{
                padding: '2px 8px', borderRadius: 20, fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
                background: autoMode ? 'rgba(46,158,104,0.12)' : 'var(--bg-secondary)',
                color: autoMode ? 'var(--green-dark)' : 'var(--text-muted)',
                border: `1px solid ${autoMode ? 'rgba(46,158,104,0.3)' : 'var(--border)'}`,
                display: 'flex', alignItems: 'center', gap: 5,
              }}>
                {autoMode && <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--green-dark)', display: 'inline-block', animation: 'pulse-live 1s infinite' }} />}
                {autoMode ? 'LIVE' : 'IDLE'}
              </span>
            </div>
            {[
              ['Market',    'BTC-PERP'],
              ['Timeframe', '1h – 4h'],
              ['Signal',    '1h candles + book'],
              ['Leverage',  `${leverage}×`],
              ['Execution', 'Hyperliquid'],
            ].map(([label, val]) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>{label}</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', fontFamily: 'var(--font-geist-mono)' }}>{val}</span>
              </div>
            ))}
          </div>

        </div>

        {/* ── CENTER: Agent log ───────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0, overflow: 'hidden' }}>

          {/* Verdict + reasoning combined card */}
          <div className="card" style={{ padding: '18px 20px', flexShrink: 0, borderColor: displayVerdict ? `${verdictColor}30` : undefined, background: displayVerdict ? verdictBg : undefined }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 32, fontWeight: 800, letterSpacing: '-0.02em', color: displayVerdict ? verdictColor : 'var(--text-muted)', lineHeight: 1 }}>
                  {displayVerdict ?? '—'}
                </span>
                {displayConfidence != null && (
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 14, fontWeight: 700, color: verdictColor, lineHeight: 1 }}>
                    {displayConfidence}%
                  </span>
                )}
                {autoCycles > 0 && (
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>cycle {autoCycles}</span>
                )}
                {latestAnalysis?.autoExecuted && (
                  <span style={{ padding: '1px 7px', borderRadius: 20, fontSize: 9, fontWeight: 700, background: 'rgba(245,158,11,0.10)', color: 'var(--amber)', border: '1px solid rgba(245,158,11,0.25)', letterSpacing: '0.05em' }}>AUTO</span>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {processing && [0,1,2].map(i => <span key={i} style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--blue)', display: 'inline-block', animation: `dotbounce 1.2s ease-in-out ${i*0.2}s infinite` }} />)}
                <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, fontWeight: 700, color: processing ? 'var(--blue)' : autoMode ? 'var(--green-dark)' : 'var(--text-muted)' }}>
                  {processing ? 'analyzing' : autoMode ? 'live' : 'idle'}
                </span>
              </div>
            </div>

            {latestAnalysis ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {latestAnalysis.content.split('\n').filter(l => l.trim()).slice(0, 6).map((line, i) => {
                  // Strip leading bullet markers, bold, and the verdict + confidence prefix ("PASS 80% —" / "LONG —")
                  const clean = line
                    .replace(/^[•\-*]\s*/, '')
                    .replace(/\*\*(.*?)\*\*/g, '$1')
                    .replace(/^(CLOSE|LONG|SHORT|PASS)\s*\d*\s*%?\s*[—–\-:·]?\s*/i, '')
                    .trim()
                  // Drop empty, verdict-only, or pure-percent leftovers
                  if (!clean || /^(CLOSE|LONG|SHORT|PASS)$/i.test(clean) || /^\d+\s*%$/.test(clean)) return null
                  return (
                    <div key={i} style={{ display: 'flex', gap: 7 }}>
                      <span style={{ color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.3, flexShrink: 0, marginTop: 1 }}>·</span>
                      <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{clean}</span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                {autoMode
                  ? <>{autoWait && !processing ? <><WaitCountdown until={autoWait.until} /> until next analysis</> : '// awaiting first cycle…'}</>
                  : '// start the agent to begin'}
              </div>
            )}
          </div>

          {/* Agent status bubble */}
          {processing ? (
            <CandleScan activeTool={activeTool} />
          ) : (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
              background: autoMode ? 'rgba(46,158,104,0.04)' : 'var(--bg-card)',
              borderRadius: 10,
              border: `1px solid ${autoMode ? 'rgba(46,158,104,0.14)' : 'var(--border)'}`,
              flexShrink: 0,
            }}>
              <span style={{
                width: 9, height: 9, borderRadius: '50%', flexShrink: 0,
                background: autoMode ? 'var(--green-dark)' : 'var(--text-muted)',
                animation: autoMode ? 'pulse-live 2s ease-in-out infinite' : 'none',
              }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, fontWeight: 700, color: autoMode ? 'var(--green-dark)' : 'var(--text-muted)', marginBottom: 2 }}>
                  {autoMode ? autoWait?.label === 'Holding position' ? 'holding position' : 'monitoring' : 'agent paused'}
                </div>
                <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {autoMode
                    ? autoWait
                      ? autoWait.label === 'Holding position'
                        ? <><WaitCountdown until={autoWait.until} /> cooldown · watching for reversal</>
                        : <>next analysis in <WaitCountdown until={autoWait.until} /></>
                      : `scanning market now · cycle ${autoCycles + 1}`
                    : 'start agent for 24/7 autonomous trading'}
                </div>
              </div>
              {autoMode && (
                <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--green-dark)', fontFamily: 'var(--font-geist-mono)', opacity: 0.7, letterSpacing: '0.05em', flexShrink: 0 }}>
                  24/7
                </span>
              )}
            </div>
          )}

          {/* Trade log */}
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, flexShrink: 0 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', fontFamily: 'var(--font-geist-mono)' }}>
                Agent Trade Log
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {tradesPlaced > 0 && (
                  <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
                    {tradesPlaced} trade{tradesPlaced !== 1 ? 's' : ''}
                  </span>
                )}
                {tradeLog.length > 0 && (
                  <button
                    onClick={() => exportTradeLog(tradeLog)}
                    style={{ fontSize: 10, fontWeight: 700, fontFamily: 'var(--font-geist-mono)', color: 'var(--text-muted)', background: 'none', border: '1px solid var(--border)', padding: '3px 8px', borderRadius: 5, cursor: 'pointer', letterSpacing: '0.06em', textTransform: 'uppercase' }}
                    title="Download trade log as CSV"
                  >
                    Export CSV
                  </button>
                )}
              </div>
            </div>

            {!mounted || tradeLog.length === 0 ? (
              <div style={{ padding: '10px 0', fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                // {autoMode ? 'awaiting first signal…' : 'start the agent to begin trading'}
              </div>
            ) : (
              <div ref={scrollRef} style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 }}>
                {/* Table header */}
                <div style={{ display: 'grid', gridTemplateColumns: '32px 70px 90px 1fr 80px 70px', gap: 0, padding: '4px 10px', borderBottom: '1px solid var(--border)' }}>
                  {['#', 'Side', 'Size', 'Price', 'P&L', 'Status'].map(h => (
                    <span key={h} style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>{h}</span>
                  ))}
                </div>
                {[...tradeLog].reverse().map((t, revIdx) => {
                  const isWin  = (t.pnl ?? 0) > 0
                  const isOpen = !t.closedAt
                  const num    = tradeLog.length - revIdx
                  return (
                    <div key={t.id} style={{ display: 'grid', gridTemplateColumns: '32px 70px 90px 1fr 80px 70px', gap: 0, padding: '7px 10px', borderBottom: '1px solid var(--border)', alignItems: 'center' }}>
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', fontWeight: 600 }}>#{num}</span>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: t.side === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)', flexShrink: 0 }} />
                        <span style={{ fontSize: 11, fontWeight: 700, color: t.side === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)' }}>
                          {t.side === 'long' ? 'Long' : 'Short'}
                        </span>
                      </span>
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600 }}>
                        {t.sizeBTC.toFixed(4)}
                      </span>
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
                        ${t.entryPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                        {t.exitPrice ? <> → ${t.exitPrice.toLocaleString('en-US', { maximumFractionDigits: 0 })}</> : ''}
                      </span>
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 800, color: isOpen ? 'var(--text-muted)' : isWin ? 'var(--green-dark)' : 'var(--pink-dark)' }}>
                        {isOpen ? '—' : `${isWin ? '+' : ''}$${(t.pnl ?? 0).toFixed(2)}`}
                      </span>
                      <span style={{ fontSize: 10, fontWeight: 600, color: isOpen ? 'var(--amber)' : isWin ? 'var(--green-dark)' : 'var(--pink-dark)' }}>
                        {isOpen ? 'Open' : isWin ? 'Win' : 'Loss'}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

        </div>

        {/* ── RIGHT: Stats ────────────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

          {/* Session P&L */}
          <div className="card" style={{ padding: '14px 16px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Session P&L</div>
            <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 30, fontWeight: 800, letterSpacing: '-0.03em', color: sessionPnL > 0 ? 'var(--green-dark)' : sessionPnL < 0 ? 'var(--pink-dark)' : 'var(--text-primary)', lineHeight: 1 }}>
              {sessionPnL > 0 ? '+' : ''}{closedTrades.length === 0 ? '+$0.00' : `$${sessionPnL.toFixed(2)}`}
            </div>
            {closedTrades.length > 0 && (
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4, fontFamily: 'var(--font-geist-mono)' }}>
                {wins > 0 ? `+${((wins / closedTrades.length) * 100).toFixed(0)}% win rate` : '0% win rate'}
              </div>
            )}
          </div>

          {/* Stats list */}
          <div className="card" style={{ padding: '14px 16px' }}>
            {[
              { label: 'Cycles run',    val: String(autoCycles) },
              { label: 'Trades placed', val: String(tradesPlaced) },
              { label: 'Wins',          val: closedTrades.length > 0 ? `${wins} / ${closedTrades.length}` : '—' },
              { label: 'Best trade',    val: tradeLog.length > 0 ? (() => { const b = Math.max(...tradeLog.filter(t => t.pnl != null).map(t => t.pnl!)); return b > 0 ? `+$${b.toFixed(2)}` : '—' })() : '—' },
              { label: 'Worst trade',   val: tradeLog.length > 0 ? (() => { const w = Math.min(...tradeLog.filter(t => t.pnl != null).map(t => t.pnl!)); return w < 0 ? `$${w.toFixed(2)}` : '—' })() : '—' },
            ].map(({ label, val }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid var(--border)' }}>
                <span style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>{label}</span>
                <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)' }}>{val}</span>
              </div>
            ))}
          </div>

          {/* Position */}
          {threads.length > 0 && (
            <div className="card" style={{ padding: '14px 16px' }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Session History</div>
              {threads.slice(0, 6).map(t => (
                <div key={t.session_id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ fontSize: 10, color: 'var(--text-secondary)', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || 'Session'}</span>
                  <button onClick={() => deleteThread(t.session_id)} style={{ fontSize: 10, color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, flexShrink: 0, lineHeight: 1 }}>✕</button>
                </div>
              ))}
            </div>
          )}

          {pos ? (
            <div className="card" style={{ padding: '14px 16px', border: `1px solid ${pos.side === 'long' ? 'rgba(58,158,104,0.25)' : 'rgba(190,74,64,0.25)'}` }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Open Position</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ padding: '2px 9px', borderRadius: 20, fontSize: 10, fontWeight: 700, background: pos.side === 'long' ? 'rgba(58,158,104,0.12)' : 'rgba(190,74,64,0.12)', color: pos.side === 'long' ? 'var(--green-dark)' : 'var(--pink-dark)', border: `1px solid ${pos.side === 'long' ? 'rgba(58,158,104,0.3)' : 'rgba(190,74,64,0.3)'}` }}>{pos.side === 'long' ? '↑ LONG' : '↓ SHORT'}</span>
                <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 800, color: 'var(--text-primary)' }}>{pos.sizeBTC.toFixed(4)}</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>Entry <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 700, color: 'var(--text-secondary)' }}>${pos.entryPx.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span></div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 10 }}>
                <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 15, fontWeight: 800, color: pnlPos ? 'var(--green-dark)' : 'var(--pink-dark)' }}>
                  {pnlPos ? '+' : ''}${pos.unrealizedPnl.toFixed(2)}
                  {(() => {
                    const baseBalance = (account?.totalEquity ?? 0) - pos.unrealizedPnl
                    if (baseBalance <= 0) return null
                    const pct = pos.unrealizedPnl / baseBalance * 100
                    return (
                      <span style={{ fontSize: 11, fontWeight: 600, opacity: 0.75, marginLeft: 5 }}>
                        ({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)
                      </span>
                    )
                  })()}
                </span>
                <button onClick={() => closePosition()} style={{ padding: '4px 12px', borderRadius: 7, border: 'none', cursor: 'pointer', background: 'rgba(190,74,64,0.08)', color: 'var(--pink-dark)', fontWeight: 700, fontSize: 11, outline: '1px solid rgba(190,74,64,0.25)' }}>Close</button>
              </div>
            </div>
          ) : (
            <div className="card" style={{ padding: '14px 16px' }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Position</div>
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 700, color: 'var(--text-muted)' }}>FLAT</div>
            </div>
          )}

        </div>
      </main>
    </div>
  )
}
