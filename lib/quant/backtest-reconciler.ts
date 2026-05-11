// ── Backtest Reconciler: Predicts what the backtest would say for any trade ──
// Answers: "If we had run the backtest at trade entry, would it have predicted this?"
// Used to measure live vs. backtest skew and validate edge in real-time.

import * as path from 'node:path'

const HL_API = 'https://api.hyperliquid.xyz/info'

// Default params from backtest.mjs line 364-377
const DEFAULT_PARAMS = {
  fastP: 8, slowP: 21,
  ema1P: 20, atrP: 14,
  stopMult: 3.5, rrTarget: 1.0, p1Target: 1.0,
  partial: false, partialFrac: 0.5,
  trailMult: 1.0,
  riskPct: 0.02,
  cooldown: 1,
  maxHoldBars: 0,
  adxMin: 0,
  volFloor: 0,
  useDailyTrend: false,
}

// ── Indicators ─────────────────────────────────────────────────────────────

function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(NaN)
  if (!values.length) return out
  let e = values[0]; out[0] = e
  for (let i = 1; i < values.length; i++) { e = values[i] * k + e * (1 - k); out[i] = e }
  return out
}

function atr(c: {t: number; o: number; h: number; l: number; c: number; v: number}[], period = 14): number[] {
  const tr = new Array(c.length).fill(0)
  for (let i = 0; i < c.length; i++) {
    if (i === 0) continue
    const h = c[i].h, l = c[i].l, pc = c[i-1].c
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc))
  }
  const out = new Array(c.length).fill(NaN)
  if (c.length <= period) return out
  let acc = 0
  for (let i = 1; i <= period; i++) acc += tr[i]
  out[period] = acc / period
  for (let i = period + 1; i < c.length; i++) out[i] = (out[i-1] * (period - 1) + tr[i]) / period
  return out
}

function rsi(c: {c: number}[], period = 14): number[] {
  const out = new Array(c.length).fill(NaN)
  if (c.length <= period) return out
  let g = 0, l = 0
  for (let i = 1; i <= period; i++) {
    const d = c[i].c - c[i-1].c
    if (d >= 0) g += d; else l -= d
  }
  let avgG = g / period, avgL = l / period
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  for (let i = period + 1; i < c.length; i++) {
    const d = c[i].c - c[i-1].c
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  }
  return out
}

function sma(values: number[], period: number): number[] {
  const out = new Array(values.length).fill(NaN)
  let acc = 0
  for (let i = 0; i < values.length; i++) {
    acc += values[i]
    if (i >= period) acc -= values[i - period]
    if (i >= period - 1) out[i] = acc / period
  }
  return out
}

// ── Data Fetch ─────────────────────────────────────────────────────────────

interface Candle {
  t: number; o: number; h: number; l: number; c: number; v: number
}

async function fetchCandles(coin: string, interval: string, days: number): Promise<Candle[]> {
  const endTime = Date.now()
  const startTime = endTime - days * 86400_000
  
  const resp = await fetch(HL_API.replace('/info', ''), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'candleSnapshot',
      coin,
      interval,
      startTime,
      endTime,
    }),
  })
  
  if (!resp.ok) throw new Error(`HL API error: ${resp.status}`)
  const raw = await resp.json()
  
  return (raw as Array<Record<string, string>>).map(c => ({
    t: Number(c.t),
    o: Number(c.o),
    h: Number(c.h),
    l: Number(c.l),
    c: Number(c.c),
    v: Number(c.v ?? 0),
  }))
}

// ── Trend Detection ────────────────────────────────────────────────────────

function trend4h(c: Candle[], ef: number[], es: number[], idx: number): 'up' | 'down' | 'range' {
  if (idx < 2) return 'range'
  const cl = c[idx].c
  if (!isFinite(ef[idx]) || !isFinite(es[idx]) || !isFinite(ef[idx - 2])) return 'range'
  
  const slopeUp = ef[idx] > ef[idx - 2]
  const slopeDn = ef[idx] < ef[idx - 2]
  
  if (ef[idx] > es[idx] && cl > es[idx] && slopeUp) return 'up'
  if (ef[idx] < es[idx] && cl < es[idx] && slopeDn) return 'down'
  return 'range'
}

// ── Signal Detection ───────────────────────────────────────────────────────

