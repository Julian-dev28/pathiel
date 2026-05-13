// Risk gates — 10+ individual gates + evalAllGates composite
// Inlined from lib/agent/risk-gates.ts (the ACTUAL source)
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

// ── Exact gate implementations from lib/agent/risk-gates.ts ────────────────────

function confidenceGate(ctx, minConfidence) {
  if (ctx.confidence >= minConfidence) return { pass: true };
  return { pass: false, reason: `confidence ${ctx.confidence.toFixed(2)} < ${minConfidence}` };
}

function maxConcurrentPositionsGate(ctx, maxConcurrent) {
  if (ctx.currentPositions.length < maxConcurrent) return { pass: true };
  return { pass: false, reason: `max positions reached (${ctx.currentPositions.length}/${maxConcurrent})` };
}

function perTradeNotionalCapGate(ctx, capUSD) {
  if (ctx.tradeNotionalUSD <= capUSD) return { pass: true };
  return { pass: false, reason: `trade notional $${ctx.tradeNotionalUSD.toFixed(0)} exceeds cap $${capUSD}` };
}

function dailyLossKillSwitch(ctx, maxDailyLoss) {
  if (ctx.dailyPnl > maxDailyLoss) return { pass: true };
  return { pass: false, reason: `daily loss killswitch triggered (PnL $${ctx.dailyPnl.toFixed(0)} <= $${maxDailyLoss})` };
}

function marketLiquidityFloor(ctx, minVolume) {
  if (ctx.marketVolume24hUSD >= minVolume) return { pass: true };
  return { pass: false, reason: `market 24h volume $${(ctx.marketVolume24hUSD / 1e6).toFixed(1)}M below floor $${(minVolume / 1e6).toFixed(1)}M` };
}

function coinAllowlistGate(ctx, allowlist, blocklist) {
  if (blocklist.length > 0 && blocklist.includes(ctx.coin)) {
    return { pass: false, reason: `${ctx.coin} is on the coin blocklist` };
  }
  if (allowlist.length > 0 && !allowlist.includes(ctx.coin)) {
    return { pass: false, reason: `${ctx.coin} not on the allowlist` };
  }
  return { pass: true };
}

function cooldownGate(ctx, lastTradeTime, cooldownMin) {
  if (lastTradeTime === undefined) return { pass: true };
  const elapsed = (Date.now() - lastTradeTime) / 60_000;
  if (elapsed >= cooldownMin) return { pass: true };
  return { pass: false, reason: `cooldown active (${Math.floor(cooldownMin - elapsed)}min remaining)` };
}

function oppositeDirectionGuard(ctx) {
  const existing = ctx.currentPositions.find(p => p.coin === ctx.coin);
  if (!existing) return { pass: true };
  if (existing.side !== ctx.tradeSide) {
    return { pass: false, reason: `opposite position exists (${ctx.coin} ${existing.side}) — no auto-flip` };
  }
  return { pass: true };
}

function correlationCap(ctx, maxCryptoCorrelated) {
  if (ctx.tradeSide !== 'long') return { pass: true };
  const cryptoCoins = new Set(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'AVAX', 'MATIC', 'LINK', 'DOT', 'UNI', 'ATOM', 'NEAR', 'FTM', 'APT', 'ARB', 'OP', 'INJ', 'TIA', 'SUI', 'SEI', 'WIF', 'PEPE', 'BONK', 'FLOKI', 'TRX', 'LTC', 'BCH', 'ETC', 'XLM', 'ALGO', 'AAVE', 'MKR', 'SNX', 'CRV', 'COMP', 'YFI', 'SUSHI', '1INCH']);
  const existingCryptoLongs = ctx.currentPositions.filter(p => cryptoCoins.has(p.coin) && p.side === 'long').length;
  if (existingCryptoLongs < maxCryptoCorrelated) return { pass: true };
  return { pass: false, reason: `crypto long correlation cap reached (${existingCryptoLongs}/${maxCryptoCorrelated})` };
}

function equityRiskCap(ctx, maxTotalNotionalPct) {
  const maxNotional = ctx.equity * maxTotalNotionalPct;
  const projectedNotional = ctx.totalOpenNotional + ctx.tradeNotionalUSD;
  if (projectedNotional <= maxNotional) return { pass: true };
  return { pass: false, reason: `total notional $${projectedNotional.toFixed(0)} would exceed ${maxTotalNotionalPct * 100}% of equity ($${maxNotional.toFixed(0)})` };
}

