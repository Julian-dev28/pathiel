import { AomiClient, Session } from '@aomi-labs/client'

const AOMI_BASE_URL = process.env.AOMI_BASE_URL ?? 'https://api.aomi.dev'
const AOMI_APP      = process.env.AOMI_APP ?? 'default'
const AOMI_API_KEY  = process.env.AOMI_API_KEY ?? ''

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

const SYSTEM = `You are an autonomous BTC-PERP momentum trader on Hyperliquid. Your job is to catch 15-minute to 1-hour momentum windows, ride them, and exit before they reverse.

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

const FORMAT = `Reply in 4-5 bullet points, no headers. First bullet MUST start with your verdict word only: LONG / SHORT / CLOSE / PASS — then one sentence on the momentum driving it. Next 2-3 bullets: current price, last 3-5 candle directions (e.g. "3 red 15m candles"), order book bid vs ask total size, current position side + unrealized PnL if open. Last bullet MUST use exact format "Confidence: X% — <one main risk>". No macro levels, no waiting for breakouts.`

export function buildPrompt(userMessage: string, hint?: string): string {
  const parts = [SYSTEM]
  if (hint) {
    parts.push(`Live market snapshot (use tools to verify/supplement):\n${hint}`)
  }
  parts.push(userMessage)
  parts.push(FORMAT)
  return parts.join('\n\n')
}
