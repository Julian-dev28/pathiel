import { AomiClient, Session } from '@aomi-labs/client'

const AOMI_BASE_URL = process.env.AOMI_BASE_URL ?? 'https://api.aomi.dev'
const AOMI_APP      = process.env.AOMI_APP ?? 'default'
const AOMI_API_KEY  = process.env.AOMI_API_KEY
const HL_WALLET     = process.env.HYPERLIQUID_WALLET_ADDRESS ?? ''

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
      // Provide wallet address so agent can call get_clearinghouse_state, get_open_orders etc.
      publicKey: HL_WALLET || undefined,
      userState: HL_WALLET ? {
        address:      HL_WALLET,
        is_connected: true,
        chain_id:     1337,
      } : undefined,
    },
  )
}

const SYSTEM = `You are an AI trading analyst for Hyperliquid BTC-PERP perpetual futures. The current market data (BTC price, order book, account position) is provided to you as context in each message. Also use brave_search to find the latest BTC news and sentiment before giving your verdict.

Do NOT call send_eip712_to_wallet or send_transaction_to_wallet. Output your verdict as text — the system executes trades based on your analysis.`

const SEARCH_INSTRUCTION = `Before answering, use brave_search to research: "BTC price action today", "Bitcoin technical analysis", "crypto market sentiment today". Then synthesize with the live market snapshot below.`

const FORMAT = `Reply in 4–6 bullet points. No headers, no paragraphs. First bullet MUST be your verdict: LONG / SHORT / PASS — one sentence why. Next 2–3 bullets: key data points from Hyperliquid tools + search. Last bullet: confidence % and main risk. Be direct and specific.`

export function buildPrompt(userMessage: string, hint?: string): string {
  const parts = [SYSTEM]
  if (hint) {
    parts.push(SEARCH_INSTRUCTION)
    parts.push(`Market snapshot:\n${hint}`)
  }
  parts.push(userMessage)
  parts.push(FORMAT)
  return parts.join('\n\n')
}
