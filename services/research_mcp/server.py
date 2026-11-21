"""pathiel research MCP server -- stdio transport (JSON-RPC 2.0, one message
per line), same framing as scripts/pathiel-mcp-server.py (the trading
server) but its own process, its own entrypoint (scripts/pathiel-research-
mcp.py), and a completely different tool family: six read-only, keyless
research tools (schemas.py), none of them an order, a key read, or an
account read.

This module, and everything it imports from services/research_mcp, must
never import order-placement or signing code -- tests/
test_no_trading_imports.py is the gate that proves it, both by grepping this
package's own source and by checking sys.modules after a full import. It
also never opens .env.local or ~/.pathiel: see coins.py, state.py and
tools.py's docstrings for the specific calls that would and how each is
avoided (use_overlay=False on every services.intel.scheduled_events /
services.intel.sources.catalysts call).

Positioning (the operator's own words): "the risk-gated research layer AI
agents trade through." This server is the free, keyless half of that
pitch -- a paid, hosted, metered version is sketched in
docs/design/RESEARCH_MCP_PAID_TIER.md, not built.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.research_mcp import tools, usage  # noqa: E402
from services.research_mcp.coins import UnsupportedCoinError  # noqa: E402
from services.research_mcp.schemas import TOOLS  # noqa: E402

SERVER_NAME = "pathiel-research"
# This server's own version (matches services/research_mcp/.claude-plugin/
# plugin.json), not pathiel.__version__ -- deliberately not importing the
# `pathiel` package at all: it has its own import-time side effects (an SSL
# cert-bundle patch, PATHIA_*/PATHIEL_* environment mirroring) that this
# server has no reason to carry, and importing it would be one more thing
# tests/test_no_trading_imports.py's reviewer has to re-check stays clean.
SERVER_VERSION = "0.1.0"

SERVER_INSTRUCTIONS = (
    "pathiel research: the risk-gated research layer AI agents trade through. Read-only and "
    "keyless -- it never touches an exchange key, an account, an order, or any of pathiel's own "
    "trading tools. Six tools: research_brief (one-call summary: levels, next catalysts, top "
    "headlines, prediction-market odds, and the latest stored desk thesis if one exists), "
    "events, prediction_odds, fred_series (needs your own free FRED_API_KEY), headlines and "
    "levels. Supported coins today: BRENTOIL, HYPE, BTC, ETH, SP500 (plus common aliases). "
    "Data sources: Hyperliquid public candles, Google News / Yahoo Finance / EIA / "
    "Investing.com / oilprice.com / Cointelegraph RSS, Polymarket and Kalshi public markets, "
    "FRED (bring your own key), and pathiel's own scheduled-event calendar. See this server's "
    "README for attribution and limits. Not financial advice."
)

# JSON-RPC 2.0 error codes, same values scripts/pathiel-mcp-server.py uses.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602

_SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# A tool result lands in the client model's context; narrow it before this
# backstop fires. Same default and override knob as the trading server.
MAX_OUTPUT_CHARS = int(os.environ.get("PATHIEL_RESEARCH_MCP_MAX_OUTPUT_CHARS", "40000"))

MAX_CONCURRENT_CALLS = int(os.environ.get("PATHIEL_RESEARCH_MCP_MAX_CONCURRENT", "6"))
DEFAULT_TOOL_TIMEOUT_S = float(os.environ.get("PATHIEL_RESEARCH_MCP_TOOL_TIMEOUT_S", "30"))


def _norm_coin(raw: Any) -> str:
    return str(raw if raw is not None else "").strip()


# name -> (handler(params) -> dict, which params are required strings)
def _handle_research_brief(params: Dict[str, Any]) -> Dict[str, Any]:
    return tools.research_brief(_norm_coin(params.get("coin")))


def _handle_events(params: Dict[str, Any]) -> Dict[str, Any]:
    coins = params.get("coins")
    if coins is not None and not isinstance(coins, list):
        raise ValueError("coins must be an array of strings")
    days = params.get("days", 7.0)
    return tools.events_tool(coins=coins, days=float(days) if days is not None else 7.0)


def _handle_prediction_odds(params: Dict[str, Any]) -> Dict[str, Any]:
    return tools.prediction_odds_tool(query=params.get("query"), coin=params.get("coin"))


def _handle_fred_series(params: Dict[str, Any]) -> Dict[str, Any]:
    series_id = params.get("series_id")
    if not series_id or not isinstance(series_id, str):
        raise ValueError("series_id is required")
    return tools.fred_series_tool(series_id, start=params.get("start"), end=params.get("end"))


def _handle_headlines(params: Dict[str, Any]) -> Dict[str, Any]:
    hours = params.get("hours", 24.0)
    return tools.headlines_tool(coin=params.get("coin"), topic=params.get("topic"),
                                hours=float(hours) if hours is not None else 24.0)


def _handle_levels(params: Dict[str, Any]) -> Dict[str, Any]:
    return tools.levels_tool(_norm_coin(params.get("coin")))


_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "research_brief": _handle_research_brief,
    "events": _handle_events,
    "prediction_odds": _handle_prediction_odds,
    "fred_series": _handle_fred_series,
    "headlines": _handle_headlines,
    "levels": _handle_levels,
}

# Every tool name in schemas.TOOLS must have a handler and vice versa --
# checked once at import, not per call, so a typo fails loudly at startup.
assert set(_HANDLERS) == {t["name"] for t in TOOLS}, "tool/handler name mismatch"


def server_tools() -> List[Dict[str, Any]]:
    return TOOLS


def _validate_required(name: str, params: Dict[str, Any]) -> Optional[str]:
    schema = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
    if not schema:
        return None
    for key in schema.get("required") or []:
        if not params.get(key):
            return f"{name} needs a non-empty {key!r} argument"
    return None


def _payload_is_error(obj: Dict[str, Any]) -> bool:
    return isinstance(obj, dict) and bool(obj.get("error"))


def _guard_output(name: str, text: str) -> str:
    if MAX_OUTPUT_CHARS <= 0 or len(text) <= MAX_OUTPUT_CHARS:
        return text
    return json.dumps({
        "truncated": True, "tool": name, "original_chars": len(text),
        "hint": "Too large for a model context. Narrow the call (coin, hours, days) and retry.",
        "head": text[: MAX_OUTPUT_CHARS // 2],
    })


def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run one tool by name; never raises. Returns the MCP tools/call result
    shape: {"content": [...], "isError": bool}. Records one usage.
    record_call(name) per invocation, success or failure alike -- a local,
    privacy-preserving count of how often each tool is called, nothing
    else (see usage.py)."""
    if name not in _HANDLERS:
        raise KeyError(name)
    usage.record_call(name)
    arguments = arguments if isinstance(arguments, dict) else {}
    missing = _validate_required(name, arguments)
    if missing:
        payload: Dict[str, Any] = {"error": missing, "tool": name}
    else:
        try:
            payload = _HANDLERS[name](arguments)
        except UnsupportedCoinError as exc:
            payload = {"error": str(exc), "tool": name}
        except (ValueError, RuntimeError) as exc:
            payload = {"error": str(exc), "tool": name}
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the server
            traceback.print_exc(file=sys.stderr)
            payload = {"error": f"{type(exc).__name__}: {exc}", "tool": name}
    text = _guard_output(name, json.dumps(payload, default=str))
    return {"content": [{"type": "text", "text": text}], "isError": _payload_is_error(payload)}


