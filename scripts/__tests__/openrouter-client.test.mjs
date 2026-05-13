// OpenRouter client tests — inspects source file for expected constants
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

function countSourcePattern(pattern) {
  const source = readFileSync(join(__dirname, '../../lib/openrouter-client.ts'), 'utf8');
  const regex = new RegExp(pattern, 'g');
  const matches = source.match(regex);
  return matches ? matches.length : 0;
}

const openrouterCount = countSourcePattern('openrouter');
const qwenCount = countSourcePattern('qwen');
const createCount = countSourcePattern('createOpenAIClient');

describe('openrouter-client', () => {
  describe('OPENROUTER_MODEL', () => {
    it('source contains OpenRouter base URL', () => {
      assert.ok(openrouterCount > 0, 'openrouter-client.ts should contain openrouter.ai');
    });

    it('source contains qwen default model', () => {
      assert.ok(qwenCount > 0, 'openrouter-client.ts should contain qwen default');
    });
  });

  describe('createOpenAIClient', () => {
    it('source contains createOpenAIClient function', () => {
      assert.ok(createCount > 0, 'openrouter-client.ts should export createOpenAIClient');
    });
  });
});