function newsBlackoutGate(ctx) {
  if (!ctx.hasBinaryNewsRisk) return { pass: true };
  return { pass: false, reason: 'binary news risk detected (Fed, earnings, hack within 2h) — standing down' };
}

function evalAllGates(ctx, config, lastTradeTime) {
  const results = {};
  results.confidence = confidenceGate(ctx, config.minAiConfidence ?? 0.8);
  results.maxConcurrent = maxConcurrentPositionsGate(ctx, config.maxConcurrent ?? 3);
  results.notionalCap = perTradeNotionalCapGate(ctx, config.maxTradeNotionalUsd ?? 200);
  results.dailyLoss = dailyLossKillSwitch(ctx, config.maxDailyLossUsd ?? -100);
  results.liquidity = marketLiquidityFloor(ctx, config.minMarketVolumeUsd ?? 5_000_000);
  results.coinFilter = coinAllowlistGate(ctx, config.coinAllowlist ?? [], config.coinBlocklist ?? []);
  results.cooldown = cooldownGate(ctx, lastTradeTime, config.cooldownMin ?? 60);
  results.oppositeGuard = oppositeDirectionGuard(ctx);
  results.correlation = correlationCap(ctx, 2);
  results.equityRisk = equityRiskCap(ctx, config.maxTotalNotionalPct ?? 0.3);
  results.news = newsBlackoutGate(ctx);

  const blockReasons = [];
  let blocked = false;
  for (const [key, result] of Object.entries(results)) {
    if (!result.pass) {
      blocked = true;
      blockReasons.push(result.reason ?? key);
    }
  }
  return { results, blocked, blockReasons };
}

// ── Test defaults ──────────────────────────────────────────────────────────────

const defaultConfig = {
  mode: 'LIVE',
  minAiConfidence: 0.5,
  maxConcurrent: 3,
  maxTradeNotionalUsd: 200,
  maxDailyLossUsd: -50,   // negative — gate checks dailyPnl > maxDailyLoss
  minMarketVolumeUsd: 1e7,
  coinAllowlist: [],
  coinBlocklist: [],
  cooldownMin: 5,
  maxTotalNotionalPct: 0.8,
};

const baseCtx = {
  confidence: 0.7,
  currentPositions: [],
  tradeNotionalUSD: 50,
  dailyPnl: -5,
  marketVolume24hUSD: 1e8,
  coin: 'BTC',
  tradeSide: 'long',
  hasBinaryNewsRisk: false,
  equity: 1000,
  totalOpenNotional: 100,
};

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('confidenceGate', () => {
  it('passes when confidence >= threshold', () => {
    assert.equal(confidenceGate({ ...baseCtx, confidence: 0.5 }, 0.5).pass, true);
    assert.equal(confidenceGate({ ...baseCtx, confidence: 0.8 }, 0.5).pass, true);
  });
  it('fails when confidence < threshold', () => {
    const result = confidenceGate({ ...baseCtx, confidence: 0.3 }, 0.5);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('confidence'));
  });
  it('fails when confidence === 0', () => {
    assert.equal(confidenceGate({ ...baseCtx, confidence: 0 }, 0.5).pass, false);
  });
  it('passes with confidence === 1.0 (max)', () => {
    assert.equal(confidenceGate({ ...baseCtx, confidence: 1.0 }, 0.5).pass, true);
  });
});

describe('maxConcurrentPositionsGate', () => {
  it('passes when under concurrent cap', () => {
    assert.equal(maxConcurrentPositionsGate(baseCtx, 3).pass, true);
    const positions = [{ coin: 'A', side: 'long', sizeUSD: 10 }];
    assert.equal(maxConcurrentPositionsGate({ ...baseCtx, currentPositions: positions }, 3).pass, true);
  });
  it('passes when equal to cap - 1', () => {
    const positions = Array.from({ length: 2 }, (_, i) => ({ coin: `pos${i}`, side: 'long', sizeUSD: 10 }));
    assert.equal(maxConcurrentPositionsGate({ ...baseCtx, currentPositions: positions }, 3).pass, true);
  });
  it('fails when at concurrent cap', () => {
    const positions = Array.from({ length: 3 }, (_, i) => ({ coin: `pos${i}`, side: 'long', sizeUSD: 10 }));
    const result = maxConcurrentPositionsGate({ ...baseCtx, currentPositions: positions }, 3);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('max positions'));
  });
  it('fails with large number of positions', () => {
    const positions = Array.from({ length: 10 }, (_, i) => ({ coin: `pos${i}`, side: i % 2 === 0 ? 'long' : 'short', sizeUSD: 10 }));
    assert.equal(maxConcurrentPositionsGate({ ...baseCtx, currentPositions: positions }, 3).pass, false);
  });
});

