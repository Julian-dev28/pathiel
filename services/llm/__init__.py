"""services/llm - the project's one LLM gateway (local Claude Code CLI only).

Public surface:

    from services.llm import complete, LlmResult
    from services.llm.errors import LlmError
"""
from __future__ import annotations

from services.llm.api import LlmResult, complete
from services.llm.errors import (
    LlmBadOutputError,
    LlmBinaryNotFoundError,
    LlmError,
    LlmProcessError,
    LlmTimeoutError,
)

__all__ = [
    "complete",
    "LlmResult",
    "LlmError",
    "LlmBinaryNotFoundError",
    "LlmTimeoutError",
    "LlmProcessError",
    "LlmBadOutputError",
]