function shouldEnter(coin: string, coinPrice: number, interval = '4h', params = DEFAULT_PARAMS) {
  return async function() {
    // Fetch candles for analysis
    const ltf = await fetchCandles(coin, interval === '4h' ? '4h' : '1h', 180)
    const htf = await fetchCandles(coin, '1d', 180)
    
    if (ltf.length < 50 || htf.length < 10) {
      return { enter: false, reason: 'insufficient_data', trend: 'unknown', confidence: 0 }
    }
    
    // Check trend alignment
    const closeH = htf.map(x => x.c)
    const efH = ema(closeH, params.fastP)
    const esH = ema(closeH, params.slowP)
    
    const currentTrend = trend4h(htf, efH, esH, htf.length - 1)
    
    if (currentTrend === 'range') {
      return { enter: false, reason: 'range regime', trend: currentTrend, confidence: 0 }
    }
    
    // Check entry conditions
    const closeL = ltf.map(x => x.c)
    const emaL = ema(closeL, params.ema1P)
    const atrL = atr(ltf, params.atrP)
    const rsiL = rsi(ltf, 14)
    const volSma = sma(ltf.map(x => x.v), 20)
    
    const i = ltf.length - 1
    const cur = ltf[i]
    const prev = ltf[i - 1]
    
    const e = emaL[i]
    
    if (!isFinite(e) || !isFinite(atrL[i])) {
      return { enter: false, reason: 'invalid_indicators', trend: currentTrend, confidence: 0 }
    }
    
    const volOk = !isFinite(volSma[i]) || cur.v >= volSma[i] * 0.8
    const rsiOk = currentTrend === 'up' ? rsiL[i] < 70 : rsiL[i] > 30
    const entryOk = currentTrend === 'up' 
      ? (prev.l <= e && cur.c > e && cur.c > cur.o)
      : (prev.h >= e && cur.c < e && cur.c < cur.o)
    
    const enter = volOk && rsiOk && entryOk
    
    // Calculate confidence based on signal strength
    const zScore = Math.abs(currentTrend === 'up' ? (closeL[i] - emaL[i]) / (atrL[i] || 1) 
                                                  : (emaL[i] - closeL[i]) / (atrL[i] || 1))
    const confidence = Math.min(100, Math.max(0, zScore * 25))
    
    return {
      enter,
      reason: enter ? 'aligned signal' : 
              !volOk ? 'low_volume' :
              !rsiOk ? 'rsi_filtered' :
              'no_entry_pattern',
      trend: currentTrend,
      confidence,
      atr: atrL[i],
      rsi: rsiL[i],
    }
  }
}

// ── Backtest Prediction ────────────────────────────────────────────────────