describe('perTradeNotionalCapGate', () => {
  it('passes when trade notional is under cap', () => {
    assert.equal(perTradeNotionalCapGate({ ...baseCtx, tradeNotionalUSD: 50 }, 200).pass, true);
  });
  it('passes when trade notional equals cap', () => {
    assert.equal(perTradeNotionalCapGate({ ...baseCtx, tradeNotionalUSD: 200 }, 200).pass, true);
  });
  it('fails when trade notional exceeds cap', () => {
    const result = perTradeNotionalCapGate({ ...baseCtx, tradeNotionalUSD: 250 }, 200);
    assert.equal(result.pass, false);
  });
  it('fails for very large notional', () => {
    const result = perTradeNotionalCapGate({ ...baseCtx, tradeNotionalUSD: 10000 }, 200);
    assert.equal(result.pass, false);
  });
});

describe('dailyLossKillSwitch', () => {
  it('passes when daily PnL is above threshold', () => {
    // Gate checks: dailyPnl > maxDailyLoss
    assert.equal(dailyLossKillSwitch({ ...baseCtx, dailyPnl: -5 }, -50).pass, true);
    assert.equal(dailyLossKillSwitch({ ...baseCtx, dailyPnl: 100 }, -50).pass, true);
  });
  it('passes when PnL is zero', () => {
    assert.equal(dailyLossKillSwitch({ ...baseCtx, dailyPnl: 0 }, -50).pass, true);
  });
  it('fails when daily PnL is exactly at threshold', () => {
    // -50 > -50 is false → fails
    const result = dailyLossKillSwitch({ ...baseCtx, dailyPnl: -50 }, -50);
    assert.equal(result.pass, false);
  });
  it('fails when daily PnL exceeds threshold (more negative)', () => {
    const result = dailyLossKillSwitch({ ...baseCtx, dailyPnl: -100 }, -50);
    assert.equal(result.pass, false);
  });
  it('handles large loss gracefully', () => {
    const result = dailyLossKillSwitch({ ...baseCtx, dailyPnl: -1000 }, -50);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('daily loss'));
  });
});

describe('marketLiquidityFloor', () => {
  it('passes when volume exceeds min threshold', () => {
    assert.equal(marketLiquidityFloor({ ...baseCtx, marketVolume24hUSD: 1e8 }, 1e7).pass, true);
  });
  it('passes when volume equals min threshold', () => {
    assert.equal(marketLiquidityFloor({ ...baseCtx, marketVolume24hUSD: 1e7 }, 1e7).pass, true);
  });
  it('fails when volume is below min threshold', () => {
    const result = marketLiquidityFloor({ ...baseCtx, marketVolume24hUSD: 1e6 }, 1e7);
    assert.equal(result.pass, false);
  });
  it('fails for illiquid market', () => {
    const result = marketLiquidityFloor({ ...baseCtx, marketVolume24hUSD: 100 }, 1e7);
    assert.equal(result.pass, false);
  });
});

describe('coinAllowlistGate', () => {
  it('passes with empty allowlist and blocklist', () => {
    assert.equal(coinAllowlistGate({ ...baseCtx, coin: 'ANYCOIN' }, [], []).pass, true);
  });
  it('blocks blocklisted coin', () => {
    const result = coinAllowlistGate({ ...baseCtx, coin: 'BADCOIN' }, [], ['BADCOIN']);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('blocklist'));
  });
  it('allows non-blocklisted coin', () => {
    assert.equal(coinAllowlistGate({ ...baseCtx, coin: 'GOODCOIN' }, [], ['BADCOIN']).pass, true);
  });
  it('blocks coin not in allowlist', () => {
    const result = coinAllowlistGate({ ...baseCtx, coin: 'SOL' }, ['BTC', 'ETH'], []);
    assert.equal(result.pass, false);
  });
  it('allows coin in allowlist', () => {
    assert.equal(coinAllowlistGate({ ...baseCtx, coin: 'BTC' }, ['BTC', 'ETH'], []).pass, true);
  });
  it('blocks when coin is both in allowlist and blocklist (blocklist wins)', () => {
    const result = coinAllowlistGate({ ...baseCtx, coin: 'BTC' }, ['BTC'], ['BTC']);
    assert.equal(result.pass, false);
  });
});

