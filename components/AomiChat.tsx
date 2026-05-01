'use client'

import React, { useState, useRef, useEffect } from 'react'
import type { KalshiMarket } from '@/lib/types'

interface Message {
  role: 'user' | 'assistant' | 'tool' | 'trade'
  content: string
  toolName?: string
  status?: 'running' | 'done'
  tradeStatus?: 'pending' | 'confirmed' | 'rejected'
  requestId?: string
}

interface AomiChatProps {
  market: KalshiMarket | null
  btcPrice: number
  strikePrice: number
  secondsLeft: number
  onTradeSignal?: (side: 'yes' | 'no', price: number) => void
}

const QUICK_PROMPTS = [
  'Should I trade this window?',
  'Analyze YES vs NO right now',
  'What edge does the market have?',
  'Search for latest BTC news',
]

function ToolBadge({ name, status }: { name: string; status: 'running' | 'done' }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 8px', borderRadius: 5,
      background: status === 'done' ? 'rgba(45,158,107,0.08)' : 'rgba(58,114,168,0.08)',
      border: `1px solid ${status === 'done' ? 'rgba(45,158,107,0.25)' : 'rgba(58,114,168,0.25)'}`,
      fontSize: 10, fontWeight: 600,
      color: status === 'done' ? 'var(--green-dark)' : 'var(--blue)',
      marginBottom: 4,
    }}>
      <span style={{
        width: 5, height: 5, borderRadius: '50%',
        background: status === 'done' ? 'var(--green)' : 'var(--blue)',
        display: 'inline-block',
        animation: status === 'running' ? 'pulse-live 1.2s ease-in-out infinite' : 'none',
      }} />
      {name}
      <span style={{ opacity: 0.6 }}>{status === 'running' ? '…' : ' ✓'}</span>
    </div>
  )
}

// ── Inline text: bold, code spans ────────────────────────────────────────────
function InlineText({ text }: { text: string }) {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return (
    <>
      {tokens.map((tok, i) => {
        if (tok.startsWith('**') && tok.endsWith('**'))
          return <strong key={i} style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{tok.slice(2, -2)}</strong>
        if (tok.startsWith('`') && tok.endsWith('`'))
          return <code key={i} style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, background: 'var(--bg-primary)', padding: '1px 5px', borderRadius: 4, color: 'var(--blue-dark)' }}>{tok.slice(1, -1)}</code>
        return <span key={i}>{tok}</span>
      })}
    </>
  )
}

// ── Recommendation pill ───────────────────────────────────────────────────────
function RecoBadge({ text }: { text: string }) {
  const upper = text.toUpperCase()
  const isPass = upper.includes('PASS')
  const isYes  = upper.includes('BUY YES') || upper.includes('YES')
  const isNo   = upper.includes('BUY NO')  || upper.includes('NO')
  const bg     = isPass ? 'var(--amber-pale, rgba(212,135,44,0.10))'
               : isYes  ? 'var(--green-pale)'
               : isNo   ? 'rgba(58,114,168,0.10)'
               : 'var(--bg-secondary)'
  const border = isPass ? '1px solid rgba(212,135,44,0.35)'
               : isYes  ? '1px solid rgba(45,158,107,0.3)'
               : isNo   ? '1px solid rgba(58,114,168,0.3)'
               : '1px solid var(--border)'
  const color  = isPass ? 'var(--amber)'
               : isYes  ? 'var(--green-dark)'
               : isNo   ? 'var(--blue-dark)'
               : 'var(--text-primary)'
  const icon   = isPass ? '◉ PASS' : isYes ? '↑ YES' : isNo ? '↓ NO' : null
  return (
    <div style={{
      margin: '8px 0 4px', padding: '8px 12px', borderRadius: 8,
      background: bg, border, color,
      fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'flex-start', gap: 6,
    }}>
      {icon && <span style={{ flexShrink: 0, fontSize: 11, marginTop: 1 }}>{icon}</span>}
      <span style={{ lineHeight: 1.5 }}><InlineText text={text} /></span>
    </div>
  )
}

