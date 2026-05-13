// Parse verdict — test the JSON extraction logic from research.ts
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

// ── Inline parseVerdict (from lib/agent/research.ts) ───────────────────────────

function parseVerdict(aiText, coin, perception) {
  if (!aiText) {
    return { verdict: 'PASS', confidence: 0, side: null, entryPx: perception.mid, stopPx: 0, tpPx: 0, reasoning: '' };
  }
  let verdict = 'PASS';
  let confidence = 0;
  let side = null;
  let entryPx = perception.mid;
  let stopPx = 0;
  let tpPx = 0;
  let reasoning = aiText.trim();

  const lines = aiText.trim().split('\n');
  let jsonStr = '';
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (line.startsWith('{') && line.includes('verdict') && line.endsWith('}')) {
      jsonStr = line;
      break;
    }
  }

  if (!jsonStr) {
    const match = aiText.match(/\{[^{}]*"verdict"[^{}]*\}/);
    if (match) jsonStr = match[0];
  }

  if (jsonStr) {
    try {
      const cleaned = jsonStr.replace(/```json?\s*/g, '').replace(/```\s*/g, '').trim();
      const parsed = JSON.parse(cleaned);

      const raw = String(parsed.verdict ?? '').toUpperCase();
      if (raw === 'LONG') verdict = 'LONG';
      else if (raw === 'SHORT') verdict = 'SHORT';
      else if (raw === 'CLOSE') verdict = 'CLOSE';

      confidence = typeof parsed.confidence === 'number' ? parsed.confidence : 0;
      side = parsed.side === 'long' ? 'long' : parsed.side === 'short' ? 'short' : null;
      entryPx = typeof parsed.entryPx === 'number' ? parsed.entryPx : perception.mid;
      stopPx = typeof parsed.stopPx === 'number' ? parsed.stopPx : 0;
      tpPx = typeof parsed.tpPx === 'number' ? parsed.tpPx : 0;
      reasoning = typeof parsed.reasoning === 'string' ? parsed.reasoning : aiText.slice(0, 500);
    } catch {
      const firstLine = aiText.trim().split('\n')[0] ?? '';
      if (/LONG/i.test(firstLine)) verdict = 'LONG';
      else if (/SHORT/i.test(firstLine)) verdict = 'SHORT';
      else if (/CLOSE/i.test(firstLine)) verdict = 'CLOSE';
    }
  } else {
    // No JSON found — fallback to keyword detection in first line
    const firstLine = aiText.trim().split('\n')[0] ?? '';
    if (/LONG/i.test(firstLine)) verdict = 'LONG';
    else if (/SHORT/i.test(firstLine)) verdict = 'SHORT';
    else if (/CLOSE/i.test(firstLine)) verdict = 'CLOSE';
  }

  return { verdict, confidence, side, entryPx, stopPx, tpPx, reasoning };
}

// ── Test fixtures ──────────────────────────────────────────────────────────────

