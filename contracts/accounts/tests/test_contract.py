import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.accounts.models import (Account, ActiveSummary, AgentConfirmIn, AgentPrepareOut,
                                       ApiKeyImportIn, SessionInfo, WatchIn)

FIX = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.parametrize("name,model", [
    ("account_trade", Account), ("account_watch", Account), ("session_new", SessionInfo),
    ("session_ready", SessionInfo), ("agent_prepare", AgentPrepareOut),
    ("summary_trade", ActiveSummary), ("summary_watch", ActiveSummary),
])
def test_fixture_round_trips(name, model):
    raw = json.loads((FIX / f"{name}.json").read_text())
    assert json.loads(model.model_validate(raw).model_dump_json()) == raw


def test_bad_address_rejected():
    with pytest.raises(ValidationError):
        WatchIn(address="0x123")


def test_signature_shape_enforced():
    with pytest.raises(ValidationError):
        AgentConfirmIn(request_id="r", signature="0xdeadbeef")


def test_private_key_shape_enforced_and_never_in_account():
    with pytest.raises(ValidationError):
        ApiKeyImportIn(main_address="0x" + "a" * 40, agent_private_key="nothex")
    assert "private" not in " ".join(Account.model_fields)


def test_extra_fields_rejected():
    raw = json.loads((FIX / "account_trade.json").read_text())
    raw["agent_private_key"] = "0x" + "1" * 64
    with pytest.raises(ValidationError):
        Account.model_validate(raw)
