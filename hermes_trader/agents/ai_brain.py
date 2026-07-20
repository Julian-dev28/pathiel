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
from typing import Any, Iterable, Mapping, Protocol

import httpx

from hermes_trader.agents.config_store import read_agent_config

logger = logging.getLogger(__name__)

AI_BRAIN_PROVIDERS = {"openrouter", "claude_cli", "codex_cli"}
DEFAULT_AI_BRAIN_PROVIDER = "openrouter"
MAX_CLI_TIMEOUT_S = 120.0
_OPENROUTER_WEB_ENGINES = {
    "auto", "native", "exa", "firecrawl", "parallel", "perplexity",
}


class AiBrainResult(str):
    """Completion text with optional provider metadata.

    This remains a real ``str`` so existing parsing, truthiness, logging, and
    injected-brain call sites keep working unchanged.  Callers that need audit
    data can inspect ``usage``, ``citations``, and ``web_search_requests``.
    """

    usage: Mapping[str, Any]
    citations: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]

    def __new__(
        cls,
        value: object = "",
        *,
        usage: Mapping[str, Any] | None = None,
        citations: Iterable[Mapping[str, Any]] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "AiBrainResult":
        obj = super().__new__(cls, str(value or ""))
        obj.usage = dict(usage) if isinstance(usage, Mapping) else {}
        obj.citations = tuple(dict(c) for c in (citations or ()) if isinstance(c, Mapping))
        obj.metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        return obj

    @property
    def web_search_requests(self) -> int:
        server_use = self.usage.get("server_tool_use", {})
        if not isinstance(server_use, Mapping):
            return 0
        try:
            return max(0, int(server_use.get("web_search_requests", 0) or 0))
        except (TypeError, ValueError):
            return 0


def completion_text(completion: object) -> str:
    """Return completion text from new metadata results or legacy strings."""
    if completion is None:
        return ""
    if isinstance(completion, str):
        return str(completion)
    text = getattr(completion, "text", None)
    if text is not None:
        return str(text)
    if isinstance(completion, Mapping):
        return str(completion.get("text") or completion.get("content") or "")
    return str(completion)


def completion_web_search_requests(completion: object) -> int:
    """Extract provider-reported search calls without inferring from the gate."""
    direct = getattr(completion, "web_search_requests", None)
    if direct is not None:
        try:
            return max(0, int(direct or 0))
        except (TypeError, ValueError):
            return 0
    usage = getattr(completion, "usage", None)
    if usage is None and isinstance(completion, Mapping):
        usage = completion.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    server_use = usage.get("server_tool_use", {})
    if not isinstance(server_use, Mapping):
        return 0
    try:
        return max(0, int(server_use.get("web_search_requests", 0) or 0))
    except (TypeError, ValueError):
        return 0


def completion_citations(completion: object) -> tuple[str, ...]:
    """Return bounded-persistence-friendly citation URLs/titles.

    ``AiBrainResult.citations`` retains the raw annotations.  This accessor
    normalizes OpenRouter's ``url_citation`` envelope for logs and analysis
    records while continuing to accept providers that already return strings.
    """
    raw = getattr(completion, "citations", None)
    if raw is None and isinstance(completion, Mapping):
        raw = completion.get("citations")
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, Mapping):
            nested = item.get("url_citation")
            source = nested if isinstance(nested, Mapping) else item
            url = str(source.get("url") or "").strip()
            title = str(source.get("title") or "").strip()
            # A titleless annotation must NOT render as "url — url": UIs
            # hyperlink the whole string and the glued em dash 404s the link
            # (ARB/cryptorank incident 2026-07-13).
            if not title or title == url or title.startswith("http"):
                value = url or title
            else:
                value = f"{title} — {url}" if url else title
        else:
            value = str(item or "").strip()
        if value:
            out.append(value)
    return tuple(out)


