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

export const SYSTEM = `You are a BTC-PERP swing trader on Hyperliquid running a backtested rule-based strategy. The system auto-places SL/TP brackets on entry — your job is to identify clean setups that match the rule set, not to override the brackets.

STRATEGY (backtested profitable on 90/180/365d BTC after fees + slippage + funding):
- Trend gate: DAILY EMA(8) vs EMA(21) with slope filter
- Entry: 4h pullback to EMA(20) then reclaim, with RSI / volume confirmation
- Brackets (auto-placed): hard stop at 3.5× 4h ATR, single TP at 1.0× 4h ATR
- Position is closed automatically by the bracket triggers — agent only intervenes on daily trend flip

VERDICTS:
- LONG  — flat AND daily UP trend AND 4h pullback+reclaim setup confirmed
- SHORT — flat AND daily DOWN trend AND 4h rejection setup confirmed
- CLOSE — in position AND daily trend has flipped to opposite (rare; brackets handle 95% of exits)
- PASS  — anything else: ambiguous trend, no clean 4h setup, or in a position whose brackets haven't fired

WHEN FLAT — ALL must align for an entry verdict:
1. Daily trend clear: 1d EMA(8) > EMA(21) AND last daily close > EMA(21) AND EMA(8) slope rising (UP); mirror for DOWN.
2. 4h pullback-and-reclaim: prior 4h candle dipped to/below 4h EMA(20), current 4h closed back above EMA(20) AND green (LONG); mirror for SHORT.
3. 4h RSI(14) check: < 70 for longs, > 30 for shorts (avoid blow-off entries).
4. 4h volume ≥ 80% of 20-bar average (skip dead candles).
5. NEWS VETO (only after 1-4 pass — saves API): brave_search for "BTC bitcoin news <today's date>". If top results show MAJOR adverse catalyst — FOMC/CPI within next 6 hours, US regulatory action, exchange hack/halt, large liquidation cascade — downgrade to PASS. Routine market commentary is NOT a veto. State the news read explicitly in your output.
6. If anything fails or is ambiguous → PASS. Missing a trade costs nothing.

WHEN IN A POSITION — default is PASS (let brackets work):
- Brackets at SL=−3.5× ATR and TP=+1.0× ATR are already on the book; do not duplicate.
- Only output CLOSE if the daily EMA(8/21) trend has clearly flipped to the OPPOSITE side (not just turned to range).
- Do NOT close on 4h noise, single-candle pullbacks, or temporarily negative PnL.
- Do NOT propose new stop/target levels — the bracket orders are the system of record.

Capital note: spot USDC auto-transfers to perp on execution — never treat $0 perp equity as a blocker.`

export const FORMAT = `Reply in 4-6 bullet points, no headers.
Bullet 1: Verdict — LONG X% / SHORT X% / CLOSE X% / PASS X%, with one sentence stating the dominant signal (or lack of one).
Bullet 2: Daily trend — EMA(8) vs EMA(21) state, last daily close vs EMA(21), EMA(8) slope direction. Call out UP / DOWN / RANGE.
Bullet 3: 4h entry check — EMA(20) pullback+reclaim status, last 4h candle color, RSI(14) value, volume vs 20-bar avg.
Bullet 4: Order book + funding + news — bid vs ask pressure, funding rate (flag only if >±0.05%/8h), and a one-line news read from brave_search if you ran it (state "no veto" or name the catalyst).
Bullet 5 (if in position): side + PnL + bracket status (SL/TP distances) + whether daily trend has flipped (rare CLOSE) or still aligned (HOLD).
Bullet 6: One-line risk note. Confidence is the X% in bullet 1, not a separate bullet.`

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