export async function predictBacktest(
  coin: string,
  coinPrice: number,
  side: 'long' | 'short',
  params = DEFAULT_PARAMS,
  days = 180
): Promise<{
  predicted_wr: number
  predicted_pf: number
  predicted_ret: number
  params_hash: string
  should_trade: boolean
  backtest_verdict: string
}> {
  try {
    // Run a simplified backtest on recent data
    const ltf = await fetchCandles(coin, '4h', days)
    const htf = await fetchCandles(coin, '1d', days)
    
    if (ltf.length < 50 || htf.length < 10) {
      return {
        predicted_wr: 0,
        predicted_pf: 0,
        predicted_ret: 0,
        params_hash: 'no_data',
        should_trade: false,
        backtest_verdict: 'insufficient data',
      }
    }
    
    // Simplified backtest simulation (matches backtest.mjs logic)
    const closeH = htf.map(x => x.c)
    const efH = ema(closeH, params.fastP)
    const esH = ema(closeH, params.slowP)
    const emaL = ema(ltf.map(x => x.c), params.ema1P)
    const atrL = atr(ltf, params.atrP)
    const rsiL = rsi(ltf, 14)
    const volSma = sma(ltf.map(x => x.v), 20)
    
    const ltfMs = ltf.length > 1 ? ltf[1].t - ltf[0].t : 3600_000
    const htfMs = htf.length > 1 ? htf[1].t - htf[0].t : 86400_000
    
    let equity = 1000
    let peak = equity
    let maxDD = 0
    let pos = null
    const results: Array<{pnl: number; reason: string}> = []
    
    for (let i = 1; i < ltf.length; i++) {
      const c = ltf[i]
      const closeMs = c.t + ltfMs
      
      // Find corresponding HTF index
      let ti4 = -1
      for (let j = 0; j < htf.length; j++) {
        if (htf[j].t + htfMs <= closeMs) ti4 = j
        else break
      }
      
      const a = atrL[i]
      if (!isFinite(a) || a <= 0) continue
      
      const trend = trend4h(htf, efH, esH, ti4 > 0 ? ti4 : 0)
      
      if (pos) {
        // Check exit conditions
        const hitSL = pos.side === 'long' ? c.l <= pos.stop : c.h >= pos.stop
        const hitTP = pos.side === 'long' ? c.h >= pos.tp : c.l <= pos.tp
        const trendFlip = (pos.side === 'long' && trend === 'down') || (pos.side === 'short' && trend === 'up')
        
        if (hitSL || hitTP || trendFlip) {
          const exitPx = hitSL ? pos.stop : hitTP ? pos.tp : c.c
          const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
          const ec = pos.entryPx * pos.size * 0.001 // 0.1% cost
          const xc = exitPx * pos.size * 0.001
          const pnl = grossPnl - ec - xc
          
          equity += pnl
          results.push({ pnl, reason: hitSL ? 'stop_loss' : hitTP ? 'take_profit' : 'trend_flip' })
          pos = null
          
          peak = Math.max(peak, equity)
          maxDD = Math.max(maxDD, (peak - equity) / peak)
        }
      }
      
      // Entry logic
      if (!pos && equity > 0) {
        const want: 'long' | 'short' | null = 
          trend === 'up' && ltf[i].c > emaL[i] && rsiL[i] < 70 ? 'long' :
          trend === 'down' && ltf[i].c < emaL[i] && rsiL[i] > 30 ? 'short' : null
        
        if (want) {
          const stopDist = a * params.stopMult
          const entryPx = c.c
          const stop = want === 'long' ? entryPx - stopDist : entryPx + stopDist
          const tp = want === 'long' ? entryPx + stopDist * params.rrTarget : entryPx - stopDist * params.rrTarget
          const dollarRisk = equity * params.riskPct
          const size = dollarRisk / stopDist
          
          pos = {
            side: want,
            entryPx,
            size,
            stop,
            tp,
            entryIdx: i,
          }
        }
      }
      
      peak = Math.max(peak, equity)
      maxDD = Math.max(maxDD, (peak - equity) / peak)
      
      // Time stop
      if (pos && params.maxHoldBars > 0 && i - pos.entryIdx >= params.maxHoldBars) {
        const exitPx = c.c
        const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
        const entryCost = pos.entryPx * pos.size * 0.001
        const exitCost = c.c * pos.size * 0.001
        const pnl = grossPnl - entryCost - exitCost
        equity += pnl
        results.push({ pnl, reason: 'time_stop' })
        pos = null
        
        peak = Math.max(peak, equity)
        maxDD = Math.max(maxDD, (peak - equity) / peak)
      }
    }
    
    // Close any open position
    if (pos) {
      const last = ltf[ltf.length - 1]
      const exitPx = last.c
      const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - pos.entryPx) * pos.size
      const pnl = grossPnl - pos.entryPx * pos.size * 0.001 - exitPx * pos.size * 0.001
      equity += pnl
      results.push({ pnl, reason: 'eod' })
    }
    
    const wins = results.filter(r => r.pnl > 0).length
    const losses = results.filter(r => r.pnl <= 0).length
    const winSum = results.filter(r => r.pnl > 0).reduce((s, r) => s + r.pnl, 0)
    const losSum = results.filter(r => r.pnl <= 0).reduce((s, r) => s + r.pnl, 0)
    const pf = losSum < 0 ? Math.abs(winSum / losSum) : Infinity
    const ret = (equity - 1000) / 1000
    
    // Hash params
    const paramsHash = JSON.stringify(params).split('').reduce((a, b) => ((a << 5) - a) + b.charCodeAt(0), 0).toString(16)
    
    // Determine if we should trade this setup
    const shouldTrade = results.length >= 10 && 
                       wins / results.length > 0.45 && 
                       ret > 0 && 
                       maxDD < 0.25
    
    return {
      predicted_wr: results.length > 0 ? wins / results.length : 0,
      predicted_pf: isFinite(pf) ? pf : Infinity,
      predicted_ret: ret,
      params_hash: paramsHash,
      should_trade: shouldTrade,
      backtest_verdict: shouldTrade ? 'trade this' : 'skip',
    }
  } catch (error: any) {
    console.log(`Backtest prediction failed for ${coin}: ${error.message}`)
    return {
      predicted_wr: 0,
      predicted_pf: 0,
      predicted_ret: 0,
      params_hash: 'error',
      should_trade: false,
      backtest_verdict: `error: ${error.message}`,
    }
  }
}

// ── Trade Attribution ──────────────────────────────────────────────────────

export async function reconcileTrades(journalFile: string): Promise<{
  matches: number
  total: number
  avg_skew: number
  verdict_distribution: Record<string, number>
}> {
  // This will be called from the analyze-journal.mjs script
  // For now, return placeholder
  return {
    matches: 0,
    total: 0,
    avg_skew: 0,
    verdict_distribution: {},
  }
}
