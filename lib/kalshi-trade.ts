/**
 * Kalshi Portfolio / Trading API
 * ────────────────────────────────
 * Authenticated endpoints (RSA-PSS signed).
 * All prices are in cents (1–99).
 */

import { buildKalshiHeaders } from './kalshi-auth'
import type { KalshiBalance, KalshiPosition, KalshiOrder, KalshiFill } from './types'
import { normalizeKalshiPosition, normalizeKalshiOrder, normalizeKalshiFill } from './types'
import { KALSHI_BASE } from './kalshi'

/** Safely extract a string error message from a Kalshi API response body.
 *  Kalshi sometimes returns error as an object: {code, message, details}.
 *  During maintenance, Kalshi returns authentication_error even with valid
 *  credentials — detect via the details field and surface a clearer message.
 */
function extractError(body: unknown, status: number): string {
  if (!body || typeof body !== 'object') return `HTTP ${status}`
  const b = body as Record<string, unknown>
  const err = b.error
  if (typeof err === 'string') return err
  if (err && typeof err === 'object') {
    const e = err as Record<string, unknown>
    const details = String(e.details ?? '')
    // Kalshi wraps maintenance/routing failures as authentication_error
    if (details.includes('service unavailable') || details.includes('unavailable')) {
      return 'Kalshi service unavailable — scheduled maintenance (3–5 AM ET weekdays)'
    }
    return String(e.message ?? e.code ?? JSON.stringify(err))
  }
  if (typeof b.message === 'string') return b.message
  return `HTTP ${status}`
}

export interface PlaceOrderParams {
  ticker: string
  side: 'yes' | 'no'
  count: number           // number of contracts
  yesPrice?: number       // limit price in cents for YES side
  noPrice?: number        // limit price in cents for NO side
  clientOrderId?: string
  ioc?: boolean           // immediate_or_cancel — fills at market or cancels instantly
}

export interface PlaceOrderResult {
  ok: boolean
  order?: KalshiOrder
  error?: string
}