function makePerception(mid = 50000) {
  return { id: 'test-1', coin: 'BTC', mid, type: 'perp', compositeScore: 80 };
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('parseVerdict', () => {
  describe('LONG verdict', () => {
    it('parses valid LONG JSON', () => {
      const text = '{"verdict":"LONG","confidence":0.8,"side":"long","entryPx":50100,"stopPx":49500,"tpPx":51500,"reasoning":"Bullish momentum"}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
      assert.equal(result.confidence, 0.8);
      assert.equal(result.side, 'long');
      assert.equal(result.entryPx, 50100);
      assert.equal(result.stopPx, 49500);
      assert.equal(result.tpPx, 51500);
    });

    it('parses lowercase LONG', () => {
      const result = parseVerdict('{"verdict":"long","confidence":0.5}', 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
    });

    it('parses LONG with mixed case', () => {
      const result = parseVerdict('{"verdict":"Long","confidence":0.5}', 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
    });
  });

  describe('SHORT verdict', () => {
    it('parses valid SHORT JSON', () => {
      const text = '{"verdict":"SHORT","confidence":0.7,"side":"short","entryPx":49900,"stopPx":50500,"tpPx":48500}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'SHORT');
      assert.equal(result.side, 'short');
    });

    it('parses uppercase SHORT', () => {
      const result = parseVerdict('{"verdict":"SHORT","confidence":0.6}', 'BTC', makePerception());
      assert.equal(result.verdict, 'SHORT');
    });
  });

  describe('PASS verdict', () => {
    it('parses valid PASS JSON', () => {
      const text = '{"verdict":"PASS","confidence":0.9,"reasoning":"No clear edge"}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
      assert.equal(result.confidence, 0.9);
    });

    it('default verdict is PASS', () => {
      const result = parseVerdict('just some text', 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
    });
  });

  describe('CLOSE verdict', () => {
    it('parses valid CLOSE JSON', () => {
      const text = '{"verdict":"CLOSE","confidence":0.85,"reasoning":"Take profits"}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'CLOSE');
    });
  });

  describe('code block wrapper', () => {
    it('strips ```json ... ``` wrapper', () => {
      const text = '```\n{"verdict":"LONG","confidence":0.7}\n```';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
      assert.equal(result.confidence, 0.7);
    });

    it('strips ``` without lang tag', () => {
      const text = '```\n{"verdict":"SHORT","confidence":0.6}\n```';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'SHORT');
    });

    it('handles json code block at end of text', () => {
      const text = 'Some reasoning\n\n```\n{"verdict":"PASS","confidence":0.5}\n```';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
    });
  });

  describe('JSON in reasoning text (regex extraction)', () => {
    it('extracts JSON embedded in multi-line text', () => {
      const text = 'Looking at the charts, RSI is overbought.\n\nFinal decision:\n{"verdict":"SHORT","confidence":0.65,"reasoning":"Overbought RSI"}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'SHORT');
      assert.equal(result.confidence, 0.65);
      assert.equal(result.reasoning, 'Overbought RSI');
    });
  });

  describe('partial JSON / missing fields', () => {
    it('handles verdict only', () => {
      const result = parseVerdict('{"verdict":"LONG"}', 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
      assert.equal(result.confidence, 0);
      assert.equal(result.side, null);
      assert.equal(result.entryPx, 50000); // falls back to perception.mid
    });

    it('handles empty JSON', () => {
      const result = parseVerdict('{}', 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
      assert.equal(result.confidence, 0);
    });

    it('handles missing confidence field', () => {
      const result = parseVerdict('{"verdict":"LONG","side":"long"}', 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
      assert.equal(result.confidence, 0);
    });

    it('handles zero confidence', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":0}', 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
      assert.equal(result.confidence, 0);
    });

    it('handles confidence as 1.0 max', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":1.0}', 'BTC', makePerception());
      assert.equal(result.confidence, 1.0);
    });
  });

  describe('fallback for invalid JSON', () => {
    it('falls back to first line for LONG keyword', () => {
      const result = parseVerdict('I think we should LONG today', 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
    });

    it('falls back to first line for SHORT keyword', () => {
      const result = parseVerdict('Market looking weak, probably SHORT', 'BTC', makePerception());
      assert.equal(result.verdict, 'SHORT');
    });

    it('falls back to first line for CLOSE keyword', () => {
      const result = parseVerdict('Time to CLOSE this position', 'BTC', makePerception());
      assert.equal(result.verdict, 'CLOSE');
    });

    it('returns PASS for text with no verdict keywords', () => {
      const result = parseVerdict('The market looks range-bound', 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
    });

    it('handles completely empty string', () => {
      const result = parseVerdict('', 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
      assert.equal(result.confidence, 0);
    });

    it('handles null text', () => {
      const result = parseVerdict(null, 'BTC', makePerception());
      assert.equal(result.verdict, 'PASS');
    });
  });

  describe('entry/stop/tp price handling', () => {
    it('uses perception.mid when entryPx not provided', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":0.5}', 'BTC', makePerception(42000));
      assert.equal(result.entryPx, 42000);
    });

    it('uses perception.mid when stopPx not provided', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":0.5}', 'BTC', makePerception());
      assert.equal(result.stopPx, 0);
    });

    it('uses perception.mid when tpPx not provided', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":0.5}', 'BTC', makePerception());
      assert.equal(result.tpPx, 0);
    });

    it('preserves custom prices when provided', () => {
      const text = '{"verdict":"LONG","confidence":0.7,"entryPx":50500,"stopPx":49800,"tpPx":52000}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.entryPx, 50500);
      assert.equal(result.stopPx, 49800);
      assert.equal(result.tpPx, 52000);
    });
  });

  describe('side handling', () => {
    it('sets side to "long" for LONG verdict', () => {
      const result = parseVerdict('{"verdict":"LONG","side":"long"}', 'BTC', makePerception());
      assert.equal(result.side, 'long');
    });

    it('sets side to "short" for SHORT verdict', () => {
      const result = parseVerdict('{"verdict":"SHORT","side":"short"}', 'BTC', makePerception());
      assert.equal(result.side, 'short');
    });

    it('nulls side for PASS verdict', () => {
      const result = parseVerdict('{"verdict":"PASS","confidence":0.5}', 'BTC', makePerception());
      assert.equal(result.side, null);
    });

    it('handles missing side field', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":0.5}', 'BTC', makePerception());
      assert.equal(result.side, null);
    });

    it('handles wrong side value', () => {
      const result = parseVerdict('{"verdict":"LONG","side":"wrong"}', 'BTC', makePerception());
      assert.equal(result.side, null);
    });
  });

  describe('reasoning extraction', () => {
    it('extracts reasoning from JSON', () => {
      const result = parseVerdict('{"verdict":"LONG","reasoning":"Strong momentum"}', 'BTC', makePerception());
      assert.equal(result.reasoning, 'Strong momentum');
    });

    it('falls back to first 500 chars of text when no reasoning field', () => {
      const text = '{"verdict":"LONG","confidence":0.5}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.reasoning, text); // falls back to aiText
    });
  });

  describe('multi-line JSON detection', () => {
    it('finds JSON on the last line', () => {
      const text = 'Analysis: bullish trend\nConfidence is high\n{"verdict":"LONG","confidence":0.9}';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'LONG');
      assert.equal(result.confidence, 0.9);
    });

    it('finds JSON in middle of text', () => {
      const text = 'Looking at indicators...\n{"verdict":"SHORT","confidence":0.6}\nConclusion: bearish';
      const result = parseVerdict(text, 'BTC', makePerception());
      assert.equal(result.verdict, 'SHORT');
    });
  });

  describe('confidence bounds', () => {
    it('handles confidence 0.0', () => {
      const result = parseVerdict('{"verdict":"PASS","confidence":0}', 'BTC', makePerception());
      assert.equal(result.confidence, 0);
    });

    it('handles confidence 0.99', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":0.99}', 'BTC', makePerception());
      assert.equal(result.confidence, 0.99);
    });

    it('handles confidence 1.0 max', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":1}', 'BTC', makePerception());
      assert.equal(result.confidence, 1);
    });

    it('defaults confidence to 0 when non-number', () => {
      const result = parseVerdict('{"verdict":"LONG","confidence":"high"}', 'BTC', makePerception());
      assert.equal(result.confidence, 0);
    });
  });
});