// ── List item: strips leading -, *, •, highlights verdict keywords ────────────
function ListItem({ text, ordered, index }: { text: string; ordered: boolean; index: number }) {
  const clean = text.replace(/^[-*•]\s*/, '').trimStart()
  const upper = clean.toUpperCase()
  const isPass  = /\bPASS\b/.test(upper)
  const isYes   = /\bBUY YES\b/.test(upper)
  const isNo    = /\bBUY NO\b/.test(upper)
  const verdict = isPass ? 'pass' : isYes ? 'yes' : isNo ? 'no' : null

  const dotColor = verdict === 'yes' ? 'var(--green)' : verdict === 'no' ? 'var(--blue)' : verdict === 'pass' ? 'var(--amber)' : 'var(--border-bright)'
  const rowBg    = verdict ? (verdict === 'yes' ? 'var(--green-pale)' : verdict === 'no' ? 'rgba(58,114,168,0.07)' : 'rgba(212,135,44,0.09)') : 'transparent'
  const rowBorder= verdict ? `1px solid ${verdict === 'yes' ? 'rgba(45,158,107,0.2)' : verdict === 'no' ? 'rgba(58,114,168,0.2)' : 'rgba(212,135,44,0.25)'}` : 'none'

  return (
    <div style={{
      display: 'flex', gap: 8, alignItems: 'flex-start',
      padding: verdict ? '6px 8px' : '2px 0',
      borderRadius: verdict ? 7 : 0,
      background: rowBg,
      border: rowBorder,
      marginBottom: verdict ? 2 : 0,
    }}>
      {ordered
        ? <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', minWidth: 16, flexShrink: 0, paddingTop: 1 }}>{index}.</span>
        : <span style={{ width: 5, height: 5, borderRadius: '50%', background: dotColor, marginTop: 5, flexShrink: 0 }} />}
      <span style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--text-primary)' }}>
        <InlineText text={clean} />
      </span>
    </div>
  )
}

