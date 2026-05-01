'use client'

import { useEffect, useState } from 'react'
import type { KalshiMarket, KalshiOrderbook } from '@/lib/types'

interface MarketCardProps {
  market: KalshiMarket | null
  orderbook: KalshiOrderbook | null
  strikePrice: number
  currentBTCPrice: number
  secondsUntilExpiry: number
  liveMode: boolean
  onRefresh?: () => void
  marketMode?: '15m' | 'hourly'
  predictedPrice?: number   // Grok's predicted BTC price (hourly mode only)
}

const fmt  = (p: number) => p.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const fmtD = (p: number) => p.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

function CountdownRing({ seconds, total, urgent }: { seconds: number; total: number; urgent: boolean }) {
  const r    = 18
  const circ = 2 * Math.PI * r
  const frac = Math.max(0, Math.min(1, seconds / total))
  const offset = circ * (1 - frac)
  return (
    <svg width={42} height={42} style={{ transform: 'rotate(-90deg)', flexShrink: 0 }}>
      <circle cx={21} cy={21} r={r} fill="none" stroke="var(--border)" strokeWidth={2.5} />
      <circle cx={21} cy={21} r={r} fill="none"
        stroke={urgent ? 'var(--pink)' : frac < 0.3 ? 'var(--amber)' : 'var(--green)'}
        strokeWidth={2.5} strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round"
        style={{ transition: 'stroke-dashoffset 1s linear, stroke 0.5s ease' }}
      />
    </svg>
  )
}

type OrderState = { status: 'idle' } | { status: 'placing' } | { status: 'ok'; orderId: string; fillCount: number } | { status: 'err'; message: string }
const QUICK_AMTS = [10, 25, 50]
const QUICK_PCTS = [25, 50, 100] as const