export async function placeOrder(params: PlaceOrderParams): Promise<PlaceOrderResult> {
  const path = '/trade-api/v2/portfolio/orders'
  const body = {
    ticker: params.ticker,
    side: params.side,
    action: 'buy',
    count: params.count,
    // Only send the price for the side being bought — sending both causes
    // Kalshi to validate yes_price + no_price = 100, which fails because
    // ask prices include the spread and sum to >100.
    ...(params.side === 'yes'
      ? { yes_price: params.yesPrice }
      : { no_price: params.noPrice }),
    type: 'limit',
    time_in_force: params.ioc ? 'immediate_or_cancel' : 'good_till_canceled',
    ...(params.clientOrderId ? { client_order_id: params.clientOrderId } : {}),
  }

  try {
    const headers = buildKalshiHeaders('POST', path)
    if (!headers['KALSHI-ACCESS-KEY']) {
      return { ok: false, error: 'Missing Kalshi credentials' }
    }

    const res = await fetch(`${KALSHI_BASE}/portfolio/orders`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    const data = await res.json()
    if (!res.ok) {
      console.error('[kalshi-trade] placeOrder failed', res.status, JSON.stringify(data))
      return { ok: false, error: extractError(data, res.status) }
    }
    return { ok: true, order: normalizeKalshiOrder(data.order) }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}

export interface SellOrderParams {
  ticker: string
  side: 'yes' | 'no'
  count: number
}

/** Market-sell contracts at the best available price (price=1 accepts any bid). */
export async function sellOrder(params: SellOrderParams): Promise<PlaceOrderResult> {
  const path = '/trade-api/v2/portfolio/orders'
  const body = {
    ticker: params.ticker,
    side: params.side,
    action: 'sell',
    count: params.count,
    // Price of 1¢ means "accept any bid" — effectively a market sell
    ...(params.side === 'yes' ? { yes_price: 1 } : { no_price: 1 }),
    time_in_force: 'immediate_or_cancel',
  }

  try {
    const headers = buildKalshiHeaders('POST', path)
    if (!headers['KALSHI-ACCESS-KEY']) {
      return { ok: false, error: 'Missing Kalshi credentials' }
    }
    const res = await fetch(`${KALSHI_BASE}/portfolio/orders`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (!res.ok) return { ok: false, error: extractError(data, res.status) }
    return { ok: true, order: normalizeKalshiOrder(data.order) }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}

/** Limit-sell contracts at 99¢ (GTC) — rests until someone pays 99¢, i.e. take-profit at max value. */
export async function limitSellOrder(params: SellOrderParams): Promise<PlaceOrderResult> {
  const path = '/trade-api/v2/portfolio/orders'
  const body = {
    ticker: params.ticker,
    side: params.side,
    action: 'sell',
    count: params.count,
    ...(params.side === 'yes' ? { yes_price: 99 } : { no_price: 99 }),
    time_in_force: 'good_till_canceled',
  }

  try {
    const headers = buildKalshiHeaders('POST', path)
    if (!headers['KALSHI-ACCESS-KEY']) {
      return { ok: false, error: 'Missing Kalshi credentials' }
    }
    const res = await fetch(`${KALSHI_BASE}/portfolio/orders`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (!res.ok) return { ok: false, error: extractError(data, res.status) }
    return { ok: true, order: normalizeKalshiOrder(data.order) }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}

export async function cancelOrder(orderId: string): Promise<{ ok: boolean; error?: string }> {
  const path = `/trade-api/v2/portfolio/orders/${orderId}`
  try {
    const headers = buildKalshiHeaders('DELETE', path)
    const res = await fetch(`${KALSHI_BASE}/portfolio/orders/${orderId}`, {
      method: 'DELETE',
      headers,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      return { ok: false, error: extractError(data, res.status) }
    }
    return { ok: true }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}

export interface BalanceResult {
  ok: boolean
  data?: KalshiBalance
  error?: string
  status?: number
}

export async function getBalance(): Promise<BalanceResult> {
  const path = '/trade-api/v2/portfolio/balance'
  try {
    const headers = buildKalshiHeaders('GET', path)
    if (!headers['KALSHI-ACCESS-KEY']) {
      return { ok: false, error: 'Kalshi API key not configured' }
    }
    const res = await fetch(`${KALSHI_BASE}/portfolio/balance`, { headers, cache: 'no-store' })
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      return { ok: false, error: extractError(body, res.status), status: res.status }
    }
    return { ok: true, data: body as KalshiBalance }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}

export interface PositionsResult {
  ok: boolean
  positions?: KalshiPosition[]
  orders?: KalshiOrder[]
  error?: string
  status?: number
}

export async function getPositions(): Promise<PositionsResult> {
  const path = '/trade-api/v2/portfolio/positions'
  try {
    const headers = buildKalshiHeaders('GET', path)
    if (!headers['KALSHI-ACCESS-KEY']) {
      return { ok: false, error: 'Kalshi API key not configured', positions: [], orders: [] }
    }
    // sign path without query params, add query to URL
    const res = await fetch(`${KALSHI_BASE}/portfolio/positions?limit=50&count_filter=position`, { headers, cache: 'no-store' })
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      return { ok: false, error: extractError(body, res.status), status: res.status, positions: [], orders: [] }
    }
    return { ok: true, positions: (body.market_positions ?? []).map(normalizeKalshiPosition), orders: [] }
  } catch (err) {
    return { ok: false, error: String(err), positions: [], orders: [] }
  }
}

export async function getFills(limit = 20): Promise<{ ok: boolean; fills: KalshiFill[]; error?: string }> {
  const path = '/trade-api/v2/portfolio/fills'
  try {
    const headers = buildKalshiHeaders('GET', path)
    if (!headers['KALSHI-ACCESS-KEY']) return { ok: false, fills: [], error: 'Missing credentials' }
    const res = await fetch(`${KALSHI_BASE}/portfolio/fills?limit=${limit}`, { headers, cache: 'no-store' })
    const body = await res.json().catch(() => null)
    if (!res.ok) return { ok: false, fills: [], error: extractError(body, res.status) }
    return { ok: true, fills: (body.fills ?? []).map(normalizeKalshiFill) }
  } catch (err) {
    return { ok: false, fills: [], error: String(err) }
  }
}

export async function getOrders(status?: string): Promise<{ ok: boolean; orders: KalshiOrder[]; error?: string }> {
  const path = '/trade-api/v2/portfolio/orders'
  try {
    const headers = buildKalshiHeaders('GET', path)
    if (!headers['KALSHI-ACCESS-KEY']) return { ok: false, orders: [], error: 'Missing credentials' }
    const query = status ? `?status=${status}&limit=20` : '?limit=20'
    const res = await fetch(`${KALSHI_BASE}/portfolio/orders${query}`, { headers, cache: 'no-store' })
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      return { ok: false, orders: [], error: extractError(body, res.status) }
    }
    return { ok: true, orders: (body.orders ?? []).map(normalizeKalshiOrder) }
  } catch (err) {
    return { ok: false, orders: [], error: String(err) }
  }
}
