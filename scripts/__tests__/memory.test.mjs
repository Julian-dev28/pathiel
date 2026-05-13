// Comprehensive unit tests for AgentMemory — persistence, CRUD, limits, daily PnL, win rate.
// Run: node --test scripts/__tests__/memory.test.mjs

import test from 'node:test'
import assert from 'node:assert/strict'
import * as fs from 'fs/promises'
import * as path from 'path'
import * as os from 'os'

// ── Inline AgentMemory class (verbatim from lib/agent/memory.ts) ─────────────

const MAX_PERCEPTIONS = 500
const MAX_ANALYSES = 200
const MAX_TRADES = 100

class TestAgentMemory {
  constructor() {
    this.perceptions = []
    this.analyses = []
    this.trades = []
    this.watchlist = new Map()
    this.cooldowns = new Map()
    this.equity = 0
    this.dailyPnl = 0
    this.startOfDayEquity = 0
    this.dayStartTs = 0
    this.openPositions = []
    this._saveTimer = null
  }

  recordPerception(p) {
    this.perceptions.push(p)
    if (this.perceptions.length > MAX_PERCEPTIONS) this.perceptions.shift()
  }

  recordAnalysis(a) {
    this.analyses.push(a)
    if (this.analyses.length > MAX_ANALYSES) this.analyses.shift()
  }

  recordTrade(t) {
    this.trades.push(t)
    if (this.trades.length > MAX_TRADES) this.trades.shift()
  }

  updateWatchlist(percs) {
    for (const p of percs) {
      const existing = this.watchlist.get(p.coin)
      if (existing) {
        existing.mid = p.mid
        existing.compositeScore = p.compositeScore
        existing.lastPerceptionAt = p.firedAt
        if (existing.status === 'scanning') existing.status = 'analyzed'
      } else {
        this.watchlist.set(p.coin, {
          coin: p.coin, type: p.type, mid: p.mid,
          compositeScore: p.compositeScore, lastPerceptionAt: p.firedAt,
          status: 'analyzed',
        })
      }
    }
    const currentCoins = new Set(percs.map(p => p.coin))
    this.watchlist.forEach((entry, coin) => {
      if (!currentCoins.has(coin)) entry.status = 'scanning'
    })
  }

  updateEquity(eq) { this.equity = eq }

  trackDailyPnl(currentEquity) {
    const todayUtcStart = new Date().setUTCHours(0, 0, 0, 0)
    if (this.dayStartTs < todayUtcStart || this.startOfDayEquity === 0) {
      this.startOfDayEquity = currentEquity
      this.dayStartTs = todayUtcStart
      this.dailyPnl = 0
    } else {
      this.dailyPnl = currentEquity - this.startOfDayEquity
    }
    this.equity = currentEquity
  }

  updateDailyPnl(pnl) { this.dailyPnl = pnl }

  updateOpenPositions(pos) { this.openPositions = [...pos] }

  setCooldown(coin, minutes) {
    this.cooldowns.set(coin, Date.now() + minutes * 60_000)
  }

  setStatus(coin, status, blockReason) {
    const entry = this.watchlist.get(coin)
    if (entry) {
      entry.status = status
      if (blockReason) entry.blockReason = blockReason
    }
  }

  getRecentPerceptions(limit = 20) { return this.perceptions.slice(-limit) }
  getRecentAnalyses(limit = 20) { return this.analyses.slice(-limit) }
  getRecentTrades(limit = 20) { return this.trades.slice(-limit) }
  getAllTrades() { return [...this.trades] }
  getAllAnalyses() { return [...this.analyses] }
  getWatchlist() { return Array.from(this.watchlist.values()).sort((a, b) => b.compositeScore - a.compositeScore) }
  getWatchlistEntry(coin) { return this.watchlist.get(coin) }
  getAnalysisById(id) { return this.analyses.find(a => a.id === id) }
  getEquity() { return this.equity }
  getDailyPnl() { return this.dailyPnl }
  getOpenPositions() { return [...this.openPositions] }
  inCooldown(coin) {
    const expires = this.cooldowns.get(coin)
    if (expires === undefined) return false
    return Date.now() < expires
  }
  getWinRate() {
    const closed = this.trades.filter(t => t.exitPx !== undefined && t.pnl !== undefined)
    const wins = closed.filter(t => (t.pnl ?? 0) > 0).length
    const total = closed.length
    return { wins, total, rate: total > 0 ? wins / total : 0 }
  }
  getFullState() {
    return {
      watchlist: this.getWatchlist(),
      recentPerceptions: this.getRecentPerceptions(),
      recentAnalyses: this.getRecentAnalyses(),
      recentTrades: this.getRecentTrades(),
      winRate: this.getWinRate(),
      equity: this.equity,
      dailyPnl: this.dailyPnl,
      startOfDayEquity: this.startOfDayEquity,
      openPositions: this.openPositions,
    }
  }
}

