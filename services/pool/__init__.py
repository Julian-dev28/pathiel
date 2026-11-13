"""services/pool: the opt-in research pool and its public scoreboard.

Members register a Hyperliquid address with a signed statement. The pool
grades that address's PUBLIC fills with the services/review harness and
publishes graded rows at least 24 hours late. It never places an order,
never reads a thesis and never feeds one (contracts/pool/tests/
test_thesis_boundary.py).

The contract is contracts/pool. The design is
docs/overhaul/RESEARCH_POOL_2026-11-12.md. Run: python -m services.pool.
"""
