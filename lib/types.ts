// ─── Kalshi Market Types ───────────────────────────────────────────────────

export interface KalshiMarket {
  ticker: string
  event_ticker: string
  series_ticker?: string
  title: string
  yes_bid: number      // cents (1–99)
  yes_ask: number
  no_bid: number
  no_ask: number
  last_price: number
  volume: number
  open_interest: number
  close_time: string   // ISO timestamp
  expiration_time: string
  status: 'open' | 'active' | 'closed' | 'settled' | 'paused' | 'finalized' | 'initialized'
  result?: string
  settlement_value?: number
  floor_strike?: number     // the BTC "price to beat"
  yes_sub_title?: string    // "Price to beat: $65,619.62"
  no_sub_title?: string
  rules_primary?: string
  market_type?: string
  // Kalshi v2 dollar fields (normalized to cents by normalizeKalshiMarket)
  yes_ask_dollars?: number
  yes_bid_dollars?: number
  no_ask_dollars?: number
  no_bid_dollars?: number
}

export type RawKalshiMarket   = Record<string, unknown>
export type RawKalshiPosition = Record<string, unknown>
export type RawKalshiOrder    = Record<string, unknown>
export type RawKalshiFill     = Record<string, unknown>

export function normalizeKalshiMarket(raw: unknown): KalshiMarket {
  const m = raw as RawKalshiMarket
  const toC = (dollars: number | undefined, cents: number | undefined): number => {
    if (cents && cents > 0) return cents
    if (dollars !== undefined && dollars >= 0) return Math.round(dollars * 100)
    return 0
  }
  const fp = (v: unknown) => parseFloat(String(v ?? 0)) || 0
  return {
    ...(m as Partial<KalshiMarket>),
    ticker:          String(m.ticker ?? ''),
    event_ticker:    String(m.event_ticker ?? ''),
    title:           String(m.title ?? ''),
    close_time:      String(m.close_time ?? ''),
    expiration_time: String(m.expiration_time ?? ''),
    status:          (m.status as KalshiMarket['status']) ?? 'closed',
    yes_ask:         toC(m.yes_ask_dollars as number | undefined, m.yes_ask as number | undefined),
    yes_bid:         toC(m.yes_bid_dollars as number | undefined, m.yes_bid as number | undefined),
    no_ask:          toC(m.no_ask_dollars  as number | undefined, m.no_ask  as number | undefined),
    no_bid:          toC(m.no_bid_dollars  as number | undefined, m.no_bid  as number | undefined),
    last_price:      toC(m.last_price_dollars as number | undefined, m.last_price as number | undefined),
    volume:          fp(m.volume_fp       ?? m.volume),
    open_interest:   fp(m.open_interest_fp ?? m.open_interest),
  }
}

export interface KalshiOrderbookLevel { price: number; delta: number }
export interface KalshiOrderbook { yes: KalshiOrderbookLevel[]; no: KalshiOrderbookLevel[] }

// ─── Market Data ──────────────────────────────────────────────────────────────

// [timestamp, low, high, open, close, volume] — Coinbase Exchange format
export type OHLCVCandle = [number, number, number, number, number, number]

export interface PricePoint { timestamp: number; price: number }

export interface BTCQuote {
  price: number
  percent_change_1h: number
  percent_change_24h: number
  volume_24h: number
  market_cap: number
  last_updated: string
}

// ─── Kalshi Portfolio ─────────────────────────────────────────────────────────

export interface KalshiBalance {
  balance: number         // cents
  portfolio_value: number // cents
}

export interface KalshiPosition {
  ticker: string
  position: number        // positive = YES, negative = NO
  realized_pnl: number    // cents
  market_exposure: number // cents
  fees_paid: number       // cents
  resting_orders_count: number
}

export interface KalshiOrder {
  order_id: string
  ticker: string
  side: 'yes' | 'no'
  action: 'buy' | 'sell'
  count: number
  fill_count: number
  remaining_count: number
  initial_count: number
  yes_price: number       // cents
  no_price: number        // cents
  status: 'resting' | 'canceled' | 'executed' | 'pending'
  created_time: string
  client_order_id?: string
}

export interface KalshiFill {
  fill_id: string
  order_id: string
  ticker: string
  side: 'yes' | 'no'
  action: 'buy' | 'sell'
  count: number
  yes_price: number       // cents
  no_price: number        // cents
  is_taker: boolean
  created_time: string
  fee_cost: string
}

export function normalizeKalshiPosition(p: RawKalshiPosition): KalshiPosition {
  const fp  = (v: unknown) => parseFloat(String(v ?? 0)) || 0
  const toC = (dollars: unknown, cents: unknown) =>
    (cents !== undefined && cents !== null && fp(cents) !== 0) ? fp(cents) : Math.round(fp(dollars) * 100)
  return {
    ticker:               String(p.ticker ?? p.market_ticker ?? ''),
    position:             fp(p.position_fp ?? p.position),
    realized_pnl:         toC(p.realized_pnl_dollars, p.realized_pnl),
    market_exposure:      toC(p.market_exposure_dollars, p.market_exposure),
    fees_paid:            toC(p.fees_paid_dollars, p.fees_paid),
    resting_orders_count: fp(p.resting_orders_count),
  }
}

export function normalizeKalshiOrder(o: RawKalshiOrder): KalshiOrder {
  const fp  = (v: unknown) => parseFloat(String(v ?? 0)) || 0
  const toC = (dollars: unknown, cents: unknown) =>
    (cents !== undefined && cents !== null && fp(cents) !== 0) ? fp(cents) : Math.round(fp(dollars) * 100)
  return {
    order_id:        String(o.order_id ?? ''),
    ticker:          String(o.ticker ?? o.market_ticker ?? ''),
    side:            o.side as 'yes' | 'no',
    action:          o.action as 'buy' | 'sell',
    count:           fp(o.count_fp ?? o.count),
    fill_count:      fp(o.fill_count_fp ?? o.fill_count),
    remaining_count: fp(o.remaining_count_fp ?? o.remaining_count),
    initial_count:   fp(o.initial_count_fp ?? o.initial_count ?? o.count_fp ?? o.count),
    yes_price:       toC(o.yes_price_dollars, o.yes_price),
    no_price:        toC(o.no_price_dollars,  o.no_price),
    status:          o.status as KalshiOrder['status'],
    created_time:    String(o.created_time ?? ''),
    client_order_id: o.client_order_id as string | undefined,
  }
}

export function normalizeKalshiFill(f: RawKalshiFill): KalshiFill {
  const fp  = (v: unknown) => parseFloat(String(v ?? 0)) || 0
  const toC = (dollars: unknown, cents: unknown) =>
    (cents !== undefined && cents !== null && fp(cents) !== 0) ? fp(cents) : Math.round(fp(dollars) * 100)
  return {
    fill_id:      String(f.fill_id ?? f.trade_id ?? ''),
    order_id:     String(f.order_id ?? ''),
    ticker:       String(f.ticker ?? f.market_ticker ?? ''),
    side:         f.side as 'yes' | 'no',
    action:       f.action as 'buy' | 'sell',
    count:        fp(f.count_fp ?? f.count),
    yes_price:    toC(f.yes_price_dollars, f.yes_price),
    no_price:     toC(f.no_price_dollars,  f.no_price),
    is_taker:     (f.is_taker as boolean | undefined) ?? false,
    created_time: String(f.created_time ?? ''),
    fee_cost:     String(f.fee_cost ?? '0'),
  }
}
