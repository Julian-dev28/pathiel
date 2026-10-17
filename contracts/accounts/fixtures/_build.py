"""Regenerate: python -m contracts.accounts.fixtures._build
Fake addresses only. The typed_data here shows the SHAPE the wallet signs;
services/accounts builds the real one from the hyperliquid SDK's signing code."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.accounts.models import (Account, ActiveSummary, AgentPrepareOut, SessionInfo)

HERE = Path(__file__).parent
T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
MAIN = "0x00000000000000000000000000000000000000a1"
AGENT = "0x00000000000000000000000000000000000000b2"
WATCHED = "0x00000000000000000000000000000000000000c3"

trade = Account(id="acc_main", label="Main", main_address=MAIN, agent_address=AGENT, mode="trade",
                source="wallet_sign", is_active=True, created_at=T0, verified_at=T0, agent_name="pathiel")
watch = Account(id="acc_watch", label="Whale I follow", main_address=WATCHED, mode="watch",
                source="watch", created_at=T0)
session_new = SessionInfo(local_mode=True, needs_onboarding=True)
session_ready = SessionInfo(local_mode=True, address=MAIN, active_account_id="acc_main", accounts_count=2,
                            can_trade=True, needs_onboarding=False)
prepare = AgentPrepareOut(
    request_id="req_1", agent_address=AGENT, expires_at=T0 + timedelta(minutes=10),
    typed_data={
        "domain": {"name": "HyperliquidSignTransaction", "version": "1", "chainId": 42161,
                   "verifyingContract": "0x0000000000000000000000000000000000000000"},
        "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                   {"name": "chainId", "type": "uint256"},
                                   {"name": "verifyingContract", "type": "address"}],
                  "HyperliquidTransaction:ApproveAgent": [
                      {"name": "hyperliquidChain", "type": "string"}, {"name": "agentAddress", "type": "address"},
                      {"name": "agentName", "type": "string"}, {"name": "nonce", "type": "uint64"}]},
        "primaryType": "HyperliquidTransaction:ApproveAgent",
        "message": {"hyperliquidChain": "Mainnet", "agentAddress": AGENT, "agentName": "pathiel",
                    "nonce": 1792238400000},
    })
summary = ActiveSummary(account=trade, equity_usd=15.21, withdrawable_usd=15.21, open_positions=0,
                        can_trade=True, blockers=[])
summary_watch = ActiveSummary(account=watch, equity_usd=48210.5, withdrawable_usd=30000.0, open_positions=3,
                              can_trade=False, blockers=["Watch-only account: connect a wallet to trade."])


def main() -> None:
    for name, obj in (("account_trade", trade), ("account_watch", watch), ("session_new", session_new),
                      ("session_ready", session_ready), ("agent_prepare", prepare),
                      ("summary_trade", summary), ("summary_watch", summary_watch)):
        (HERE / f"{name}.json").write_text(obj.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
