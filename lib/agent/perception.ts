// Perception scan engine — sweeps all Hyperliquid markets for trigger signals.
// Importable from Node.js scripts (no next/* imports).

import type { HLMarket } from '../hl-universe';
import type { Candle, TriggerHit } from './triggers';
import { pctMoveSpike, volumeSpike, breakout, rangeCompression, trendStrength, compositeScore } from './triggers';
import type { TriggerConfig } from './config';
import { DEFAULT_CONFIG } from './config';

// ── Perception result type ──────────────────────────────────────────────────

export type Perception = {
  id: string;        // perception ID for research lookup
  coin: string;      // HL's exact ticker
  type: 'perp' | 'spot';
  firedAt: number;   // Date.now()
  mid: number;
  triggers: TriggerHit[];
  compositeScore: number;
  // Pre-AI technical analysis (optional, filled by ta-filter)
  taSignal?: 'CONFIRMED' | 'WEAK' | 'REJECTED';
  taScore?: number;
  taTrend4h?: string;
  taRsi4h?: number | null;
  taAtr4pct?: number | null;
  taReason?: string;
};

interface CacheEntry { candles: Candle[]; cachedAt: number; }

// ── Inline semaphore (no p-limit dependency) ────────────────────────────────

async function runWithSemaphore<T>(
  items: T[],
  concurrency: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let index = 0;

  async function worker() {
    while (true) {
      const currentIndex = index++;
      if (currentIndex >= items.length) return;
      await fn(items[currentIndex]);
    }
  }

  const workers = Array.from(
    { length: Math.min(concurrency, items.length) },
    () => worker(),
  );
  await Promise.all(workers);
}

// ── Candle fetch with cache ─────────────────────────────────────────────────

function baseUrl(): string {
  if (process.env.NEXT_PUBLIC_BASE_URL) return process.env.NEXT_PUBLIC_BASE_URL;
  return 'http://localhost:3000';
}

function makeCacheKey(coin: string, interval: string, count: number): string {
  return `${coin}:${interval}:${count}`;
}

const candleCache = new Map<string, CacheEntry>();

async function fetchCandles(
  coin: string,
  interval: string,
  count: number,
  cacheTtlMs: number,
): Promise<Candle[] | null> {
  const key = makeCacheKey(coin, interval, count);
  const cached = candleCache.get(key);
  if (cached && Date.now() - cached.cachedAt < cacheTtlMs) {
    return cached.candles;
  }

  const url =
    `${baseUrl()}/api/hl/candles?coin=${encodeURIComponent(coin)}&interval=${encodeURIComponent(interval)}&count=${count}`;

  const res = await fetch(url);
  if (!res.ok) return null;

  const body = await res.json();
  const candlesArr = (body as { candles?: Array<{ t: number; o: string; h: string; l: string; c: string; v: string }> }).candles;
  if (!candlesArr || candlesArr.length === 0) return null;

  const candles: Candle[] = candlesArr.map(c => ({
    t: c.t,
    o: parseFloat(c.o),
    h: parseFloat(c.h),
    l: parseFloat(c.l),
    c: parseFloat(c.c),
    v: parseFloat(c.v ?? '0'),
  }));

  candleCache.set(key, { candles, cachedAt: Date.now() });
  return candles;
}

// ── Scan single market ──────────────────────────────────────────────────────

async function scanMarket(
  market: HLMarket,
  mid: number,
  config: TriggerConfig,
  minScore: number,
): Promise<Perception | null> {
  const candles = await fetchCandles(
    market.coin,
    config.scan.candleInterval,
    config.scan.candleCount,
    config.scan.cacheTtlMs,
  );

  if (!candles || candles.length < 50) return null;

  const hits: TriggerHit[] = [
    pctMoveSpike(candles, config.thresholds.sigmaThreshold),
    volumeSpike(candles, config.thresholds.sigmaThreshold),
    breakout(candles, config.thresholds.breakoutLookback),
    rangeCompression(candles, config.thresholds.bbLength, config.thresholds.bbStdDev),
    trendStrength(candles, config.thresholds.adxPeriod),
  ];

  // Require at least 2 triggers to co-fire — single-trigger (e.g. ADX-only) is not
  // a trade entry signal and would score 100/100 under the normalized formula.
  const firedCount = hits.filter(h => h.fired).length;
  if (firedCount < 2) return null;

  const score = compositeScore(hits, config.weights);

  if (score < minScore) return null;

  return {
    id: `${market.coin}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    coin: market.coin,
    type: market.type,
    firedAt: Date.now(),
    mid,
    triggers: hits,
    compositeScore: score,
  };
}

// ── Main scan entry point ───────────────────────────────────────────────────

export async function scanOnce(opts: {
  universe: HLMarket[];
  minScore: number;
  config?: TriggerConfig;
}): Promise<Perception[]> {
  const started = Date.now();
  const config = opts.config ?? DEFAULT_CONFIG;
  const minScore = opts.minScore;

  // Step 1: Fetch all mids
  let mids: Record<string, number>;
  try {
    const res = await fetch(`${baseUrl()}/api/hl/all-mids`);
    if (!res.ok) {
      process.stderr.write(`[scanOnce] all-mids fetch failed: ${res.status}\n`);
      return [];
    }
    const raw = await res.json() as Record<string, string | number>;
    mids = {};
    for (const [coin, val] of Object.entries(raw)) {
      mids[coin] = typeof val === 'string' ? parseFloat(val) : val;
    }
  } catch (err) {
    process.stderr.write(`[scanOnce] all-mids error: ${err}\n`);
    return [];
  }

  // Step 2: Filter universe to markets with valid mid prices
  // Exclude spot pairs (coin names starting with @) — they trade at 1x leverage
  // and produce noise spikes that don't correlate with directional edge.
  const markets = opts.universe.filter(
    m => (mids[m.coin] ?? 0) > 0 && !m.coin.startsWith('@')
  );

  if (markets.length === 0) return [];

  // Step 3: Parallel fetch with inline semaphore
  const results: (Perception | null)[] = new Array(markets.length).fill(null);

  await runWithSemaphore(
    markets.map((m, i) => ({ market: m, index: i })),
    config.scan.maxConcurrency,
    async item => {
      try {
        results[item.index] = await scanMarket(item.market, mids[item.market.coin] ?? 0, config, minScore);
      } catch (err) {
        process.stderr.write(`[scanOnce] scan error for ${item.market.coin}: ${err}\n`);
        results[item.index] = null;
      }
    },
  );

  // Step 4: Filter nulls, sort by compositeScore descending
  const filtered = results.filter((r): r is Perception => r !== null);

  const elapsed = Date.now() - started;
  const triggerCount = filtered.length;

  process.stderr.write(
    `[scan] scanned ${markets.length} markets, ${triggerCount} triggers fired in ${elapsed}ms\n`,
  );

  return filtered.sort((a, b) => b.compositeScore - a.compositeScore);
}

// ── Cache management (for scripts/tests) ────────────────────────────────────

export function clearCandleCache(): void {
  candleCache.clear();
}

export function getCandleCacheStats(): { size: number } {
  return { size: candleCache.size };
}
