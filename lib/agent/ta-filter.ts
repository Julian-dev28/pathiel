// Pre-AI technical analysis filter — pure statistical validation of triggered signals.
// Computes multi-timeframe indicators, trend alignment, volatility, and volume confirmation.
// Only returns CONFIRMED when signals are strong enough — AI is the LAST step.
// Importable from Node.js scripts (no next/* imports).

import type { Perception } from './perception'
import type { Candle } from './triggers'
import { ema, sma, atr, rsi, adx } from './triggers'

const HL_API = 'https://api.hyperliquid.xyz'

export type TASignal = 'CONFIRMED' | 'WEAK' | 'REJECTED'

interface TAResult {
  signal: TASignal
  score: number       // 0-100
  trend1h: 'bullish' | 'bearish' | 'flat'
  trend4h: 'bullish' | 'bearish' | 'flat'
  trend1d: 'bullish' | 'bearish' | 'flat'
  trendAligned: boolean
  rsi4h: number | null
  atr4pct: number | null  // ATR as % of price (on 4h)
  adx4h: number | null
  emaCross: boolean      // 4h EMA8 crossed above/below EMA21 recently
  volumeConfirm: boolean // volume ≥ 80% of 20-bar average
  reason: string
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

type CandleRow = { t: number; o: string; h: string; l: string; c: string; v: string }

async function fetchCandles(coin: string, interval: string, count: number): Promise<Candle[]> {
  const msPerCandle: Record<string, number> = {
    '1m': 60_000, '5m': 300_000, '15m': 900_000,
    '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
  }
  const intvMs = msPerCandle[interval] ?? 300_000
  const endTime = Date.now()
  const startTime = endTime - intvMs * count
  const raw = await hlPost({ type: 'candleSnapshot', req: { coin, interval, startTime, endTime } }) as CandleRow[]
  if (!Array.isArray(raw) || raw.length < 30) return []
  return raw.map(c => ({
    t: c.t, o: parseFloat(c.o), h: parseFloat(c.h),
    l: parseFloat(c.l), c: parseFloat(c.c), v: parseFloat(c.v ?? '0'),
  }))
}

function assessTrend(candles: Candle[]): 'bullish' | 'bearish' | 'flat' {
  if (candles.length < 30) return 'flat'
  const closes = candles.map(c => c.c)
  const ema8Arr = ema(closes, 8)
  const ema21Arr = ema(closes, 21)
  const i = closes.length - 1
  const e8 = ema8Arr[i], e21 = ema21Arr[i]
  if (!isFinite(e8) || !isFinite(e21)) return 'flat'

  // Slope: compare EMA8 now vs 3 bars ago
  const e8Prev = ema8Arr[Math.max(0, i - 3)]
  const emaCross = e8 > e21  // bullish cross
  const slopeRising = e8 > e8Prev

  if (emaCross && slopeRising) return 'bullish'
  if (!emaCross && !slopeRising) return 'bearish'
  return 'flat'
}

function computeATR4pct(candles: Candle[]): number | null {
  if (candles.length < 20) return null
  const atrArr = atr(candles, 14)
  const last = atrArr[atrArr.length - 1]
  const lastClose = candles[candles.length - 1].c
  if (!isFinite(last) || lastClose === 0) return null
  return (last / lastClose) * 100
}

function computeRSI(candles: Candle[]): number | null {
  if (candles.length < 20) return null
  const arr = rsi(candles, 14)
  const last = arr[arr.length - 1]
  return isFinite(last) ? last : null
}

function computeADX(candles: Candle[]): number | null {
  if (candles.length < 30) return null
  const arr = adx(candles, 14)
  const last = arr[arr.length - 1]
  return isFinite(last) ? last : null
}

function checkVolumeConfirm(candles: Candle[]): boolean {
  if (candles.length < 21) return false
  const lastVol = candles[candles.length - 1].v
  const avgVol = candles.slice(-21, -1).reduce((s, c) => s + c.v, 0) / 20
  return avgVol === 0 ? false : lastVol >= avgVol * 0.8
}

function checkEMACrossRecent(candles: Candle[]): boolean {
  if (candles.length < 25) return false
  const closes = candles.map(c => c.c)
  const ema8Arr = ema(closes, 8)
  const ema21Arr = ema(closes, 21)
  // Check if a crossover happened within the last 3 bars
  for (let i = closes.length - 3; i < closes.length; i++) {
    if (i < 1) continue
    const prev8 = ema8Arr[i - 1], prev21 = ema21Arr[i - 1]
    const curr8 = ema8Arr[i], curr21 = ema21Arr[i]
    if (!isFinite(prev8) || !isFinite(prev21) || !isFinite(curr8) || !isFinite(curr21)) continue
    if ((prev8 <= prev21 && curr8 > curr21) || (prev8 >= prev21 && curr8 < curr21)) return true
  }
  return false
}

/**
 * Run full multi-TF technical analysis on a triggered perception.
 * Returns a TA signal with score and reason.
 */
export async function analyzePerception(perception: Perception): Promise<TAResult> {
  try {
    const [c1h, c4h, c1d] = await Promise.all([
      fetchCandles(perception.coin, '1h', 60),
      fetchCandles(perception.coin, '4h', 60),
      fetchCandles(perception.coin, '1d', 40),
    ])

    if (c4h.length < 30) {
      return {
        signal: 'REJECTED',
        score: 0,
        trend1h: 'flat', trend4h: 'flat', trend1d: 'flat',
        trendAligned: false,
        rsi4h: null, atr4pct: null, adx4h: null,
        emaCross: false, volumeConfirm: false,
        reason: 'insufficient candle data',
      }
    }

    const t1h = assessTrend(c1h)
    const t4h = assessTrend(c4h)
    const t1d = assessTrend(c1d)

    // Trend alignment: higher timeframe should agree with the trigger direction
    const isBullish = t4h === 'bullish' || t1d === 'bullish'
    const isBearish = t4h === 'bearish' || t1d === 'bearish'
    const trendAligned = isBullish || isBearish  // at least one major TF has a trend

    const rsi4h = computeRSI(c4h)
    const atr4pct = computeATR4pct(c4h)
    const adx4h = computeADX(c4h)
    const emaCross = checkEMACrossRecent(c4h)
    const volumeConfirm = checkVolumeConfirm(c4h)

    // Scoring: 0-100 based on signal quality
    let score = 0
    const reasons: string[] = []

    // Trend alignment: +20
    if (trendAligned) { score += 20; reasons.push('trend aligned') }

    // RSI: +15 if not overbought (for long) or oversold (for short)
    if (rsi4h !== null) {
      if (rsi4h > 30 && rsi4h < 70) { score += 15; reasons.push(`RSI ${rsi4h.toFixed(0)}`) }
    }

    // ATR: +15 if meaningful volatility (≥0.5%)
    if (atr4pct !== null && atr4pct >= 0.5) { score += 15; reasons.push(`ATR ${atr4pct.toFixed(1)}%`) }

    // ADX: +15 if trending strength ≥ 25
    if (adx4h !== null && adx4h >= 25) { score += 15; reasons.push(`ADX ${adx4h.toFixed(0)}`) }

    // EMA cross: +10 if recent cross
    if (emaCross) { score += 10; reasons.push('EMA cross') }

    // Volume: +10 if confirming
    if (volumeConfirm) { score += 10; reasons.push('volume confirmed') }

    // Perception trigger score: scale to add up to 15
    score += Math.min(15, perception.compositeScore / 100 * 15)

    const verdict = score >= 45 ? "CONFIRMED" : score >= 30 ? "WEAK" : "REJECTED"

    return {
      signal: verdict,
      score: Math.min(100, score),
      trend1h: t1h, trend4h: t4h, trend1d: t1d,
      trendAligned, rsi4h, atr4pct, adx4h, emaCross, volumeConfirm,
      reason: reasons.length > 0 ? reasons.join(', ') : 'no signals',
    }
  } catch (err) {
    return {
      signal: 'REJECTED',
      score: 0,
      trend1h: 'flat', trend4h: 'flat', trend1d: 'flat',
      trendAligned: false,
      rsi4h: null, atr4pct: null, adx4h: null,
      emaCross: false, volumeConfirm: false,
      reason: `TA error: ${err instanceof Error ? err.message : String(err)}`,
    }
  }
}

/**
 * Batch analyze multiple perceptions. Uses a semaphore to avoid hammering HL.
 */
export async function analyzePerceptions(
  perceptions: Perception[],
  concurrency: number = 3,
): Promise<Map<string, TAResult>> {
  const results = new Map<string, TAResult>()
  const semaphore = Array.from({ length: concurrency }, () => Promise.resolve())

  async function analyzeOne(p: Perception): Promise<void> {
    const result = await analyzePerception(p)
    results.set(p.id, result)
  }

  // Run with simple semaphore
  let idx = 0
  async function worker(): Promise<void> {
    while (idx < perceptions.length) {
      const p = perceptions[idx++]
      await analyzeOne(p)
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, perceptions.length) }, () => worker())
  )

  return results
}
