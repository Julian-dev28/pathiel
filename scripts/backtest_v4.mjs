// V4 momentum + Markov regime strategy.
// Run: node scripts/backtest_v4.mjs [coin=xyz:URNM] [days=90]
//
// Two ideas combined:
// 1. PURE TREND FOLLOWING (no mean reversion): ride sustained moves.
//    - Enter LONG when close > EMA(20) AND EMA(20) slope rising AND momentum positive (close > N bars ago).
//    - Enter SHORT mirror.
//    - Exit when trend flips (close vs EMA, or z-score returns to neutral).
//    - ATR trailing stop catches reversals without needing mean-reversion logic.
//
// 2. MARKOV REGIME GATE: classify each bar's state from rolling return z-score.
//    States: SU (strong up), U, N (neutral), D, SD (strong down). 5 states.
//    Build transition matrix on first 40% of bars (in-sample), then for every
//    new bar in-trade, compute P(continuation | current state). Only enter if
//    P(continuation) > threshold (default 0.55). Skip otherwise.
//
// Costs included: same as v3 (HL taker fee 0.045%, 0.05% slippage, 0.01%/8h funding).

import fs from 'node:fs'
import path from 'node:path'

const HL_API      = 'https://api.hyperliquid.xyz/info'
const STARTING_EQ = 1000
const FEE         = 0.00045
const SLIPPAGE    = 0.0005
const FUND_PER_8H = 0.0001
const tradeCost   = (notional) => notional * (FEE + SLIPPAGE)
const fundingCost = (notional, hours, side) => notional * FUND_PER_8H * (hours / 8) * (side === 'long' ? 1 : -1)

// ── Indicators ─────────────────────────────────────────────────────────────
function ema(values, period) {
  const k = 2 / (period + 1), out = new Array(values.length).fill(NaN)
  if (!values.length) return out
  let e = values[0]; out[0] = e
  for (let i = 1; i < values.length; i++) { e = values[i] * k + e * (1 - k); out[i] = e }
  return out
}
function atr(c, period = 14) {
  const tr = new Array(c.length).fill(0)
  for (let i = 1; i < c.length; i++) {
    const h = c[i].h, l = c[i].l, pc = c[i - 1].c
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc))
  }
  const out = new Array(c.length).fill(NaN)
  if (c.length <= period) return out
  let acc = 0
  for (let i = 1; i <= period; i++) acc += tr[i]
  out[period] = acc / period
  for (let i = period + 1; i < c.length; i++) out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
  return out
}
// rolling mean and std of log returns over `lookback` bars
function rollingZ(c, lookback = 20) {
  const ret = new Array(c.length).fill(0)
  for (let i = 1; i < c.length; i++) ret[i] = Math.log(c[i].c / c[i - 1].c)
  const z = new Array(c.length).fill(0)
  for (let i = lookback; i < c.length; i++) {
    const slice = ret.slice(i - lookback, i)
    const mean  = slice.reduce((s, x) => s + x, 0) / lookback
    const v     = slice.reduce((s, x) => s + (x - mean) ** 2, 0) / lookback
    const sd    = Math.sqrt(v)
    z[i] = sd > 0 ? (ret[i] - mean) / sd : 0
  }
  return z
}

// ── Markov regime ──────────────────────────────────────────────────────────
const STATES = ['SD', 'D', 'N', 'U', 'SU']
function classify(z) {
  if (z >= 1.5)  return 4 // SU
  if (z >= 0.4)  return 3 // U
  if (z >  -0.4) return 2 // N
  if (z >  -1.5) return 1 // D
  return 0                // SD
}

// Build 5×5 transition counts from a state sequence, return probability matrix
function buildMatrix(states) {
  const M = Array.from({ length: 5 }, () => Array(5).fill(0))
  for (let i = 1; i < states.length; i++) M[states[i - 1]][states[i]]++
  for (let r = 0; r < 5; r++) {
    const total = M[r].reduce((s, x) => s + x, 0)
    if (total > 0) for (let c = 0; c < 5; c++) M[r][c] /= total
  }
  return M
}

// P(next state ∈ {U, SU} | current = state)
function pUp(M, state)   { return M[state][3] + M[state][4] }
function pDown(M, state) { return M[state][0] + M[state][1] }

// ── Data fetch ─────────────────────────────────────────────────────────────
async function fetchCandles(coin, interval, startTime, endTime) {
  const out = []
  let t = startTime
  for (let i = 0; i < 80; i++) {
    const res = await fetch(HL_API, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'candleSnapshot', req: { coin, interval, startTime: t, endTime } }),
    })
    if (!res.ok) throw new Error(`HL ${interval} ${res.status}`)
    const raw = await res.json()
    if (!Array.isArray(raw) || raw.length === 0) break
    for (const c of raw) out.push({ t: c.t, o: +c.o, h: +c.h, l: +c.l, c: +c.c, v: +(c.v ?? 0) })
    const lastT = raw[raw.length - 1].t
    if (lastT <= t || lastT >= endTime) break
    t = lastT + 1
  }
  const map = new Map()
  for (const c of out) map.set(c.t, c)
  return [...map.values()].sort((a, b) => a.t - b.t)
}