function TradeBox({ yesBid, yesAsk, noBid, noAsk, ticker, liveMode, side, onSideChange }: {
  yesBid: number; yesAsk: number
  noBid: number;  noAsk: number
  ticker: string; liveMode: boolean
  side: 'yes' | 'no'; onSideChange: (s: 'yes' | 'no') => void
}) {
  const [amtStr, setAmtStr]   = useState('10')
  const [order, setOrder]     = useState<OrderState>({ status: 'idle' })
  const [balance, setBalance] = useState<number | null>(null)  // dollars

  const isYes     = side === 'yes'
  const bid       = isYes ? yesBid  : noBid
  const ask       = isYes ? yesAsk  : noAsk
  const col       = isYes ? 'var(--green-dark)' : 'var(--pink-dark)'
  const colLight  = isYes ? 'var(--green)' : 'var(--pink)'
  const colPale   = isYes ? 'var(--green-pale)' : 'var(--pink-pale)'

  const amt        = Math.max(0.01, parseFloat(amtStr) || 0.01)
  const contracts  = Math.max(1, Math.floor(amt / (ask / 100)))
  const actualCost = (contracts * ask / 100).toFixed(2)
  const profit     = (contracts * (1 - ask / 100)).toFixed(2)

  useEffect(() => {
    if (!liveMode) return
    fetch('/api/balance', { cache: 'no-store' }).then(r => r.json()).then(d => {
      if (d.balance != null) setBalance(d.balance / 100)
    }).catch(() => {})
  }, [liveMode])

  function handleAmt(raw: string) {
    if (raw === '' || /^\d*\.?\d*$/.test(raw)) setAmtStr(raw)
  }
  function handleAmtBlur() {
    const v = parseFloat(amtStr)
    setAmtStr(isNaN(v) || v <= 0 ? '1' : String(v))
  }

  async function placeIt(overrideAmt?: number) {
    if (!liveMode) return
    setOrder({ status: 'placing' })
    const dollars = overrideAmt ?? amt
    const cnt = Math.max(1, Math.floor(dollars / (ask / 100)))
    try {
      const body = { ticker, side, count: cnt, ...(side === 'yes' ? { yesPrice: ask } : { noPrice: ask }), clientOrderId: `manual-${side}-${Date.now()}` }
      const res  = await fetch('/api/place-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await res.json()
      if (!res.ok || !data.ok) {
        const rawErr = data.error
        setOrder({ status: 'err', message: typeof rawErr === 'string' ? rawErr : (rawErr?.message ?? rawErr?.code) ? String(rawErr.message ?? rawErr.code) : `HTTP ${res.status}` })
      } else {
        setOrder({ status: 'ok', orderId: data.order?.order_id ?? '', fillCount: data.order?.fill_count ?? 0 })
        fetch('/api/balance', { cache: 'no-store' }).then(r => r.json()).then(d => { if (d.balance != null) setBalance(d.balance / 100) }).catch(() => {})
        setTimeout(() => setOrder({ status: 'idle' }), 4000)
      }
    } catch (err) { setOrder({ status: 'err', message: String(err) }) }
  }

  function pctDollars(pct: number) {
    if (balance == null) return null
    return Math.max(1, Math.floor(balance * pct / 100))
  }

  return (
    <div>
      {/* YES / NO tab row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--border)', marginBottom: 12 }}>
        {(['yes', 'no'] as const).map(s => {
          const active   = side === s
          const a        = s === 'yes' ? yesAsk : noAsk
          const activeC  = s === 'yes' ? 'var(--green-dark)' : 'var(--pink-dark)'
          const activeLC = s === 'yes' ? 'var(--green)' : 'var(--pink)'
          return (
            <button key={s} onClick={() => { onSideChange(s); setOrder({ status: 'idle' }) }}
              style={{
                padding: '10px 8px', border: 'none',
                borderBottom: active ? `2px solid ${activeLC}` : '2px solid transparent',
                background: 'transparent', cursor: 'pointer', transition: 'all 0.15s',
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1,
                marginBottom: -1,
              }}>
              <span style={{ fontSize: 9, fontWeight: 700, color: active ? activeC : 'var(--text-muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                {s === 'yes' ? 'Yes' : 'No'}
              </span>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 18, fontWeight: 800, color: active ? activeC : 'var(--text-secondary)', lineHeight: 1.1 }}>
                {a}¢
              </span>
            </button>
          )
        })}
      </div>

      {/* Bid / Ask */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Bid</span>
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>{bid}¢</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Ask</span>
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, fontWeight: 700, color: 'var(--text-primary)' }}>{ask}¢</span>
      </div>

      {/* Ask bar */}
      <div style={{ height: 3, borderRadius: 2, background: 'var(--border)', overflow: 'hidden', marginBottom: 12 }}>
        <div style={{ height: '100%', width: `${Math.min(100, ask / 2)}%`, background: colLight, borderRadius: 2, transition: 'width 0.6s ease' }} />
      </div>

      {/* Quick dollar amounts */}
      <div style={{ display: 'flex', gap: 5, marginBottom: 5 }}>
        {QUICK_AMTS.map(q => (
          <button key={q}
            disabled={order.status === 'placing'}
            onClick={() => { setAmtStr(String(q)); if (liveMode) placeIt(q) }}
            title={liveMode ? `Buy ${side} for $${q}` : `Set amount to $${q}`}
            style={{
              flex: 1, padding: '7px 0', borderRadius: 8,
              border: `1px solid ${liveMode ? 'var(--border-bright)' : 'var(--border)'}`,
              background: 'var(--bg-secondary)',
              fontSize: 11, fontWeight: 700, color: liveMode ? col : 'var(--text-muted)',
              cursor: order.status === 'placing' ? 'not-allowed' : 'pointer',
              transition: 'all 0.12s', fontFamily: 'var(--font-geist-mono)',
            }}
            onMouseEnter={e => { if (liveMode && order.status !== 'placing') { e.currentTarget.style.background = colPale; e.currentTarget.style.borderColor = colLight } }}
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-secondary)'; e.currentTarget.style.borderColor = liveMode ? 'var(--border-bright)' : 'var(--border)' }}
          >
            ${q}
          </button>
        ))}
      </div>

      {/* Pct-of-balance quick buttons — live only */}
      {liveMode && (
        <div style={{ display: 'flex', gap: 5, marginBottom: 10 }}>
          {QUICK_PCTS.map(pct => {
            const dollars = pctDollars(pct)
            const label   = pct === 100 ? 'MAX' : `${pct}%`
            return (
              <button key={pct}
                disabled={order.status === 'placing' || dollars == null}
                onClick={() => { if (dollars) { setAmtStr(String(dollars)); placeIt(dollars) } }}
                title={dollars != null ? `Buy ${side} · ${label} of balance ($${dollars})` : 'Loading balance…'}
                style={{
                  flex: 1, padding: '6px 0', borderRadius: 8,
                  border: `1px solid ${pct === 100 ? (isYes ? 'rgba(46,158,104,0.5)' : 'rgba(190,74,64,0.5)') : 'var(--border)'}`,
                  background: pct === 100 ? colPale : 'transparent',
                  fontSize: 10, fontWeight: 800, color: pct === 100 ? col : 'var(--text-muted)',
                  cursor: order.status === 'placing' || dollars == null ? 'not-allowed' : 'pointer',
                  transition: 'all 0.12s',
                  letterSpacing: '0.02em',
                }}
                onMouseEnter={e => { if (dollars && order.status !== 'placing') { e.currentTarget.style.background = colPale; e.currentTarget.style.color = col } }}
                onMouseLeave={e => { e.currentTarget.style.background = pct === 100 ? colPale : 'transparent'; e.currentTarget.style.color = pct === 100 ? col : 'var(--text-muted)' }}
              >
                {label}
                {dollars != null && <span style={{ display: 'block', fontSize: 8, fontWeight: 600, opacity: 0.65, marginTop: 1 }}>${dollars}</span>}
              </button>
            )
          })}
        </div>
      )}

      {/* Custom amount input */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-muted)' }}>$</span>
        <input
          type="text" inputMode="decimal" value={amtStr}
          onChange={e => handleAmt(e.target.value)}
          onBlur={handleAmtBlur}
          onFocus={e => e.target.select()}
          style={{
            flex: 1, textAlign: 'center', fontFamily: 'var(--font-geist-mono)',
            fontSize: 14, fontWeight: 700, color: 'var(--text-primary)',
            border: 'none', borderBottom: '1.5px solid var(--border-bright)',
            borderRadius: 0, padding: '4px 4px',
            background: 'transparent', outline: 'none',
          }}
        />
        {balance != null && liveMode && (
          <span style={{ fontSize: 9, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>bal ${balance.toFixed(0)}</span>
        )}
      </div>

      {/* Contracts summary */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12, alignItems: 'center' }}>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{contracts} contracts · ${actualCost}</span>
        <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--green-dark)', fontFamily: 'var(--font-geist-mono)' }}>+${profit} if win</span>
      </div>

      {/* Primary action */}
      {order.status === 'idle' && (
        <button onClick={() => placeIt()} disabled={!liveMode}
          style={{
            width: '100%', padding: '12px 0', borderRadius: 10,
            border: 'none',
            background: liveMode ? colLight : 'var(--bg-secondary)',
            fontSize: 13, fontWeight: 700, letterSpacing: '0.02em',
            color: liveMode ? '#fff' : 'var(--text-muted)',
            cursor: liveMode ? 'pointer' : 'not-allowed',
            transition: 'opacity 0.15s',
          }}
          onMouseEnter={e => { if (liveMode) e.currentTarget.style.opacity = '0.85' }}
          onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
        >
          {liveMode ? `Buy ${isYes ? 'Yes' : 'No'} · $${actualCost}` : `Paper · ${isYes ? 'Yes' : 'No'} @ ${ask}¢`}
        </button>
      )}
      {order.status === 'placing' && (
        <div style={{ padding: '12px 0', textAlign: 'center', fontSize: 11, color: 'var(--text-muted)' }}>
          Placing...
        </div>
      )}
      {order.status === 'ok' && (
        <div style={{ padding: '10px 0', textAlign: 'center', animation: 'scaleIn 0.2s ease' }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--green-dark)' }}>✓ Order placed</span>
          <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 8 }}>
            {order.fillCount > 0 ? `${order.fillCount} filled` : 'On book'}
          </span>
        </div>
      )}
      {order.status === 'err' && (
        <div style={{ padding: '10px 0', animation: 'scaleIn 0.2s ease' }}>
          <div style={{ fontSize: 11, color: 'var(--pink-dark)', marginBottom: 4, lineHeight: 1.4 }}>{order.message}</div>
          <button onClick={() => setOrder({ status: 'idle' })} style={{ fontSize: 10, color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>Dismiss</button>
        </div>
      )}
    </div>
  )
}

