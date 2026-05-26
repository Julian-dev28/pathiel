# HIP-3 Tokenized Equity Integration Handoff Prompt

**Task**: Add HIP-3 Tokenized Equity Market Support to Hermes Trader

**Current state**
Hermes Trader runs a continuous loop (`scripts/trading_loop.py`) that does:
- `scan_once()` → TA filter → research → execute
- Only scans native Hyperliquid crypto perpetuals via `get_universe()` + `get_all_hl_mids()`
- Uses existing components: `analyze_perception`, `research`, `maybe_execute`, DSL exit engine, `.agent-memory.json`, config, etc.

**Goal**
Extend the engine so it can also scan, analyze, and trade tokenized equity/commodity markets on Hyperliquid (HIP-3 markets) such as:
- `xyz:NVDA`, `xyz:TSLA`, `xyz:AAPL`, etc.
- `km:GLDMINE` (gold), oil, indices, SPACEX, etc.

**Requirements**
1. Update the market universe / scanner to include HIP-3 markets when they are enabled.
2. Make sure `scan_once` and the perception layer can generate signals on these assets (the same TA triggers should work).
3. Ensure the research agent and executor can handle asset names that contain `:` (e.g. `xyz:NVDA`).
4. DSL exit engine must be able to track and exit these positions (it already does some synthesis, so this may need light extension).
5. Keep all existing behavior for native crypto markets. Do not break the current loop.
6. Add a config flag or simple toggle (`enable_hip3` or similar) so it can be turned on/off cleanly.
7. Update `status.py` and any reporting so it shows HIP-3 positions and equity correctly.

**Key files to modify**
- `hermes_trader/client/universe.py`
- `hermes_trader/agents/perception.py`
- `hermes_trader/agents/research.py` (if needed for asset naming)
- `hermes_trader/agents/executor.py`
- `hermes_trader/agents/dsl_exit.py`
- `scripts/trading_loop.py`
- Any relevant config or memory handling

**Constraints**
- Do not rewrite the whole scanner or research logic from scratch.
- Prefer small, targeted changes that reuse as much existing code as possible.
- Keep the same scoring / filtering / sizing model.

**Deliverables**
- Working implementation that can produce triggers on at least `xyz:NVDA` (and a couple other HIP-3 markets).
- Brief `README` or comment block explaining how to enable HIP-3 scanning.
- Confirmation that a full loop cycle runs without errors on both crypto and tokenized equity markets.

**Context**
Current equity is small (~$272) and the user wants to deploy into NVDA tokenized perps via the engine instead of manual trading. This is a high-priority extension.

**Related session learning (26 May 2026)**
- Risk gate default in `risk_gates.py` was raised from 200 → 300 to unblock ~$291 notional trades.
- Recent trades on `hyna:XRP` and repeated PENDLE entries failed with size_usd=0 because sizing logic did not handle HIP-3 asset names.
- perception layer had very low output (only 6 perceptions despite 100 trades).