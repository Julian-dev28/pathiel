// GET /api/scanner/commodities — commodity + equity market prices via MarketDataAdapter

export const runtime = 'nodejs'

import { CommodityEquityAdapter } from '@/lib/market-data'

export async function GET() {
  const adapter = new CommodityEquityAdapter()
  const markets = await adapter.getMarkets()

  return Response.json({
    isLive: adapter.isLive(),
    provider: adapter.provider,
    markets,
  })
}