describe('cooldownGate', () => {
  it('passes when no previous trade', () => {
    assert.equal(cooldownGate(baseCtx, undefined, 5).pass, true);
  });
  it('passes when cooldown has expired', () => {
    const oldTime = Date.now() - 10 * 60_000;
    assert.equal(cooldownGate({ ...baseCtx, lastTradeTime: undefined }, oldTime, 5).pass, true);
  });
  it('passes when cooldown is exactly at threshold', () => {
    const exactTime = Date.now() - 5 * 60_000;
    // Need to pass lastTradeTime as the 2nd arg
    assert.equal(cooldownGate({ ...baseCtx, currentPositions: [] }, exactTime, 5).pass, true);
  });
  it('blocks when cooldown is active', () => {
    const recentTime = Date.now() - 2 * 60_000;
    const result = cooldownGate({ ...baseCtx, currentPositions: [] }, recentTime, 5);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('cooldown'));
  });
});

describe('oppositeDirectionGuard', () => {
  it('passes when no open positions', () => {
    assert.equal(oppositeDirectionGuard(baseCtx).pass, true);
  });
  it('fails when opposite-side position exists for SAME coin', () => {
    const positions = [{ coin: 'BTC', side: 'short', sizeUSD: 50 }];
    const result = oppositeDirectionGuard({ ...baseCtx, currentPositions: positions, tradeSide: 'long' });
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('opposite position'));
  });
  it('passes when other side position exists for DIFFERENT coin', () => {
    const positions = [{ coin: 'ETH', side: 'short', sizeUSD: 50 }];
    assert.equal(oppositeDirectionGuard({ ...baseCtx, currentPositions: positions, tradeSide: 'long' }).pass, true);
  });
  it('passes when same-side position exists for DIFFERENT coin', () => {
    const positions = [{ coin: 'ETH', side: 'long', sizeUSD: 50 }];
    assert.equal(oppositeDirectionGuard({ ...baseCtx, currentPositions: positions, tradeSide: 'long' }).pass, true);
  });
});

describe('correlationCap', () => {
  it('passes when no crypto longs', () => {
    assert.equal(correlationCap(baseCtx, 2).pass, true);
  });
  it('passes with 1 crypto long (cap=2)', () => {
    const positions = [{ coin: 'BTC', side: 'long', sizeUSD: 100 }];
    assert.equal(correlationCap({ ...baseCtx, currentPositions: positions }, 2).pass, true);
  });
  it('blocks with 2 crypto longs (cap=2) because gate uses strict <', () => {
    const positions = [
      { coin: 'BTC', side: 'long', sizeUSD: 100 },
      { coin: 'ETH', side: 'long', sizeUSD: 100 },
    ];
    const result = correlationCap({ ...baseCtx, currentPositions: positions }, 2);
    // Gate: count < maxCryptoCorrelated, so 2 < 2 = false → BLOCK
    assert.equal(result.pass, false);
  });
  it('passes with 1 crypto long (cap=2)', () => {
    const positions = [{ coin: 'BTC', side: 'long', sizeUSD: 100 }];
    assert.equal(correlationCap({ ...baseCtx, currentPositions: positions }, 2).pass, true);
  });
  it('blocks with 3 crypto longs (cap=2)', () => {
    const positions = [
      { coin: 'BTC', side: 'long', sizeUSD: 100 },
      { coin: 'ETH', side: 'long', sizeUSD: 100 },
      { coin: 'SOL', side: 'long', sizeUSD: 100 },
    ];
    const result = correlationCap({ ...baseCtx, currentPositions: positions }, 2);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('correlation cap'));
  });
  it('always passes for short trades', () => {
    const positions = [
      { coin: 'BTC', side: 'long', sizeUSD: 100 },
      { coin: 'ETH', side: 'long', sizeUSD: 100 },
      { coin: 'SOL', side: 'long', sizeUSD: 100 },
    ];
    assert.equal(correlationCap({ ...baseCtx, currentPositions: positions, tradeSide: 'short' }, 2).pass, true);
  });
  it('ignores short positions when counting longs', () => {
    const positions = [
      { coin: 'BTC', side: 'short', sizeUSD: 100 },
      { coin: 'ETH', side: 'short', sizeUSD: 100 },
      { coin: 'SOL', side: 'short', sizeUSD: 100 },
    ];
    assert.equal(correlationCap({ ...baseCtx, currentPositions: positions }, 2).pass, true);
  });
});

