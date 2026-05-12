// Persistent agent memory — survives restarts, loads from .agent-memory.json on init.
// Importable from Next.js routes and standalone Node scripts (no next/* imports).
import { promises as fs } from 'fs';
import * as path from 'path';

export type AgentVerdict = 'PASS' | 'LONG' | 'SHORT' | 'CLOSE';

export type AgentAnalysis = {
  id: string;
  perceptionId: string;
  coin: string;
  verdict: AgentVerdict;
  confidence: number;
  side?: 'long' | 'short' | null;
  entryPx?: number;
  stopPx?: number;
  tpPx?: number;
  reasoning: string;
  newsContext?: string;
  createdAt: number;
};

export type AgentTrade = {
  id: string;
  analysisId: string;
  coin: string;
  side: 'long' | 'short';
  entryPx: number;
  sizeUSD: number;
  orderId?: string;
  exitPx?: number;
  pnl?: number;
  executedAt: number;
  exitedAt?: number;
};

export type WatchlistEntry = {
  coin: string;
  type: 'perp' | 'spot';
  mid: number;
  compositeScore: number;
  lastPerceptionAt: number;
  status: 'scanning' | 'analyzing' | 'analyzed' | 'blocked' | 'executed';
  blockReason?: string;
};

type RawPerception = {
  id: string;
  coin: string;
  type: string;
  firedAt: number;
  mid: number;
  triggers: unknown[];
  compositeScore: number;
  taSignal?: string;
  taScore?: number;
};

const MAX_PERCEPTIONS = 500;
const MAX_ANALYSES = 200;
const MAX_TRADES = 100;

const MEMORY_FILE = path.join(process.cwd(), '.agent-memory.json');

const DEFAULT_STATE = {
  perceptions: [] as RawPerception[],
  analyses: [] as AgentAnalysis[],
  trades: [] as AgentTrade[],
  watchlist: [] as Array<{ coin: string; type: string; mid: number; compositeScore: number; lastPerceptionAt: number; status: string; blockReason?: string }>,
  cooldowns: [] as Array<{ coin: string; expires: number }>,
  equity: 0,
  dailyPnl: 0,
  openPositions: [] as Array<{ coin: string; side: string; sizeUSD: number; entryPx: number }>,
};

export class AgentMemory {
  private static instance: AgentMemory;

  private perceptions: RawPerception[] = [];
  private analyses: AgentAnalysis[] = [];
  private trades: AgentTrade[] = [];
  private watchlist: Map<string, WatchlistEntry> = new Map();
  private cooldowns: Map<string, number> = new Map();
  private equity = 0;
  private dailyPnl = 0;
  private openPositions: Array<{ coin: string; side: 'long' | 'short'; sizeUSD: number; entryPx: number }> = [];

  private _saveTimer: ReturnType<typeof setTimeout> | null = null;
  private _initialized = false;

  static getInstance(): AgentMemory {
    if (!AgentMemory.instance) {
      AgentMemory.instance = new AgentMemory();
    }
    return AgentMemory.instance;
  }

  // ── Persistence ───────────────────────────────────────────────────────

  private async load(): Promise<void> {
    if (this._initialized) return;
    try {
      const raw = await fs.readFile(MEMORY_FILE, 'utf8');
      const data = JSON.parse(raw) as typeof DEFAULT_STATE;

      this.perceptions = (data.perceptions ?? []).slice(-MAX_PERCEPTIONS);
      this.analyses = (data.analyses ?? []).slice(-MAX_ANALYSES);
      this.trades = (data.trades ?? []).slice(-MAX_TRADES);

      // Rebuild watchlist map
      this.watchlist.clear();
      for (const w of (data.watchlist ?? [])) {
        this.watchlist.set(w.coin, {
          coin: w.coin,
          type: w.type as 'perp' | 'spot',
          mid: w.mid,
          compositeScore: w.compositeScore,
          lastPerceptionAt: w.lastPerceptionAt,
          status: w.status as WatchlistEntry['status'],
          blockReason: w.blockReason,
        });
      }

      // Rebuild cooldowns map
      this.cooldowns.clear();
      for (const c of (data.cooldowns ?? [])) {
        if (c.expires > Date.now()) {
          this.cooldowns.set(c.coin, c.expires);
        }
      }

      this.equity = data.equity ?? 0;
      this.dailyPnl = data.dailyPnl ?? 0;
      this.openPositions = (data.openPositions ?? []) as typeof this.openPositions;

      process.stderr.write(`[memory] loaded ${this.perceptions.length} perceptions, ${this.analyses.length} analyses, ${this.trades.length} trades from ${MEMORY_FILE}\n`);
    } catch {
      process.stderr.write('[memory] no existing memory file found, starting fresh\n');
    }
    this._initialized = true;
  }

