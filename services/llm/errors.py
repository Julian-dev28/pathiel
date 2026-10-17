"""Failure types for services.llm.

complete() never returns a sentinel for failure (no "" text, no ok=False
flag) - it either returns a valid LlmResult or raises one of these. Callers
that need to treat "the model gateway is down" as a first-class outcome
(services/thesis's repair loop and fallback path, for instance) catch
``LlmError`` and branch on the type only when they need to; most callers can
just catch ``LlmError``.
"""
from __future__ import annotations


class LlmError(RuntimeError):
    """Base for every services.llm failure."""


class LlmBinaryNotFoundError(LlmError):
    """The configured CLI binary does not exist / is not on PATH."""


class LlmTimeoutError(LlmError):
    """The CLI did not finish within the bounded timeout."""


class LlmProcessError(LlmError):
    """The CLI exited non-zero."""


class LlmBadOutputError(LlmError):
    """Empty stdout, non-JSON envelope, or an envelope with is_error=true."""
