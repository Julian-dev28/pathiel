'use client'

import { useEffect, useState } from 'react'
import type { HLAccount } from '@/lib/hyperliquid'

interface HLMarketCardProps {
  btcPrice:  number | null
  account:   HLAccount | null
  onRefresh: () => void
}

interface OBLevel { px: string; sz: string }
interface Orderbook { bids: OBLevel[]; asks: OBLevel[] }

type OrderState =
  | { status: 'idle' }
  | { status: 'placing' }
  | { status: 'ok'; sizeBTC: number; midPrice: number }
  | { status: 'err'; message: string }

const RISK_PCTS  = [1, 2, 5, 10] as const
const LEVERAGES  = [1, 2, 3, 5, 10, 20] as const

const fmtPrice = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })

const fmtPriceD = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

function SkeletonBar({ width }: { width: string }) {
  return (
    <div style={{
      height: 22, borderRadius: 4, width,
      background: 'linear-gradient(90deg, var(--bg-secondary) 25%, var(--border) 50%, var(--bg-secondary) 75%)',
      backgroundSize: '200% 100%',
      animation: 'shimmer 1.4s ease infinite',
    }} />
  )
}

function OrderbookBars({ book }: { book: Orderbook | null }) {
  if (!book) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonBar key={i} width={`${85 - i * 8}%`} />
        ))}
        <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonBar key={i} width={`${60 + i * 7}%`} />
        ))}
      </div>
    )
  }

  const topAsks = [...book.asks].slice(0, 5).reverse()
  const topBids = book.bids.slice(0, 5)

  const allSizes = [...topAsks, ...topBids].map(l => parseFloat(l.sz))
  const maxSz = Math.max(...allSizes, 0.001)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {topAsks.map((lvl, i) => {
        const pct = (parseFloat(lvl.sz) / maxSz) * 100
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, position: 'relative', height: 20 }}>
            <div style={{
              position: 'absolute', right: 0, top: 0, bottom: 0,
              width: `${pct}%`, background: 'var(--pink-pale)',
              borderRadius: 3, transition: 'width 0.4s ease',
            }} />
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--pink-dark)', zIndex: 1, flex: 1 }}>
              {fmtPrice(parseFloat(lvl.px))}
            </span>
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'var(--text-muted)', zIndex: 1, minWidth: 40, textAlign: 'right' }}>
              {parseFloat(lvl.sz).toFixed(3)}
            </span>
          </div>
        )
      })}

      <div style={{ height: 1, background: 'var(--border)', margin: '3px 0' }} />

      {topBids.map((lvl, i) => {
        const pct = (parseFloat(lvl.sz) / maxSz) * 100
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, position: 'relative', height: 20 }}>
            <div style={{
              position: 'absolute', right: 0, top: 0, bottom: 0,
              width: `${pct}%`, background: 'var(--green-pale)',
              borderRadius: 3, transition: 'width 0.4s ease',
            }} />
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--green-dark)', zIndex: 1, flex: 1 }}>
              {fmtPrice(parseFloat(lvl.px))}
            </span>
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'var(--text-muted)', zIndex: 1, minWidth: 40, textAlign: 'right' }}>
              {parseFloat(lvl.sz).toFixed(3)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default function HLMarketCard({ btcPrice, account, onRefresh }: HLMarketCardProps) {
  const [spinning,  setSpinning]  = useState(false)
  const [amount,    setAmount]    = useState<string>('')
  const [leverage,  setLeverage]  = useState<number>(5)
  const [order,     setOrder]     = useState<OrderState>({ status: 'idle' })
  const [book, setBook]         = useState<Orderbook | null>(null)

  useEffect(() => {
    let canceled = false
    async function fetchBook() {
      try {
        const res  = await fetch('/api/hl/orderbook', { cache: 'no-store' })
        const data = await res.json() as Orderbook
        if (!canceled) setBook(data)
      } catch {}
    }
    fetchBook()
    const id = setInterval(fetchBook, 3000)
    return () => { canceled = true; clearInterval(id) }
  }, [])

  function handleRefresh() {
    if (spinning) return
    setSpinning(true)
    onRefresh()
    setTimeout(() => setSpinning(false), 800)
  }

  async function placeOrder(side: 'long' | 'short') {
    setOrder({ status: 'placing' })
    try {
      const res  = await fetch('/api/hl/place-order', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ side, riskUSD: parseFloat(amount) || 0, leverage }),
      })
      const data = await res.json() as { ok: boolean; sizeBTC?: number; midPrice?: number; error?: string }
      if (!res.ok || !data.ok) {
        setOrder({ status: 'err', message: data.error ?? `HTTP ${res.status}` })
      } else {
        setOrder({ status: 'ok', sizeBTC: data.sizeBTC ?? 0, midPrice: data.midPrice ?? 0 })
        onRefresh()
        setTimeout(() => setOrder({ status: 'idle' }), 5000)
      }
    } catch (err) {
      setOrder({ status: 'err', message: String(err) })
    }
  }

  async function closePosition() {
    setOrder({ status: 'placing' })
    try {
      const res  = await fetch('/api/hl/close-position', { method: 'POST' })
      const data = await res.json() as { ok: boolean; sizeBTC?: number; midPrice?: number; error?: string }
      if (!res.ok || !data.ok) {
        setOrder({ status: 'err', message: data.error ?? `HTTP ${res.status}` })
      } else {
        setOrder({ status: 'ok', sizeBTC: data.sizeBTC ?? 0, midPrice: data.midPrice ?? 0 })
        onRefresh()
        setTimeout(() => setOrder({ status: 'idle' }), 5000)
      }
    } catch (err) {
      setOrder({ status: 'err', message: String(err) })
    }
  }

  const pos      = account?.position ?? null
  const isLong   = pos?.side === 'long'
  const isShort  = pos?.side === 'short'
  const priceCol = isLong ? 'var(--green-dark)' : isShort ? 'var(--pink-dark)' : 'var(--text-primary)'

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ height: 3, background: isLong ? 'var(--green)' : isShort ? 'var(--pink)' : 'var(--border)', transition: 'background 0.4s' }} />

      <div style={{ padding: '16px 18px' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span className="status-dot live" />
            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '-0.01em' }}>
              BTC-PERP · Hyperliquid
            </span>
          </div>
          <button onClick={handleRefresh} title="Refresh"
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, color: 'var(--text-muted)', padding: 0, lineHeight: 1 }}>
            <span style={{ display: 'inline-block', animation: spinning ? 'spin-slow 0.8s linear infinite' : 'none' }}>↻</span>
          </button>
        </div>

        {/* BTC Price */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>
            BTC Price
          </div>
          <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 30, fontWeight: 800, color: priceCol, letterSpacing: '-0.03em', lineHeight: 1, transition: 'color 0.4s' }}>
            {btcPrice ? fmtPrice(btcPrice) : '—'}
          </div>
        </div>

        {/* Position pill + details */}
        <div style={{ marginBottom: 16, paddingBottom: 14, borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: pos ? 10 : 0 }}>
            {!pos && (
              <span style={{
                display: 'inline-flex', alignItems: 'center',
                padding: '3px 10px', borderRadius: 100,
                background: 'var(--bg-secondary)', border: '1px solid var(--border)',
                fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', letterSpacing: '0.04em',
              }}>
                FLAT
              </span>
            )}
            {pos && (
              <span style={{
                display: 'inline-flex', alignItems: 'center',
                padding: '3px 10px', borderRadius: 100,
                background: isLong ? 'var(--green-pale)' : 'var(--pink-pale)',
                border: `1px solid ${isLong ? 'rgba(46,158,104,0.3)' : 'rgba(190,74,64,0.3)'}`,
                fontSize: 10, fontWeight: 700,
                color: isLong ? 'var(--green-dark)' : 'var(--pink-dark)',
                letterSpacing: '0.04em',
              }}>
                {isLong ? 'LONG' : 'SHORT'}
              </span>
            )}
          </div>

          {pos && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 2 }}>Size</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
                    {pos.sizeBTC.toFixed(4)} BTC
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 2 }}>Entry</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)' }}>
                    {fmtPrice(pos.entryPx)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 2 }}>Unr. PnL</div>
                  <div style={{
                    fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700,
                    color: pos.unrealizedPnl >= 0 ? 'var(--green-dark)' : 'var(--pink-dark)',
                  }}>
                    {pos.unrealizedPnl >= 0 ? '+' : ''}{fmtPriceD(pos.unrealizedPnl)}
                  </div>
                </div>
              </div>
              {btcPrice && (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingTop: 6, borderTop: '1px solid var(--border)' }}>
                  <span style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>Total notional</span>
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)' }}>
                    {fmtPrice(pos.sizeBTC * btcPrice)}
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Order Book */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8, display: 'flex', justifyContent: 'space-between' }}>
            <span>Order Book</span>
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontWeight: 400 }}>price · size BTC</span>
          </div>
          <OrderbookBars book={book} />
        </div>

        {/* Amount input */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 7 }}>
            Amount (USD)
          </div>
          <div style={{ position: 'relative', marginBottom: 6 }}>
            <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', fontSize: 13, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)', pointerEvents: 'none' }}>$</span>
            <input
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={amount}
              onChange={e => setAmount(e.target.value)}
              style={{
                width: '100%', boxSizing: 'border-box',
                padding: '8px 10px 8px 22px',
                borderRadius: 8, border: '1.5px solid var(--border)',
                background: 'var(--bg-secondary)',
                fontFamily: 'var(--font-geist-mono)', fontSize: 13, fontWeight: 700,
                color: 'var(--text-primary)', outline: 'none',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--blue)' }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 5 }}>
            {RISK_PCTS.map(pct => {
              const equity = account?.totalEquity ?? 0
              return (
                <button key={pct}
                  onClick={() => setAmount((equity * pct / 100).toFixed(2))}
                  style={{
                    flex: 1, padding: '5px 0', borderRadius: 7,
                    border: '1px solid var(--border)',
                    background: 'var(--bg-secondary)',
                    fontSize: 10, fontWeight: 700,
                    color: 'var(--text-muted)',
                    cursor: 'pointer', transition: 'all 0.12s',
                    fontFamily: 'var(--font-geist-mono)',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--blue)'; e.currentTarget.style.color = 'var(--blue)' }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' }}
                >
                  {pct}%
                </button>
              )
            })}
          </div>
        </div>

        {/* Leverage selector */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 7 }}>Leverage</div>
          <div style={{ display: 'flex', gap: 5 }}>
            {LEVERAGES.map(lev => {
              const active = leverage === lev
              return (
                <button key={lev} onClick={() => setLeverage(lev)}
                  style={{
                    flex: 1, padding: '6px 0', borderRadius: 8,
                    border: active ? '1.5px solid var(--amber)' : '1px solid var(--border)',
                    background: active ? 'var(--amber-pale)' : 'var(--bg-secondary)',
                    fontSize: 11, fontWeight: 700,
                    color: active ? 'var(--amber)' : 'var(--text-secondary)',
                    cursor: 'pointer', transition: 'all 0.12s',
                    fontFamily: 'var(--font-geist-mono)',
                  }}>
                  {lev}×
                </button>
              )
            })}
          </div>
        </div>

        {/* Order notional preview */}
        {parseFloat(amount) > 0 && btcPrice && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 10px', marginBottom: 8, borderRadius: 8, background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
            <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Order notional</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
                {fmtPrice(parseFloat(amount) * leverage)}
              </span>
              <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'var(--text-muted)' }}>
                ≈ {(parseFloat(amount) * leverage / btcPrice).toFixed(5)} BTC
              </span>
            </div>
          </div>
        )}

        {/* Trade buttons */}
        <div style={{ display: 'grid', gridTemplateColumns: pos ? '1fr 1fr 1fr' : '1fr 1fr', gap: 6, marginBottom: 10 }}>
          <button
            disabled={order.status === 'placing'}
            onClick={() => placeOrder('long')}
            style={{
              padding: '10px 0', borderRadius: 9, cursor: order.status === 'placing' ? 'not-allowed' : 'pointer',
              border: '1.5px solid var(--green)', background: 'var(--green-pale)',
              fontSize: 12, fontWeight: 800, color: 'var(--green-dark)', letterSpacing: '0.02em',
              transition: 'all 0.12s',
            }}
            onMouseEnter={e => { if (order.status !== 'placing') { e.currentTarget.style.background = 'var(--green)'; e.currentTarget.style.color = '#fff' } }}
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--green-pale)'; e.currentTarget.style.color = 'var(--green-dark)' }}
          >
            ↑ Long
          </button>
          <button
            disabled={order.status === 'placing'}
            onClick={() => placeOrder('short')}
            style={{
              padding: '10px 0', borderRadius: 9, cursor: order.status === 'placing' ? 'not-allowed' : 'pointer',
              border: '1.5px solid var(--pink)', background: 'var(--pink-pale)',
              fontSize: 12, fontWeight: 800, color: 'var(--pink-dark)', letterSpacing: '0.02em',
              transition: 'all 0.12s',
            }}
            onMouseEnter={e => { if (order.status !== 'placing') { e.currentTarget.style.background = 'var(--pink)'; e.currentTarget.style.color = '#fff' } }}
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--pink-pale)'; e.currentTarget.style.color = 'var(--pink-dark)' }}
          >
            ↓ Short
          </button>
          {pos && (
            <button
              disabled={order.status === 'placing'}
              onClick={closePosition}
              style={{
                padding: '10px 0', borderRadius: 9, cursor: order.status === 'placing' ? 'not-allowed' : 'pointer',
                border: '1.5px solid rgba(176,118,16,0.6)', background: 'var(--amber-pale)',
                fontSize: 12, fontWeight: 800, color: 'var(--amber)', letterSpacing: '0.02em',
                transition: 'all 0.12s',
              }}
              onMouseEnter={e => { if (order.status !== 'placing') { e.currentTarget.style.background = 'var(--amber)'; e.currentTarget.style.color = '#fff' } }}
              onMouseLeave={e => { e.currentTarget.style.background = 'var(--amber-pale)'; e.currentTarget.style.color = 'var(--amber)' }}
            >
              ✕ Close
            </button>
          )}
        </div>

        {/* Order status */}
        {order.status === 'placing' && (
          <div style={{ padding: '8px 0', textAlign: 'center', fontSize: 11, color: 'var(--text-muted)' }}>
            Placing…
          </div>
        )}
        {order.status === 'ok' && (
          <div style={{ padding: '8px 10px', borderRadius: 8, background: 'var(--green-pale)', border: '1px solid rgba(46,158,104,0.2)', animation: 'fadeSlideIn 0.2s ease' }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--green-dark)' }}>✓ Filled</span>
            <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 8, fontFamily: 'var(--font-geist-mono)' }}>
              {order.sizeBTC.toFixed(4)} BTC @ {fmtPrice(order.midPrice)}
            </span>
          </div>
        )}
        {order.status === 'err' && (
          <div style={{ padding: '8px 10px', borderRadius: 8, background: 'var(--pink-pale)', border: '1px solid rgba(190,74,64,0.2)', animation: 'fadeSlideIn 0.2s ease' }}>
            <div style={{ fontSize: 11, color: 'var(--pink-dark)', lineHeight: 1.4, marginBottom: 4 }}>{order.message}</div>
            <button onClick={() => setOrder({ status: 'idle' })} style={{ fontSize: 10, color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
              Dismiss
            </button>
          </div>
        )}

        {/* Info strip */}
        <div style={{ marginTop: 14, paddingTop: 10, borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {[
            ['Agent', 'OpenRouter'],
            ['Leverage', `${leverage}×`],
            ['Market', 'BTC-PERP'],
          ].map(([label, val]) => (
            <div key={label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 600, marginBottom: 2 }}>{label}</div>
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, fontWeight: 700, color: 'var(--text-secondary)' }}>{val}</div>
            </div>
          ))}
        </div>

      </div>
    </div>
  )
}
