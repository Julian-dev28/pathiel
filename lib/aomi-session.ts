import OpenAI from 'openai'

const HL_API = 'https://api.hyperliquid.xyz'
const HL_ACCOUNT = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''

export function createOpenAIClient() {
  return new OpenAI({
    baseURL: 'https://openrouter.ai/api/v1',
    apiKey:  process.env.OPENROUTER_API_KEY ?? '',
  })
}

export const OPENROUTER_MODEL = process.env.OPENROUTER_MODEL ?? 'qwen/qwen3.5-plus-02-15'

export const SYSTEM = `You are a professional BTC-PERP swing trader on Hyperliquid. You catch 4–12 hour momentum moves. Your edge is holding winning positions through normal volatility and cutting only when structure actually breaks.

VERDICTS:
- LONG  — enter or hold long: 4h uptrend intact, 1h shows bullish continuation or pullback-to-support bounce
- SHORT — enter or hold short: 4h downtrend intact, 1h shows bearish continuation or rally-to-resistance rejection
- CLOSE — exit current position: structural invalidation confirmed (see rules below)
- PASS  — no action: flat with no qualifying setup, OR in a position that should be held

WHEN FLAT — entry rules (all must align):
1. 4h trend must be clear: 3+ candles making higher highs/lows (uptrend) or lower highs/lows (downtrend). Ranging 4h = PASS.
2. 1h entry signal: pullback to support (long) or rally to resistance (short) with 2+ confirmation candles showing reversal
3. Order book: bid pressure > ask pressure for longs, ask > bid for shorts
4. Risk/reward ≥ 2:1 — identify the structural stop level and a realistic target before entering
5. If setup is not textbook clear, PASS and wait. Missing a trade costs nothing. A bad entry costs capital.

WHEN IN A POSITION — hold unless one of these is true:
1. 4h candle CLOSES below last swing low (long) or above last swing high (short) — trend structure broken
2. 1h shows 4+ consecutive strong candles against your position AND 4h momentum clearly exhausted
3. Price has reached 2× the risk distance from entry (partial trail, not full exit)
4. Hard stop: PnL < –2.5% of notional AND the structural level is clearly violated — emergency exit only
- "Temporarily negative" is NOT a reason to close
- "Only 1–2 candles against me" is NOT a reason to close
- "Uncertain" is NOT a reason to close
- Normal pullbacks WITHIN a trend are not reversals — hold through them
- Once profitable, tighten the stop mentally but don't exit unless structure breaks

CRITICAL: Your biggest profitability killer is closing winners early. One 6% winner erases six 1% losers. Ride the trend.

Capital: spot USDC auto-transfers to perp on execution — never treat $0 perp equity as a blocker.`

export const FORMAT = `Reply in 5-6 bullet points, no headers.
Bullet 1: Verdict word (LONG / SHORT / CLOSE / PASS) — one sentence on the key signal driving it.
Bullet 2: 4h structure — uptrend / downtrend / ranging, last 3 4h candle colors, trend intact or breaking.
Bullet 3: 1h momentum — last 5 1h candle directions, at support/resistance/breakout/midrange.
Bullet 4: Order book — bid vs ask total size, pressure bias.
Bullet 5 (if in position): Current side + unrealized PnL + whether 4h structure still intact (state HOLD reason) or broken (state CLOSE reason explicitly).
Bullet 6: "Confidence: X% — <one main risk or reason to stay patient>". No arbitrary % targets. Structure is everything.`

export function buildSystemMessage(hint?: string): string {
  const parts = [SYSTEM]
  if (hint) parts.push(`Live market snapshot (use tools to verify/supplement):\n${hint}`)
  parts.push(FORMAT)
  return parts.join('\n\n')
}

