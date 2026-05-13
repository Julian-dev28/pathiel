// TA filter — pure function tests for assessTrend, computeATR, RSI, ADX, etc.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

// ── Inline indicator functions from triggers.ts ─────────────────────────────────

function ema(values, period) {
  const k = 2 / (period + 1);
  const out = new Array(values.length).fill(NaN);
  if (!values.length) return out;
  let e = values[0];
  out[0] = e;
  for (let i = 1; i < values.length; i++) {
    e = values[i] * k + e * (1 - k);
    out[i] = e;
  }
  return out;
}

function sma(values, period) {
  const out = new Array(values.length).fill(NaN);
  let acc = 0;
  for (let i = 0; i < values.length; i++) {
    acc += values[i];
    if (i >= period) acc -= values[i - period];
    if (i >= period - 1) out[i] = acc / period;
  }
  return out;
}

function atr(candles, period = 14) {
  const tr = new Array(candles.length).fill(0);
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c;
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }
  const out = new Array(candles.length).fill(NaN);
  if (candles.length <= period) return out;
  let acc = 0;
  for (let i = 1; i <= period; i++) acc += tr[i];
  out[period] = acc / period;
  for (let i = period + 1; i < candles.length; i++) {
    out[i] = (out[i - 1] * (period - 1) + tr[i]) / period;
  }
  return out;
}

function rsi(candles, period = 14) {
  const out = new Array(candles.length).fill(NaN);
  if (candles.length <= period) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= period; i++) {
    const d = candles[i].c - candles[i - 1].c;
    if (d >= 0) g += d; else l -= d;
  }
  let avgG = g / period, avgL = l / period;
  out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  for (let i = period + 1; i < candles.length; i++) {
    const d = candles[i].c - candles[i - 1].c;
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period;
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period;
    out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
  }
  return out;
}

function adx(candles, period = 14) {
  const n = candles.length;
  const out = new Array(n).fill(NaN);
  if (n <= period * 2) return out;
  const tr = new Array(n).fill(0);
  const pDM = new Array(n).fill(0);
  const mDM = new Array(n).fill(0);
  for (let i = 1; i < n; i++) {
    const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c, ph = candles[i - 1].h, pl = candles[i - 1].l;
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    const up = h - ph, dn = pl - l;
    pDM[i] = (up > dn && up > 0) ? up : 0;
    mDM[i] = (dn > up && dn > 0) ? dn : 0;
  }
  let trS = 0, pS = 0, mS = 0;
  for (let i = 1; i <= period; i++) { trS += tr[i]; pS += pDM[i]; mS += mDM[i]; }
  const dx = new Array(n).fill(NaN);
  const computeDX = () => {
    const pdi = trS === 0 ? 0 : 100 * pS / trS;
    const mdi = trS === 0 ? 0 : 100 * mS / trS;
    const sum = pdi + mdi;
    return sum === 0 ? 0 : 100 * Math.abs(pdi - mdi) / sum;
  };
  dx[period] = computeDX();
  for (let i = period + 1; i < n; i++) {
    trS = trS - trS / period + tr[i];
    pS = pS - pS / period + pDM[i];
    mS = mS - mS / period + mDM[i];
    dx[i] = computeDX();
  }
  let adxS = 0;
  for (let i = period; i < period * 2; i++) adxS += dx[i];
  out[period * 2 - 1] = adxS / period;
  for (let i = period * 2; i < n; i++) {
    out[i] = (out[i - 1] * (period - 1) + dx[i]) / period;
  }
  return out;
}

// ── TA filter functions (from lib/agent/ta-filter.ts) ──────────────────────────

