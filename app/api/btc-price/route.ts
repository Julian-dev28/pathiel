import { NextResponse } from 'next/server'
import https from 'node:https'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// ── In-memory price cache (survives warm instances, resets on cold start) ─────
const g = globalThis as typeof globalThis & {
  _btcPriceCache?: { price: number; change24h: number; source: string; at: number }
}
const CACHE_TTL_MS = 30_000  // 30s

function coinbaseFetch(url: string, timeoutMs = 5_000): Promise<Response> {
  // Local Node.js often can't verify Coinbase's intermediate cert chain.
  // Skip TLS verification in dev only; Vercel production is unaffected.
  if (process.env.NODE_ENV === 'production') {
    const ac = new AbortController()
    setTimeout(() => ac.abort(), timeoutMs)
    return fetch(url, { cache: 'no-store', signal: ac.signal })
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { req.destroy(new Error('coinbase fetch timeout')) }, timeoutMs)
    const req = https.get(url, { rejectUnauthorized: false }, (res) => {
      const chunks: Buffer[] = []
      res.on('data', (c: Buffer) => chunks.push(c))
      res.on('end', () => {
        clearTimeout(timer)
        const body = Buffer.concat(chunks).toString('utf-8')
        resolve(new Response(body, { status: res.statusCode ?? 200 }))
      })
    })
    req.on('error', (e) => { clearTimeout(timer); reject(e) })
    req.end()
  })
}

export async function GET() {
  // ── Serve cache if fresh ───────────────────────────────────────────────────
  const cached = g._btcPriceCache
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
    return NextResponse.json({
      price:              cached.price,
      percent_change_1h:  0,
      percent_change_24h: cached.change24h,
      source:             cached.source,
      last_updated:       new Date(cached.at).toISOString(),
    })
  }

  // ── Coinbase Exchange — same feed Kalshi settles KXBTC15M against ──────────
  try {
    const res = await coinbaseFetch('https://api.exchange.coinbase.com/products/BTC-USD/ticker')
    if (res.ok) {
      const cb = await res.json()
      const price = parseFloat(cb?.price)
      if (price > 0) {
        g._btcPriceCache = { price, change24h: 0, source: 'coinbase-exchange', at: Date.now() }
        return NextResponse.json({
          price, percent_change_1h: 0, percent_change_24h: 0,
          source: 'coinbase-exchange', last_updated: new Date().toISOString(),
        })
      }
    } else {
      console.warn(`[btc-price] Coinbase Exchange ${res.status}`)
    }
  } catch (e) { console.warn('[btc-price] Coinbase Exchange threw:', e) }

  // ── Stale cache beats a 502 ────────────────────────────────────────────────
  if (cached) {
    console.warn('[btc-price] Coinbase Exchange failed — serving stale cache')
    return NextResponse.json({
      price:              cached.price,
      percent_change_1h:  0,
      percent_change_24h: cached.change24h,
      source:             `${cached.source}:stale`,
      last_updated:       new Date(cached.at).toISOString(),
    })
  }

  return NextResponse.json({ error: 'BTC price unavailable' }, { status: 502 })
}
