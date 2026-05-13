#!/usr/bin/env node
// Run all test suites for hermes-trader.
// Usage: node scripts/__tests__/run-all.mjs

import { spawn } from 'child_process'
import { readdirSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const testFiles = readdirSync(__dirname)
  .filter(f => f.endsWith('.test.mjs'))
  .sort()

if (testFiles.length === 0) {
  console.error('No test files found in', __dirname)
  process.exit(1)
}

console.log(`\n══════════════════════════════════════════════`)
console.log(`  HERMES-TRADER TEST SUITE`)
console.log(`  ${testFiles.length} test files`)
console.log(`══════════════════════════════════════════════\n`)

let totalTests = 0
let totalPassed = 0
let totalFailed = 0

for (const file of testFiles) {
  const filePath = join(__dirname, file)
  console.log(`\n── ${file} ──`)

  await new Promise((resolve, reject) => {
    const proc = spawn('node', ['--test', filePath], { stdio: ['inherit', 'pipe', 'inherit'] })
    let output = ''
    proc.stdout.on('data', (d) => { output += d.toString() })
    proc.on('close', (code) => {
      // Parse summary from output
      const passMatch = output.match(/tests (\d+)\s+passed/)
      const failMatch = output.match(/tests (\d+)\s+failed/)
      const skipMatch = output.match(/tests (\d+)\s+skipped/)

      if (passMatch) totalPassed += parseInt(passMatch[1])
      if (failMatch) totalFailed += parseInt(failMatch[1])
      if (skipMatch) totalTests += parseInt(skipMatch[1])
      if (passMatch) totalTests += parseInt(passMatch[1])
      if (failMatch) totalTests += parseInt(failMatch[1])

      if (code === 0) {
        console.log(`  ✓ ${file} passed`)
      } else {
        console.log(`  ✗ ${file} failed (exit ${code})`)
      }
      resolve()
    })
  })
}

console.log(`\n══════════════════════════════════════════════`)
console.log(`  TOTAL: ${totalTests} tests | ${totalPassed} passed | ${totalFailed} failed`)
console.log(`══════════════════════════════════════════════\n`)

process.exit(totalFailed > 0 ? 1 : 0)
