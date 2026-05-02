import { AomiClient, Session } from '@aomi-labs/client'

const AOMI_BASE_URL = process.env.AOMI_BASE_URL ?? 'https://api.aomi.dev'
const AOMI_APP      = process.env.AOMI_APP ?? 'default'
const AOMI_API_KEY  = process.env.AOMI_API_KEY

// Master account holds all funds — AOMI tools must query this address for real balances/positions
const HL_MASTER  = process.env.HYPERLIQUID_MASTER_ADDRESS ?? ''
const HL_WALLET  = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''
const HL_ACCOUNT = HL_MASTER || HL_WALLET  // prefer master; fall back to wallet if no master set

export function createAomiClient() {
  return new AomiClient({ baseUrl: AOMI_BASE_URL, apiKey: AOMI_API_KEY })
}

export function createSession(sessionId?: string) {
  return new Session(
    { baseUrl: AOMI_BASE_URL, apiKey: AOMI_API_KEY },
    {
      app:       AOMI_APP,
      sessionId: sessionId ?? crypto.randomUUID(),
      apiKey:    AOMI_API_KEY,
      publicKey: HL_ACCOUNT || undefined,
      userState: HL_ACCOUNT ? {
        address:      HL_ACCOUNT,
        is_connected: true,
        chain_id:     1337,
      } : undefined,
    },
  )
}

const SYSTEM = `You are an autonomous short-term BTC-PERP momentum trader on Hyperliquid. Your goal is to trade as many profitable windows as possible — catch trends early, ride them, close or flip when they reverse.

Trading rules:
- LONG: upward momentum right now — green candles accelerating, bid side heavier than ask in order book
- SHORT: downward momentum right now — red candles, ask pressure dominates, or a LONG that is losing steam
- CLOSE: current position momentum is fading or reversing — exit before it turns into a loss, then reassess
- PASS: only when there is genuine sideways chop with zero readable edge — keep PASS rare, bias toward acting

Do NOT wait for macro levels like "$79K breakout" or "$77K breakdown". Trade momentum across 5 minute to 4 hour timeframes — whatever the current structure shows. A 60%+ directional read is enough to act. Be decisive. Flip direction when the trend changes.`

const FORMAT = `Reply in 4–5 bullet points, no headers. First bullet MUST start with your verdict: LONG / SHORT / CLOSE / PASS — one sentence on the near-term momentum driving it. Next 2–3 bullets: specific data (current price, last few candles direction, order book bid vs ask size, current position PnL if any). Last bullet: confidence % and the one main risk to this trade. No macro targets, no waiting for levels.`

export function buildPrompt(userMessage: string, hint?: string): string {
  const parts = [SYSTEM]
  if (hint) {
    parts.push(`Live market snapshot (use tools to verify/supplement):\n${hint}`)
  }
  parts.push(userMessage)
  parts.push(FORMAT)
  return parts.join('\n\n')
}