  /** Debounced save to disk — writes 200ms after last mutation. */
  private scheduleSave(): void {
    if (this._saveTimer) clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => this.flush(), 200);
  }

  private async flush(): Promise<void> {
    try {
      const data = {
        perceptions: this.perceptions,
        analyses: this.analyses,
        trades: this.trades,
        watchlist: Array.from(this.watchlist.values()),
        cooldowns: Array.from(this.cooldowns.entries()).map(([coin, expires]) => ({ coin, expires })),
        equity: this.equity,
        dailyPnl: this.dailyPnl,
        openPositions: this.openPositions,
      };
      await fs.writeFile(MEMORY_FILE, JSON.stringify(data, null, 2), 'utf8');
    } catch (err) {
      process.stderr.write(`[memory] save failed: ${err}\n`);
    }
  }

  /** Force immediate save. Call before process exit. */
  async saveNow(): Promise<void> {
    if (this._saveTimer) {
      clearTimeout(this._saveTimer);
      this._saveTimer = null;
    }
    await this.flush();
  }

  // ── Write operations ──────────────────────────────────────────────────

  recordPerception(p: RawPerception): void {
    this.perceptions.push(p);
    if (this.perceptions.length > MAX_PERCEPTIONS) {
      this.perceptions.shift();
    }
    this.scheduleSave();
  }

  recordAnalysis(a: AgentAnalysis): void {
    this.analyses.push(a);
    if (this.analyses.length > MAX_ANALYSES) {
      this.analyses.shift();
    }
    this.scheduleSave();
  }

  recordTrade(t: AgentTrade): void {
    this.trades.push(t);
    if (this.trades.length > MAX_TRADES) {
      this.trades.shift();
    }
    this.scheduleSave();
  }

  updateWatchlist(
    percs: { coin: string; type: string; mid: number; compositeScore: number; firedAt: number }[]
  ): void {
    for (const p of percs) {
      const existing = this.watchlist.get(p.coin);
      if (existing) {
        existing.mid = p.mid;
        existing.compositeScore = p.compositeScore;
        existing.lastPerceptionAt = p.firedAt;
        if (existing.status === 'scanning') {
          existing.status = 'analyzed';
        }
      } else {
        this.watchlist.set(p.coin, {
          coin: p.coin,
          type: p.type as 'perp' | 'spot',
          mid: p.mid,
          compositeScore: p.compositeScore,
          lastPerceptionAt: p.firedAt,
          status: 'analyzed',
        });
      }
    }

    // Sweep entries not seen in this batch to 'scanning'
    const currentCoins = new Set(percs.map(p => p.coin));
    this.watchlist.forEach((entry, coin) => {
      if (!currentCoins.has(coin)) {
        entry.status = 'scanning';
      }
    });

    this.scheduleSave();
  }

  updateEquity(eq: number): void {
    this.equity = eq;
    this.scheduleSave();
  }

  updateDailyPnl(pnl: number): void {
    this.dailyPnl = pnl;
    this.scheduleSave();
  }

  updateOpenPositions(pos: typeof this.openPositions): void {
    this.openPositions = [...pos];
    this.scheduleSave();
  }

  setCooldown(coin: string, minutes: number): void {
    this.cooldowns.set(coin, Date.now() + minutes * 60_000);
    this.scheduleSave();
  }

  setStatus(
    coin: string,
    status: WatchlistEntry['status'],
    blockReason?: string
  ): void {
    const entry = this.watchlist.get(coin);
    if (entry) {
      entry.status = status;
      if (blockReason) entry.blockReason = blockReason;
    }
    this.scheduleSave();
  }

  // ── Read operations ───────────────────────────────────────────────────

  async ensureLoaded(): Promise<void> {
    if (!this._initialized) {
      await this.load();
    }
  }

  getRecentPerceptions(limit = 20): RawPerception[] {
    return this.perceptions.slice(-limit);
  }

  getRecentAnalyses(limit = 20): AgentAnalysis[] {
    return this.analyses.slice(-limit);
  }

  getRecentTrades(limit = 20): AgentTrade[] {
    return this.trades.slice(-limit);
  }

  getAllTrades(): AgentTrade[] {
    return [...this.trades];
  }

  getAllAnalyses(): AgentAnalysis[] {
    return [...this.analyses];
  }

  getWatchlist(): WatchlistEntry[] {
    return Array.from(this.watchlist.values()).sort(
      (a, b) => b.compositeScore - a.compositeScore
    );
  }

  getWatchlistEntry(coin: string): WatchlistEntry | undefined {
    return this.watchlist.get(coin);
  }

  getAnalysisById(id: string): AgentAnalysis | undefined {
    return this.analyses.find(a => a.id === id);
  }

  getWinRate(): { wins: number; total: number; rate: number } {
    const closed = this.trades.filter(t => t.exitPx !== undefined && t.pnl !== undefined);
    const wins = closed.filter(t => (t.pnl ?? 0) > 0).length;
    const total = closed.length;
    return { wins, total, rate: total > 0 ? wins / total : 0 };
  }

  getEquity(): number {
    return this.equity;
  }

  getDailyPnl(): number {
    return this.dailyPnl;
  }

  getOpenPositions(): typeof this.openPositions {
    return [...this.openPositions];
  }

  inCooldown(coin: string): boolean {
    const expires = this.cooldowns.get(coin);
    if (expires === undefined) return false;
    return Date.now() < expires;
  }

  getFullState(): Record<string, unknown> {
    return {
      watchlist: this.getWatchlist(),
      recentPerceptions: this.getRecentPerceptions(),
      recentAnalyses: this.getRecentAnalyses(),
      recentTrades: this.getRecentTrades(),
      winRate: this.getWinRate(),
      equity: this.equity,
      dailyPnl: this.dailyPnl,
      openPositions: this.openPositions,
    };
  }
}

export const memory = AgentMemory.getInstance();
