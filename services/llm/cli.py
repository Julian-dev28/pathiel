"""Subprocess mechanics for the local Claude Code CLI.

Reuses the invocation shape hard-won in pathiel/agents/ai_brain.py's
ClaudeCliBrain / _run_cli / _kill_process_group: a dedicated process group
(``start_new_session=True``) so a timeout can SIGTERM-then-SIGKILL the whole
group and not just the parent, ``--output-format json`` so usage/cost ride
along in the envelope instead of needing a second call, and the same
timeout ceiling (see config.MAX_TIMEOUT_S). This module does not fork that
logic silently - it is the same shape, ported to raise typed exceptions
instead of returning "" for every failure mode (ai_brain's callers treat ""
as "the model declined", which is correct for a verdict brain where a quiet
model IS a valid outcome; services/thesis needs to tell "the model declined"
apart from "the CLI crashed" so it knows whether a repair round can help).

One deliberate difference from ai_brain: this wrapper passes the system
prompt via ``--system-prompt`` instead of concatenating system+user into one
stdin blob. Measured live on 2026-10-17 (one-line "OK" ping, fable model):

    concatenated into stdin, no --system-prompt: cache_creation_input_tokens
        3846, cost $0.0773
    --system-prompt "You are a terse trading desk assistant.":
        cache_creation_input_tokens 830, cost $0.0168

~4.6x cheaper per call, because --system-prompt replaces Claude Code's own
(much longer) default system prompt instead of adding our text on top of it.
See services/llm/README.md for the full probe log, including confirmation
that --json-schema handles nested $defs/$ref (tested against a two-level
schema), which is what lets services/thesis ask for strict JSON.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from typing import Any, Mapping, Sequence

from services.llm.errors import (
    LlmBadOutputError,
    LlmBinaryNotFoundError,
    LlmProcessError,
    LlmTimeoutError,
)


def build_args(
    *,
    binary: str,
    model: str | None,
    system_prompt: str,
    max_turns: int,
    tools: str,
    json_schema: Mapping[str, Any] | None = None,
    fallback_models: Sequence[str] = (),
    max_budget_usd: float | None = None,
    persist_session: bool = False,
    resume_session: str | None = None,
) -> list[str]:
    """Build the argv for one non-interactive CLI call.

    ``tools=""`` disables all tools (ai_brain's non-web-search default);
    ``tools="WebSearch"`` is the web-search mode. ``--safe-mode`` is always
    on. By default every call also passes ``--no-session-persistence``: a
    fresh, non-resumable turn, so two concurrent callers (or two desks
    refreshing at once) cannot bleed session state into each other. Pass
    ``persist_session=True`` to keep the session on disk instead (so a later
    call can ``--resume`` it); pass ``resume_session=<id>`` to resume one -
    verified live 2026-10-17 that ``-p --resume <id>`` recalls a prior turn
    without the caller re-sending it (see services/llm/README.md). A caller
    that resumes almost always also wants persistence turned back off once
    this turn is the last one it needs, which is why the two are independent
    flags rather than one "keep it open forever" switch.
    """
    args: list[str] = [
        binary,
        "-p",
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--tools", tools,
        "--safe-mode",
        "--system-prompt", system_prompt,
    ]
    if not persist_session:
        args.append("--no-session-persistence")
    if resume_session:
        args += ["--resume", resume_session]
    if model:
        args += ["--model", model]
    if fallback_models:
        args += ["--fallback-model", ",".join(fallback_models)]
    if max_budget_usd is not None:
        args += ["--max-budget-usd", f"{max_budget_usd:.4f}"]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema)]
    return args


def run_cli(
    args: Sequence[str],
    user_text: str,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the CLI, feed ``user_text`` on stdin, return the parsed envelope.

    Raises LlmBinaryNotFoundError, LlmTimeoutError, LlmProcessError or
    LlmBadOutputError. Never returns a sentinel for failure, so a caller
    cannot forget to check one.
    """
    try:
        proc = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=dict(env) if env is not None else None,
        )
    except FileNotFoundError as exc:
        raise LlmBinaryNotFoundError(f"CLI binary not found: {args[0]}") from exc
    except Exception as exc:  # pragma: no cover - defensive, OS-dependent
        raise LlmProcessError(f"CLI launch failed for {args[0]}: {exc}") from exc

    try:
        stdout, stderr = proc.communicate(user_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise LlmTimeoutError(f"CLI timed out after {timeout_s:.0f}s: {args[0]}")

    if proc.returncode != 0:
        tail = (stderr or stdout or "").strip()[:500]
        raise LlmProcessError(f"CLI exited {proc.returncode}: {args[0]} {tail}")

    stdout = (stdout or "").strip()
    if not stdout:
        raise LlmBadOutputError(f"CLI returned empty stdout: {args[0]}")

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LlmBadOutputError(f"CLI did not return valid JSON: {exc}") from exc

    if not isinstance(envelope, dict):
        raise LlmBadOutputError(f"CLI envelope was not a JSON object: {type(envelope).__name__}")

    if bool(envelope.get("is_error")):
        raise LlmBadOutputError(f"CLI error envelope: {envelope.get('result') or envelope}")

    return envelope


def _kill_process_group(proc: "subprocess.Popen[str]") -> None:
    """SIGTERM the whole process group, SIGKILL if it does not die in 2s.

    Identical to ai_brain._kill_process_group: a plain proc.kill() only hits
    the immediate child, and `claude -p` can leave its own children (an MCP
    server it spawned, a tool subprocess) running past a timeout otherwise.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