def _result_msg(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error_msg(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def dispatch_message(msg: Any) -> Optional[Dict[str, Any]]:
    """One inbound JSON-RPC message -> the response to send, or None for a
    notification (no "id"): the MCP spec treats a reply to one as a
    protocol violation, so this never sends one."""
    if not isinstance(msg, dict):
        return _error_msg(None, _INVALID_REQUEST, "message must be a JSON object")
    method = msg.get("method")
    if "id" not in msg:
        return None
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(method, str):
        return _error_msg(msg_id, _INVALID_REQUEST, "method must be a string")
    if not isinstance(params, dict):
        return _error_msg(msg_id, _INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in _SUPPORTED_PROTOCOL_VERSIONS else _SUPPORTED_PROTOCOL_VERSIONS[0]
        return _result_msg(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        })
    if method == "ping":
        return _result_msg(msg_id, {})
    if method == "tools/list":
        return _result_msg(msg_id, {"tools": server_tools()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in _HANDLERS:
            return _error_msg(msg_id, _INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            return _result_msg(msg_id, call_tool(name, params.get("arguments") or {}))
        except Exception as exc:  # noqa: BLE001 - call_tool already catches handler errors;
            traceback.print_exc(file=sys.stderr)  # this is a last-resort net, e.g. a bad arguments shape.
            return _error_msg(msg_id, -32603, f"Internal error: {exc}")
    return _error_msg(msg_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


_SEND_LOCK = __import__("threading").Lock()
_OUT = None


def _out():
    return _OUT if _OUT is not None else sys.stdout


def _send(msg: Dict[str, Any]) -> None:
    line = json.dumps(msg, default=str) + "\n"
    with _SEND_LOCK:
        out = _out()
        out.write(line)
        out.flush()


def _timeout_result(name: str, seconds: float) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps({
        "error": f"{name} did not finish within {seconds:.0f}s. It may still complete; try again.",
        "tool": name, "timed_out": True})}], "isError": True}


def _route(msg: Any, pool: ThreadPoolExecutor) -> None:
    if not isinstance(msg, dict):
        _send(_error_msg(None, _INVALID_REQUEST, "message must be a JSON object"))
        return
    if "id" not in msg:
        return  # a notification; nothing to answer, nothing this server needs to act on
    method = msg.get("method")
    params = msg.get("params")
    if method == "tools/call" and isinstance(params, dict):
        msg_id = msg.get("id")
        name = params.get("name")

        def work() -> None:
            try:
                response = _result_msg(msg_id, call_tool(name, params.get("arguments") or {}))
            except KeyError:
                response = _error_msg(msg_id, _INVALID_PARAMS, f"Unknown tool: {name}")
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                response = _error_msg(msg_id, -32603, f"Internal error: {exc}")
            _send(response)

        if not isinstance(name, str) or name not in _HANDLERS:
            _send(_error_msg(msg_id, _INVALID_PARAMS, f"Unknown tool: {name}"))
            return
        fut = pool.submit(work)
        try:
            fut.result(timeout=DEFAULT_TOOL_TIMEOUT_S)
        except FutureTimeoutError:
            # work() still runs in the background and will send its own
            # answer when done; this just stops the main loop waiting on it.
            # A client that already got this timeout result and later
            # receives the real one simply ignores the second message with
            # the same id -- the MCP spec allows at most one reply per id,
            # it does not forbid a late extra one being disregarded.
            _send(_result_msg(msg_id, _timeout_result(name, DEFAULT_TOOL_TIMEOUT_S)))
        return
    try:
        response = dispatch_message(msg)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        response = _error_msg(msg.get("id"), -32603, f"Internal error: {exc}")
    if response is not None:
        _send(response)


def run() -> None:
    """stdio loop: read one JSON-RPC message per line from stdin, dispatch,
    write one line per response to stdout. stdout is the protocol channel
    -- nothing else may write to it, so this redirects the real stdout to
    the protocol writer and points sys.stdout at stderr for anything else
    (a stray print, a library banner) that would otherwise corrupt the
    stream, same discipline scripts/pathiel-mcp-server.py uses."""
    global _OUT
    _OUT = sys.stdout
    sys.stdout = sys.stderr
    pool = ThreadPoolExecutor(max_workers=max(1, MAX_CONCURRENT_CALLS),
                              thread_name_prefix="research-mcp-tool")
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                _send(_error_msg(None, _PARSE_ERROR, f"Parse error: {exc}"))
                continue
            _route(msg, pool)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
    sys.stderr.flush()
    os._exit(0)
