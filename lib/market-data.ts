// ── Market Data Adapter Interface
// Typed abstraction: HL crypto + Polygon.io/Tiingo commodities & equities.
// Swap adapters without touching scanner UI or engine logic.

export type MarketCategory = 'crypto' | 'commodity' | 'equity' | 'index'

export interface Market {
  symbol: string
  name: string
  category: MarketCategory
  mid: number
  leverage: number
  volume24h: number
  dataProvider: string // 'hyperliquid' | 'polygon' | 'tiingo'
}

export interface MarketDataAdapter {
  category: MarketCategory
  provider: string
  getMarkets(): Promise<Market[]>
  getAllMids(): Promise<Record<string, string>>
  getCandles(symbol: string, interval: string, count: number): Promise<Candle[]>
  isLive(): boolean // true if data source is configured
}

export interface Candle {
  t: number; o: number; h: number; l: number; c: number; v: number
}

// ── Hyperliquid adapter (crypto perp/spot) ─

export class HLAdapter implements MarketDataAdapter {
  category: MarketCategory = 'crypto'
  provider = 'hyperliquid'
  readonly HL_API = 'https://api.hyperliquid.xyz'

  async getMarkets(): Promise<Market[]> {
    const res = await fetch(`${this.HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'meta' }),
    })
    const meta = await res.json() as { universe: Array<{ name: string; maxLeverage: number }> }
    return meta.universe.map(u => ({
      symbol: u.name,
      name: `${u.name}-PERP`,
      category: 'crypto',
      mid: 0, // filled from allMids separately
      leverage: u.maxLeverage,
      volume24h: 0,
      dataProvider: 'hyperliquid',
    }))
  }

  async getAllMids(): Promise<Record<string, string>> {
    const res = await fetch(`${this.HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'allMids' }),
    })
    return res.json() as Promise<Record<string, string>>
  }

  async getCandles(symbol: string, interval: string, count: number): Promise<Candle[]> {
    const endTime = Date.now()
    const msMap: Record<string, number> = { '1m': 60_000, '5m': 300_000, '15m': 900_000, '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000 }
    const ms = msMap[interval] ?? 300_000
    const startTime = endTime - count * ms
    const res = await fetch(`${this.HL_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'candleSnapshot', req: { coin: symbol, interval, startTime, endTime } }),
    })
    const raw = await res.json() as Array<{ t: number; o: string; h: string; l: string; c: string; v: string }>
    return raw.map(c => ({ t: c.t, o: +c.o, h: +c.h, l: +c.l, c: +c.c, v: +(c.v ?? 0) }))
  }

  isLive(): boolean { return true }
}

// ── Commodity + equity adapter (Polygon.io / Tiingo) ─

const COMMODITY_LIST = [
  { symbol: 'BRENTOIL', name: 'Brent Crude Oil',  ticker: 'BZ1!', mid: 77.50 },
  { symbol: 'WTIOIL',   name: 'WTI Crude Oil',    ticker: 'CL1!', mid: 74.20 },
  { symbol: 'NATGAS',   name: 'Natural Gas',      ticker: 'NG1!', mid: 2.92 },
  { symbol: 'URNM',     name: 'Uranium ETF',      ticker: 'URNM', mid: 65.71 },
  { symbol: 'SOY',      name: 'Soybeans',         ticker: 'ZS1!', mid: 1195.10 },
  { symbol: 'COPPER',   name: 'Copper',           ticker: 'HG1!', mid: 4.50 },
  { symbol: 'GOLD',     name: 'Gold',             ticker: 'GC1!', mid: 2340.00 },
  { symbol: 'SILVER',   name: 'Silver',           ticker: 'SI1!', mid: 30.50 },
  { symbol: 'CORN',     name: 'Corn',             ticker: 'ZC1!', mid: 440.00 },
]

const EQUITY_LIST = [
  { symbol: 'TSLA',  name: 'Tesla',         ticker: 'TSLA',  mid: 350 },
  { symbol: 'NVDA',  name: 'NVIDIA',        ticker: 'NVDA',  mid: 880 },
  { symbol: 'AAPL',  name: 'Apple',         ticker: 'AAPL',  mid: 190 },
  { symbol: 'AMZN',  name: 'Amazon',        ticker: 'AMZN',  mid: 186 },
  { symbol: 'GOOGL', name: 'Alphabet',      ticker: 'GOOGL', mid: 175 },
  { symbol: 'MSFT',  name: 'Microsoft',     ticker: 'MSFT',  mid: 420 },
  { symbol: 'META',  name: 'Meta',          ticker: 'META',  mid: 500 },
  { symbol: 'COIN',  name: 'Coinbase',      ticker: 'COIN',  mid: 220 },
  { symbol: 'MSTR',  name: 'MicroStrategy', ticker: 'MSTR',  mid: 1400 },
]

export class CommodityEquityAdapter implements MarketDataAdapter {
  category: MarketCategory = 'commodity'
  provider = 'polygon' // or 'tiingo'
  readonly hasKey = !!process.env.POLYGON_API_KEY

  async getMarkets(): Promise<Market[]> {
    // Polygon/Tiingo key required for real data. Without it, return empty.
    if (!this.hasKey) return []
    return this._fetchLive()
  }

  async getAllMids(): Promise<Record<string, string>> {
    const all = await this.getMarkets()
    const mids: Record<string, string> = {}
    for (const m of all) mids[m.symbol] = String(m.mid)
    return mids
  }

  async getCandles(_symbol: string, _interval: string, _count: number): Promise<Candle[]> {
    return []
  }

  isLive(): boolean { return this.hasKey }

  private async _fetchLive(): Promise<Market[]> {
    const apiKey = process.env.POLYGON_API_KEY ?? ''
    const markets: Market[] = []

    for (const c of COMMODITY_LIST) {
      try {
        const res = await fetch(`https://api.polygon.io/v2/aggs/ticker/${c.ticker}/prev?apiKey=${apiKey}`)
        const data = await res.json() as { results?: Array<{ c: number; v: number }> }
        const last = data.results?.[0]
        markets.push({
          symbol: c.symbol, name: c.name, category: 'commodity',
          mid: last?.c ?? c.mid,
          leverage: 10, volume24h: (last?.v ?? 10_000_000) * (last?.c ?? c.mid),
          dataProvider: 'polygon',
        })
      } catch {
        markets.push({ symbol: c.symbol, name: c.name, category: 'commodity', mid: c.mid, leverage: 10, volume24h: 0, dataProvider: 'polygon(simulated)' })
      }
    }

    for (const e of EQUITY_LIST) {
      try {
        const res = await fetch(`https://api.polygon.io/v2/aggs/ticker/${e.ticker}/prev?apiKey=${apiKey}`)
        const data = await res.json() as { results?: Array<{ c: number; v: number }> }
        const last = data.results?.[0]
        markets.push({
          symbol: e.symbol, name: e.name, category: 'equity',
          mid: last?.c ?? e.mid,
          leverage: 5, volume24h: (last?.v ?? 50_000_000) * (last?.c ?? e.mid),
          dataProvider: 'polygon',
        })
      } catch {
        markets.push({ symbol: e.symbol, name: e.name, category: 'equity', mid: e.mid, leverage: 5, volume24h: 0, dataProvider: 'polygon(simulated)' })
      }
    }

    return markets
  }
}