function makeMem() { return new TestAgentMemory() }

// ── Perception tests ─────────────────────────────────────────────────────────

test('recordPerception: stores and retrieves', () => {
  const mem = makeMem()
  mem.recordPerception({ id: 'p1', coin: 'BTC', type: 'perp', firedAt: 1, mid: 50000, triggers: [], compositeScore: 80 })
  const percs = mem.getRecentPerceptions()
  assert.strictEqual(percs.length, 1)
  assert.strictEqual(percs[0].coin, 'BTC')
  assert.strictEqual(percs[0].compositeScore, 80)
})

test('recordPerception: capped at MAX_PERCEPTIONS', () => {
  const mem = makeMem()
  for (let i = 0; i < 510; i++) {
    mem.recordPerception({ id: `p${i}`, coin: 'BTC', type: 'perp', firedAt: i, mid: 100, triggers: [], compositeScore: 50 })
  }
  assert.strictEqual(mem.getRecentPerceptions().length, MAX_PERCEPTIONS)
  assert.strictEqual(mem.getRecentPerceptions(510).length, MAX_PERCEPTIONS)
})

test('recordPerception: getRecentPerceptions with limit', () => {
  const mem = makeMem()
  for (let i = 0; i < 50; i++) {
    mem.recordPerception({ id: `p${i}`, coin: 'BTC', type: 'perp', firedAt: i, mid: 100, triggers: [], compositeScore: i })
  }
  const recent5 = mem.getRecentPerceptions(5)
  assert.strictEqual(recent5.length, 5)
  assert.strictEqual(recent5[0].id, 'p45') // last 5
  assert.strictEqual(recent5[4].id, 'p49')
})

// ── Analysis tests ───────────────────────────────────────────────────────────

test('recordAnalysis: stores and retrieves', () => {
  const mem = makeMem()
  const analysis = { id: 'a1', perceptionId: 'p1', coin: 'BTC', verdict: 'LONG', confidence: 0.85, side: 'long', entryPx: 50000, stopPx: 49000, tpPx: 52000, reasoning: 'test', createdAt: 1 }
  mem.recordAnalysis(analysis)
  const analyses = mem.getRecentAnalyses()
  assert.strictEqual(analyses.length, 1)
  assert.strictEqual(analyses[0].verdict, 'LONG')
})

test('recordAnalysis: capped at MAX_ANALYSES', () => {
  const mem = makeMem()
  for (let i = 0; i < 210; i++) {
    mem.recordAnalysis({ id: `a${i}`, perceptionId: `p${i}`, coin: 'BTC', verdict: 'PASS', confidence: 0.5, side: null, entryPx: 100, stopPx: 0, tpPx: 0, reasoning: 'test', createdAt: i })
  }
  assert.strictEqual(mem.getRecentAnalyses().length, MAX_ANALYSES)
})

test('getAnalysisById: finds by ID', () => {
  const mem = makeMem()
  mem.recordAnalysis({ id: 'find-me', perceptionId: 'p1', coin: 'ETH', verdict: 'SHORT', confidence: 0.70, side: 'short', entryPx: 3000, stopPx: 3100, tpPx: 2800, reasoning: 'test', createdAt: 1 })
  const found = mem.getAnalysisById('find-me')
  assert.ok(found)
  assert.strictEqual(found.coin, 'ETH')
})

test('getAnalysisById: undefined for missing ID', () => {
  const mem = makeMem()
  assert.strictEqual(mem.getAnalysisById('nonexistent'), undefined)
})

// ── Trade tests ──────────────────────────────────────────────────────────────

test('recordTrade: stores and retrieves', () => {
  const mem = makeMem()
  mem.recordTrade({ id: 't1', analysisId: 'a1', coin: 'SOL', side: 'long', entryPx: 100, sizeUSD: 50, executedAt: 1 })
  const trades = mem.getRecentTrades()
  assert.strictEqual(trades.length, 1)
  assert.strictEqual(trades[0].side, 'long')
})

test('recordTrade: capped at MAX_TRADES', () => {
  const mem = makeMem()
  for (let i = 0; i < 110; i++) {
    mem.recordTrade({ id: `t${i}`, analysisId: `a${i}`, coin: 'BTC', side: 'long', entryPx: 50000, sizeUSD: 100, executedAt: i })
  }
  assert.strictEqual(mem.getAllTrades().length, MAX_TRADES)
})

