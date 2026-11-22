"""Model adapters for the thesis replay eval: anything that turns
(system_prompt, user_prompt) into a text answer the caller then parses as
a ThesisDraft.

CLAUDE.md "LLM access": the project's one sanctioned path to an LLM is the
local Claude Code CLI via services.llm.complete -- hosted LLM APIs are
banned. ``ClaudeCodeAdapter`` wraps that gateway unchanged, pinned to one
``--model`` alias (opus / sonnet / haiku / fable); it is the only adapter
this eval's CLI actually drives.

``CommandAdapter`` is the generic stand-in the brief asks for: ANY local
CLI that reads a prompt on stdin and prints a JSON answer on stdout (e.g.
``ollama run llama3``), so an open-weight model can be plugged into this
eval later without touching this module again. Nothing here installs
anything -- ``ollama_installed()`` only checks whether a binary is already
on PATH, for the CLI to report, never to fetch or run an installer.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence

CLAUDE_CODE_ALIASES = ("opus", "sonnet", "haiku", "fable")
COMMAND_PREFIX = "command:"
DEFAULT_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class AdapterResult:
    text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    duration_s: float = 0.0


class ModelAdapter(Protocol):
    def complete(self, system_prompt: str, user_prompt: str, *, max_output_tokens: int,
                json_schema: Optional[Mapping[str, Any]], tag: str) -> AdapterResult:
        ...


@dataclass(frozen=True)
class ClaudeCodeAdapter:
    """services.llm.complete, pinned to one ``--model`` alias. Every call
    still lands in $PATHIEL_STATE_DIR/llm/usage.jsonl through that gateway
    unchanged -- this adapter adds no bookkeeping of its own, just the
    (system, user) -> AdapterResult shape the replay orchestrator wants."""
    alias: str
    timeout_s: float = DEFAULT_TIMEOUT_S

    def complete(self, system_prompt: str, user_prompt: str, *, max_output_tokens: int,
                json_schema: Optional[Mapping[str, Any]], tag: str) -> AdapterResult:
        from services.llm import complete as llm_complete

        result = llm_complete(system_prompt, user_prompt, max_output_tokens=max_output_tokens,
                              model=self.alias, json_schema=json_schema, tag=tag,
                              timeout_s=self.timeout_s)
        return AdapterResult(text=result.text, model=result.model, input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens, cost_usd=result.cost_usd,
                            duration_s=result.duration_s)


@dataclass(frozen=True)
class CommandAdapter:
    """A generic local CLI: ``argv`` is run as a subprocess, the system and
    user prompts (clearly labeled) are written to its stdin, and whatever
    it prints to stdout (stripped) is handed back as the answer text --
    no schema or envelope is assumed, so a command that does not actually
    speak the thesis JSON shape simply fails the caller's own parse step,
    the same way a Claude Code answer that fails validation does. A
    non-zero exit or a timeout raises; the caller (thesis_replay.run_model_
    once) is responsible for degrading that into a void case rather than
    crashing the whole eval."""
    argv: Sequence[str]
    timeout_s: float = DEFAULT_TIMEOUT_S

    def complete(self, system_prompt: str, user_prompt: str, *, max_output_tokens: int,
                json_schema: Optional[Mapping[str, Any]], tag: str) -> AdapterResult:
        stdin_text = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n"
        t0 = time.monotonic()
        proc = subprocess.run(list(self.argv), input=stdin_text, capture_output=True, text=True,
                              timeout=self.timeout_s)
        duration = time.monotonic() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"command adapter {list(self.argv)!r} exited {proc.returncode}: "
                              f"{proc.stderr.strip()[:500]}")
        return AdapterResult(text=proc.stdout.strip(), model=f"command:{self.argv[0]}", duration_s=duration)


def resolve_adapter(spec: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> ModelAdapter:
    """``spec`` is one of CLAUDE_CODE_ALIASES, or ``command:<cli> [args...]``
    (split with shlex -- a command containing a comma must be the last
    entry in a ``--models`` list, since the CLI itself splits on comma)."""
    spec = spec.strip()
    if spec in CLAUDE_CODE_ALIASES:
        return ClaudeCodeAdapter(spec, timeout_s=timeout_s)
    if spec.startswith(COMMAND_PREFIX):
        argv = shlex.split(spec[len(COMMAND_PREFIX):])
        if not argv:
            raise ValueError(f"empty command in model spec {spec!r}")
        return CommandAdapter(tuple(argv), timeout_s=timeout_s)
    raise ValueError(f"unknown model spec {spec!r}: expected one of {CLAUDE_CODE_ALIASES} "
                     f"or '{COMMAND_PREFIX}<local CLI that reads a prompt on stdin>'")


def ollama_installed() -> bool:
    """Check only -- never installs or runs anything. True if an `ollama`
    binary is already on PATH, so the CLI can report whether the
    `command:ollama ...` adapter is actually usable on this machine."""
    return shutil.which("ollama") is not None
