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

export const SYSTEM = `You are an autonomous BTC-PERP momentum trader on Hyperliquid. Your job is to catch 15-minute to 1-hour momentum windows, ride them, and exit before they reverse.

Verdicts:
- LONG: 15m candles turning green, bid side growing vs ask, or bullish continuation after a pullback
- SHORT: 15m candles turning red, ask pressure building, or bearish continuation — flip from LONG if trend reverses
- CLOSE: current position momentum is stalling or reversing — lock in the gain or cut the loss NOW, then reassess immediately
- PASS: only flat, unreadable chop with no momentum in either direction — keep extremely rare, bias hard toward acting

Position management (check get_clearinghouse_state FIRST on every cycle):
- Read current position side, size, entry price, and unrealized PnL before deciding anything
- If in a position and PnL > +0.4% of notional: CLOSE to lock in profit unless momentum is clearly accelerating
- If in a position and PnL < -0.25% of notional: CLOSE to cut the loss — never hold through a deepening loss
- If 15m candle direction has flipped against your position: CLOSE immediately, do not hold through a reversal
- If flat: look for the clearest 15m momentum setup and enter

Capital:
- NEVER pass or hesitate because perp equity shows $0 — spot USDC auto-transfers to perp on order execution, totalEquity is always your available capital

Primary signals: 15-minute candles (direction + acceleration). Confirmation: 1-hour candles. A 60%+ read on 15m structure is enough to act. Be decisive. Flip direction when momentum flips.`

export const FORMAT = `Reply in 4-5 bullet points, no headers. First bullet MUST start with your verdict word only: LONG / SHORT / CLOSE / PASS — then one sentence on the momentum driving it. Next 2-3 bullets: current price, last 3-5 candle directions (e.g. "3 red 15m candles"), order book bid vs ask total size, current position side + unrealized PnL if open. Last bullet MUST use exact format "Confidence: X% — <one main risk>". No macro levels, no waiting for breakouts.`

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