export default function MarketCard({ market, orderbook, strikePrice, currentBTCPrice, secondsUntilExpiry, liveMode, onRefresh, marketMode = '15m', predictedPrice }: MarketCardProps) {
  const [countdown, setCountdown]     = useState(secondsUntilExpiry)
  const [spinning, setSpinning]       = useState(false)
  const [sellingAll, setSellingAll]   = useState(false)
  const [sellingHalf, setSellingHalf] = useState(false)
  const [limitingAll, setLimitingAll] = useState(false)
  const [cancelingAll, setCancelingAll] = useState(false)
  const [flipping, setFlipping]       = useState(false)
  const [side, setSide]               = useState<'yes' | 'no'>('yes')
  const [hotkeyFlash, setHotkeyFlash] = useState<string | null>(null)
  const [allingIn, setAllingIn]       = useState(false)

  async function batchSell(route: string, setter: (v: boolean) => void) {
    setter(true)
    try {
      const d = await fetch('/api/positions', { cache: 'no-store' }).then(r => r.json())
      await Promise.all((d.positions ?? []).map((pos: { ticker: string; position: number }) => {
        const s = pos.position > 0 ? 'yes' : 'no'
        return fetch(route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: pos.ticker, side: s, count: Math.abs(pos.position) }) })
      }))
    } finally { setter(false) }
  }

  async function batchLimitSell() {
    setLimitingAll(true)
    try {
      const d = await fetch('/api/positions', { cache: 'no-store' }).then(r => r.json())
      const restingSells = (d.orders ?? []).filter((o: { action: string; status: string }) => o.action === 'sell' && o.status === 'resting')
      await Promise.all(restingSells.map((o: { order_id: string }) => fetch(`/api/cancel-order/${o.order_id}`, { method: 'DELETE' })))
      await Promise.all(
        (d.positions ?? [])
          .filter((pos: { position: number }) => pos.position !== 0)
          .map((pos: { ticker: string; position: number }) => {
            const s = pos.position > 0 ? 'yes' : 'no'
            return fetch('/api/limit-sell-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: pos.ticker, side: s, count: Math.abs(pos.position) }) })
          })
      )
    } finally { setLimitingAll(false) }
  }

  async function cancelAllOrders() {
    setCancelingAll(true)
    try {
      const d = await fetch('/api/positions', { cache: 'no-store' }).then(r => r.json())
      await Promise.all((d.orders ?? []).filter((o: { status: string }) => o.status === 'resting').map((o: { order_id: string }) => fetch(`/api/cancel-order/${o.order_id}`, { method: 'DELETE' })))
    } finally { setCancelingAll(false) }
  }

  async function allIn() {
    if (!liveMode || !market) return
    setAllingIn(true)
    const ask = side === 'yes' ? market.yes_ask : market.no_ask
    try {
      const balData  = await fetch('/api/balance', { cache: 'no-store' }).then(r => r.json())
      const availCents = balData.balance ?? 0
      const maxAfford  = ask > 0 ? Math.floor(availCents / ask) : 0
      const count      = Math.min(500, Math.max(1, maxAfford))
      if (maxAfford < 1) { setHotkeyFlash('Insufficient balance'); setTimeout(() => setHotkeyFlash(null), 3000); return }
      const res  = await fetch('/api/place-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: market.ticker, side, count, ...(side === 'yes' ? { yesPrice: ask } : { noPrice: ask }), clientOrderId: `allin-${side}-${Date.now()}` }) })
      const data = await res.json()
      setHotkeyFlash(data.ok ? `All in ${side} · ${count} @ ${ask}¢` : `Failed: ${data.error ?? 'unknown'}`)
    } catch { setHotkeyFlash('Network error') }
    finally { setAllingIn(false); setTimeout(() => setHotkeyFlash(null), 3000) }
  }

  async function sellHalf() {
    if (!liveMode || !market) return
    setSellingHalf(true)
    try {
      const d   = await fetch('/api/positions', { cache: 'no-store' }).then(r => r.json())
      const pos = (d.positions ?? []).find((p: { ticker: string; position: number }) => p.ticker === market.ticker)
      if (!pos || pos.position === 0) { setHotkeyFlash('No position to sell'); setTimeout(() => setHotkeyFlash(null), 2500); return }
      const half = Math.max(1, Math.floor(Math.abs(pos.position) / 2))
      const s    = pos.position > 0 ? 'yes' : 'no'
      await fetch('/api/sell-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: market.ticker, side: s, count: half }) })
      setHotkeyFlash(`Sold ${half} of ${Math.abs(pos.position)} contracts`)
      setTimeout(() => setHotkeyFlash(null), 3000)
    } catch { setHotkeyFlash('Sell error') }
    finally { setSellingHalf(false) }
  }

  async function flipPosition() {
    if (!liveMode || !market) return
    setFlipping(true)
    try {
      const d   = await fetch('/api/positions', { cache: 'no-store' }).then(r => r.json())
      const pos = (d.positions ?? []).find((p: { ticker: string; position: number }) => p.ticker === market.ticker)
      const closeSide = pos && pos.position > 0 ? 'yes' : pos && pos.position < 0 ? 'no' : null
      if (closeSide && Math.abs(pos.position) > 0) {
        await fetch('/api/sell-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: market.ticker, side: closeSide, count: Math.abs(pos.position) }) })
      }
      const newSide  = side
      const ask      = newSide === 'yes' ? market.yes_ask : market.no_ask
      const balData  = await fetch('/api/balance', { cache: 'no-store' }).then(r => r.json())
      const maxAfford = ask > 0 ? Math.floor((balData.balance ?? 0) / ask) : 0
      const count     = Math.min(500, Math.max(1, maxAfford))
      if (count > 0) {
        const res  = await fetch('/api/place-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: market.ticker, side: newSide, count, ...(newSide === 'yes' ? { yesPrice: ask } : { noPrice: ask }), clientOrderId: `flip-${newSide}-${Date.now()}` }) })
        const data = await res.json()
        setHotkeyFlash(data.ok ? `Flipped → ${newSide.toUpperCase()} ${count}` : `Flip error: ${data.error ?? 'unknown'}`)
      } else {
        setHotkeyFlash(closeSide ? 'Closed position — no balance to flip' : 'Insufficient balance')
      }
    } catch { setHotkeyFlash('Flip error') }
    finally { setFlipping(false); setTimeout(() => setHotkeyFlash(null), 3000) }
  }

  useEffect(() => {
    if (!liveMode || !market) return
    let flashTimer: ReturnType<typeof setTimeout>
    function flash(msg: string) { setHotkeyFlash(msg); clearTimeout(flashTimer); flashTimer = setTimeout(() => setHotkeyFlash(null), 2500) }
    async function buy(dollars: number) {
      const ask = side === 'yes' ? market!.yes_ask : market!.no_ask
      const cnt = Math.max(1, Math.floor(dollars / (ask / 100)))
      flash(`Buying ${side} $${dollars}…`)
      try {
        const res  = await fetch('/api/place-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: market!.ticker, side, count: cnt, ...(side === 'yes' ? { yesPrice: ask } : { noPrice: ask }), clientOrderId: `hotkey-${side}-${Date.now()}` }) })
        const data = await res.json()
        flash(data.ok ? `✓ ${side} $${dollars}` : `Failed: ${data.error ?? 'Order failed'}`)
      } catch { flash('Network error') }
    }
    function onKey(e: KeyboardEvent) {
      if (!e.shiftKey) return
      if (e.code === 'Digit1') { e.preventDefault(); buy(10) }
      else if (e.code === 'Digit2') { e.preventDefault(); buy(20) }
      else if (e.code === 'Digit3') { e.preventDefault(); buy(50) }
      else if (e.code === 'Digit4') { e.preventDefault(); batchLimitSell() }
      else if (e.code === 'Digit5') { e.preventDefault(); cancelAllOrders() }
      else if (e.code === 'Digit6') { e.preventDefault(); batchSell('/api/sell-order', setSellingAll) }
      else if (e.code === 'KeyY')   { e.preventDefault(); setSide('yes') }
      else if (e.code === 'KeyN')   { e.preventDefault(); setSide('no') }
      else if (e.code === 'KeyA')   { e.preventDefault(); allIn() }
      else if (e.code === 'KeyH')   { e.preventDefault(); sellHalf() }
      else if (e.code === 'KeyF')   { e.preventDefault(); flipPosition() }
    }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); clearTimeout(flashTimer) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode, market, side])

  function handleRefresh() {
    if (spinning) return
    setSpinning(true)
    onRefresh?.()
    setTimeout(() => setSpinning(false), 800)
  }

  useEffect(() => { setCountdown(secondsUntilExpiry) }, [secondsUntilExpiry])
  useEffect(() => {
    const id = setInterval(() => setCountdown(c => Math.max(0, c - 1)), 1000)
    return () => clearInterval(id)
  }, [])

  const isHourly = marketMode === 'hourly'
  const mins    = Math.floor(countdown / 60)
  const secs    = Math.floor(countdown % 60)
  const urgencyThreshold = isHourly ? 600 : 120  // 10 min warning for hourly, 2 min for 15m
  const urgency = countdown > 0 && countdown < urgencyThreshold
  // In hourly mode, compare Grok's predicted price vs strike; fall back to current price
  const comparePrice = (isHourly && predictedPrice && predictedPrice > 0) ? predictedPrice : currentBTCPrice
  const above   = strikePrice > 0 && comparePrice > strikePrice
  const diff    = currentBTCPrice - strikePrice
  const pct     = strikePrice > 0 ? (diff / strikePrice) * 100 : 0
  const trendCol = above ? 'var(--green-dark)' : 'var(--pink-dark)'
  const ringTotal = isHourly ? 3600 : 900

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* Top accent line */}
      <div style={{ height: 3, background: above ? 'var(--green)' : 'var(--pink)', transition: 'background 0.5s' }} />

      <div style={{ padding: '16px 18px' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="status-dot live" />
            <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.02em' }}>
            {isHourly ? 'Hourly Market' : 'Active Market'}
          </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {market && (
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'var(--text-muted)' }}>
                {market.ticker}
              </span>
            )}
            <button onClick={handleRefresh} title="Refresh"
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, color: 'var(--text-muted)', padding: 0, lineHeight: 1 }}>
              <span style={{ display: 'inline-block', animation: spinning ? 'spin-slow 0.8s linear infinite' : 'none' }}>↻</span>
            </button>
          </div>
        </div>

        {market ? (
          <>
            {/* Strike price */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
                {isHourly ? 'Strike (Most Liquid)' : 'Strike Price'}
              </div>
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 26, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.03em', lineHeight: 1 }}>
                {strikePrice > 0 ? fmtD(strikePrice) : '—'}
              </div>
            </div>

            {/* Hourly mode: Grok predicted price row */}
            {isHourly && predictedPrice && predictedPrice > 0 && (
              <div style={{
                marginBottom: 12, padding: '8px 12px', borderRadius: 10,
                background: above ? 'var(--green-pale)' : 'var(--pink-pale)',
                border: `1px solid ${above ? 'rgba(45,158,107,0.25)' : 'rgba(224,111,160,0.25)'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              }}>
                <div>
                  <div style={{ fontSize: 8, color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 2 }}>Grok Forecast</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 17, fontWeight: 800, color: trendCol, lineHeight: 1 }}>
                    {fmt(predictedPrice)}
                  </div>
                </div>
                <div style={{ fontSize: 22, color: trendCol }}>{above ? '↑' : '↓'}</div>
              </div>
            )}

            {/* BTC vs Strike — flat row, no box */}
            <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', paddingBottom: 14, borderBottom: '1px solid var(--border)', marginBottom: 14 }}>
              <div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)', marginBottom: 3 }}>BTC now</div>
                <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 20, fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1 }}>
                  {currentBTCPrice > 0 ? fmt(currentBTCPrice) : '—'}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 20, color: trendCol, lineHeight: 1 }}>{above ? '↑' : '↓'}</span>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 700, color: trendCol }}>
                    {strikePrice > 0 ? `${diff >= 0 ? '+' : ''}${fmt(diff)}` : '—'}
                  </div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)', marginTop: 1 }}>
                    {pct >= 0 ? '+' : ''}{pct.toFixed(3)}%
                  </div>
                </div>
              </div>
            </div>

            {/* Trade box — no extra border, lives in card */}
            <div style={{ marginBottom: liveMode ? 14 : 0 }}>
              <TradeBox
                yesBid={market.yes_bid} yesAsk={market.yes_ask}
                noBid={market.no_bid}   noAsk={market.no_ask}
                ticker={market.ticker}  liveMode={liveMode}
                side={side} onSideChange={setSide}
              />
            </div>

            {/* Live-only actions */}
            {liveMode && (
              <>
                {/* All In — primary action */}
                <button disabled={allingIn} onClick={allIn}
                  style={{
                    width: '100%', marginBottom: 8, padding: '11px 0', borderRadius: 10,
                    cursor: allingIn ? 'not-allowed' : 'pointer',
                    border: `1.5px solid ${side === 'yes' ? 'var(--green)' : 'var(--pink)'}`,
                    background: 'transparent',
                    fontSize: 12, fontWeight: 800, letterSpacing: '0.04em',
                    color: side === 'yes' ? 'var(--green-dark)' : 'var(--pink-dark)',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => { if (!allingIn) { e.currentTarget.style.background = side === 'yes' ? 'var(--green)' : 'var(--pink)'; e.currentTarget.style.color = '#fff' } }}
                  onMouseLeave={e => { if (!allingIn) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = side === 'yes' ? 'var(--green-dark)' : 'var(--pink-dark)' } }}
                >
                  {allingIn ? '…' : `All In ${side === 'yes' ? 'Yes' : 'No'}`}
                </button>

                {/* Sell 50% + Flip — prominent secondary row */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
                  <button disabled={sellingHalf} onClick={sellHalf}
                    style={{
                      padding: '9px 0', borderRadius: 9, cursor: sellingHalf ? 'not-allowed' : 'pointer',
                      border: '1.5px solid rgba(176,118,16,0.5)', background: 'var(--amber-pale)',
                      fontSize: 11, fontWeight: 700, color: 'var(--amber)',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={e => { if (!sellingHalf) { e.currentTarget.style.background = 'var(--amber)'; e.currentTarget.style.color = '#fff' } }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'var(--amber-pale)'; e.currentTarget.style.color = 'var(--amber)' }}
                  >
                    {sellingHalf ? '…' : 'Sell 50%'}
                  </button>
                  <button disabled={flipping} onClick={flipPosition}
                    style={{
                      padding: '9px 0', borderRadius: 9, cursor: flipping ? 'not-allowed' : 'pointer',
                      border: '1.5px solid rgba(60,110,160,0.4)', background: 'var(--blue-pale)',
                      fontSize: 11, fontWeight: 700, color: 'var(--blue-dark)',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={e => { if (!flipping) { e.currentTarget.style.background = 'var(--blue)'; e.currentTarget.style.color = '#fff' } }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'var(--blue-pale)'; e.currentTarget.style.color = 'var(--blue-dark)' }}
                  >
                    {flipping ? '…' : `Flip → ${side === 'yes' ? 'NO' : 'YES'}`}
                  </button>
                </div>

                {/* Text-link actions */}
                <div style={{ display: 'flex', gap: 0, borderTop: '1px solid var(--border)', paddingTop: 8, marginBottom: 14 }}>
                  {[
                    { label: limitingAll  ? '…' : 'Limit 99¢',  fn: batchLimitSell,   col: 'var(--brown)' },
                    { label: cancelingAll ? '…' : 'Cancel All',  fn: cancelAllOrders,   col: 'var(--text-muted)' },
                    { label: sellingAll   ? '…' : 'Sell All',    fn: () => batchSell('/api/sell-order', setSellingAll), col: 'var(--pink-dark)' },
                  ].map(({ label, fn, col }, i, arr) => (
                    <button key={label} onClick={fn}
                      style={{
                        flex: 1, padding: '5px 4px', background: 'none', border: 'none',
                        borderRight: i < arr.length - 1 ? '1px solid var(--border)' : 'none',
                        fontSize: 10, fontWeight: 600, color: col, cursor: 'pointer',
                        transition: 'opacity 0.12s',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.opacity = '0.55' }}
                      onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* Countdown — inline, no box */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 4 }}>
              <CountdownRing seconds={countdown} total={ringTotal} urgent={urgency} />
              <div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 1 }}>
                  {isHourly ? 'Hour closes in' : 'Expires in'}
                </div>
                <div style={{
                  fontFamily: 'var(--font-geist-mono)', fontSize: 22, fontWeight: 800, lineHeight: 1,
                  color: urgency ? 'var(--pink-dark)' : 'var(--text-primary)',
                  animation: urgency ? 'urgentPulse 1s ease infinite' : 'none',
                }}>
                  {`${mins}:${String(secs).padStart(2, '0')}`}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>
                  {isHourly ? 'KXBTCD hourly' : 'CF Benchmarks'}
                </div>
              </div>
            </div>

            {/* Volume / OI */}
            <div style={{ display: 'flex', gap: 20, marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              {[['Volume', market.volume], ['Open Interest', market.open_interest]].map(([k, v]) => (
                <div key={String(k)}>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>{k}</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
                    {Number(v).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div style={{ padding: '32px 0', textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>No market</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {isHourly ? 'Run pipeline to discover KXBTCD market' : 'No open KXBTC15M markets'}
            </div>
          </div>
        )}

        {/* Hotkeys */}
        {liveMode && market && (
          <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border)', display: 'flex', flexWrap: 'wrap', gap: '3px 8px', alignItems: 'center' }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: side === 'yes' ? 'var(--green-dark)' : 'var(--pink-dark)', marginRight: 4 }}>
              {side === 'yes' ? 'YES' : 'NO'}
            </span>
            {[['⇧Y','YES'],['⇧N','NO'],['⇧A','All In'],['⇧1','$10'],['⇧2','$25'],['⇧3','$50'],['⇧H','½ Sell'],['⇧F','Flip'],['⇧4','Limit'],['⇧5','Cancel'],['⇧6','Sell All']].map(([key, label]) => (
              <span key={key} style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
                <span style={{ fontWeight: 700, color: 'var(--text-secondary)' }}>{key}</span> {label}
              </span>
            ))}
          </div>
        )}

        {/* Hotkey flash */}
        {hotkeyFlash && (
          <div style={{
            marginTop: 8, padding: '8px 12px', borderRadius: 8,
            background: 'var(--bg-secondary)', border: '1px solid var(--border)',
            fontSize: 11, color: 'var(--text-primary)', fontWeight: 600,
            animation: 'fadeSlideIn 0.2s ease',
          }}>
            {hotkeyFlash}
          </div>
        )}
      </div>
    </div>
  )
}
