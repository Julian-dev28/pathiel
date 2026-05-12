// Deep-analysis pipeline: perception -> multi-TF indicators -> news -> AI verdict -> persist
// Importable from Node.js scripts (no next/* imports).

import type { Perception } from './perception'
import type { AgentAnalysis, AgentVerdict } from './memory'
import { memory } from './memory'
import { buildSystemPrompt } from './system-prompt'
import { ema, sma, atr as calcAtr, rsi, adx } from './triggers'
import { createOpenAIClient, OPENROUTER_MODEL } from '../openrouter-client'
import { readAgentConfig as readConfig } from './config-store'
import * as crypto from 'crypto'

const HL_API = 'https://api.hyperliquid.xyz'

type CandleRow = { t: number; o: string; h: string; l: string; c: string; v: string }
type Candle = { t: number; o: number; h: number; l: number; c: number; v: number }

interface IndicatorSnapshot {
  ema8: number | null
  ema21: number | null
  slopeUp: boolean | null
  rsi14: number | null
  atr14: number | null
  adx14: number | null
  lastClose: number
  lastTime: number
}

interface NewsResult {
  title: string
  description: string
  url: string
  recency: number
}

async function hlPost(body: object): Promise<unknown> {
  const res = await fetch(`${HL_API}/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HL API ${res.status}`)
  return res.json()
}

async function fetchCandles(coin: string, interval: string, count: number): Promise<Candle[]> {
  const msPerCandle: Record<string, number> = {
    '1m': 60_000, '5m': 300_000, '15m': 900_000, '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
  }
  const intvMs = msPerCandle[interval] ?? 300_000
  const endTime = Date.now()
  const startTime = endTime - intvMs * count
  const raw = await hlPost({ type: 'candleSnapshot', req: { coin, interval, startTime, endTime } }) as CandleRow[]
  if (!Array.isArray(raw)) return []
  return raw.map(c => ({
    t: c.t, o: parseFloat(c.o), h: parseFloat(c.h), l: parseFloat(c.l), c: parseFloat(c.c), v: parseFloat(c.v ?? '0'),
  }))
}

async function fetchFundingRate(coin: string): Promise<string> {
  try {
    const raw = await hlPost({ type: 'fundingHistory', coin, startTime: Date.now() - 86_400_000 }) as Array<{ funding: string }>
    if (Array.isArray(raw) && raw.length > 0) return raw[raw.length - 1].funding
  } catch { /* skip */ }
  return 'N/A'
}

function computeIndicators(candles: Candle[]): IndicatorSnapshot {
  const closes = candles.map(c => c.c)
  const ema8Arr = ema(closes, 8)
  const ema21Arr = ema(closes, 21)
  const lastEma8 = ema8Arr[ema8Arr.length - 1]
  const lastEma21 = ema21Arr[ema21Arr.length - 1]

  let slopeUp: boolean | null = null
  if (isFinite(lastEma8) && ema8Arr.length >= 3 && isFinite(ema8Arr[ema8Arr.length - 3])) {
    slopeUp = lastEma8 > ema8Arr[ema8Arr.length - 3]
  }

  const rsi14Arr = rsi(candles, 14)
  const atr14Arr = calcAtr(candles, 14)
  const adx14Arr = adx(candles, 14)

  return {
    ema8: isFinite(lastEma8) ? lastEma8 : null,
    ema21: isFinite(lastEma21) ? lastEma21 : null,
    slopeUp,
    rsi14: isFinite(rsi14Arr[rsi14Arr.length - 1]) ? rsi14Arr[rsi14Arr.length - 1] : null,
    atr14: isFinite(atr14Arr[atr14Arr.length - 1]) ? atr14Arr[atr14Arr.length - 1] : null,
    adx14: isFinite(adx14Arr[adx14Arr.length - 1]) ? adx14Arr[adx14Arr.length - 1] : null,
    lastClose: closes[closes.length - 1] ?? 0,
    lastTime: candles[candles.length - 1]?.t ?? 0,
  }
}

async function fetchNews(coin: string): Promise<NewsResult[]> {
  const apiKey = process.env.BRAVE_API_KEY
  if (!apiKey) return []

  try {
    const query = encodeURIComponent(`${coin} crypto news today`)
    const res = await fetch(`https://api.search.brave.com/res/v1/web/search?q=${query}&count=3`, {
      headers: { 'X-Subscription-Token': apiKey, Accept: 'application/json' },
    })
    if (!res.ok) return []
    const data = await res.json() as { web?: { results?: Array<{ title: string; description: string; url: string; page_age?: string }> } }
    const results = (data.web?.results ?? []).map(r => ({
      title: r.title,
      description: r.description,
      url: r.url,
      recency: r.page_age ? Math.round((Date.now() - new Date(r.page_age).getTime()) / 3_600_000) : 0,
    }))
    return results.slice(0, 3)
  } catch {
    return []
  }
}