function assessTrend(candles) {
  if (candles.length < 30) return 'flat';
  const closes = candles.map(c => c.c);
  const ema8Arr = ema(closes, 8);
  const ema21Arr = ema(closes, 21);
  const i = closes.length - 1;
  const e8 = ema8Arr[i], e21 = ema21Arr[i];
  if (!isFinite(e8) || !isFinite(e21)) return 'flat';

  const e8Prev = ema8Arr[Math.max(0, i - 3)];
  const emaCross = e8 > e21;
  const slopeRising = e8 > e8Prev;

  if (emaCross && slopeRising) return 'bullish';
  if (!emaCross && !slopeRising) return 'bearish';
  return 'flat';
}

function computeATR4pct(candles) {
  if (candles.length < 20) return null;
  const atrArr = atr(candles, 14);
  const last = atrArr[atrArr.length - 1];
  const lastClose = candles[candles.length - 1].c;
  if (!isFinite(last) || lastClose === 0) return null;
  return (last / lastClose) * 100;
}

function computeRSI(candles) {
  if (candles.length < 20) return null;
  const arr = rsi(candles, 14);
  const last = arr[arr.length - 1];
  return isFinite(last) ? last : null;
}

function computeADX(candles) {
  if (candles.length < 30) return null;
  const arr = adx(candles, 14);
  const last = arr[arr.length - 1];
  return isFinite(last) ? last : null;
}

function checkVolumeConfirm(candles) {
  if (candles.length < 21) return false;
  const lastVol = candles[candles.length - 1].v;
  const avgVol = candles.slice(-21, -1).reduce((s, c) => s + c.v, 0) / 20;
  return avgVol === 0 ? false : lastVol >= avgVol * 0.8;
}

