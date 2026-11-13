"""services/intel: deterministic fetch, parse, dedup, score of the desk's view
of the world (news, policy, catalysts, prediction-market odds, positioning).

Entry point: `services.intel.api.build_packet(spec, now=None) -> IntelPacket`.
Zero LLM calls. See README.md for the source list and contracts/strategies/
models.py for the IntelPacket shape this module fills in.
"""
