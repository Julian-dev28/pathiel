'use client'

import { useEffect, useState, useCallback } from 'react'
import type { KalshiBalance, KalshiPosition, KalshiOrder, KalshiFill } from '@/lib/types'

interface PortfolioData {
  balance: KalshiBalance | null
  positions: KalshiPosition[]
  orders: KalshiOrder[]
  fills: KalshiFill[]
}

function fmtTicker(ticker: string): string {
  const m = ticker.match(/KXBTC15M-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})/)
  if (m) {
    const [, , mon, day, hh, mm] = m
    return `${parseInt(day)} ${mon[0]+mon.slice(1).toLowerCase()} ${hh}:${mm}`
  }
  return ticker.replace('KXBTC15M-', '')
}

function orderPrice(ord: KalshiOrder): number {
  return ord.side === 'yes' ? ord.yes_price : ord.no_price
}

// Small colored side indicator — no background, just colored text
function SideTag({ action, side }: { action: string; side: string }) {
  const isYes = side === 'yes'
  const isBuy = action === 'buy'
  const color = isYes ? 'var(--green-dark)' : 'var(--pink-dark)'
  const label = `${isBuy ? 'Buy' : 'Sell'} ${isYes ? 'Yes' : 'No'}`
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, color,
      letterSpacing: '-0.01em',
    }}>
      {label}
    </span>
  )
}