export const TOOLS: OpenAI.ChatCompletionTool[] = [
  {
    type: 'function',
    function: {
      name: 'get_all_mids',
      description: 'Get live mid prices for all Hyperliquid perpetual markets',
      parameters: { type: 'object', properties: {}, required: [] },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_l2_book',
      description: 'Get level-2 order book (bid/ask depth) for a coin',
      parameters: {
        type: 'object',
        properties: {
          coin:    { type: 'string', description: 'Coin symbol e.g. BTC' },
          nLevels: { type: 'number', description: 'Number of price levels (default 20)' },
        },
        required: ['coin'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_clearinghouse_state',
      description: 'Get perpetual account state: positions, equity, margin summary for a user',
      parameters: {
        type: 'object',
        properties: { user: { type: 'string', description: 'Wallet address (use master account address)' } },
        required: ['user'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_open_orders',
      description: 'Get open orders for a user on Hyperliquid',
      parameters: {
        type: 'object',
        properties: { user: { type: 'string', description: 'Wallet address' } },
        required: ['user'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_user_fills',
      description: 'Get recent trade fills for a user',
      parameters: {
        type: 'object',
        properties: { user: { type: 'string', description: 'Wallet address' } },
        required: ['user'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_funding_history',
      description: 'Get funding rate history for a coin',
      parameters: {
        type: 'object',
        properties: {
          coin:      { type: 'string', description: 'Coin symbol e.g. BTC' },
          startTime: { type: 'number', description: 'Start timestamp in ms (defaults to 24h ago)' },
        },
        required: ['coin'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_candle_snapshot',
      description: 'Get OHLCV candle data for a coin on Hyperliquid',
      parameters: {
        type: 'object',
        properties: {
          coin:     { type: 'string', description: 'Coin symbol e.g. BTC' },
          interval: { type: 'string', description: 'Candle interval: 1m, 5m, 15m, 1h, 4h, 1d' },
          count:    { type: 'number', description: 'Number of candles to return (default 10)' },
        },
        required: ['coin', 'interval'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_meta',
      description: 'Get Hyperliquid exchange metadata (assets, leverage limits)',
      parameters: { type: 'object', properties: {}, required: [] },
    },
  },
  ...(process.env.BRAVE_API_KEY ? [{
    type: 'function' as const,
    function: {
      name: 'brave_search',
      description: 'Search the web for current BTC news, macro events, or sentiment using Brave Search',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Search query, e.g. "BTC price today" or "Bitcoin news"' },
          count: { type: 'number', description: 'Number of results to return (default 5, max 10)' },
        },
        required: ['query'],
      },
    },
  }] : []),
]

async function hlPost(body: object): Promise<unknown> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

export async function executeTool(name: string, args: Record<string, unknown>): Promise<string> {
  const user = (args.user as string) || HL_ACCOUNT
  try {
    switch (name) {
      case 'get_all_mids':
        return JSON.stringify(await hlPost({ type: 'allMids' }))

      case 'get_l2_book':
        return JSON.stringify(await hlPost({ type: 'l2Book', coin: args.coin ?? 'BTC', nLevels: args.nLevels ?? 20 }))

      case 'get_clearinghouse_state':
        return JSON.stringify(await hlPost({ type: 'clearinghouseState', user }))

      case 'get_open_orders':
        return JSON.stringify(await hlPost({ type: 'openOrders', user }))

      case 'get_user_fills':
        return JSON.stringify(await hlPost({ type: 'userFills', user }))

      case 'get_funding_history': {
        const startTime = (args.startTime as number) ?? (Date.now() - 86_400_000)
        return JSON.stringify(await hlPost({ type: 'fundingHistory', coin: args.coin ?? 'BTC', startTime }))
      }

      case 'get_candle_snapshot': {
        const interval = (args.interval as string) ?? '15m'
        const count    = (args.count as number) ?? 10
        const msPerCandle: Record<string, number> = { '1m': 60000, '5m': 300000, '15m': 900000, '1h': 3600000, '4h': 14400000, '1d': 86400000 }
        const endTime   = Date.now()
        const startTime = endTime - (msPerCandle[interval] ?? 900000) * count
        return JSON.stringify(await hlPost({ type: 'candleSnapshot', req: { coin: args.coin ?? 'BTC', interval, startTime, endTime } }))
      }

      case 'get_meta':
        return JSON.stringify(await hlPost({ type: 'meta' }))

      case 'brave_search': {
        const apiKey = process.env.BRAVE_API_KEY
        if (!apiKey) return JSON.stringify({ error: 'BRAVE_API_KEY not set' })
        const query = encodeURIComponent((args.query as string) ?? 'BTC price')
        const count = Math.min((args.count as number) ?? 5, 10)
        const res = await fetch(`https://api.search.brave.com/res/v1/web/search?q=${query}&count=${count}`, {
          headers: { 'X-Subscription-Token': apiKey, Accept: 'application/json' },
        })
        const data = await res.json() as {
          web?: { results?: Array<{ title: string; description: string; url: string }> }
        }
        const results = (data.web?.results ?? []).map(r => ({
          title:       r.title,
          description: r.description,
          url:         r.url,
        }))
        return JSON.stringify(results)
      }

      default:
        return JSON.stringify({ error: `Unknown tool: ${name}` })
    }
  } catch (err) {
    return JSON.stringify({ error: String(err) })
  }
}
