// Pre-AI technical analysis filter — pure statistical validation of triggered signals.
// Importable from Node.js scripts (no next/* imports).

import type { Perception } from './perception'
import type { Candle, TASignal } from '../types'
import { ema, atr, rsi, adx } from './triggers'
import { fetchHLCandles } from '../hl-client'

export type { TASignal }

interface TAResult {
  signal: TASignal
  score: number
  trend1h: 'bullish' | 'bearish' | 'flat'
  trend4h: 'bullish' | 'bearish' | 'flat'
  trend1d: 'bullish' | 'bearish' | 'flat'
  trendAligned: boolean
  rsi4h: number | null
  atr4pct: number | null
  adx4h: number | null
  emaCross: boolean
  volumeConfirm: boolean
  reason: string
}

function assessTrend(candles: Candle[]): 'bullish' | 'bearish' | 'flat' {
  if (candles.length < 30) return 'flat'
  const closes = candles.map(c => c.c)
  const ema8Arr = ema(closes, 8)
  const ema21Arr = ema(closes, 21)
  const i = closes.length - 1
  const e8 = ema8Arr[i], e21 = ema21Arr[i]
  if (!isFinite(e8) || !isFinite(e21)) return 'flat'

  const e8Prev = ema8Arr[Math.max(0, i - 3)]
  const emaCross = e8 > e21
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
  for (let i = closes.length - 3; i < closes.length; i++) {
    if (i < 1) continue
    const prev8 = ema8Arr[i - 1], prev21 = ema21Arr[i - 1]
    const curr8 = ema8Arr[i], curr21 = ema21Arr[i]
    if (!isFinite(prev8) || !isFinite(prev21) || !isFinite(curr8) || !isFinite(curr21)) continue
    if ((prev8 <= prev21 && curr8 > curr21) || (prev8 >= prev21 && curr8 < curr21)) return true
  }
  return false
}

export async function analyzePerception(perception: Perception): Promise<TAResult> {
  try {
    const [c1h, c4h, c1d] = await Promise.all([
      fetchHLCandles(perception.coin, '1h', 60),
      fetchHLCandles(perception.coin, '4h', 60),
      fetchHLCandles(perception.coin, '1d', 40),
    ])

    if (c4h.length < 30) {
      return {
        signal: 'REJECTED', score: 0,
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

    const isBullish = t4h === 'bullish' || t1d === 'bullish'
    const isBearish = t4h === 'bearish' || t1d === 'bearish'
    const trendAligned = isBullish || isBearish

    const rsi4h = computeRSI(c4h)
    const atr4pct = computeATR4pct(c4h)
    const adx4h = computeADX(c4h)
    const emaCross = checkEMACrossRecent(c4h)
    const volumeConfirm = checkVolumeConfirm(c4h)

    let score = 0
    const reasons: string[] = []

    if (trendAligned) { score += 20; reasons.push('trend aligned') }
    if (rsi4h !== null && rsi4h > 30 && rsi4h < 70) { score += 15; reasons.push(`RSI ${rsi4h.toFixed(0)}`) }
    if (atr4pct !== null && atr4pct >= 0.5) { score += 15; reasons.push(`ATR ${atr4pct.toFixed(1)}%`) }
    if (adx4h !== null && adx4h >= 25) { score += 15; reasons.push(`ADX ${adx4h.toFixed(0)}`) }
    if (emaCross) { score += 10; reasons.push('EMA cross') }
    if (volumeConfirm) { score += 10; reasons.push('volume confirmed') }
    score += Math.min(15, perception.compositeScore / 100 * 15)

    const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED'

    return {
      signal: verdict, score: Math.min(100, score),
      trend1h: t1h, trend4h: t4h, trend1d: t1d,
      trendAligned, rsi4h, atr4pct, adx4h, emaCross, volumeConfirm,
      reason: reasons.length > 0 ? reasons.join(', ') : 'no signals',
    }
  } catch (err) {
    return {
      signal: 'REJECTED', score: 0,
      trend1h: 'flat', trend4h: 'flat', trend1d: 'flat',
      trendAligned: false,
      rsi4h: null, atr4pct: null, adx4h: null,
      emaCross: false, volumeConfirm: false,
      reason: `TA error: ${err instanceof Error ? err.message : String(err)}`,
    }
  }
}

export async function analyzePerceptions(
  perceptions: Perception[],
  concurrency: number = 3,
): Promise<Map<string, TAResult>> {
  const results = new Map<string, TAResult>()
  let idx = 0
  async function worker(): Promise<void> {
    while (idx < perceptions.length) {
      const p = perceptions[idx++]
      results.set(p.id, await analyzePerception(p))
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, perceptions.length) }, () => worker())
  )
  return results
}
