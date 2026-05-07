// Rule-based backtest of the swing strategy (v3 — optimizing for max PnL).
// Run:   node scripts/backtest.mjs [days=90]
// Sweep: node scripts/backtest.mjs sweep [days=180]
//
// V3 changes vs v2:
//   - COMPOUNDING risk sizing (riskPct of CURRENT equity, not fixed dollars).
//   - PARTIAL take-profit: scale out 50% at p1Target × ATR, runner targets rrTarget × ATR.
//   - ATR-trailing stop on the runner (instead of breakeven trail), so big trends pay big.
//   - Multi-bar entry confirmation reduces false setups.
//   - Volume confirmation: current bar volume > 0.8× rolling 20-bar average (skip dead bars).
//   - 4h trend uses both close > EMA21 AND EMA8 slope rising (real momentum, not just snapshot).
//   - Hard stop sized via ATR multiplier (configurable).

import fs from 'node:fs'
import path from 'node:path'

const HL_API      = 'https://api.hyperliquid.xyz/info'
const STARTING_EQ = 1000
let COIN          = 'BTC'

// ── Real-world execution costs (Hyperliquid BTC-PERP) ──────────────────────
// Taker fee: 0.045%. We pay it on entry, partial TP, full TP, and SL exit.
// Slippage: stops & TP triggers fill at market; budget +0.05% adverse on each fill.
// Funding: ~0.01%/8h average on BTC longs; shorts receive on average.
//          Apply as cost on net hours held × side (longs pay, shorts get).
const FEE         = 0.00045
const SLIPPAGE    = 0.0005
const FUND_PER_8H = 0.0001

const tradeCost = (notional) => notional * (FEE + SLIPPAGE)
const fundingCost = (notional, hours, side) =>
  notional * FUND_PER_8H * (hours / 8) * (side === 'long' ? 1 : -1)

// ── Indicators ─────────────────────────────────────────────────────────────
function ema(values, period) {
  const k = 2 / (period + 1)
  const out = new Array(values.length).fill(NaN)
  if (!values.length) return out
  let e = values[0]; out[0] = e
  for (let i = 1; i < values.length; i++) { e = values[i] * k + e * (1 - k); out[i] = e }
  return out
}
function sma(values, period) {
  const out = new Array(values.length).fill(NaN)
  let acc = 0
  for (let i = 0; i < values.length; i++) {
    acc += values[i]
    if (i >= period) acc -= values[i - period]
    if (i >= period - 1) out[i] = acc / period
  }
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
function rsi(c, period = 14) {
  const out = new Array(c.length).fill(NaN)
  if (c.length <= period) return out
  let g = 0, l = 0
  for (let i = 1; i <= period; i++) {
    const d = c[i].c - c[i - 1].c
    if (d >= 0) g += d; else l -= d
  }
  let avgG = g / period, avgL = l / period
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  for (let i = period + 1; i < c.length; i++) {
    const d = c[i].c - c[i - 1].c
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL)
  }
  return out
}