function buildUserMessage(
  coin: string,
  perception: Perception,
  tf1h: IndicatorSnapshot,
  tf4h: IndicatorSnapshot,
  tf1d: IndicatorSnapshot,
  fundingRate: string,
  newsHeadlines: NewsResult[],
  equity: number,
  openPositions: Array<{ coin: string; side: string; sizeUSD: number }>,
  mode: string,
): string {
  const triggerSummary = perception.triggers
    .filter(t => t.fired)
    .map(t => `${t.name}: ${t.reason}`)
    .join(', ') || 'no triggers fired'

  const indicatorBlock = (label: string, snap: IndicatorSnapshot): string => {
    const parts: string[] = []
    if (snap.ema8 !== null && snap.ema21 !== null) {
      parts.push(`EMA8=${snap.ema8.toFixed(4)}, EMA21=${snap.ema21.toFixed(4)}, ${snap.ema8 > snap.ema21 ? 'bullish' : 'bearish'}`)
    }
    if (snap.slopeUp !== null) parts.push(`EMA8 slope: ${snap.slopeUp ? 'rising' : 'falling'}`)
    if (snap.rsi14 !== null) parts.push(`RSI(14)=${snap.rsi14.toFixed(1)}`)
    if (snap.atr14 !== null) parts.push(`ATR(14)=${snap.atr14.toFixed(4)}`)
    if (snap.adx14 !== null) parts.push(`ADX(14)=${snap.adx14.toFixed(1)}`)
    parts.push(`last close=${snap.lastClose.toFixed(4)}`)
    return `${label}: ${parts.join(' | ')}`
  }

  const newsBlock = newsHeadlines.length > 0
    ? `News (top 3):\n${newsHeadlines.map(n => `- [${n.recency}h ago] ${n.title} — ${n.description}`).join('\n')}`
    : 'News: no headlines (BRAVE_API_KEY not set or empty results)'

  const positionBlock = openPositions.length > 0
    ? `Open positions: ${openPositions.map(p => `${p.coin} ${p.side} $${p.sizeUSD.toFixed(0)}`).join(', ')}`
    : 'Open positions: none'

  return [
    `Candidate: ${coin} (HL ${perception.type}-PERP)`,
    `Current mid: $${perception.mid.toFixed(4)}`,
    `Perception score: ${perception.compositeScore}/100`,
    `Fired triggers: ${triggerSummary}`,
    '',
    'Market context (multi-timeframe):',
    indicatorBlock('1h', tf1h),
    indicatorBlock('4h', tf4h),
    indicatorBlock('1d', tf1d),
    '',
    `Funding rate (latest): ${fundingRate}`,
    `Equity: $${equity.toFixed(2)}`,
    positionBlock,
    '',
    newsBlock,
    '',
    `Mode: ${mode} — ${mode === 'LIVE' ? 'your verdict will execute against real funds' : 'analysis only, no execution'}`,
    '',
    'Respond with 3-5 bullet points of reasoning, then output your decision as VALID JSON on the very last line:',
    '{"verdict":"PASS"|"LONG"|"SHORT"|"CLOSE","confidence":0.0-1.0,"side":"long"|"short"|null,"entryPx":number,"stopPx":number,"tpPx":number,"reasoning":"brief"}',
    'Nothing after the JSON.',
  ].join('\n')
}

async function callAI(systemPrompt: string, userMessage: string): Promise<string> {
  const client = createOpenAIClient()
  const response = await client.chat.completions.create({
    model: OPENROUTER_MODEL,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMessage },
    ],
    stream: false,
    max_tokens: 1024,  // trimmed: 2-3 bullets + JSON only
    temperature: 0.1,
  })
  return response.choices[0]?.message?.content ?? ''
}

function parseVerdict(aiText: string, coin: string, perception: Perception): {
  verdict: AgentVerdict; confidence: number; side: 'long' | 'short' | null;
  entryPx: number; stopPx: number; tpPx: number; reasoning: string;
} {
  let verdict: AgentVerdict = 'PASS'
  let confidence = 0
  let side: 'long' | 'short' | null = null
  let entryPx = perception.mid
  let stopPx = 0
  let tpPx = 0
  let reasoning = aiText.trim()

  const lines = aiText.trim().split('\n')
  let jsonStr = ''
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim()
    if (line.startsWith('{') && line.includes('verdict') && line.endsWith('}')) {
      jsonStr = line
      break
    }
  }

  if (!jsonStr) {
    const match = aiText.match(/\{[^{}]*"verdict"[^{}]*\}/)
    if (match) jsonStr = match[0]
  }

  if (jsonStr) {
    try {
      const cleaned = jsonStr.replace(/```json?\s*/g, '').replace(/```\s*/g, '').trim()
      const parsed = JSON.parse(cleaned) as Record<string, unknown>

      const raw = String(parsed.verdict ?? '').toUpperCase()
      if (raw === 'LONG') verdict = 'LONG'
      else if (raw === 'SHORT') verdict = 'SHORT'
      else if (raw === 'CLOSE') verdict = 'CLOSE'

      confidence = typeof parsed.confidence === 'number' ? parsed.confidence : 0
      side = parsed.side === 'long' ? 'long' : parsed.side === 'short' ? 'short' : null
      entryPx = typeof parsed.entryPx === 'number' ? parsed.entryPx : perception.mid
      stopPx = typeof parsed.stopPx === 'number' ? parsed.stopPx : 0
      tpPx = typeof parsed.tpPx === 'number' ? parsed.tpPx : 0
      reasoning = typeof parsed.reasoning === 'string' ? parsed.reasoning : aiText.slice(0, 500)
    } catch {
      const firstLine = aiText.trim().split('\n')[0] ?? ''
      if (/LONG/i.test(firstLine)) verdict = 'LONG'
      else if (/SHORT/i.test(firstLine)) verdict = 'SHORT'
      else if (/CLOSE/i.test(firstLine)) verdict = 'CLOSE'
    }
  }

  return { verdict, confidence, side, entryPx, stopPx, tpPx, reasoning }
}

