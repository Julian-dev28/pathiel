"""The shipped pathiel-agent skill must point other MCP clients at the public
launcher, never at the server script (the maintainer path: mainnet and the
tracked 20x config). Review finding of 2026-11-12."""
from __future__ import annotations

import re
from pathlib import Path

REF = Path(__file__).resolve().parents[1] / "skills" / "pathiel-agent" / "references" / "mcp-config.md"


def test_client_config_uses_the_public_launcher():
    text = REF.read_text()
    assert "scripts/pathiel-mcp\n" in text or "scripts/pathiel-mcp`" in text
    assert re.search(r"command:\s*\S*scripts/pathiel-mcp\s*$", text, re.M)


def test_server_script_is_named_only_with_the_warning():
    text = REF.read_text()
    for m in re.finditer(r"pathiel-mcp-server\.py", text):
        window = text[max(0, m.start() - 200): m.end() + 200]
        assert "maintainer path" in window, window
    assert not re.search(r"^\s*(python3?\s+)?\S*scripts/pathiel-mcp-server\.py\s*$", text, re.M)
