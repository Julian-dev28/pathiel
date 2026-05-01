'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import type { KalshiMarket, KalshiOrderbook, PricePoint } from '@/lib/types'

const QUOTE_MS  = 250    // 250ms YES/NO bid/ask refresh (4 req/s — well within Kalshi Basic 20/s)
const BTC_MS    = 1_000  // 1s BTC price refresh (Coinbase public API, no strict limit)
const OB_MS     = 500    // 500ms orderbook depth refresh

interface MarketTick {
  liveMarket: KalshiMarket | null
  liveOrderbook: KalshiOrderbook | null
  liveBTCPrice: number | null
  livePriceHistory: PricePoint[]
  marketError: string | null
  refresh: () => void
}

/** Fetch with automatic retry on 429 — reads Retry-After header */
async function fetchWithRetry(url: string, retries = 3): Promise<Response> {
  for (let i = 0; i < retries; i++) {
    const res = await fetch(url, { cache: 'no-store' })
    if (res.status !== 429) return res
    const retryAfter = res.headers.get('Retry-After')
    const waitMs = retryAfter ? parseFloat(retryAfter) * 1000 : 300 * (i + 1)
    await new Promise(r => setTimeout(r, waitMs))
  }
  return fetch(url, { cache: 'no-store' })
}

/**
 * Three independent polling loops:
 *   1. Quote  — 500ms  — /api/market-quote/{ticker} (single market, fast) or /api/markets fallback
 *   2. BTC    — 2s     — /api/btc-price
 *   3. OB     — 1s     — /api/orderbook/{ticker}
 */
export function useMarketTick(ticker: string | null, marketMode: '15m' | 'hourly' = '15m'): MarketTick {
  const [liveMarket,       setLiveMarket]       = useState<KalshiMarket | null>(null)
  const [liveOrderbook,    setLiveOrderbook]    = useState<KalshiOrderbook | null>(null)
  const [liveBTCPrice,     setLiveBTCPrice]     = useState<number | null>(null)
  const [livePriceHistory, setLivePriceHistory] = useState<PricePoint[]>([])
  const [marketError,      setMarketError]      = useState<string | null>(null)

  const prevTickerRef   = useRef(ticker)
  const marketCloseRef  = useRef<number | null>(null)  // ms timestamp of current market's close_time
  const marketModeRef   = useRef(marketMode)

  useEffect(() => { marketModeRef.current = marketMode }, [marketMode])

  useEffect(() => {
    if (ticker !== prevTickerRef.current) {
      prevTickerRef.current  = ticker
      marketCloseRef.current = null
      setLiveMarket(null)
      setLiveOrderbook(null)
      setLivePriceHistory([])
    }
  }, [ticker])

  const quoteTickRef = useRef<(() => void) | null>(null)
  const refresh = useCallback(() => { quoteTickRef.current?.() }, [])

  // ── Loop 1: YES/NO bid/ask — 500ms ──────────────────────────────────────────
  useEffect(() => {
    let mounted = true
    // Exponential backoff when no active window (503s during off-hours)
    let backoff = 0
    let skip    = 0

    async function quoteTick() {
      if (skip < backoff) { skip++; return }
      skip = 0

      try {
        const t = prevTickerRef.current
        // If current market has passed its close_time, discover the next one
        const marketClosed = marketCloseRef.current !== null && marketCloseRef.current < Date.now()
        // In hourly mode: never fall back to /api/markets (which returns KXBTC15M).
        // Pipeline provides the KXBTCD ticker — just stay quiet until it arrives.
        const isHourly = marketModeRef.current === 'hourly'
        const url = (t && !marketClosed)
          ? `/api/market-quote/${encodeURIComponent(t)}`
          : isHourly ? null : '/api/markets'
        if (!url) return  // hourly mode, no ticker yet — skip

        const res = await fetchWithRetry(url)

        if (!res.ok) {
          backoff = Math.min(backoff * 2 + 1, 60)  // back off up to 30s (60 × 500ms)
          try {
            const errData = await res.json()
            if (mounted) setMarketError(errData.error ?? `http_${res.status}`)
          } catch { if (mounted) setMarketError(`http_${res.status}`) }
          return
        }

        backoff = 0
        if (mounted) setMarketError(null)
        const data = await res.json()

        // /api/market-quote returns { market } ; /api/markets returns { markets: [] }
        let market: KalshiMarket | null = null
        if (data.market && !marketClosed) {
          market = data.market
        } else if (data.markets?.length) {
          // Prefer a market with live quotes; fall back to any open window (quotes arrive seconds after open)
          const hasQuotes = (m: KalshiMarket) => (m.yes_ask ?? 0) > 1 && (m.yes_ask ?? 100) < 99
          const isOpen    = (m: KalshiMarket) => !m.close_time || new Date(m.close_time).getTime() > Date.now()
          market = data.markets.find(hasQuotes) ?? data.markets.find(isOpen) ?? null
          // Auto-switch to the newly discovered market
          if (market && market.ticker !== prevTickerRef.current) {
            prevTickerRef.current = market.ticker
            if (mounted) { setLiveOrderbook(null); setLivePriceHistory([]) }
          }
        }

        if (mounted && market) {
          if (market.close_time) {
            marketCloseRef.current = new Date(market.close_time).getTime()
          }
          setLiveMarket(market)
        }
      } catch {
        backoff = Math.min(backoff * 2 + 1, 60)
        if (mounted) setMarketError('network_error')
      }
    }

    quoteTickRef.current = quoteTick
    quoteTick()
    const id = setInterval(quoteTick, QUOTE_MS)
    return () => { mounted = false; clearInterval(id); quoteTickRef.current = null }
  }, [ticker])

  // ── Loop 2: BTC price — 2s ───────────────────────────────────────────────────
  useEffect(() => {
    let mounted = true

    async function btcTick() {
      try {
        const res = await fetch('/api/btc-price', { cache: 'no-store' })
        if (!res.ok) return
        const data = await res.json()
        if (!mounted || !(data.price > 0)) return
        setLiveBTCPrice(data.price)
        setLivePriceHistory(prev => {
          const now = Date.now()
          if (prev.length === 0) {
            return [
              { timestamp: now - BTC_MS, price: data.price },
              { timestamp: now,          price: data.price },
            ]
          }
          const last = prev[prev.length - 1]
          if (Math.abs(last.price - data.price) < 0.01) return prev
          return [...prev, { timestamp: now, price: data.price }].slice(-600)
        })
      } catch { /* network blip */ }
    }

    btcTick()
    const id = setInterval(btcTick, BTC_MS)
    return () => { mounted = false; clearInterval(id) }
  }, [])

  // ── Loop 3: Orderbook depth — 1s ─────────────────────────────────────────────
  useEffect(() => {
    let mounted = true

    async function obTick() {
      const t = prevTickerRef.current
      if (!t) return
      try {
        const res = await fetchWithRetry(`/api/orderbook/${encodeURIComponent(t)}`)
        if (res.ok && mounted) {
          const data = await res.json()
          if (data.orderbook) setLiveOrderbook(data.orderbook)
        }
      } catch { /* network blip */ }
    }

    obTick()
    const id = setInterval(obTick, OB_MS)
    return () => { mounted = false; clearInterval(id) }
  }, [ticker])

  return { liveMarket, liveOrderbook, liveBTCPrice, livePriceHistory, marketError, refresh }
}