// ── Data fetch ─────────────────────────────────────────────────────────────
async function fetchCandles(interval, startTime, endTime) {
  const out = []
  let t = startTime
  for (let i = 0; i < 80; i++) {
    const res = await fetch(HL_API, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'candleSnapshot', req: { coin: COIN, interval, startTime: t, endTime } }),
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
const cache = {}
async function getCandles(interval, days) {
  const k = `${interval}-${days}`
  if (cache[k]) return cache[k]
  const endTime = Date.now()
  return cache[k] = await fetchCandles(interval, endTime - days * 86400_000, endTime)
}

function latestClosed(c, periodMs, refMs) {
  let lo = 0, hi = c.length - 1, r = -1
  while (lo <= hi) {
    const m = (lo + hi) >> 1
    if (c[m].t + periodMs <= refMs) { r = m; lo = m + 1 } else hi = m - 1
  }
  return r
}

// ── Strategy ───────────────────────────────────────────────────────────────
function trend4h(c4, ef, es, idx) {
  if (idx < 2) return 'range'
  const cl = c4[idx].c
  if (!isFinite(ef[idx]) || !isFinite(es[idx]) || !isFinite(ef[idx - 2])) return 'range'
  const slopeUp = ef[idx] > ef[idx - 2]
  const slopeDn = ef[idx] < ef[idx - 2]
  if (ef[idx] > es[idx] && cl > es[idx] && slopeUp) return 'up'
  if (ef[idx] < es[idx] && cl < es[idx] && slopeDn) return 'down'
  return 'range'
}

function entrySignal(c1, ema1, rsi1, volSma, i, want) {
  if (i < 2) return false
  const cur = c1[i], prev = c1[i - 1]
  const e = ema1[i]
  if (!isFinite(e)) return false
  const volOk = !isFinite(volSma[i]) || cur.v >= volSma[i] * 0.8
  if (!volOk) return false
  if (want === 'long') {
    if (rsi1[i] > 70) return false // not chasing into overbought
    return prev.l <= e && cur.c > e && cur.c > cur.o
  } else {
    if (rsi1[i] < 30) return false
    return prev.h >= e && cur.c < e && cur.c < cur.o
  }
}

// HTF = higher-timeframe (4h or 1d depending on mode). LTF = lower (1h or 4h).
function runOne(ltf, htf, p) {
  const closeH = htf.map(x => x.c)
  const efH = ema(closeH, p.fastP)
  const esH = ema(closeH, p.slowP)
  const emaL = ema(ltf.map(x => x.c), p.ema1P)
  const atrL = atr(ltf, p.atrP)
  const rsiL = rsi(ltf, 14)
  const volSma = sma(ltf.map(x => x.v), 20)
  const ltfMs = (ltf[1]?.t ?? 0) - (ltf[0]?.t ?? 0) || 3600_000
  const htfMs = (htf[1]?.t ?? 0) - (htf[0]?.t ?? 0) || 4 * 3600_000

  let equity = STARTING_EQ
  let peak = equity, maxDD = 0
  let pos = null
  const trades = []
  let lastExitIdx = -10

  for (let i = 1; i < ltf.length; i++) {
    const c = ltf[i]
    const closeMs = c.t + ltfMs
    const ti4 = latestClosed(htf, htfMs, closeMs)
    const trend = trend4h(htf, efH, esH, ti4)
    const a = atrL[i]
    if (!isFinite(a) || a <= 0) { peak = Math.max(peak, equity); continue }

    if (pos) {
      // intra-bar: check stops in pessimistic order (SL first), then partial TP, then full TP
      const hitSL  = pos.side === 'long' ? c.l <= pos.stop  : c.h >= pos.stop
      const hitTP1 = !pos.partialTaken && (pos.side === 'long' ? c.h >= pos.tp1 : c.l <= pos.tp1)
      const hitTP  = pos.side === 'long' ? c.h >= pos.tp : c.l <= pos.tp

      if (hitSL) {
        const exitPx  = pos.stop
        const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
        const exitCost = tradeCost(exitPx * pos.size)
        const fund     = fundingCost(pos.entryPx * pos.totalSize, (c.t - pos.entryT) / 3600_000, pos.side)
        const pnl      = grossPnl + pos.bookedPnl - exitCost - pos.entryCost - pos.partialCost - fund
        equity += pnl
        trades.push({ ...pos, exitT: c.t, exitPx, pnl, reason: pos.partialTaken ? (pos.trailing ? 'trail_stop' : 'be_stop') : 'hard_stop' })
        pos = null; lastExitIdx = i
      } else if (hitTP) {
        const exitPx   = pos.tp
        const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
        const exitCost = tradeCost(exitPx * pos.size)
        const fund     = fundingCost(pos.entryPx * pos.totalSize, (c.t - pos.entryT) / 3600_000, pos.side)
        const pnl      = grossPnl + pos.bookedPnl - exitCost - pos.entryCost - pos.partialCost - fund
        equity += pnl
        trades.push({ ...pos, exitT: c.t, exitPx, pnl, reason: 'take_profit' })
        pos = null; lastExitIdx = i
      } else if (p.maxHoldBars && (i - pos.entryIdx) >= p.maxHoldBars) {
        // Time-based exit: force resolution within maxHoldBars
        const exitPx   = c.c
        const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
        const exitCost = tradeCost(exitPx * pos.size)
        const fund     = fundingCost(pos.entryPx * pos.totalSize, (c.t - pos.entryT) / 3600_000, pos.side)
        const pnl      = grossPnl + pos.bookedPnl - exitCost - pos.entryCost - pos.partialCost - fund
        equity += pnl
        trades.push({ ...pos, exitT: c.t, exitPx, pnl, reason: 'time_stop' })
        pos = null; lastExitIdx = i
      } else {
        if (hitTP1 && p.partial) {
          const exitPx     = pos.tp1
          const halfSize   = pos.size * p.partialFrac
          const partialPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * halfSize
          pos.bookedPnl   += partialPnl
          pos.partialCost += tradeCost(exitPx * halfSize)
          pos.size        -= halfSize
          pos.partialTaken = true
          pos.stop = pos.entryPx
        }
        // ATR trailing on remainder once trailing
        if (pos.partialTaken) {
          const trailDist = a * p.trailMult
          const newStop = pos.side === 'long' ? c.h - trailDist : c.l + trailDist
          if (pos.side === 'long' && newStop > pos.stop) { pos.stop = newStop; pos.trailing = true }
          if (pos.side === 'short' && newStop < pos.stop) { pos.stop = newStop; pos.trailing = true }
        }
        if ((pos.side === 'long' && trend === 'down') || (pos.side === 'short' && trend === 'up')) {
          const exitPx   = c.c
          const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
          const exitCost = tradeCost(exitPx * pos.size)
          const fund     = fundingCost(pos.entryPx * pos.totalSize, (c.t - pos.entryT) / 3600_000, pos.side)
          const pnl      = grossPnl + pos.bookedPnl - exitCost - pos.entryCost - pos.partialCost - fund
          equity += pnl
          trades.push({ ...pos, exitT: c.t, exitPx, pnl, reason: 'trend_flip' })
          pos = null; lastExitIdx = i
        }
      }
    }

    if (!pos && equity > 0 && i - lastExitIdx >= p.cooldown) {
      let want = null
      if (trend === 'up'   && entrySignal(ltf, emaL, rsiL, volSma, i, 'long'))  want = 'long'
      if (!want && trend === 'down' && entrySignal(ltf, emaL, rsiL, volSma, i, 'short')) want = 'short'
      if (want) {
        const stopDist = a * p.stopMult
        const entryPx  = c.c // gross fill; FEE + SLIPPAGE are charged separately via tradeCost
        const stop     = want === 'long' ? entryPx - stopDist : entryPx + stopDist
        const tp       = want === 'long' ? entryPx + stopDist * p.rrTarget : entryPx - stopDist * p.rrTarget
        const tp1      = want === 'long' ? entryPx + stopDist * p.p1Target : entryPx - stopDist * p.p1Target
        const dollarRisk = equity * p.riskPct
        const size = dollarRisk / stopDist
        pos = {
          side: want, entryT: c.t, entryIdx: i, entryPx, size, totalSize: size, stop, tp, tp1,
          partialTaken: false, trailing: false, bookedPnl: 0,
          entryCost: tradeCost(entryPx * size),
          partialCost: 0,
        }
      }
    }

    peak = Math.max(peak, equity)
    maxDD = Math.max(maxDD, (peak - equity) / peak)
  }

  if (pos) {
    const last     = ltf[ltf.length - 1]
    const exitPx   = last.c
    const grossPnl = (pos.side === 'long' ? exitPx - pos.entryPx : pos.entryPx - exitPx) * pos.size
    const exitCost = tradeCost(exitPx * pos.size)
    const fund     = fundingCost(pos.entryPx * pos.totalSize, (last.t - pos.entryT) / 3600_000, pos.side)
    const pnl      = grossPnl + pos.bookedPnl - exitCost - pos.entryCost - pos.partialCost - fund
    equity += pnl
    trades.push({ ...pos, exitT: last.t, exitPx, pnl, reason: 'eod' })
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
  }
}

// ── Reporting ──────────────────────────────────────────────────────────────
const fmtPct = x => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(2)}%`
const fmtUsd = x => `${x >= 0 ? '+' : '-'}$${Math.abs(x).toFixed(2)}`
function report(label, r, days) {
  const annu = r.ret * (365 / days)
  console.log(`${label.padEnd(36)} n=${String(r.n).padStart(3)}  WR=${(r.winRate*100).toFixed(1).padStart(5)}%  PF=${(isFinite(r.pf) ? r.pf.toFixed(2) : '∞').padStart(5)}  ret=${fmtPct(r.ret).padStart(9)}  ann=${fmtPct(annu).padStart(9)}  DD=${fmtPct(-r.maxDD).padStart(8)}  MAR=${(r.maxDD > 0 ? r.ret/r.maxDD : 0).toFixed(2).padStart(5)}`)
}

// ROBUST winner: profitable on 90/180/365 BTC windows after realistic costs.
// Tuned via 5040-combo sweep × 3 windows × 4h-entry/1d-trend, ranked by MIN-return.
// Wide stop (3.5× ATR) + tight TP (1× ATR), no partial → 53-62% WR, PF 1.13-1.52.
const DEFAULT = {
  fastP: 8, slowP: 21,
  ema1P: 20, atrP: 14,
  stopMult: 3.5, rrTarget: 1.0, p1Target: 1.0,
  partial: false, partialFrac: 0.5,
  trailMult: 1.0,
  riskPct: 0.02,
  cooldown: 1,
  maxHoldBars: 0,
}

async function runWindow(days, params, label = 'run', mode = '1h-4h') {
  const [ltf, htf] = mode === '4h-1d'
    ? [await getCandles('4h', days), await getCandles('1d', days)]
    : mode === '4h-4h'
    ? [await getCandles('4h', days), await getCandles('4h', days)]
    : [await getCandles('1h', days), await getCandles('4h', days)]
  const r = runOne(ltf, htf, params)
  report(`${label} ${days}d ${mode}`, r, days)
  return r
}

async function main() {
  const mode = process.argv[2]

  if (mode === 'multi') {
    const days = Number(process.argv[3] ?? 180)
    const coins = (process.argv[4] ?? 'BTC,ETH,SOL,BNB,AVAX,DOGE,LINK,ARB,SUI,kPEPE,XRP,LTC,APT,INJ,OP').split(',')
    console.log(`Comparing ${coins.length} markets on ${days}d window [4h-1d, default params, realistic costs]\n`)
    console.log('coin'.padEnd(10) + 'n     WR     PF     ret      ann       DD       MAR')
    console.log('-'.repeat(80))
    const summary = []
    for (const c of coins) {
      COIN = c
      // clear cache so we re-fetch per coin
      for (const k of Object.keys(cache)) delete cache[k]
      try {
        const ltf = await getCandles('4h', days)
        const htf = await getCandles('1d', days)
        if (ltf.length < 50 || htf.length < 20) { console.log(`${c.padEnd(10)} (insufficient data)`); continue }
        const r = runOne(ltf, htf, DEFAULT)
        const annu = r.ret * (365 / days)
        const line = `${c.padEnd(10)}n=${String(r.n).padStart(3)}  WR=${(r.winRate*100).toFixed(1).padStart(5)}%  PF=${(isFinite(r.pf)?r.pf.toFixed(2):'∞').padStart(5)}  ret=${fmtPct(r.ret).padStart(8)}  ann=${fmtPct(annu).padStart(9)}  DD=${fmtPct(-r.maxDD).padStart(8)}  MAR=${(r.maxDD>0?r.ret/r.maxDD:0).toFixed(2).padStart(5)}`
        console.log(line)
        summary.push({ coin: c, ret: r.ret, pf: r.pf, n: r.n, dd: r.maxDD })
      } catch (e) {
        console.log(`${c.padEnd(10)} ERROR ${e.message}`)
      }
    }
    summary.sort((a, b) => b.ret - a.ret)
    console.log('\nRanked by return:')
    for (const s of summary.slice(0, 10)) {
      console.log(`  ${s.coin.padEnd(10)} ${fmtPct(s.ret).padStart(8)}  PF ${(isFinite(s.pf)?s.pf.toFixed(2):'∞').padStart(5)}  n=${s.n}  DD ${fmtPct(-s.dd)}`)
    }
    return
  }

  if (mode === 'robust') {
    const tfMode = process.argv[3] ?? '4h-1d'
    console.log(`Robustness sweep [${tfMode}] across 90/180/365d. Ranking by MIN return across all three.\n`)
    const ds = [90, 180, 365]
    const sets = await Promise.all(ds.map(async d => tfMode === '4h-1d'
      ? [await getCandles('4h', d), await getCandles('1d', d)]
      : tfMode === '4h-4h'
      ? [await getCandles('4h', d), await getCandles('4h', d)]
      : [await getCandles('1h', d), await getCandles('4h', d)]
    ))
    const grid = []
    for (const stopMult of [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 5.0])
      for (const rrTarget of [1.0, 1.5, 2.0, 3.0, 4.0, 6.0])
        for (const p1Target of [0.5, 1.0, 1.5, 2.0, 3.0])
          for (const partial of [true, false])
            for (const trailMult of [1.0, 1.5, 2.5, 4.0])
              for (const riskPct of [0.005, 0.01, 0.02])
                grid.push({ ...DEFAULT, stopMult, rrTarget, p1Target, partial, trailMult, riskPct, maxHoldBars: 0 })
    console.log(`Testing ${grid.length} combos × 3 windows...\n`)
    const scored = grid.map(p => {
      const rs = sets.map(([l, h]) => runOne(l, h, p))
      const minRet = Math.min(...rs.map(r => r.ret))
      const allPositive = rs.every(r => r.ret > 0)
      const allOk  = rs.every(r => r.maxDD < 0.30 && r.n >= 5)
      const totalRet = rs.reduce((s, r) => s + r.ret, 0)
      return { p, rs, minRet, allPositive, allOk, totalRet }
    }).filter(x => x.allOk && x.allPositive)
    scored.sort((a, b) => b.minRet - a.minRet)
    console.log(`Combos profitable on ALL THREE windows: ${scored.length}\n`)
    console.log('Top 10 by MIN return across all three windows:\n')
    for (const { p, rs } of scored.slice(0, 10)) {
      const tag = `s${p.stopMult} rr${p.rrTarget} p1=${p.p1Target}${p.partial?'P':''} tr${p.trailMult} r${(p.riskPct*100).toFixed(1)}%`
      console.log(`▶ ${tag}`)
      for (let i = 0; i < ds.length; i++) report(`  ${ds[i]}d`, rs[i], ds[i])
      console.log()
    }
    return
  }

  if (mode === 'sweep') {
    const days = Number(process.argv[3] ?? 180)
    const tfMode = process.argv[4] ?? '1h-4h'
    if (process.argv[5]) COIN = process.argv[5]
    const [ltf, htf] = tfMode === '4h-1d'
      ? [await getCandles('4h', days), await getCandles('1d', days)]
      : tfMode === '4h-4h'
      ? [await getCandles('4h', days), await getCandles('4h', days)]
      : [await getCandles('1h', days), await getCandles('4h', days)]
    console.log(`Sweeping ${COIN} over ${days}d  [${tfMode}]  ltf=${ltf.length} htf=${htf.length}\n`)
    const grid = []
    for (const stopMult of [0.5, 0.75, 1.0, 1.5, 2.0])
      for (const rrTarget of [0.4, 0.6, 0.8, 1.0, 1.5])
        for (const p1Target of [0.3, 0.4, 0.5])
          for (const partial of [true, false])
            for (const maxHoldBars of [1, 2, 3])  // 4h, 8h, 12h max hold (4h candles)
              for (const riskPct of [0.005, 0.01, 0.02])
                grid.push({ ...DEFAULT, stopMult, rrTarget, p1Target, partial, maxHoldBars, riskPct, trailMult: 1.0 })

    console.log(`Testing ${grid.length} combinations...\n`)
    const results = grid.map(p => ({ p, r: runOne(ltf, htf, p) })).filter(x => x.r.maxDD < 0.25 && x.r.n >= 10)
    results.sort((a, b) => b.r.ret - a.r.ret)
    console.log('Top 12 by return (filtered: max DD < 25%):')
    for (const { p, r } of results.slice(0, 12)) {
      const tag = `s${p.stopMult} rr${p.rrTarget} p1=${p.p1Target}${p.partial?'P':''} tr${p.trailMult} r${(p.riskPct*100).toFixed(1)}%`
      report(tag, r, days)
    }
    return
  }

  const days = Number(process.argv[2] ?? 180)
  console.log(`Strategy · BTC 4h-entry / 1d-trend · stop=${DEFAULT.stopMult}× / TP=${DEFAULT.rrTarget}× / partial ${DEFAULT.p1Target}× · ${(DEFAULT.riskPct*100).toFixed(1)}% risk`)
  console.log(`Costs included: ${(FEE*100).toFixed(3)}% taker fee + ${(SLIPPAGE*100).toFixed(3)}% slippage per fill + ${(FUND_PER_8H*100).toFixed(3)}% funding/8h\n`)
  for (const d of [90, 180, 365]) await runWindow(d, DEFAULT, 'default', '4h-1d')

  const ltf = await getCandles('4h', days)
  const htf = await getCandles('1d', days)
  const { trades } = runOne(ltf, htf, DEFAULT)
  const headers = ['side','entry_iso','entry_px','exit_iso','exit_px','pnl_usd','hold_h','reason']
  const rows = trades.map(t => [
    t.side,
    new Date(t.entryT).toISOString(),
    t.entryPx.toFixed(2),
    new Date(t.exitT).toISOString(),
    t.exitPx.toFixed(2),
    t.pnl.toFixed(2),
    ((t.exitT - t.entryT) / 3600_000).toFixed(1),
    t.reason,
  ])
  fs.writeFileSync(path.resolve('backtest-trades.csv'), [headers, ...rows].map(r => r.join(',')).join('\n'))
  console.log(`\nCSV written: backtest-trades.csv (${days}d, ${trades.length} trades)`)
}

main().catch(e => { console.error(e); process.exit(1) })
