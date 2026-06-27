"""Pluggable AI brain providers for research verdict completion."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import signal
import subprocess
from typing import Any, Mapping, Protocol

import httpx

from hermes_trader.agents.config_store import read_agent_config

logger = logging.getLogger(__name__)

AI_BRAIN_PROVIDERS = {"openrouter", "claude_cli", "codex_cli"}
DEFAULT_AI_BRAIN_PROVIDER = "openrouter"
MAX_CLI_TIMEOUT_S = 120.0


class AiBrain(Protocol):
    """Completion backend for the research prompt -> verdict-text seam."""

    provider: str

    def complete(self, system_prompt: str, user_message: str) -> str:
        """Return model text ending in verdict JSON, or ``""`` on failure."""


def _read_ai_brain_config() -> Mapping[str, Any]:
    try:
        cfg = read_agent_config()
    except Exception as exc:
        logger.error(f"[ai-brain] config read failed: {exc}; using OpenRouter")
        return {}
    brain_cfg = cfg.get("ai_brain", {}) if isinstance(cfg, dict) else {}
    return brain_cfg if isinstance(brain_cfg, dict) else {}


def _normalise_provider(raw: object) -> str:
    provider = str(raw or "").strip().lower().replace("-", "_")
    aliases = {
        "claude": "claude_cli",
        "codex": "codex_cli",
        "open_router": "openrouter",
    }
    provider = aliases.get(provider, provider)
    if provider in AI_BRAIN_PROVIDERS:
        return provider
    if provider:
        logger.warning(
            f"[ai-brain] unknown provider {provider!r}; falling back to {DEFAULT_AI_BRAIN_PROVIDER}"
        )
    return DEFAULT_AI_BRAIN_PROVIDER


def selected_ai_brain_provider(config: Mapping[str, Any] | None = None) -> str:
    """Hot-read provider selector.

    ``AI_BRAIN_PROVIDER`` wins over ``config["ai_brain"]["provider"]`` so an
    operator can revert without touching the config file.
    """
    env_provider = os.environ.get("AI_BRAIN_PROVIDER")
    if env_provider:
        return _normalise_provider(env_provider)
    brain_cfg: Mapping[str, Any]
    if config is None:
        brain_cfg = _read_ai_brain_config()
    else:
        nested = config.get("ai_brain", {}) if isinstance(config, Mapping) else {}
        brain_cfg = nested if isinstance(nested, Mapping) else {}
    return _normalise_provider(brain_cfg.get("provider", DEFAULT_AI_BRAIN_PROVIDER))


def get_brain(provider: str | None = None) -> AiBrain:
    """Return the configured AI brain strategy."""
    selected = _normalise_provider(provider) if provider else selected_ai_brain_provider()
    if selected == "claude_cli":
        return ClaudeCliBrain()
    if selected == "codex_cli":
        return CodexCliBrain()
    return OpenRouterBrain()


class OpenRouterBrain:
    provider = "openrouter"

    def complete(self, system_prompt: str, user_message: str) -> str:
        """Call OpenRouter (runs the async client in a fresh event loop)."""
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        model = os.environ.get("OPENROUTER_MODEL", "x-ai/grok-4.3")

        if not openrouter_key:
            logger.warning("[research] OPENROUTER_API_KEY not set — returning empty response")
            return ""

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._async_do_call(openrouter_key, model, system_prompt, user_message)
            )
        except Exception as exc:
            logger.error(
                f"[research] OpenRouter call FAILED: {type(exc).__name__}: {exc} — "
                "AI research is DOWN, all verdicts will default to PASS until fixed."
            )
            return ""
        finally:
            loop.close()

    async def _async_do_call(
        self,
        openrouter_key: str,
        model: str,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Async POST to OpenRouter, including the 402 degraded-token retry."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:

            async def _post(max_toks: int):
                return await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "stream": False,
                        # Output is a verdict JSON + 2-3 sentences (~150-300
                        # visible tokens). Reasoning models can burn hidden
                        # tokens before the JSON, so leave headroom.
                        "max_tokens": max_toks,
                        "temperature": 0.1,
                    },
                    headers={"Authorization": f"Bearer {openrouter_key}"},
                )

            try:
                initial_max_tokens = int(os.environ.get("OPENROUTER_MAX_TOKENS", "2048"))
            except (TypeError, ValueError):
                initial_max_tokens = 2048
            initial_max_tokens = max(500, min(initial_max_tokens, 4096))

            resp = await _post(initial_max_tokens)
            if resp.status_code == 402:
                # "...You requested up to N tokens, but can only afford 842..."
                m = re.search(r"can only afford (\d+)", resp.text or "")
                if m and int(m.group(1)) >= 500:
                    budget = int(m.group(1)) - 50
                    logger.warning(
                        f"[research] 402 with affordability hint — retrying DEGRADED "
                        f"at max_tokens={budget} (add credits to restore full reasoning)"
                    )
                    resp = await _post(budget)

            if resp.is_success:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                logger.error("[research] LLM returned 200 but no choices — empty response")
                return ""

            body = resp.text[:200] if resp.text else ""
            logger.error(
                f"[research] LLM call FAILED: HTTP {resp.status_code} — AI research is "
                f"DOWN, all verdicts will default to PASS until fixed. {body}"
            )
        return ""


class ClaudeCliBrain:
    provider = "claude_cli"

    def complete(self, system_prompt: str, user_message: str) -> str:
        brain_cfg = _read_ai_brain_config()
        provider_cfg = _provider_config(brain_cfg, self.provider)
        prompt = _combined_prompt(system_prompt, user_message)
        max_turns = _bounded_int(
            os.environ.get("CLAUDE_CLI_MAX_TURNS") or provider_cfg.get("max_turns"),
            default=1,
            minimum=1,
            maximum=20,
        )
        args = _command_parts(
            os.environ.get("CLAUDE_CLI_COMMAND") or provider_cfg.get("command"),
            ["claude"],
        ) + [
            "-p",
            "--output-format",
            "json",
            "--max-turns",
            str(max_turns),
            "--tools",
            "",
            "--safe-mode",
            "--no-session-persistence",
        ]
        stdout = _run_cli(args, prompt, _cli_timeout_s(brain_cfg))
        if not stdout:
            return ""
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            logger.error(f"[ai-brain] claude_cli returned non-JSON envelope: {exc}")
            return ""
        if bool(envelope.get("is_error")):
            logger.error(f"[ai-brain] claude_cli error envelope: {envelope.get('result') or envelope}")
            return ""
        result = str(envelope.get("result") or "")
        return _validated_cli_result(self.provider, result)


class CodexCliBrain:
    provider = "codex_cli"

    def complete(self, system_prompt: str, user_message: str) -> str:
        brain_cfg = _read_ai_brain_config()
        provider_cfg = _provider_config(brain_cfg, self.provider)
        prompt = _combined_prompt(system_prompt, user_message)
        args = _command_parts(
            os.environ.get("CODEX_CLI_COMMAND") or provider_cfg.get("command"),
            ["codex"],
        ) + [
            "exec",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-rules",
            "-",
        ]
        stdout = _run_cli(args, prompt, _cli_timeout_s(brain_cfg))
        return _validated_cli_result(self.provider, stdout)


def _combined_prompt(system_prompt: str, user_message: str) -> str:
    return f"{system_prompt}\n\n{user_message}"


def _provider_config(brain_cfg: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    cfg = brain_cfg.get(provider, {}) if isinstance(brain_cfg, Mapping) else {}
    return cfg if isinstance(cfg, Mapping) else {}


def _bounded_int(raw: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _cli_timeout_s(brain_cfg: Mapping[str, Any]) -> float:
    raw = os.environ.get("AI_BRAIN_TIMEOUT_S") or brain_cfg.get("timeout_s")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = MAX_CLI_TIMEOUT_S
    return max(1.0, min(value, MAX_CLI_TIMEOUT_S))


def _command_parts(raw: object, default: list[str]) -> list[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw if str(p).strip()]
        return parts or default[:]
    if isinstance(raw, str) and raw.strip():
        try:
            return shlex.split(raw)
        except ValueError as exc:
            logger.error(f"[ai-brain] invalid command {raw!r}: {exc}; using {default[0]}")
    return default[:]


def _run_cli(args: list[str], prompt: str, timeout_s: float) -> str:
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        logger.error(f"[ai-brain] CLI binary not found: {args[0]}")
        return ""
    except Exception as exc:
        logger.error(f"[ai-brain] CLI launch failed for {args[0]}: {exc}")
        return ""

    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        logger.error(f"[ai-brain] CLI timeout after {timeout_s:.0f}s: {args[0]}")
        return ""

    if proc.returncode != 0:
        err = (stderr or stdout or "").strip()[:500]
        logger.error(f"[ai-brain] CLI exited {proc.returncode}: {args[0]} {err}")
        return ""

    if not (stdout or "").strip():
        logger.error(f"[ai-brain] CLI returned empty stdout: {args[0]}")
        return ""
    if stderr and stderr.strip():
        logger.debug(f"[ai-brain] CLI stderr from {args[0]}: {stderr.strip()[:500]}")
    return stdout


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()


def _validated_cli_result(provider: str, result: str) -> str:
    result = result or ""
    if not result.strip():
        logger.error(f"[ai-brain] {provider} returned empty result")
        return ""
    if not _contains_parseable_verdict_json(result):
        logger.error(f"[ai-brain] {provider} returned no parseable verdict JSON")
        return ""
    return result


def _contains_parseable_verdict_json(text: str) -> bool:
    candidates: list[str] = []
    for line in reversed((text or "").strip().splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and "verdict" in stripped and stripped.endswith("}"):
            candidates.append(stripped)
            break
    candidates.extend(match.group(0) for match in re.finditer(r'\{[^{}]*"verdict"[^{}]*\}', text or ""))

    for candidate in candidates:
        cleaned = re.sub(r"```json\s*|```", "", candidate).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            return True
    return False