// ── Strategy ───────────────────────────────────────────────────────────────
function runMomentumMarkov(c, p) {
  const closes = c.map(x => x.c)
  const e20    = ema(closes, p.emaP)
  const a14    = atr(c, 14)
  const zs     = rollingZ(c, p.zLookback)
  const states = zs.map(classify)

  // Build Markov matrix on the first `trainFrac` of the series (in-sample)
  const trainEnd = Math.floor(c.length * p.trainFrac)
  const trainStates = states.slice(p.zLookback, trainEnd)
  const M = buildMatrix(trainStates)

  let equity = STARTING_EQ
  let peak = equity, maxDD = 0
  let pos = null
  const trades = []

  for (let i = trainEnd; i < c.length; i++) {
    const cur = c[i]
    if (!isFinite(e20[i]) || !isFinite(a14[i]) || a14[i] <= 0) continue

    const slopeUp = i >= 3 && e20[i] > e20[i - 3]
    const slopeDn = i >= 3 && e20[i] < e20[i - 3]
    const above   = cur.c > e20[i]
    const below   = cur.c < e20[i]
    const momoN   = i >= p.momoBars && cur.c > c[i - p.momoBars].c
    const momoD   = i >= p.momoBars && cur.c < c[i - p.momoBars].c
    const state   = states[i]
    const pU      = pUp(M, state)
    const pD      = pDown(M, state)

    if (pos) {
      // intra-bar SL check
      const hitSL = pos.side === 'long' ? cur.l <= pos.stop : cur.h >= pos.stop
      if (hitSL) {
        const exitPx   = pos.stop
        const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
        const exitCost = tradeCost(exitPx * pos.size)
        const fund     = fundingCost(pos.entryPx * pos.size, (cur.t - pos.entryT) / 3600_000, pos.side)
        const pnl      = grossPnl - exitCost - pos.entryCost - fund
        equity += pnl
        trades.push({ ...pos, exitT: cur.t, exitPx, pnl, reason: 'trail_stop' })
        pos = null
      } else {
        // Trail the stop
        const trailDist = a14[i] * p.trailMult
        const newStop   = pos.side === 'long' ? cur.h - trailDist : cur.l + trailDist
        if (pos.side === 'long' && newStop > pos.stop) pos.stop = newStop
        if (pos.side === 'short' && newStop < pos.stop) pos.stop = newStop
        // Trend-flip exit: EMA crossover or Markov regime flip
        const trendFlip = pos.side === 'long' ? (below && slopeDn) : (above && slopeUp)
        const regimeFlip = pos.side === 'long' ? (state <= 1 && pD > p.exitProb) : (state >= 3 && pU > p.exitProb)
        if (trendFlip || regimeFlip) {
          const exitPx   = cur.c
          const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
          const exitCost = tradeCost(exitPx * pos.size)
          const fund     = fundingCost(pos.entryPx * pos.size, (cur.t - pos.entryT) / 3600_000, pos.side)
          const pnl      = grossPnl - exitCost - pos.entryCost - fund
          equity += pnl
          trades.push({ ...pos, exitT: cur.t, exitPx, pnl, reason: regimeFlip ? 'regime_flip' : 'trend_flip' })
          pos = null
        }
      }
    }

    if (!pos && equity > 0) {
      let want = null
      // LONG: above EMA, slope up, momentum up, Markov says continuation likely
      if (above && slopeUp && momoN && pU >= p.entryProb) want = 'long'
      else if (below && slopeDn && momoD && pD >= p.entryProb) want = 'short'
      if (want) {
        const stopDist = a14[i] * p.stopMult
        const entryPx  = cur.c
        const stop     = want === 'long' ? entryPx - stopDist : entryPx + stopDist
        const dollarRisk = equity * p.riskPct
        const size = dollarRisk / stopDist
        pos = {
          side: want, entryT: cur.t, entryPx, size, stop,
          entryCost: tradeCost(entryPx * size),
        }
      }
    }

    peak = Math.max(peak, equity)
    maxDD = Math.max(maxDD, (peak - equity) / peak)
  }

  if (pos) {
    const last = c[c.length - 1]
    const grossPnl = (pos.side === 'long' ? last.c - pos.entryPx : pos.entryPx - last.c) * pos.size
    const exitCost = tradeCost(last.c * pos.size)
    const fund     = fundingCost(pos.entryPx * pos.size, (last.t - pos.entryT) / 3600_000, pos.side)
    const pnl      = grossPnl - exitCost - pos.entryCost - fund
    equity += pnl
    trades.push({ ...pos, exitT: last.t, exitPx: last.c, pnl, reason: 'eod' })
  }

  const wins = trades.filter(t => t.pnl > 0)
  const losses = trades.filter(t => t.pnl <= 0)
  const winSum = wins.reduce((s, t) => s + t.pnl, 0)
  const losSum = losses.reduce((s, t) => s + t.pnl, 0)
  const pf = losSum < 0 ? Math.abs(winSum / losSum) : Infinity

  return {
    trades, equity, ret: equity / STARTING_EQ - 1, maxDD,
    winRate: trades.length ? wins.length / trades.length : 0,
    pf, n: trades.length,
    matrix: M, trainEnd,
  }
}

