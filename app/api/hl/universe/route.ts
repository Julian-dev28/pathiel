export const runtime = 'nodejs'

import { getUniverse } from '../../../../lib/hl-universe'
import type { HLMarket } from '../../../../lib/hl-universe'

export async function GET() {
  try {
    const markets = await getUniverse() as HLMarket[]
    return Response.json({ markets, count: markets.length })
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 502 })
  }
}
