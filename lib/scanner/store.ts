import type { Trigger } from './engine'

// ── Extended trigger with analysis/trade metadata ────────────────────────────

export interface StoredTrigger extends Trigger {
  analyzed?: 'pass' | 'long' | 'short' | 'close'
  orderId?: string
  gateResults?: Record<string, { pass: boolean; reason?: string }>
  // Individual trigger scores (from lib/scanner/triggers output)
  pctMoveSpike?: number
  volumeSpike?: number
  breakoutScore?: number
  rangeCompression?: number
}

// ── Singleton ring buffer ────────────────────────────────────────────────────────

class TriggerStore {
  private static instance: TriggerStore

  private history: StoredTrigger[] = []
  private maxSize = 500

  private analyzed: Map<string, { verdict: string; ts: number }> = new Map()

  private constructor() {}

  static getInstance(): TriggerStore {
    if (!TriggerStore.instance) {
      TriggerStore.instance = new TriggerStore()
    }
    return TriggerStore.instance
  }

  // ── Write operations ──────────────────────────────────────────────────────

  appendTrigger(t: StoredTrigger): void {
    this.history.push(t)
    if (this.history.length > this.maxSize) {
      this.history = this.history.slice(-this.maxSize)
    }
  }

  markAnalyzed(coin: string, verdict: 'pass' | 'long' | 'short' | 'close'): void {
    this.analyzed.set(coin, { verdict, ts: Date.now() })
  }

  markTraded(coin: string, orderId: string): void {
    for (let i = this.history.length - 1; i >= 0; i--) {
      const t = this.history[i]
      if (t.coin === coin && !t.orderId) {
        t.orderId = orderId
        break
      }
    }
  }

  // ── Read operations ───────────────────────────────────────────────────────

  getActiveTriggers(limit = 20): StoredTrigger[] {
    const cutoff = Date.now() - 2 * 60 * 60 * 1000 // last 2 hours
    return this.history
      .filter(t => t.firedAt >= cutoff)
      .sort((a, b) => b.compositeScore - a.compositeScore)
      .slice(0, limit)
  }

  getHistory(limit = 50): StoredTrigger[] {
    return this.history.slice(-limit).reverse()
  }

  wasRecentlyAnalyzed(coin: string, withinMs = 10 * 60 * 1000): boolean {
    const entry = this.analyzed.get(coin)
    if (!entry) return false
    return Date.now() - entry.ts < withinMs
  }

  getLastTriggerForCoin(coin: string): StoredTrigger | undefined {
    for (let i = this.history.length - 1; i >= 0; i--) {
      if (this.history[i].coin === coin) return this.history[i]
    }
    return undefined
  }
}

export const triggerStore = TriggerStore.getInstance()