describe('equityRiskCap', () => {
  it('passes when projected notional is under cap', () => {
    assert.equal(equityRiskCap(baseCtx, 0.8).pass, true); // 200 <= 800
  });
  it('passes when zero notional', () => {
    assert.equal(equityRiskCap({ ...baseCtx, totalOpenNotional: 0, tradeNotionalUSD: 0 }, 0.8).pass, true);
  });
  it('fails when projected notional exceeds cap', () => {
    const result = equityRiskCap({ ...baseCtx, totalOpenNotional: 900, tradeNotionalUSD: 100, equity: 1000 }, 0.8);
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('total notional'));
  });
  it('passes with exactly at cap', () => {
    // equity 1000 * 0.8 = 800, totalOpenNotional 750 + tradeNotional 50 = 800
    assert.equal(equityRiskCap({ ...baseCtx, totalOpenNotional: 750 }, 0.8).pass, true);
  });
});

describe('newsBlackoutGate', () => {
  it('passes when no binary news risk', () => {
    assert.equal(newsBlackoutGate(baseCtx).pass, true);
  });
  it('blocks when binary news risk is set', () => {
    const result = newsBlackoutGate({ ...baseCtx, hasBinaryNewsRisk: true });
    assert.equal(result.pass, false);
    assert.ok(result.reason.includes('binary news'));
  });
});

describe('evalAllGates', () => {
  it('all gates pass with healthy ctx', () => {
    const result = evalAllGates(baseCtx, defaultConfig, undefined);
    assert.equal(result.blocked, false);
    assert.equal(result.blockReasons.length, 0);
    assert.ok(result.results);
    const gateKeys = Object.keys(result.results);
    assert.ok(gateKeys.length >= 9, `Expected >= 9 gates, got ${gateKeys.length}`);
  });
  it('multiple gates block simultaneously', () => {
    const badCtx = {
      ...baseCtx,
      confidence: 0.1,
      currentPositions: Array.from({ length: 3 }, (_, i) => ({ coin: `p${i}`, side: 'long', sizeUSD: 10 })),
      tradeNotionalUSD: 500,
      dailyPnl: -100,
      marketVolume24hUSD: 100,
      hasBinaryNewsRisk: true,
    };
    const result = evalAllGates(badCtx, defaultConfig, undefined);
    assert.equal(result.blocked, true);
    assert.ok(result.blockReasons.length > 1);
  });
  it('results structure has all gate names', () => {
    const result = evalAllGates(baseCtx, defaultConfig, undefined);
    const expectedGates = ['confidence', 'maxConcurrent', 'notionalCap', 'dailyLoss', 'liquidity', 'coinFilter', 'cooldown', 'oppositeGuard', 'correlation', 'equityRisk', 'news'];
    for (const name of expectedGates) {
      assert.ok(result.results[name], `Missing gate: ${name}`);
      assert.ok(result.results[name].hasOwnProperty('pass'), `Gate ${name} missing 'pass'`);
    }
  });
  it('blocks single gate correctly', () => {
    const result = evalAllGates({ ...baseCtx, confidence: 0.1 }, defaultConfig, undefined);
    assert.equal(result.blocked, true);
    assert.ok(result.blockReasons.some(r => r.includes('confidence')));
  });
  it('passes with cooldown for new trade', () => {
    const result = evalAllGates(baseCtx, defaultConfig, undefined);
    assert.equal(result.results.cooldown.pass, true);
  });
  it('passes allowlist when both empty', () => {
    const result = evalAllGates(baseCtx, defaultConfig, undefined);
    assert.equal(result.results.coinFilter.pass, true);
  });
  it('passes news blackout when no risk', () => {
    const result = evalAllGates(baseCtx, defaultConfig, undefined);
    assert.equal(result.results.news.pass, true);
  });
});