export default function PositionsPanel({ liveMode }: { liveMode: boolean }) {
  const [data, setData] = useState<PortfolioData>({ balance: null, positions: [], orders: [], fills: [] })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchPortfolio = useCallback(async () => {
    if (!liveMode) return
    setLoading(true)
    setError(null)
    try {
      const [balRes, posRes] = await Promise.all([
        fetch('/api/balance', { cache: 'no-store' }),
        fetch('/api/positions', { cache: 'no-store' }),
      ])

      const balBody = await balRes.json().catch(() => null)
      if (!balRes.ok) {
        const rawErr = balBody?.error
        const base = typeof rawErr === 'string' ? rawErr
          : (rawErr?.message ?? rawErr?.code)
          ? String(rawErr.message ?? rawErr.code)
          : `Auth error (HTTP ${balRes.status})`
        const errMsg = base === 'authentication_error'
          ? 'Kalshi maintenance — try again shortly'
          : base
        setError(errMsg)
        setLoading(false)
        return
      }

      let positions: KalshiPosition[] = []
      let orders: KalshiOrder[] = []
      let fills: KalshiFill[] = []
      if (posRes.ok) {
        const d = await posRes.json()
        positions = d.positions ?? []
        orders = d.orders ?? []
        fills = d.fills ?? []
      }

      setData({ balance: balBody, positions, orders, fills })
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [liveMode])

  useEffect(() => {
    if (!liveMode) return
    fetchPortfolio()
    const id = setInterval(fetchPortfolio, 2_000)
    return () => clearInterval(id)
  }, [liveMode, fetchPortfolio])

  if (!liveMode) return null

  const { balance, positions, orders, fills } = data

  const availableCash  = balance ? balance.balance / 100 : null
  const positionsValue = balance ? balance.portfolio_value / 100 : null
  const inOrdersCents  = orders.reduce((sum, ord) => {
    const price = ord.side === 'yes' ? ord.yes_price : ord.no_price
    return sum + price * ord.remaining_count
  }, 0)
  const inOrders = inOrdersCents / 100
  const totalEquity = availableCash !== null && positionsValue !== null
    ? availableCash + positionsValue
    : null

  const fmt = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="card" style={{ padding: '20px 20px 16px' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '-0.01em' }}>Kalshi Account</span>
        </div>
        <button onClick={fetchPortfolio} disabled={loading}
          style={{ background: 'none', border: 'none', cursor: loading ? 'wait' : 'pointer', fontSize: 13, color: 'var(--text-muted)', padding: 0, lineHeight: 1 }}
          title="Refresh">
          ↻
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 10, color: 'var(--red)', background: 'var(--red-pale)', borderRadius: 8, padding: '8px 10px', marginBottom: 14, lineHeight: 1.5 }}>
          {error}
        </div>
      )}

      {/* Equity */}
      {totalEquity !== null && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600, marginBottom: 4 }}>
            Total Equity
          </div>
          <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 30, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.03em', lineHeight: 1 }}>
            ${fmt(totalEquity)}
          </div>
        </div>
      )}

      {/* Breakdown row */}
      {totalEquity !== null && (
        <div style={{ display: 'flex', gap: 0, marginBottom: 18, borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', padding: '10px 0' }}>
          {[
            ['Available',  availableCash !== null ? `$${fmt(availableCash)}`   : '—', 'var(--text-primary)'],
            ['In Orders',  inOrders > 0           ? `$${fmt(inOrders)}`        : '$0.00', 'var(--amber)'],
            ['Positions',  positionsValue !== null ? `$${fmt(positionsValue)}` : '—', 'var(--green-dark)'],
          ].map(([label, val, col], idx, arr) => (
            <div key={label} style={{
              flex: 1, textAlign: idx === 0 ? 'left' : idx === arr.length - 1 ? 'right' : 'center',
            }}>
              <div style={{ fontSize: 8, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: 3 }}>{label}</div>
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: col }}>{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Open Positions */}
      {positions.length > 0 && (
        <section style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
            Positions · {positions.length}
          </div>
          {positions.slice(0, 6).map((pos, i) => {
            const isYes  = pos.position > 0
            const qty    = Math.abs(pos.position)
            const costDollars = pos.market_exposure / 100
            const rpnl   = pos.realized_pnl / 100
            const color  = isYes ? 'var(--green-dark)' : 'var(--pink-dark)'
            return (
              <div key={pos.ticker} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '8px 0',
                borderBottom: i < positions.length - 1 ? '1px solid var(--border)' : 'none',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ width: 3, height: 28, borderRadius: 2, background: color, flexShrink: 0 }} />
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-primary)', fontFamily: 'var(--font-geist-mono)' }}>
                      {qty}× <span style={{ color }}>{isYes ? 'YES' : 'NO'}</span>
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 1 }}>{fmtTicker(pos.ticker)}</div>
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 12, fontWeight: 700, color: rpnl !== 0 ? (rpnl >= 0 ? 'var(--green-dark)' : 'var(--pink)') : 'var(--text-primary)' }}>
                    {rpnl !== 0 ? `${rpnl >= 0 ? '+' : ''}$${fmt(rpnl)}` : `$${fmt(costDollars)}`}
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 1 }}>
                    {rpnl !== 0 ? 'realized' : 'cost'}
                  </div>
                </div>
              </div>
            )
          })}
        </section>
      )}

      {/* Resting Orders */}
      {orders.length > 0 && (
        <section style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
            Orders · {orders.length}
          </div>
          {orders.slice(0, 5).map((ord, i) => {
            const price = orderPrice(ord)
            const cost  = ((price / 100) * ord.remaining_count).toFixed(2)
            const isYes = ord.side === 'yes'
            return (
              <div key={ord.order_id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '7px 0',
                borderBottom: i < orders.length - 1 ? '1px solid var(--border)' : 'none',
              }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                    <SideTag action="buy" side={ord.side} />
                    <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>
                      {ord.remaining_count}× @ {price}¢
                    </span>
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'var(--font-geist-mono)' }}>
                    {fmtTicker(ord.ticker)}
                    {ord.fill_count > 0 && <span style={{ marginLeft: 6, color: 'var(--green-dark)' }}>{ord.fill_count} filled</span>}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-secondary)' }}>${cost}</span>
                  <CancelButton orderId={ord.order_id} onCancel={fetchPortfolio} />
                </div>
              </div>
            )
          })}
        </section>
      )}

      {/* Recent Fills */}
      {fills.length > 0 && (
        <section>
          <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
            Recent Fills · {fills.length}
          </div>
          {fills.slice(0, 8).map((fill, i) => {
            const price = fill.side === 'yes' ? fill.yes_price : fill.no_price
            const cost  = ((price / 100) * fill.count).toFixed(2)
            const time  = new Date(fill.created_time).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
            const isYes = fill.side === 'yes'
            const isBuy = fill.action === 'buy'
            const dotColor = isYes ? 'var(--green)' : 'var(--pink)'
            return (
              <div key={fill.fill_id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '7px 0',
                borderBottom: i < Math.min(fills.length, 8) - 1 ? '1px solid var(--border)' : 'none',
              }}>
                {/* Left: dot + action + trade info */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: dotColor, flexShrink: 0,
                    opacity: isBuy ? 1 : 0.5,
                  }} />
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <SideTag action={fill.action} side={fill.side} />
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, color: 'var(--text-primary)', fontWeight: 600 }}>
                        {fill.count}× @ {price}¢
                      </span>
                    </div>
                  </div>
                </div>

                {/* Right: amount + time */}
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 11, fontWeight: 700, color: 'var(--text-primary)' }}>
                    ${cost}
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 1 }}>{time}</div>
                </div>
              </div>
            )
          })}
        </section>
      )}

      {!loading && positions.length === 0 && orders.length === 0 && fills.length === 0 && balance && (
        <div style={{ textAlign: 'center', padding: '16px 0', fontSize: 11, color: 'var(--text-muted)' }}>
          No activity yet
        </div>
      )}

      {loading && !balance && (
        <div style={{ textAlign: 'center', padding: '16px 0', fontSize: 11, color: 'var(--text-muted)' }}>
          Connecting...
        </div>
      )}
    </div>
  )
}

function CancelButton({ orderId, onCancel }: { orderId: string; onCancel: () => void }) {
  const [canceling, setCanceling] = useState(false)

  async function handleCancel() {
    setCanceling(true)
    try {
      await fetch(`/api/cancel-order/${orderId}`, { method: 'DELETE' })
      onCancel()
    } finally {
      setCanceling(false)
    }
  }

  return (
    <button onClick={handleCancel} disabled={canceling} style={{
      background: 'none', border: '1px solid var(--border-bright)', borderRadius: 6,
      padding: '3px 9px', fontSize: 9, color: 'var(--text-muted)',
      cursor: canceling ? 'not-allowed' : 'pointer', fontWeight: 600,
    }}>
      {canceling ? '…' : 'Cancel'}
    </button>
  )
}