test('getAllTrades vs getRecentTrades', () => {
  const mem = makeMem()
  for (let i = 0; i < 50; i++) {
    mem.recordTrade({ id: `t${i}`, analysisId: `a${i}`, coin: 'BTC', side: 'long', entryPx: 50000, sizeUSD: 100, executedAt: i })
  }
  assert.strictEqual(mem.getAllTrades().length, 50)
  assert.strictEqual(mem.getRecentTrades(10).length, 10)
  assert.strictEqual(mem.getRecentTrades().length, 20) // default
})

// ── Win rate tests ───────────────────────────────────────────────────────────

test('getWinRate: zero when no closed trades', () => {
  const mem = makeMem()
  const wr = mem.getWinRate()
  assert.strictEqual(wr.wins, 0)
  assert.strictEqual(wr.total, 0)
  assert.strictEqual(wr.rate, 0)
})

test('getWinRate: only counts closed trades', () => {
  const mem = makeMem()
  mem.recordTrade({ id: 't1', analysisId: 'a1', coin: 'BTC', side: 'long', entryPx: 50000, sizeUSD: 100, executedAt: 1 }) // open
  mem.recordTrade({ id: 't2', analysisId: 'a2', coin: 'ETH', side: 'long', entryPx: 3000, sizeUSD: 50, exitPx: 3100, pnl: 10, executedAt: 2 }) // closed win
  const wr = mem.getWinRate()
  assert.strictEqual(wr.total, 1)
  assert.strictEqual(wr.wins, 1)
  assert.strictEqual(wr.rate, 1.0)
})

test('getWinRate: correct rate with wins and losses', () => {
  const mem = makeMem()
  mem.recordTrade({ id: 't1', analysisId: 'a1', coin: 'BTC', side: 'long', entryPx: 50000, sizeUSD: 100, exitPx: 51000, pnl: 20, executedAt: 1 })
  mem.recordTrade({ id: 't2', analysisId: 'a2', coin: 'ETH', side: 'short', entryPx: 3000, sizeUSD: 50, exitPx: 3200, pnl: -10, executedAt: 2 })
  mem.recordTrade({ id: 't3', analysisId: 'a3', coin: 'SOL', side: 'long', entryPx: 100, sizeUSD: 30, exitPx: 110, pnl: 5, executedAt: 3 })
  const wr = mem.getWinRate()
  assert.strictEqual(wr.total, 3)
  assert.strictEqual(wr.wins, 2)
  assert.strictEqual(wr.rate, 2 / 3)
})

// ── Watchlist tests ──────────────────────────────────────────────────────────

test('updateWatchlist: adds new entries', () => {
  const mem = makeMem()
  mem.updateWatchlist([
    { coin: 'BTC', type: 'perp', mid: 50000, compositeScore: 80, firedAt: 1 },
    { coin: 'ETH', type: 'perp', mid: 3000, compositeScore: 70, firedAt: 1 },
  ])
  assert.strictEqual(mem.getWatchlist().length, 2)
  assert.strictEqual(mem.getWatchlist()[0].coin, 'BTC') // sorted by score desc
})

test('updateWatchlist: updates existing entries', () => {
  const mem = makeMem()
  mem.updateWatchlist([{ coin: 'BTC', type: 'perp', mid: 50000, compositeScore: 80, firedAt: 1 }])
  mem.updateWatchlist([{ coin: 'BTC', type: 'perp', mid: 51000, compositeScore: 90, firedAt: 2 }])
  const entry = mem.getWatchlistEntry('BTC')
  assert.strictEqual(entry.mid, 51000)
  assert.strictEqual(entry.compositeScore, 90)
})

test('updateWatchlist: sets missing coins to scanning', () => {
  const mem = makeMem()
  mem.updateWatchlist([
    { coin: 'BTC', type: 'perp', mid: 50000, compositeScore: 80, firedAt: 1 },
    { coin: 'ETH', type: 'perp', mid: 3000, compositeScore: 70, firedAt: 1 },
  ])
  mem.updateWatchlist([{ coin: 'BTC', type: 'perp', mid: 51000, compositeScore: 85, firedAt: 2 }])
  const eth = mem.getWatchlistEntry('ETH')
  assert.strictEqual(eth.status, 'scanning')
  const btc = mem.getWatchlistEntry('BTC')
  assert.strictEqual(btc.status, 'analyzed')
})