export async function research(coin: string, perception: Perception): Promise<AgentAnalysis> {
  try {
    const [c1h, c4h, c1d, fundingRaw] = await Promise.all([
      fetchCandles(coin, '1h', 100).catch(() => [] as Candle[]),
      fetchCandles(coin, '4h', 100).catch(() => [] as Candle[]),
      fetchCandles(coin, '1d', 60).catch(() => [] as Candle[]),
      fetchFundingRate(coin),
    ])

    const tf1h = computeIndicators(c1h)
    const tf4h = computeIndicators(c4h)
    const tf1d = computeIndicators(c1d)

    // Skip news fetch — saves Brave API calls + ~400 tokens/call. Technical signals are the edge.
    const news: NewsResult[] = []

    const config = await readConfig()
    const mode = (config.mode as string) || 'OFF'

    let equity = 0
    let openPositions: Array<{ coin: string; side: string; sizeUSD: number }> = []
    try {
      const user = process.env.HYPERLIQUID_MASTER_ADDRESS || process.env.HYPERLIQUID_WALLET_ADDRESS || ''
      const [perpRaw, spotRaw] = await Promise.all([
        hlPost({ type: 'clearinghouseState', user }) as Promise<{
          marginSummary?: { accountValue: string }
          assetPositions?: Array<{ position: { coin: string; szi: string } }>
        }>,
        hlPost({ type: 'spotClearinghouseState', user }) as Promise<{
          balances?: Array<{ coin: string; total: string }>
        }>,
      ])

      const perpEquity = parseFloat(perpRaw.marginSummary?.accountValue ?? '0')
      const spotUSDC = (spotRaw.balances ?? []).find(b => b.coin === 'USDC')
      equity = perpEquity + (spotUSDC ? parseFloat(spotUSDC.total) : 0)

      // Check perp positions for context
      openPositions = (perpRaw.assetPositions ?? [])
        .filter(p => parseFloat(p.position.szi) !== 0)
        .map(p => ({
          coin: p.position.coin,
          side: parseFloat(p.position.szi) > 0 ? 'long' : 'short',
          sizeUSD: Math.abs(parseFloat(p.position.szi)) * (tf4h.lastClose || perception.mid),
        }))
    } catch { /* skip account context */ }

    const wr = memory.getWinRate()

    const systemPrompt = buildSystemPrompt({ mode: mode as 'OFF' | 'LIVE', winRate: wr.rate, recentTrades: wr.total })
    const userMessage = buildUserMessage(coin, perception, tf1h, tf4h, tf1d, fundingRaw, news, equity, openPositions, mode)

    const aiText = await callAI(systemPrompt, userMessage)
    const parsed = parseVerdict(aiText, coin, perception)

    const analysis: AgentAnalysis = {
      id: crypto.randomUUID(),
      perceptionId: memory.getRecentPerceptions(1)[0]?.id ?? 'unknown',
      coin,
      verdict: parsed.verdict,
      confidence: parsed.confidence,
      side: parsed.side,
      entryPx: parsed.entryPx,
      stopPx: parsed.stopPx,
      tpPx: parsed.tpPx,
      reasoning: parsed.reasoning,
      newsContext: news.length > 0 ? news.map(n => n.title).join('; ') : 'no news',
      createdAt: Date.now(),
    }

    memory.recordAnalysis(analysis)
    return analysis
  } catch (err) {
    const fallback: AgentAnalysis = {
      id: crypto.randomUUID(),
      perceptionId: 'unknown',
      coin,
      verdict: 'PASS',
      confidence: 0,
      side: null,
      entryPx: perception.mid,
      stopPx: 0,
      tpPx: 0,
      reasoning: `Research failed: ${err instanceof Error ? err.message : String(err)}`,
      createdAt: Date.now(),
    }
    memory.recordAnalysis(fallback)
    return fallback
  }
}