class AiBrain(Protocol):
    """Completion backend for the research prompt -> verdict-text seam."""

    provider: str

    def complete(self, system_prompt: str, user_message: str,
                 web_search: bool = False) -> str:
        """Return model text ending in verdict JSON, or ``""`` on failure.

        ``web_search=True`` asks the backend to allow live web lookups for the
        completion. Only claude_cli honors it; the other providers ignore the
        flag (no error) so the research seam can request it unconditionally."""


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

    def complete(self, system_prompt: str, user_message: str,
                 web_search: bool = False) -> str:
        """Call OpenRouter (runs the async client in a fresh event loop)."""
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        model = os.environ.get("OPENROUTER_MODEL", "x-ai/grok-4.3")

        if not openrouter_key:
            logger.warning("[research] OPENROUTER_API_KEY not set — returning empty response")
            return ""

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._async_do_call(
                    openrouter_key, model, system_prompt, user_message,
                    web_search=web_search,
                )
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
        web_search: bool = False,
    ) -> str:
        """Async POST to OpenRouter, including the 402 degraded-token retry."""
        tools = [_openrouter_web_search_tool()] if web_search else None
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:

            async def _post(max_toks: int):
                payload: dict[str, Any] = {
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
                }
                if tools is not None:
                    payload["tools"] = tools
                return await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
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
                    message = choices[0].get("message", {}) or {}
                    annotations = message.get("annotations", [])
                    return AiBrainResult(
                        message.get("content", ""),
                        usage=data.get("usage"),
                        citations=annotations if isinstance(annotations, list) else None,
                        metadata={"id": data.get("id"), "model": data.get("model")},
                    )
                logger.error("[research] LLM returned 200 but no choices — empty response")
                return ""

            body = resp.text[:200] if resp.text else ""
            logger.error(
                f"[research] LLM call FAILED: HTTP {resp.status_code} — AI research is "
                f"DOWN, all verdicts will default to PASS until fixed. {body}"
            )
        return ""


def _openrouter_web_search_tool() -> dict[str, Any]:
    """Build the bounded OpenRouter server-tool declaration.

    Native search is the deliberate default for Grok.  The result and total
    caps keep an agentic model from growing search cost/context without bound;
    OpenRouter may ignore the per-call result cap for native providers, but the
    total cap still documents and enforces the request-level budget where the
    provider supports it.
    """
    engine = str(os.environ.get("OPENROUTER_WEB_ENGINE", "native") or "native").lower()
    if engine not in _OPENROUTER_WEB_ENGINES:
        logger.warning(f"[ai-brain] invalid OpenRouter web engine {engine!r}; using native")
        engine = "native"
    max_results = _bounded_int(
        os.environ.get("OPENROUTER_WEB_MAX_RESULTS"),
        default=5, minimum=1, maximum=10,
    )
    max_total_results = _bounded_int(
        os.environ.get("OPENROUTER_WEB_MAX_TOTAL_RESULTS"),
        default=10, minimum=max_results, maximum=25,
    )
    return {
        "type": "openrouter:web_search",
        "parameters": {
            "engine": engine,
            "max_results": max_results,
            "max_total_results": max_total_results,
        },
    }


class ClaudeCliBrain:
    provider = "claude_cli"

    def complete(self, system_prompt: str, user_message: str,
                 web_search: bool = False) -> str:
        brain_cfg = _read_ai_brain_config()
        provider_cfg = _provider_config(brain_cfg, self.provider)
        prompt = _combined_prompt(system_prompt, user_message)
        # A web-search completion needs turns for the tool round-trips
        # (verified live: search + verdict lands in 2-4 turns; 8 is headroom).
        if web_search:
            max_turns = _bounded_int(
                os.environ.get("CLAUDE_CLI_WEB_MAX_TURNS")
                or provider_cfg.get("web_search_max_turns"),
                default=8,
                minimum=2,
                maximum=20,
            )
            tools = "WebSearch"
        else:
            max_turns = _bounded_int(
                os.environ.get("CLAUDE_CLI_MAX_TURNS") or provider_cfg.get("max_turns"),
                default=1,
                minimum=1,
                maximum=20,
            )
            tools = ""
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
            tools,
            "--safe-mode",
            "--no-session-persistence",
        ]
        # Operator-pinned model (2026-07-20: route verdicts through Sonnet 5).
        # Unset = the CLI's own default; env outranks config like the other knobs.
        model = str(os.environ.get("CLAUDE_CLI_MODEL") or provider_cfg.get("model") or "").strip()
        if model:
            args += ["--model", model]
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
        validated = _validated_cli_result(self.provider, result)
        if not validated:
            return ""
        envelope_meta = {
            key: envelope[key]
            for key in ("modelUsage", "total_cost_usd", "num_turns", "duration_ms", "duration_api_ms")
            if key in envelope
        }
        return AiBrainResult(
            validated,
            usage=envelope.get("usage"),
            metadata=envelope_meta,
        )


class CodexCliBrain:
    provider = "codex_cli"

    def complete(self, system_prompt: str, user_message: str,
                 web_search: bool = False) -> str:
        if web_search:
            logger.debug("[ai-brain] web_search requested but codex_cli runs read-only sandbox — ignored")
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