// ── Reporting ──────────────────────────────────────────────────────────────
const fmtPct = x => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`

function printMatrix(M) {
  console.log('  Markov transition matrix (rows=from, cols=to):')
  console.log('         ' + STATES.map(s => s.padStart(7)).join(''))
  for (let r = 0; r < 5; r++) {
    const row = M[r].map(p => p.toFixed(2).padStart(7)).join('')
    console.log(`    ${STATES[r].padEnd(3)} ${row}`)
  }
}

function buyAndHold(c) {
  const ret = c[c.length - 1].c / c[0].c - 1
  return ret
}

const DEFAULT = {
  emaP: 20, momoBars: 5, zLookback: 20,
  stopMult: 2.5, trailMult: 2.0,
  trainFrac: 0.4,
  entryProb: 0.50, exitProb: 0.55,
  riskPct: 0.02,
}

async function main() {
  const coin = process.argv[2] ?? 'xyz:URNM'
  const days = Number(process.argv[3] ?? 90)
  const interval = process.argv[4] ?? '4h'

  console.log(`v4 momentum + Markov · ${coin} · ${days}d · ${interval} bars\n`)
  const endTime = Date.now()
  const startTime = endTime - days * 86400_000
  const candles = await fetchCandles(coin, interval, startTime, endTime)
  if (candles.length < 50) { console.error(`Insufficient data: ${candles.length} candles`); process.exit(1) }
  console.log(`Fetched ${candles.length} ${interval} candles\n`)

  // Sweep (entryProb=0 disables Markov gate entirely; pure momentum)
  const grid = []
  for (const stopMult of [1.5, 2.5, 4.0])
    for (const trailMult of [1.5, 2.5, 4.0])
      for (const momoBars of [3, 5, 10])
        for (const entryProb of [0.0, 0.30, 0.45])
          for (const riskPct of [0.005, 0.01, 0.02])
            grid.push({ ...DEFAULT, stopMult, trailMult, momoBars, entryProb, riskPct })

  console.log(`Sweeping ${grid.length} param combos...\n`)
  const allResults = grid.map(p => ({ p, r: runMomentumMarkov(candles, p) }))
  const tradeCounts = allResults.map(x => x.r.n)
  console.log(`Trade-count histogram: max=${Math.max(...tradeCounts)} min=${Math.min(...tradeCounts)} avg=${(tradeCounts.reduce((s,x)=>s+x,0)/tradeCounts.length).toFixed(1)}`)
  const results = allResults.filter(x => x.r.n >= 1)
  results.sort((a, b) => b.r.ret - a.r.ret)

  console.log(`Buy-and-hold ${coin} over ${days}d (test segment): ${fmtPct(buyAndHold(candles.slice(Math.floor(candles.length * DEFAULT.trainFrac))))}`)
  console.log(`Buy-and-hold ${coin} full ${days}d:                 ${fmtPct(buyAndHold(candles))}\n`)

  console.log('Top 10:')
  for (const { p, r } of results.slice(0, 10)) {
    const annu = r.ret * (365 / (days * 0.6)) // testing on remaining 60% after train
    console.log(`  s=${p.stopMult} tr=${p.trailMult} momo=${p.momoBars} pE=${p.entryProb} r=${(p.riskPct*100).toFixed(1)}%  n=${String(r.n).padStart(2)}  WR=${(r.winRate*100).toFixed(1).padStart(5)}%  PF=${(isFinite(r.pf)?r.pf.toFixed(2):'∞').padStart(5)}  ret=${fmtPct(r.ret).padStart(8)}  DD=${fmtPct(-r.maxDD).padStart(8)}`)
  }

  // Print Markov matrix from best run
  if (results.length > 0) {
    console.log()
    printMatrix(results[0].r.matrix)
  }
}

main().catch(e => { console.error(e); process.exit(1) })
