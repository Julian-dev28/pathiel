'use client'

import { useState, useEffect, useRef } from 'react'
import type { HLAccount } from '@/lib/hyperliquid'

export interface PricePoint {
  timestamp: number
  price:     number
}

export interface HLTick {
  btcPrice:       number | null
  priceHistory:   PricePoint[]
  account:        HLAccount | null
  error:          string | null
  refreshAccount: () => void
}

const PRICE_MS   = 2_000
const ACCOUNT_MS = 5_000
const MAX_POINTS = 7200  // 4h at 2s

export function useHLTick(): HLTick {
  const [btcPrice,     setBtcPrice]     = useState<number | null>(null)
  const [priceHistory, setPriceHistory] = useState<PricePoint[]>([])
  const [account,      setAccount]      = useState<HLAccount | null>(null)
  const [error,        setError]        = useState<string | null>(null)
  const [acctTick,     setAcctTick]     = useState(0)
  const historyRef = useRef<PricePoint[]>([])

  // Pre-populate with recent candle close prices for live-candle building
  useEffect(() => {
    fetch('/api/hl/candles?window=1h', { cache: 'no-store' })
      .then(r => r.json())
      .then((d: { candles?: Array<{ t: number; c: number }> }) => {
        if (d.candles && d.candles.length > 0) {
          const pts = d.candles.map(c => ({ timestamp: c.t, price: c.c }))
          historyRef.current = pts.slice(-MAX_POINTS)
          setPriceHistory([...historyRef.current])
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    let mounted = true
    async function tick() {
      try {
        const res  = await fetch('/api/hl/price', { cache: 'no-store' })
        const data = await res.json() as { price?: number }
        if (mounted && data.price && data.price > 0) {
          setBtcPrice(data.price)
          const next = [...historyRef.current, { timestamp: Date.now(), price: data.price }]
          if (next.length > MAX_POINTS) next.splice(0, next.length - MAX_POINTS)
          historyRef.current = next
          setPriceHistory([...next])
        }
      } catch { /* blip */ }
    }
    tick()
    const id = setInterval(tick, PRICE_MS)
    return () => { mounted = false; clearInterval(id) }
  }, [])

  useEffect(() => {
    let mounted = true
    async function tick() {
      try {
        const res  = await fetch('/api/hl/account', { cache: 'no-store' })
        if (!res.ok) { if (mounted) setError(`account ${res.status}`); return }
        const data = await res.json() as HLAccount & { error?: string }
        if (data.error) { if (mounted) setError(data.error); return }
        if (mounted) { setAccount(data); setError(null) }
      } catch (e) { if (mounted) setError(String(e)) }
    }
    tick()
    const id = setInterval(tick, ACCOUNT_MS)
    return () => { mounted = false; clearInterval(id) }
  }, [acctTick])

  return {
    btcPrice,
    priceHistory,
    account,
    error,
    refreshAccount: () => setAcctTick(n => n + 1),
  }
}
