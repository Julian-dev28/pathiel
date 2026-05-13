// System prompt builder — test buildSystemPrompt output
// Inlined from lib/agent/system-prompt.ts (ACTUAL source)
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

function buildSystemPrompt({ mode, winRate, recentTrades }) {
  const modeDesc =
    mode === 'OFF'
      ? 'You are in OFF mode — analyze and output your verdict. No execution will occur.'
      : 'You are in LIVE mode — your verdict will be auto-executed against real funds. Be extremely precise.'

  const trades = recentTrades ?? 0
  const rate = winRate ?? 0
  const trackRecord =
    trades === 0
      ? 'No trade history yet.'
      : `Recent track record: ${trades} trades, win rate ${Math.round(rate * 100)}%.`

  return [
    'You are an autonomous quant trading agent for Hyperliquid perpetual markets.',
    `OPERATING MODE: ${modeDesc}`,
    '',
    'CONTEXT YOU RECEIVE:',
    '- Composite trigger score (0–100) from technical triggers (returns, volume, breakouts, squeezes)',
    '- Multi-tf indicators: 1h/4h/1d EMA8/21, RSI(14), ATR(14), funding rate',
    '- Account state: equity, open positions',
    '',
    'DECISION — output VALID JSON on the LAST line:',
    '{',
    '  "verdict": "PASS" | "LONG" | "SHORT" | "CLOSE",',
    '  "confidence": 0.0–1.0,',
    '  "side": "long" | "short" | null,',
    '  "entryPx": number, "stopPx": number, "tpPx": number,',
    '  "reasoning": "brief summary"',
    '}',
    '',
    'HARD RULES:',
    '1. If risk caps (notional, daily loss) would be exceeded → PASS.',
    '2. If already in position on this coin → prefer HOLD or CLOSE.',
    '3. SL must be ATR-sized (default 3.5× ATR). TP ≥ 1.0× ATR.',
    '4. Never output entryPx without stopPx.',
    '5. VERDICT is PASS unless: (a) score ≥ 80, (b) 4h EMA trend confirms, (c) ATR ≥ 0.5% of price.',
    '6. Confidence: all 3 met = 0.90–1.0, 2 of 3 = 0.70–0.89, < 2 = PASS.',
    '',
    trackRecord,
    '',
    'OUTPUT: 2–3 bullet max, then JSON on last line. Nothing after.',
  ].join('\n')
}

describe('buildSystemPrompt', () => {
  it('OFF mode includes no execution text', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('No execution will occur'));
    assert.ok(prompt.includes('OFF mode'));
  });

  it('LIVE mode includes auto-execution text', () => {
    const prompt = buildSystemPrompt({ mode: 'LIVE', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('auto-executed against real funds'));
    assert.ok(prompt.includes('LIVE mode'));
  });

  it('OFF mode does NOT include live execution text', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(!prompt.includes('auto-executed'));
  });

  it('LIVE mode does NOT include no-execution text', () => {
    const prompt = buildSystemPrompt({ mode: 'LIVE', winRate: 0, recentTrades: 0 });
    assert.ok(!prompt.includes('No execution will occur'));
  });

  it('shows no-trade-history text when recentTrades === 0', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('No trade history yet.'));
  });

  it('shows track record when recentTrades > 0', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0.65, recentTrades: 50 });
    assert.ok(prompt.includes('Recent track record: 50 trades'));
    assert.ok(prompt.includes('win rate 65%'));
  });

  it('does not show win rate text when recentTrades === 0', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0.8, recentTrades: 0 });
    assert.ok(prompt.includes('No trade history yet.'));
    assert.ok(!prompt.includes('win rate'));
  });

  it('includes JSON schema with all verdict options', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('PASS'));
    assert.ok(prompt.includes('LONG'));
    assert.ok(prompt.includes('SHORT'));
    assert.ok(prompt.includes('CLOSE'));
    assert.ok(prompt.includes('confidence'));
    assert.ok(prompt.includes('entryPx'));
    assert.ok(prompt.includes('stopPx'));
    assert.ok(prompt.includes('tpPx'));
    assert.ok(prompt.includes('reasoning'));
    assert.ok(prompt.includes('side'));
  });

  it('includes hard rules section', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('HARD RULES'));
    assert.ok(prompt.includes('risk caps'));
    assert.ok(prompt.includes('PASS'));
  });

  it('includes multi-TF indicator references', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('EMA8/21'));
    assert.ok(prompt.includes('RSI(14)'));
    assert.ok(prompt.includes('ATR(14)'));
    assert.ok(prompt.includes('1h/4h/1d'));
  });

  it('handles default values (mode OFF, no winRate, no trades)', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF' });
    assert.ok(prompt.includes('No execution'));
    assert.ok(prompt.includes('No trade history yet.'));
  });

  it('formats win rate as integer percentage', () => {
    const prompt = buildSystemPrompt({ winRate: 0.654, recentTrades: 10 });
    assert.ok(prompt.includes('65%'));
    assert.ok(!prompt.includes('65.4'));
  });

  it('formats win rate 100%', () => {
    const prompt = buildSystemPrompt({ winRate: 1.0, recentTrades: 10 });
    assert.ok(prompt.includes('100%'));
  });

  it('prompt contains Hyperliquid in header', () => {
    const prompt = buildSystemPrompt({ mode: 'OFF', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('Hyperliquid perpetual markets'));
  });

  it('prompt contains operating mode label', () => {
    const prompt = buildSystemPrompt({ mode: 'LIVE', winRate: 0, recentTrades: 0 });
    assert.ok(prompt.includes('OPERATING MODE'));
  });
});
