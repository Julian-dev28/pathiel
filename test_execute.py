#!/usr/bin/env python3
"""Test script to debug the execute pipeline directly."""
import sys
import os

# Load .env.local
env_path = '.env.local'
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ[key.strip()] = val.strip()

from hermes_agent.agents.research import research
from hermes_agent.agents.executor import maybe_execute
from hermes_agent.agents.perception import scan_once
from hermes_agent.agents.config import get_config
from hermes_agent.client.universe import get_universe

print("[TEST] Starting execute pipeline test...")
print(f"[TEST] Mode: {get_config().get('mode', 'OFF')}")

# Step 1: Scan for triggers
print("\n[TEST] Step 1: Scanning for triggers...")
universe = get_universe()
results = scan_once(universe=universe, min_score=20, config=get_config())
print(f"[TEST] Found {len(results)} triggers")

if not results:
    print("[TEST] No triggers found. Exiting.")
    sys.exit(0)

# Step 2: Research the first trigger
coin = results[0]['coin']
print(f"\n[TEST] Step 2: Researching {coin}...")
analysis = research(coin, results[0])
print(f"[TEST] Verdict: {analysis['verdict']}, Confidence: {analysis['confidence']}")

if analysis['verdict'] not in ('LONG', 'SHORT'):
    print(f"[TEST] Verdict is {analysis['verdict']} - no trade needed. Exiting.")
    sys.exit(0)

# Step 3: Execute
print(f"\n[TEST] Step 3: Executing trade...")
try:
    result = maybe_execute(analysis)
    print(f"[TEST] Result: {result}")
except Exception as e:
    import traceback
    print(f"\n[TEST] EXCEPTION: {e}")
    print(f"\n[TEST] Traceback:\n{traceback.format_exc()}")
    sys.exit(1)

print("\n[TEST] Done!")