// ── Main structured renderer ──────────────────────────────────────────────────
function AssistantMessage({ content }: { content: string }) {
  const lines = content.split('\n')
  const nodes: React.ReactNode[] = []
  let listBuf: { text: string; ordered: boolean }[] = []
  let listOrdered = false
  let key = 0

  function flushList() {
    if (!listBuf.length) return
    nodes.push(
      <div key={key++} style={{ margin: '4px 0 6px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {listBuf.map((item, i) => <ListItem key={i} text={item.text} ordered={item.ordered} index={i + 1} />)}
      </div>
    )
    listBuf = []
  }

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    const line = raw.trimEnd()

    // H2 / H3 headers
    if (line.startsWith('## ')) {
      flushList()
      nodes.push(
        <div key={key++} style={{ fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--text-muted)', marginTop: 10, marginBottom: 3 }}>
          {line.slice(3)}
        </div>
      )
      continue
    }
    if (line.startsWith('### ')) {
      flushList()
      nodes.push(
        <div key={key++} style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', marginTop: 7, marginBottom: 2 }}>
          <InlineText text={line.slice(4)} />
        </div>
      )
      continue
    }

    // Horizontal rule
    if (/^---+$/.test(line)) {
      flushList()
      nodes.push(<div key={key++} style={{ height: 1, background: 'var(--border)', margin: '8px 0' }} />)
      continue
    }

    // Recommendation line — starts with "I would", "Recommendation", "Recommend:", "**Recommendation"
    const isReco = /^(\*\*)?I would (PASS|BUY|recommend)|^(\*\*)?Recommendation/i.test(line) ||
                   /^(\*\*)?My (Take|Recommendation)/i.test(line) ||
                   /^(\*\*)?PASS|^(\*\*)?BUY (YES|NO)/i.test(line.replace(/^#+\s*/, ''))
    if (isReco && line.trim()) {
      flushList()
      nodes.push(<RecoBadge key={key++} text={line.replace(/^#+\s*/, '')} />)
      continue
    }

    // Ordered list item (1. 2. etc.)
    const orderedMatch = line.match(/^(\d+)\.\s+(.+)/)
    if (orderedMatch) {
      if (listBuf.length && !listOrdered) flushList()
      listOrdered = true
      listBuf.push({ text: orderedMatch[2], ordered: true })
      continue
    }

    // Unordered list item
    const unorderedMatch = line.match(/^[-*•]\s+(.+)/)
    if (unorderedMatch) {
      if (listBuf.length && listOrdered) flushList()
      listOrdered = false
      listBuf.push({ text: unorderedMatch[1], ordered: false })
      continue
    }

    // Bullet items starting with emoji (✅ ❌ ⚠)
    const emojiItem = line.match(/^([✅❌⚠️⚠])\s*(.*)/)
    if (emojiItem) {
      if (listBuf.length && listOrdered) flushList()
      listOrdered = false
      listBuf.push({ text: line, ordered: false })
      continue
    }

    // Plain paragraph
    flushList()
    if (!line.trim()) {
      // blank line — small gap (skip extra whitespace)
      continue
    }
    nodes.push(
      <p key={key++} style={{ margin: '3px 0', fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>
        <InlineText text={line} />
      </p>
    )
  }

  flushList()
  return <div style={{ display: 'flex', flexDirection: 'column' }}>{nodes}</div>
}

export default function AomiChat({ market, btcPrice, strikePrice, secondsLeft, onTradeSignal }: AomiChatProps) {
  const [messages, setMessages]         = useState<Message[]>([])
  const [input, setInput]               = useState('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [secretsOk, setSecretsOk]       = useState<boolean | null>(null)
  const [clientId]  = useState(() => {
    if (typeof window === 'undefined') return crypto.randomUUID()
    const stored = localStorage.getItem('aomi-client-id')
    if (stored) return stored
    const id = crypto.randomUUID()
    localStorage.setItem('aomi-client-id', id)
    return id
  })
  const [sessionId] = useState(() => crypto.randomUUID())
  const bottomRef   = useRef<HTMLDivElement>(null)
  const inputRef    = useRef<HTMLInputElement>(null)

  // Register Kalshi credentials with AOMI once on mount
  useEffect(() => {
    fetch('/api/aomi/secrets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clientId }),
    })
      .then(r => r.json())
      .then(d => setSecretsOk(d.ok ?? false))
      .catch(() => setSecretsOk(false))
  }, [clientId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Greet on first market load
  useEffect(() => {
    if (!market || messages.length > 0) return
    const minutesLeft = (secondsLeft / 60).toFixed(1)
    const aboveStrike = btcPrice > strikePrice
    const dist = Math.abs(btcPrice - strikePrice)
    setMessages([{
      role: 'assistant',
      content: `**AOMI is watching this window.** BTC $${btcPrice.toLocaleString()} — ${aboveStrike ? 'above' : 'below'} the $${strikePrice.toLocaleString()} strike by $${dist.toFixed(0)}. ${minutesLeft}m left. Ask me to analyze or execute a trade.`,
    }])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market?.ticker])

  async function executeTrade(side: 'yes' | 'no') {
    if (!market) return
    const price = side === 'yes' ? market.yes_ask : market.no_ask
    try {
      const res = await fetch('/api/place-order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: market.ticker, side, count: 1,
          yesPrice: market.yes_ask, noPrice: market.no_ask,
          clientOrderId: `chat-${Date.now()}`,
        }),
      })
      const data = await res.json()
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.ok
          ? `✅ Order placed — **${side.toUpperCase()} @ ${price}¢** (ID: ${data.orderId})`
          : `❌ Order failed: ${data.error}`,
      }])
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Trade error: ${String(err)}` }])
    }
  }

  async function send(text: string) {
    if (!text.trim() || isProcessing) return
    const userMsg = text.trim()
    setInput('')
    setMessages(prev => [...prev,
      { role: 'user', content: userMsg },
      { role: 'tool', content: '', toolName: 'brave_search', status: 'running' },
    ])
    setIsProcessing(true)

    const hint = market
      ? [
          `Market: ${market.ticker}`,
          `BTC spot: $${btcPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
          `Strike: $${strikePrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
          `YES ask: ${market.yes_ask}¢  YES bid: ${market.yes_bid}¢`,
          `NO ask: ${market.no_ask}¢  NO bid: ${market.no_bid}¢`,
          `Time left: ${secondsLeft}s`,
          btcPrice > strikePrice
            ? `BTC is $${(btcPrice - strikePrice).toFixed(2)} ABOVE strike — YES is currently winning.`
            : `BTC is $${(strikePrice - btcPrice).toFixed(2)} BELOW strike — NO is currently winning.`,
        ].join('\n')
      : undefined

    let assistantText = ''

    try {
      const res = await fetch('/api/aomi/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg, hint, sessionId }),
      })

      if (!res.ok || !res.body) throw new Error('Request failed')

      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let assistantInserted = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const ev = JSON.parse(line.slice(6))

            if (ev.type === 'tool') {
              setMessages(prev => {
                const copy = [...prev]
                const idx = copy.findIndex(m => m.role === 'tool' && m.toolName === ev.name && m.status === 'running')
                if (idx >= 0) copy[idx] = { ...copy[idx], status: ev.status }
                else copy.splice(copy.length - 1, 0, { role: 'tool', content: '', toolName: ev.name, status: ev.status })
                return copy
              })

            } else if (ev.type === 'trade_request') {
              // AOMI's wallet_tx_request — show pending trade card above assistant message
              setMessages(prev => {
                const copy = [...prev]
                copy.splice(copy.length - 1, 0, {
                  role: 'trade', content: JSON.stringify(ev.payload),
                  requestId: ev.requestId, tradeStatus: 'pending',
                })
                return copy
              })

            } else if (ev.type === 'trade_confirmed') {
              setMessages(prev => prev.map(m =>
                m.requestId === ev.requestId ? { ...m, tradeStatus: 'confirmed' } : m
              ))

            } else if (ev.type === 'trade_rejected') {
              setMessages(prev => prev.map(m =>
                m.requestId === ev.requestId ? { ...m, tradeStatus: 'rejected' } : m
              ))

            } else if (ev.type === 'message') {
              assistantText = ev.text
              setMessages(prev => {
                // Mark search done, insert assistant message
                const next = prev.map(m =>
                  m.role === 'tool' && m.status === 'running'
                    ? { ...m, status: 'done' as const } : m
                )
                if (!assistantInserted) {
                  assistantInserted = true
                  return [...next, { role: 'assistant' as const, content: assistantText }]
                }
                const last = next[next.length - 1]
                if (last?.role === 'assistant') next[next.length - 1] = { ...last, content: assistantText }
                return next
              })
              if (onTradeSignal) {
                const yesM = assistantText.match(/buy\s+yes\s+at\s+(\d+)¢/i)
                const noM  = assistantText.match(/buy\s+no\s+at\s+(\d+)¢/i)
                if (yesM) onTradeSignal('yes', parseInt(yesM[1]))
                else if (noM) onTradeSignal('no', parseInt(noM[1]))
              }

            } else if (ev.type === 'error') {
              assistantText = `Error: ${ev.text}`
              setMessages(prev => {
                const copy = [...prev]
                const last = copy[copy.length - 1]
                if (last?.role === 'assistant') copy[copy.length - 1] = { ...last, content: assistantText }
                return copy
              })
            }
          } catch { /* malformed event */ }
        }
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Failed to reach AOMI: ${err instanceof Error ? err.message : 'unknown error'}` }])
    } finally {
      setIsProcessing(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
  }

  const noMarket = !market

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', height: 440, padding: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
      }}>
        <div style={{
          width: 7, height: 7, borderRadius: '50%',
          background: noMarket ? 'var(--text-muted)' : 'var(--green)',
          animation: noMarket ? 'none' : 'pulse-live 2s ease-in-out infinite',
          flexShrink: 0,
        }} />
        <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.07em', color: 'var(--text-primary)' }}>AOMI AGENT</span>
        <span style={{ fontSize: 9, color: 'var(--text-muted)', marginLeft: 2 }}>· kalshi trading</span>
        {secretsOk !== null && (
          <span style={{
            fontSize: 8, fontWeight: 700, padding: '1px 5px', borderRadius: 4, marginLeft: 4,
            background: secretsOk ? 'var(--green-pale)' : 'rgba(212,135,44,0.1)',
            border: secretsOk ? '1px solid rgba(45,158,107,0.25)' : '1px solid rgba(212,135,44,0.3)',
            color: secretsOk ? 'var(--green-dark)' : 'var(--amber)',
          }}>
            {secretsOk ? 'creds ✓' : 'no creds'}
          </span>
        )}
        <div style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
          api.aomi.dev
        </div>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', marginTop: 60, color: 'var(--text-muted)', fontSize: 12 }}>
            {noMarket ? 'Waiting for active market…' : 'Ask AOMI to analyze this window.'}
          </div>
        )}

        {messages.map((msg, i) => {
          // Trade request card — emitted by AOMI's wallet_tx_request event
          if (msg.role === 'trade') {
            const status = msg.tradeStatus ?? 'pending'
            return (
              <div key={i} style={{
                padding: '10px 12px', borderRadius: 10,
                background: status === 'confirmed' ? 'var(--green-pale)' : status === 'rejected' ? 'rgba(192,69,62,0.07)' : 'rgba(58,114,168,0.07)',
                border: status === 'confirmed' ? '1px solid rgba(45,158,107,0.3)' : status === 'rejected' ? '1px solid rgba(192,69,62,0.25)' : '1px solid rgba(58,114,168,0.25)',
              }}>
                <div style={{ fontSize: 9, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
                  AOMI wallet_tx_request
                </div>
                <div style={{ fontSize: 12, fontWeight: 700, color: status === 'confirmed' ? 'var(--green-dark)' : status === 'rejected' ? 'var(--red)' : 'var(--blue-dark)' }}>
                  {status === 'pending' ? 'Executing trade via Kalshi plugin…' : status === 'confirmed' ? '✓ Order placed' : '✗ Order rejected'}
                </div>
              </div>
            )
          }

          if (msg.role === 'tool') {
            return (
              <div key={i} style={{ paddingLeft: 8 }}>
                <ToolBadge name={msg.toolName ?? 'tool'} status={msg.status ?? 'done'} />
              </div>
            )
          }
          if (msg.role === 'user') {
            return (
              <div key={i} style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <div style={{
                  maxWidth: '75%', padding: '8px 12px', borderRadius: '12px 12px 2px 12px',
                  background: 'var(--blue)', color: '#fff', fontSize: 13, lineHeight: 1.5,
                }}>
                  {msg.content}
                </div>
              </div>
            )
          }
          const hasBuyYes = /BUY YES/i.test(msg.content)
          const hasBuyNo  = /BUY NO/i.test(msg.content)
          return (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <div style={{
                width: 24, height: 24, borderRadius: '50%', flexShrink: 0,
                background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 10, fontWeight: 800, color: 'var(--text-secondary)',
              }}>A</div>
              <div style={{
                flex: 1, padding: '8px 12px', borderRadius: '2px 12px 12px 12px',
                background: 'var(--bg-secondary)', border: '1px solid var(--border)',
              }}>
                {msg.content
                  ? <AssistantMessage content={msg.content} />
                  : <span style={{ opacity: 0.4, fontSize: 12, animation: 'pulse-live 1s ease infinite' }}>●●●</span>}
                {market && (hasBuyYes || hasBuyNo) && (
                  <div style={{ display: 'flex', gap: 6, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
                    {hasBuyYes && (
                      <button onClick={() => executeTrade('yes')} style={{
                        padding: '5px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
                        background: 'var(--green)', color: '#fff', fontWeight: 700, fontSize: 11,
                      }}>Buy YES @ {market.yes_ask}¢</button>
                    )}
                    {hasBuyNo && (
                      <button onClick={() => executeTrade('no')} style={{
                        padding: '5px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
                        background: 'var(--pink)', color: '#fff', fontWeight: 700, fontSize: 11,
                      }}>Buy NO @ {market.no_ask}¢</button>
                    )}
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', alignSelf: 'center', marginLeft: 4 }}>
                      · executes via Kalshi
                    </span>
                  </div>
                )}
              </div>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Quick prompts */}
      {messages.length <= 1 && !isProcessing && (
        <div style={{ padding: '0 14px 8px', display: 'flex', flexWrap: 'wrap', gap: 5, flexShrink: 0 }}>
          {QUICK_PROMPTS.map(p => (
            <button
              key={p}
              onClick={() => send(p)}
              disabled={noMarket}
              style={{
                padding: '4px 10px', borderRadius: 20, fontSize: 10, fontWeight: 600,
                border: '1px solid var(--border)', background: 'var(--bg-secondary)',
                color: 'var(--text-secondary)', cursor: noMarket ? 'not-allowed' : 'pointer',
                opacity: noMarket ? 0.4 : 1, transition: 'all 0.15s',
              }}
              onMouseEnter={e => { if (!noMarket) (e.currentTarget as HTMLElement).style.borderColor = 'var(--blue)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)' }}
            >
              {p}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div style={{
        padding: '10px 12px', borderTop: '1px solid var(--border)',
        display: 'flex', gap: 8, flexShrink: 0,
      }}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder={noMarket ? 'No active market…' : 'Ask AOMI anything about this window…'}
          disabled={isProcessing || noMarket}
          style={{
            flex: 1, padding: '8px 12px', borderRadius: 8, fontSize: 12,
            border: '1px solid var(--border)', background: 'var(--bg-secondary)',
            color: 'var(--text-primary)', outline: 'none',
          }}
        />
        <button
          onClick={() => send(input)}
          disabled={!input.trim() || isProcessing || noMarket}
          style={{
            padding: '8px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
            border: '1px solid var(--blue)', background: 'var(--blue)',
            color: '#fff', cursor: 'pointer', flexShrink: 0,
            opacity: (!input.trim() || isProcessing || noMarket) ? 0.5 : 1,
            transition: 'opacity 0.15s',
          }}
        >
          {isProcessing ? '…' : '→'}
        </button>
      </div>
    </div>
  )
}