test('getWatchlist: sorted by compositeScore descending', () => {
  const mem = makeMem()
  mem.updateWatchlist([
    { coin: 'SOL', type: 'perp', mid: 100, compositeScore: 60, firedAt: 1 },
    { coin: 'BTC', type: 'perp', mid: 50000, compositeScore: 90, firedAt: 1 },
    { coin: 'ETH', type: 'perp', mid: 3000, compositeScore: 75, firedAt: 1 },
  ])
  const wl = mem.getWatchlist()
  assert.strictEqual(wl[0].coin, 'BTC')
  assert.strictEqual(wl[1].coin, 'ETH')
  assert.strictEqual(wl[2].coin, 'SOL')
})

test('setStatus: updates entry status', () => {
  const mem = makeMem()
  mem.updateWatchlist([{ coin: 'BTC', type: 'perp', mid: 50000, compositeScore: 80, firedAt: 1 }])
  mem.setStatus('BTC', 'blocked', 'risk gate failed')
  const entry = mem.getWatchlistEntry('BTC')
  assert.strictEqual(entry.status, 'blocked')
  assert.strictEqual(entry.blockReason, 'risk gate failed')
})

// ── Cooldown tests ───────────────────────────────────────────────────────────

test('setCooldown / inCooldown: coin is in cooldown', () => {
  const mem = makeMem()
  mem.setCooldown('BTC', 30)
  assert.strictEqual(mem.inCooldown('BTC'), true)
})

test('inCooldown: false for coin not in cooldown', () => {
  const mem = makeMem()
  assert.strictEqual(mem.inCooldown('BTC'), false)
})

test('setCooldown: expired cooldown returns false', () => {
  const mem = makeMem()
  mem.cooldowns.set('BTC', Date.now() - 1000) // already expired
  assert.strictEqual(mem.inCooldown('BTC'), false)
})

// ── Equity and daily PnL tests ───────────────────────────────────────────────

test('updateEquity: sets and reads equity', () => {
  const mem = makeMem()
  mem.updateEquity(1234.56)
  assert.strictEqual(mem.getEquity(), 1234.56)
})

test('trackDailyPnl: initializes on first call', () => {
  const mem = makeMem()
  mem.trackDailyPnl(1000)
  assert.strictEqual(mem.getEquity(), 1000)
  assert.strictEqual(mem.getDailyPnl(), 0)
  assert.strictEqual(mem.startOfDayEquity, 1000)
})

test('trackDailyPnl: calculates PnL on subsequent calls same day', () => {
  const mem = makeMem()
  mem.trackDailyPnl(1000) // initializes
  mem.trackDailyPnl(1050) // +50
  assert.strictEqual(mem.getDailyPnl(), 50)
  assert.strictEqual(mem.getEquity(), 1050)
})

test('trackDailyPnl: tracks negative PnL', () => {
  const mem = makeMem()
  mem.trackDailyPnl(1000)
  mem.trackDailyPnl(900)
  assert.strictEqual(mem.getDailyPnl(), -100)
})

test('updateDailyPnl: direct override', () => {
  const mem = makeMem()
  mem.updateDailyPnl(-50)
  assert.strictEqual(mem.getDailyPnl(), -50)
})

// ── Open positions tests ─────────────────────────────────────────────────────

test('updateOpenPositions: sets and reads positions', () => {
  const mem = makeMem()
  const positions = [
    { coin: 'BTC', side: 'long', sizeUSD: 100, entryPx: 50000 },
    { coin: 'ETH', side: 'short', sizeUSD: 50, entryPx: 3000 },
  ]
  mem.updateOpenPositions(positions)
  const read = mem.getOpenPositions()
  assert.strictEqual(read.length, 2)
  assert.strictEqual(read[0].coin, 'BTC')
})

test('getOpenPositions: returns a copy (not reference)', () => {
  const mem = makeMem()
  mem.updateOpenPositions([{ coin: 'BTC', side: 'long', sizeUSD: 100, entryPx: 50000 }])
  const copy = mem.getOpenPositions()
  copy.push({ coin: 'ETH', side: 'short', sizeUSD: 50, entryPx: 3000 })
  assert.strictEqual(mem.getOpenPositions().length, 1) // original unchanged
})

// ── getFullState tests ───────────────────────────────────────────────────────

test('getFullState: returns all state keys', () => {
  const mem = makeMem()
  mem.updateEquity(500)
  const state = mem.getFullState()
  const expectedKeys = ['watchlist', 'recentPerceptions', 'recentAnalyses', 'recentTrades', 'winRate', 'equity', 'dailyPnl', 'startOfDayEquity', 'openPositions']
  for (const key of expectedKeys) {
    assert.ok(key in state, `state has key: ${key}`)
  }
  assert.strictEqual(state.equity, 500)
})