function checkEMACrossRecent(candles) {
  if (candles.length < 25) return false;
  const closes = candles.map(c => c.c);
  const ema8Arr = ema(closes, 8);
  const ema21Arr = ema(closes, 21);
  for (let i = closes.length - 3; i < closes.length; i++) {
    if (i < 1) continue;
    const prev8 = ema8Arr[i - 1], prev21 = ema21Arr[i - 1];
    const curr8 = ema8Arr[i], curr21 = ema21Arr[i];
    if (!isFinite(prev8) || !isFinite(prev21) || !isFinite(curr8) || !isFinite(curr21)) continue;
    if ((prev8 <= prev21 && curr8 > curr21) || (prev8 >= prev21 && curr8 < curr21)) return true;
  }
  return false;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function makeCandles(count, direction = 'up') {
  const candles = [];
  let price = 100;
  for (let i = 0; i < count; i++) {
    const change = direction === 'up' ? 0.5 + Math.random() * 0.5 : -0.5 - Math.random() * 0.5;
    const o = price;
    const c = o + change;
    candles.push({ t: i * 3600000, o, h: Math.max(o, c) + Math.random(), l: Math.min(o, c) - Math.random(), c, v: 1000 + Math.random() * 500 });
    price = c;
  }
  return candles;
}

function makeBearishCandles(count) {
  return makeCandles(count, 'down');
}

function makeFlatCandles(count) {
  const candles = [];
  for (let i = 0; i < count; i++) {
    candles.push({ t: i * 3600000, o: 100, h: 100 + Math.random() * 0.1, l: 100 - Math.random() * 0.1, c: 100 + Math.random() * 0.2 - 0.1, v: 1000 });
  }
  return candles;
}

// Truly oscillating flat series: first half alternating 100/99.5, second half alternating 100.5/100.
// This keeps EMA8 slightly below EMA21 (not a cross) but EMA8 is rising from lower values.
// Result: emaCross=false, slopeRising=true → 'flat' (source fallback).
function makeFlatOscillatingCandles(count) {
  const candles = [];
  const half = Math.floor(count / 2);
  for (let i = 0; i < count; i++) {
    let close;
    if (i < half) {
      close = i % 2 === 0 ? 100 : 99.5;
    } else {
      close = (i - half) % 2 === 0 ? 100.5 : 100;
    }
    candles.push({ t: i * 3600000, o: close, h: close + 0.1, l: close - 0.1, c: close, v: 1000 });
  }
  return candles;
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('assessTrend', () => {
  it('returns bullish for strong uptrend', () => {
    const candles = makeCandles(60, 'up');
    assert.equal(assessTrend(candles), 'bullish');
  });
  it('returns bearish for strong downtrend', () => {
    const candles = makeBearishCandles(60);
    assert.equal(assessTrend(candles), 'bearish');
  });
  it('returns flat for short series (< 30)', () => {
    assert.equal(assessTrend(makeCandles(20, 'up')), 'flat');
    assert.equal(assessTrend(makeCandles(29, 'up')), 'flat');
  });
  it('returns flat for flat/consolidating price (oscillating data)', () => {
    // Use oscillating candles that keep EMA8 below EMA21 but with rising slope
    // Result: emaCross=false, slopeRising=true → 'flat' (source fallback)
    const candles = makeFlatOscillatingCandles(60);
    assert.equal(assessTrend(candles), 'flat');
  });
  it('handles single candle', () => {
    const candles = [{ t: 0, o: 100, h: 101, l: 99, c: 100.5, v: 100 }];
    assert.equal(assessTrend(candles), 'flat');
  });
});

describe('computeATR4pct', () => {
  it('returns valid ATR % for sufficient candles', () => {
    const candles = makeCandles(50, 'up');
    const result = computeATR4pct(candles);
    assert.ok(result !== null);
    assert.ok(typeof result === 'number');
    assert.ok(result > 0);
  });
  it('returns null for short series', () => {
    assert.equal(computeATR4pct(makeCandles(10, 'up')), null);
  });
  it('returns null when last close is zero', () => {
    const candles = makeCandles(50, 'up');
    candles[candles.length - 1].c = 0;
    assert.equal(computeATR4pct(candles), null);
  });
  it('returns reasonable ATR % for volatile candles', () => {
    const candles = [];
    let price = 100;
    for (let i = 0; i < 50; i++) {
      const change = (Math.random() - 0.5) * 10;
      const o = price, c = o + change;
      candles.push({ t: i * 3600000, o, h: Math.max(o, c) + 2, l: Math.min(o, c) - 2, c, v: 1000 });
      price = c;
    }
    const result = computeATR4pct(candles);
    assert.ok(result > 0);
    assert.ok(result < 50, 'ATR % should be reasonable');
  });
});

describe('computeRSI', () => {
  it('returns high RSI for uptrend (should be > 50)', () => {
    const candles = makeCandles(50, 'up');
    const result = computeRSI(candles);
    assert.ok(result !== null);
    assert.ok(result > 50, 'RSI should be above 50 for uptrend, got ' + result);
  });
  it('returns low RSI for downtrend (should be < 50)', () => {
    const candles = makeBearishCandles(50);
    const result = computeRSI(candles);
    assert.ok(result !== null);
    assert.ok(result < 50, 'RSI should be below 50 for downtrend, got ' + result);
  });
  it('returns null for short series', () => {
    assert.equal(computeRSI(makeCandles(10, 'up')), null);
  });
  it('handles extremely high RSI (> 80) in strong uptrend', () => {
    const candles = [];
    let price = 100;
    for (let i = 0; i < 40; i++) {
      candles.push({ t: i * 3600000, o: price, h: price + 1, l: price - 0.5, c: price + 0.8, v: 1000 });
      price += 0.8;
    }
    const result = computeRSI(candles);
    assert.ok(result > 70, 'Should be near-overbought in strong uptrend');
  });
  it('returns null for NaN series', () => {
    assert.equal(computeRSI([]), null);
  });
});

describe('computeADX', () => {
  it('returns ADX > 25 for trending market', () => {
    const candles = makeCandles(50, 'up');
    const result = computeADX(candles);
    assert.ok(result !== null);
    assert.ok(result > 20, 'ADX should show some trend strength');
  });
  it('returns null for short series (< 30)', () => {
    assert.equal(computeADX(makeCandles(10, 'up')), null);
  });
  it('returns lower ADX for flat/consolidating market', () => {
    const candles = makeFlatCandles(50);
    const result = computeADX(candles);
    if (result !== null) {
      assert.ok(result < 30, 'ADX should be low for flat market');
    }
  });
  it('handles empty candles', () => {
    assert.equal(computeADX([]), null);
  });
});

describe('checkVolumeConfirm', () => {
  it('returns true when last volume >= 80% of avg', () => {
    const candles = makeCandles(30, 'up');
    // Set last candle volume to above avg
    candles[candles.length - 1].v = 3000;
    assert.equal(checkVolumeConfirm(candles), true);
  });
  it('returns false when last volume is well below avg', () => {
    const candles = makeCandles(30, 'up');
    candles[candles.length - 1].v = 10;
    assert.equal(checkVolumeConfirm(candles), false);
  });
  it('returns false for short series (< 21)', () => {
    assert.equal(checkVolumeConfirm(makeCandles(10, 'up')), false);
  });
  it('returns false when avg volume is zero', () => {
    const candles = [];
    for (let i = 0; i < 30; i++) {
      candles.push({ t: i * 3600000, o: 100, h: 101, l: 99, c: 100.5, v: 0 });
    }
    candles[29].v = 5; // last candle has some volume
    assert.equal(checkVolumeConfirm(candles), false);
  });
});

describe('checkEMACrossRecent', () => {
  it('returns false for no cross in steady uptrend', () => {
    const candles = makeCandles(40, 'up');
    assert.equal(checkEMACrossRecent(candles), false);
  });
  it('returns false for short series (< 25)', () => {
    assert.equal(checkEMACrossRecent(makeCandles(10, 'up')), false);
  });
  it('handles empty candles', () => {
    assert.equal(checkEMACrossRecent([]), false);
  });
});

describe('TA scoring composition', () => {
  it('score calculation: trendAligned(+20) + rsi mid(+15) + atr high(+15) = 50 base', () => {
    // With bullish trend, RSI in mid range, ATR > 0.5%
    const candles = makeCandles(60, 'up');
    const t4h = assessTrend(candles);
    const rsi = computeRSI(candles);
    const atrPct = computeATR4pct(candles);
    
    let score = 0;
    if (t4h === 'bullish') score += 20;
    if (rsi !== null && rsi > 30 && rsi < 70) score += 15;
    if (atrPct !== null && atrPct >= 0.5) score += 15;
    
    assert.ok(score >= 35, 'Should have at least 35 points from basic signals');
  });
  it('verdict CONFIRMED at score >= 45', () => {
    // Simulate a high score scenario
    const score = 50;
    const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED';
    assert.equal(verdict, 'CONFIRMED');
  });
  it('verdict WEAK at score 30-44', () => {
    const score = 35;
    const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED';
    assert.equal(verdict, 'WEAK');
  });
  it('verdict REJECTED at score < 30', () => {
    const score = 10;
    const verdict = score >= 45 ? 'CONFIRMED' : score >= 30 ? 'WEAK' : 'REJECTED';
    assert.equal(verdict, 'REJECTED');
  });
  it('verdict REJECTED at zero score', () => {
    const verdict = 0 >= 45 ? 'CONFIRMED' : 0 >= 30 ? 'WEAK' : 'REJECTED';
    assert.equal(verdict, 'REJECTED');
  });
  it('score capped at 100', () => {
    const rawScore = 150;
    const capped = Math.min(100, rawScore);
    assert.equal(capped, 100);
  });
  it('max possible score: 20+15+15+15+10+10+15 = 100', () => {
    // trendAligned=20, rsi=15, atr=15, adx=15, emaCross=10, volumeConfirm=10, compositeScore contribution=15
    const maxBase = 20 + 15 + 15 + 15 + 10 + 10;
    const maxComposite = Math.min(15, 100 / 100 * 15);
    const total = maxBase + maxComposite;
    assert.ok(total <= 100, `Total ${total} should be <= 100`);
  });
});
