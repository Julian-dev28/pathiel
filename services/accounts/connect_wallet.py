"""Connect a Hyperliquid wallet to a plugin install with one signature.

The flow, in order (contracts/wallet_connect is the boundary; README.md
there has the picture):

  1. refuse on the operator's own checkout, before any file is read or
     written (contracts.wallet_connect.operator_refusal)
  2. preflight: the env file is not a checkout's own .env.local, it names
     the same network this process runs, and it holds no key yet (unless
     replace=True)
  3. take a lock next to the env file, so two flows never race on it
  4. generate an agent key on this machine (the SDK's own recipe:
     secrets.token_hex(32)); it stays in this process
  5. serve a one-shot page on 127.0.0.1, a random port and a secret path.
     The page asks the browser wallet to sign the ApproveAgent typed data
     on the wallet's current chain and POSTs {address, signature, chainId}
  6. rebuild the typed data here (the page cannot change the agent, the
     nonce or the network), recover the signer, and require it to equal the
     address the page reported
  7. post approveAgent to Hyperliquid once (no retry: a retry replays the
     nonce), then read /info until the agent shows up for that account
  8. only then write HYPERLIQUID_PRIVATE_KEY (agent key),
     HYPERLIQUID_WALLET_ADDRESS (agent) and HYPERLIQUID_MASTER_ADDRESS (the
     signer) into the env file: every other line kept, mode 600, atomic

Nothing in a result, a log line, an HTTP response or the page ever carries
the agent private key. Only its address leaves the process.

Venue calls go through a `Venue` (exchange + info). Production uses
HttpVenue on contracts.runtime.api_url(); every test passes a fake.
"""
from __future__ import annotations

import errno
import hashlib
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

import contracts.runtime as runtime
import contracts.wallet_connect as contract
from contracts.wallet_connect import ConnectResult
from services.accounts import agent_approval as aa

HOME_ENV_FILE = os.path.join("~", ".pathiel", ".env.local")
LOCK_SUFFIX = ".connect-lock"
STALE_LOCK_S = contract.PAGE_TIMEOUT_S + 300
MAX_BODY_BYTES = 4096
REQUEST_SOCKET_TIMEOUT_S = 15        # one slow or silent connection never holds the page
CONFIRM_TRIES = 5
WORKING_GRACE_S = 180                # longer than one post plus every confirm read can take
CONFIRM_WAIT_S = 1.0
VENUE_CONNECT_TIMEOUT_S = 3.0
VENUE_READ_TIMEOUT_S = 10.0
_MAX_CHAIN_ID = 2 ** 53 - 1          # what a JS number holds exactly

_CODE_ROOT = Path(__file__).resolve().parents[2]
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SIGNATURE_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")
_ASSIGN_RE = re.compile(r"^(\s*)(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


class ConnectError(Exception):
    """A refusal with a contract error code. The message is safe to show."""

    def __init__(self, code: str, message: str):
        if code not in contract.ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message


# ── the env file ─────────────────────────────────────────────────────────────

def resolve_env_file(env: Optional[Dict[str, str]] = None) -> Path:
    """PATHIEL_ENV_FILE (the launcher always sets it), else
    ~/.pathiel/.env.local. Never a checkout's own .env.local: unlike
    scripts/_state_env.py this has no checkout candidate at all."""
    e = os.environ if env is None else env
    raw = (e.get("PATHIEL_ENV_FILE") or "").strip()
    return Path(os.path.expanduser(raw or HOME_ENV_FILE))


def _unquote(value: str) -> str:
    v = re.sub(r"\s#.*$", "", value).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def env_values(text: str) -> Dict[str, str]:
    """Every assignment in an env file, first one wins (scripts/_state_env.py
    and the launcher both read the first). Comments and blank lines skipped."""
    out: Dict[str, str] = {}
    for line in text.replace("﻿", "").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ASSIGN_RE.match(line)
        if m and m.group(3) not in out:
            out[m.group(3)] = _unquote(m.group(4))
    return out


def _read_env(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def has_key(text: str) -> bool:
    """True when any HYPERLIQUID_PRIVATE_KEY line in `text` has a value."""
    for line in text.replace("﻿", "").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ASSIGN_RE.match(line)
        if m and m.group(3) == contract.ENV_PRIVATE_KEY and _unquote(m.group(4)):
            return True
    return False


def merged_env_text(old: str, values: Dict[str, str], header: str) -> str:
    """`old` with each key of `values` set: the first assignment of a key is
    rewritten in place (an `export ` prefix kept), later assignments of the
    same key are dropped (a loader that reads the last one must not see a
    stale key), and a key with no line yet is appended under `header`.
    Every other line, comments included, is kept byte for byte."""
    newline = "\r\n" if "\r\n" in old else "\n"
    lines = old.splitlines(keepends=True)
    seen: Dict[str, bool] = {}
    out: List[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        m = None if body.lstrip().startswith("#") else _ASSIGN_RE.match(body.replace("﻿", ""))
        key = m.group(3) if m else None
        if key in values:
            if seen.get(key):
                continue
            seen[key] = True
            bom = "﻿" if body.startswith("﻿") else ""
            ending = line[len(body):] or newline
            out.append(f"{bom}{m.group(1)}{m.group(2) or ''}{key}={values[key]}{ending}")
        else:
            out.append(line)
    missing = [k for k in values if not seen.get(k)]
    if missing:
        if out and not out[-1].endswith(("\n", "\r")):
            out.append(newline)
        if out:
            out.append(newline)
        out.append(f"# {header}{newline}")
        for key in missing:
            out.append(f"{key}={values[key]}{newline}")
    return "".join(out)


def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` through a mode-600 temp file in the same
    directory, fsync, rename over. A crash leaves the old file or the new
    one, never half of either."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def write_credentials(env_file: Path, private_key: str, agent_address: str,
                      master_address: str, *, replace: bool, network: str) -> bool:
    """Put the three keys into `env_file` (see merged_env_text). Refuses
    with key_exists when a key is already set and replace is False. Returns
    True when an existing key was replaced."""
    target = Path(os.path.realpath(env_file))
    old = _read_env(target)
    existed = has_key(old)
    if existed and not replace:
        raise ConnectError("key_exists", f"a key is already set in {env_file}")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = (f"Added by pathiel wallet connect on {day}: an API wallet on {network} that "
              f"{master_address} approved. It can trade, never withdraw.")
    values = {contract.ENV_PRIVATE_KEY: private_key,
              contract.ENV_WALLET_ADDRESS: agent_address,
              contract.ENV_MASTER_ADDRESS: master_address}
    _atomic_write(target, merged_env_text(old, values, header))
    return existed


# ── preflight ────────────────────────────────────────────────────────────────

def _is_checkout_env_file(real: Path) -> bool:
    """True for <checkout>/.env.local: the code root this module runs from,
    or any directory that holds a pathiel server (a maintainer checkout)."""
    if real.name != ".env.local":
        return False
    parent = real.parent
    return parent == _CODE_ROOT or (parent / "scripts" / "pathiel-mcp-server.py").exists()


def preflight(env_file: Path, *, replace: bool) -> str:
    """The checks that run before anything is generated or served. Returns
    the network. Raises ConnectError. The operator check is first and reads
    nothing."""
    refusal = contract.operator_refusal()
    if refusal is not None:
        raise ConnectError("operator_install", refusal["error"])
    real = Path(os.path.realpath(env_file))
    if _is_checkout_env_file(real):
        raise ConnectError("repo_env_file",
                           f"{env_file} is a checkout's own .env.local; wallet connect writes only "
                           "a plugin install's env file (~/.pathiel/.env.local).")
    net = runtime.network()
    text = _read_env(real)
    source = (os.environ.get(runtime.ENV_NETWORK_SOURCE) or "").strip().lower()
    if source != "environment":
        raw = env_values(text).get(runtime.ENV_NETWORK, "")
        file_net = runtime.network({runtime.ENV_INSTALL: "public", runtime.ENV_NETWORK: raw})
        if file_net != net:
            raise ConnectError(
                "network_mismatch",
                f"{env_file} says {file_net} but this pathiel process runs on {net}: it started "
                "before the file changed. Reconnect pathiel in /mcp, then connect again.")
    if has_key(text) and not replace:
        raise ConnectError(
            "key_exists",
            f"a key is already set in {env_file}. To swap it for a new one, connect again with "
            "replace. The old API wallet stays approved until you remove it in the Hyperliquid "
            "app (More, API).")
    return net


# ── the lock ─────────────────────────────────────────────────────────────────

def _lock_path(env_file: Path) -> Path:
    real = Path(os.path.realpath(env_file))
    return real.with_name(real.name + LOCK_SUFFIX)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def acquire_lock(env_file: Path, now: Callable[[], float] = time.time) -> Path:
    """Create <env file>.connect-lock exclusively. A lock whose process is
    gone, or older than the page could live, is stale and taken over once.
    Raises ConnectError('busy')."""
    path = _lock_path(env_file)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if attempt == 2:
                break
            try:
                held = json.loads(path.read_text(encoding="utf-8") or "{}")
                pid, created = int(held.get("pid", 0)), float(held.get("created", 0))
            except (OSError, ValueError, TypeError):
                pid, created = 0, 0.0
            if pid and _pid_alive(pid) and now() - created < STALE_LOCK_S:
                break
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "created": now()}, fh)
        return path
    raise ConnectError("busy", "another wallet connect is already running for this env file. "
                               "Finish or cancel it, or wait 10 minutes.")


def release_lock(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        held = json.loads(path.read_text(encoding="utf-8") or "{}")
        if int(held.get("pid", -1)) != os.getpid():
            return
        path.unlink()
    except (OSError, ValueError, TypeError):
        pass


# ── the venue ────────────────────────────────────────────────────────────────

class Venue(Protocol):
    def exchange(self, payload: Dict[str, Any]) -> Any: ...
    def info(self, payload: Dict[str, Any]) -> Any: ...


class HttpVenue:
    """Hyperliquid over HTTP. `exchange` posts exactly once with a plain
    requests.post, not the shared hl_client session: that session retries a
    POST on 502/503/504, and a retry replays the nonce (see
    scripts/approve_builder_fee.py post_once). Proxies from the environment
    are honored, as everywhere in pathiel."""

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")

    def exchange(self, payload: Dict[str, Any]) -> Any:
        import requests
        resp = requests.post(f"{self.api_url}/exchange", json=payload,
                             timeout=(VENUE_CONNECT_TIMEOUT_S, VENUE_READ_TIMEOUT_S))
        if resp.status_code != 200:
            return {"status": "err", "response": f"HTTP {resp.status_code}: {(resp.text or '')[:200]}"}
        return resp.json()

    def info(self, payload: Dict[str, Any]) -> Any:
        import requests
        resp = requests.post(f"{self.api_url}/info", json=payload,
                             timeout=(VENUE_CONNECT_TIMEOUT_S, VENUE_READ_TIMEOUT_S))
        resp.raise_for_status()
        return resp.json()


def agent_is_approved(venue: Venue, master: str, agent: str) -> bool:
    """True when Hyperliquid lists `agent` for `master`: extraAgents first,
    then userRole of the agent. Raises when both reads fail."""
    agent_lc, master_lc = agent.lower(), master.lower()
    errors = 0
    try:
        agents = venue.info({"type": "extraAgents", "user": master})
        for entry in agents if isinstance(agents, list) else []:
            if isinstance(entry, dict) and str(entry.get("address", "")).lower() == agent_lc:
                return True
    except Exception:   # noqa: BLE001 - a failed read is not "not approved"
        errors += 1
    try:
        role = venue.info({"type": "userRole", "user": agent})
        if isinstance(role, dict) and role.get("role") == "agent":
            data = role.get("data") or {}
            if str(data.get("user", "")).lower() == master_lc:
                return True
    except Exception:   # noqa: BLE001
        errors += 1
    if errors == 2:
        raise ConnectionError("both /info reads failed")
    return False


def agent_valid_until(venue: Venue, master: str, agent: str) -> Optional[str]:
    """The UTC date Hyperliquid's approval of `agent` ends, from extraAgents'
    validUntil, or None when it cannot be read. Live, 2026-11-13: an
    approveAgent with no expiry in its name came back valid for 90 days, so
    an API wallet stops trading on that date unless the user connects again."""
    try:
        for entry in venue.info({"type": "extraAgents", "user": master}) or []:
            if isinstance(entry, dict) and str(entry.get("address", "")).lower() == agent.lower():
                ms = entry.get("validUntil")
                if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
                    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
    except Exception:   # noqa: BLE001 - the date is advice, never a reason to fail
        return None
    return None


# ── the page ─────────────────────────────────────────────────────────────────

PAGE_STYLE = """
:root{color-scheme:light dark;--fg:#1d1d1f;--bg:#fafafa;--muted:#5f6368;--card:#fff;--line:#d9d9de;--red:#c5221f;--green:#137333;--blue:#1a56db}
@media (prefers-color-scheme:dark){:root{--fg:#ececf1;--bg:#141416;--muted:#a1a1aa;--card:#1e1e22;--line:#34343a;--red:#f28b82;--green:#81c995;--blue:#8ab4f8}}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:560px;margin:0 auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px}
h1{font-size:22px;margin:0 0 12px}
.net{display:inline-block;font-weight:700;padding:2px 10px;border-radius:6px;border:2px solid currentColor}
.mainnet{color:var(--red)}.testnet{color:var(--blue)}
code{font:13px/1.4 ui-monospace,Menlo,monospace;word-break:break-all}
ul{padding-left:20px}
#wallets button{display:flex;align-items:center;gap:10px;width:100%;margin:8px 0;padding:12px;font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:8px;cursor:pointer}
#wallets button:disabled{opacity:.5;cursor:default}
#wallets img{width:28px;height:28px}
#status{margin-top:16px;min-height:24px}
.ok{color:var(--green);font-weight:600}.err{color:var(--red);font-weight:600}
.muted{color:var(--muted);font-size:14px}
"""

PAGE_SCRIPT = """
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("cfg").textContent);
  var wallets = [];
  var busy = false, finished = false;
  function $(id) { return document.getElementById(id); }
  function say(text, kind) { var s = $("status"); s.textContent = text; s.className = kind || ""; }
  function render() {
    var box = $("wallets");
    box.textContent = "";
    wallets.forEach(function (w) {
      var b = document.createElement("button");
      b.type = "button";
      b.disabled = busy || finished;
      var icon = w.info.icon || "";
      if (icon.indexOf("data:image/") === 0) {
        var img = document.createElement("img"); img.src = icon; img.alt = ""; b.appendChild(img);
      }
      b.appendChild(document.createTextNode("Connect " + (w.info.name || "browser wallet")));
      b.addEventListener("click", function () { connect(w); });
      box.appendChild(b);
    });
  }
  window.addEventListener("eip6963:announceProvider", function (e) {
    var d = e.detail;
    if (!d || !d.info || !d.provider) return;
    if (wallets.some(function (w) { return w.info.uuid === d.info.uuid; })) return;
    wallets.push({ info: d.info, provider: d.provider });
    render();
  });
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  setTimeout(function () {
    if (!wallets.length && window.ethereum) {
      wallets.push({ info: { name: "browser wallet", uuid: "window.ethereum" }, provider: window.ethereum });
      render();
    }
    if (!wallets.length) {
      say("No browser wallet found. Unlock or install one (MetaMask, Rabby, Coinbase Wallet), then reload this page.", "err");
    }
  }, 700);
  async function connect(w) {
    if (busy || finished) return;
    busy = true; render();
    try {
      say("Asking your wallet for the account...");
      var accounts = await w.provider.request({ method: "eth_requestAccounts" });
      if (!accounts || !accounts.length) throw new Error("the wallet returned no account");
      var address = accounts[0];
      var chainId = cfg.defaultChainId;
      try { var hex = await w.provider.request({ method: "eth_chainId" }); var n = parseInt(hex, 16); if (n > 0) chainId = n; } catch (e) {}
      $("account").textContent = address;
      var typed = JSON.parse(JSON.stringify(cfg.typedData));
      typed.domain.chainId = chainId;
      say("Check your wallet: it asks you to sign HyperliquidTransaction:ApproveAgent for " + cfg.agentAddress + ".");
      var signature = await w.provider.request({ method: "eth_signTypedData_v4", params: [address, JSON.stringify(typed)] });
      say("Sending the approval to Hyperliquid...");
      var resp = await fetch(cfg.submitPath, {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "omit", cache: "no-store",
        body: JSON.stringify({ address: address, signature: signature, chainId: chainId })
      });
      var out = await resp.json();
      finished = true;
      if (out.ok) {
        say("Connected. " + out.agent_address + " can now trade for " + out.master_address + "." + (out.valid_until ? " Hyperliquid keeps it approved until " + out.valid_until + "; connect again before then." : "") + " Close this tab and go back to Claude Code.", "ok");
      } else {
        say("Not connected: " + (out.error || "unknown error") + " Start again from Claude Code.", "err");
      }
    } catch (e) {
      if (e && e.code === 4001) {
        say("You declined in the wallet. Nothing was signed or sent. Pick a wallet to try again.");
      } else {
        say("Error: " + ((e && e.message) || e) + ". Ask Claude for the status before you try again.", "err");
      }
    } finally {
      busy = false; render();
    }
  }
})();
"""


def _csp_hash(text: str) -> str:
    import base64
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


# Inline only, pinned by hash: no external script, style, font or frame. The
# config block is <script type="application/json">, which is data, never run.
CSP = ("default-src 'none'; "
       f"script-src {_csp_hash(PAGE_SCRIPT)}; "
       f"style-src {_csp_hash(PAGE_STYLE)}; "
       "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Connect your Hyperliquid wallet</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>Connect your Hyperliquid wallet to pathiel</h1>
<p>Network: <span class="net {net_class}">{net_label}</span></p>
<p>{net_note}</p>
<p>Your wallet signs one approval, Hyperliquid's <code>approveAgent</code>, for this new API wallet:</p>
<p><code>{agent}</code></p>
<ul>
<li>This key can trade but never withdraw.</li>
<li>pathiel never sees your main key. It stays in your wallet.</li>
<li>The new key was made on this computer and is saved only in your env file.</li>
<li>The Hyperliquid app lists it as <code>{agent_name}</code>. It replaces an earlier pathiel API wallet of that name on this account, and no other.</li>
<li>You can remove it any time in the Hyperliquid app (More, API).</li>
</ul>
<div id="wallets"></div>
<p class="muted">Your account: <code id="account">not connected yet</code></p>
<p id="status" role="status"></p>
<p class="muted">This page works once and closes in 10 minutes.</p>
</main>
<script id="cfg" type="application/json">{cfg}</script>
<script>{script}</script>
</body>
</html>
"""


def _json_for_html(obj: Any) -> str:
    return (json.dumps(obj, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def render_page(*, network: str, agent_address: str, typed_data: Dict[str, Any],
                submit_path: str) -> str:
    mainnet = network == "mainnet"
    cfg = {"network": network, "agentAddress": agent_address, "typedData": typed_data,
           "submitPath": submit_path, "defaultChainId": aa.DEFAULT_WALLET_SIGNATURE_CHAIN_ID}
    return PAGE_TEMPLATE.format(
        style=PAGE_STYLE, script=PAGE_SCRIPT, cfg=_json_for_html(cfg),
        net_class="mainnet" if mainnet else "testnet",
        net_label="MAINNET" if mainnet else "TESTNET",
        net_note=("Real money: this key will place orders with your account's real funds."
                  if mainnet else "Test funds only. Your wallet signs for the Hyperliquid testnet."),
        agent=html.escape(agent_address), agent_name=html.escape(contract.AGENT_NAME))


# ── the flow ─────────────────────────────────────────────────────────────────

class ConnectFlow:
    """One connect attempt. Construct (preflight, lock, key), start() (the
    page), then serve() blocks until done, failed, cancelled or timed out.
    status() is safe from any thread."""

    def __init__(self, env_file: Path, *, replace: bool = False, venue: Optional[Venue] = None,
                 timeout_s: float = contract.PAGE_TIMEOUT_S, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 confirm_tries: int = CONFIRM_TRIES, confirm_wait_s: float = CONFIRM_WAIT_S):
        self.env_file = Path(env_file)
        self.replace = bool(replace)
        self.network = preflight(self.env_file, replace=self.replace)
        self._hl_chain = aa.hyperliquid_chain_for(self.network)
        self._venue: Venue = venue if venue is not None else HttpVenue(runtime.api_url())
        self._clock, self._sleep = clock, sleep
        self._timeout_s = float(timeout_s)
        self._confirm_tries, self._confirm_wait_s = int(confirm_tries), float(confirm_wait_s)
        self._lock_file: Optional[Path] = acquire_lock(self.env_file, now=clock)
        try:
            from eth_account import Account
            key = "0x" + secrets.token_hex(32)      # the SDK's Exchange.approve_agent recipe
            self.agent_address: str = Account.from_key(key).address
            self.__key: Optional[str] = key
        except BaseException:
            release_lock(self._lock_file)
            raise
        self._nonce = aa.new_nonce_ms()
        self._token = secrets.token_urlsafe(32)
        self._mutex = threading.Lock()
        self._state = "waiting"
        self._result: ConnectResult = {}
        self._finished = threading.Event()
        self._page_opened = False
        self._httpd: Optional[ThreadingHTTPServer] = None
        self.port = 0
        self._deadline = 0.0

    def __repr__(self) -> str:   # never the key
        return f"<ConnectFlow {self.network} agent={self.agent_address} state={self._state}>"

    # ── public surface ──
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self._token}/"

    @property
    def state(self) -> str:
        with self._mutex:
            return self._state

    def typed_data_template(self) -> Dict[str, Any]:
        """What the page signs, before it sets domain.chainId to the wallet's chain."""
        return aa.build_typed_data(self.agent_address, self._nonce, contract.AGENT_NAME,
                                   hyperliquid_chain=self._hl_chain)

    def start(self) -> str:
        try:
            self._httpd = _make_server(self)
        except OSError as e:
            self._finish_failed("server_failed", f"the local page could not start ({e.strerror or e})")
            self._cleanup()
            raise ConnectError("server_failed", self._result["error"]) from None
        self.port = self._httpd.server_address[1]
        self._deadline = self._clock() + self._timeout_s
        return self.url

    def serve(self) -> ConnectResult:
        """Handle requests until the flow ends. Then close the page, release
        the lock and drop the key from memory."""
        try:
            while True:
                with self._mutex:
                    if self._state in contract.FINAL_STATES:
                        break
                    if self._state == "waiting" and self._clock() >= self._deadline:
                        self._set_final(self._failure(
                            "timeout", "nobody signed within 10 minutes; nothing was sent or "
                                       "written. Start again when ready."))
                        break
                if self._httpd is None:
                    # Closed while a submission is in flight (Ctrl-C on the
                    # CLI during the venue call): let it finish, so the
                    # result is the real one and the write is never cut off.
                    self._finished.wait(timeout=WORKING_GRACE_S)
                    break
                self._httpd.handle_request()
        finally:
            self._cleanup()
        return self.result()

    def cancel(self) -> bool:
        with self._mutex:
            if self._state != "waiting":
                return False
            self._set_final(self._failure("cancelled", "cancelled; nothing was sent or written",
                                          state="cancelled"))
            return True

    def result(self) -> ConnectResult:
        with self._mutex:
            return dict(self._result) if self._result else self._status_locked()

    def status(self) -> ConnectResult:
        with self._mutex:
            if self._state in contract.FINAL_STATES:
                return dict(self._result)
            return self._status_locked()

    # ── internals ──
    def _status_locked(self) -> ConnectResult:
        out: ConnectResult = {"ok": True, "state": self._state, "network": self.network,
                              "agent_address": self.agent_address}
        if self._state == "waiting":
            out["url"] = self.url
            out["expires_in_s"] = max(0, int(self._deadline - self._clock()))
            out["next"] = ("Open the URL in the browser that has your wallet, pick the wallet and "
                           "approve. Then call wallet_connect with status=true."
                           if not self._page_opened else
                           "The page is open: approve the request in your wallet, then call "
                           "wallet_connect with status=true.")
        else:
            out["next"] = "Hyperliquid is confirming the approval; ask again in a few seconds."
        return out

    def _failure(self, code: str, error: str, state: str = "failed") -> ConnectResult:
        return {"ok": False, "state": state, "network": self.network, "code": code,
                "error": error, "agent_address": self.agent_address,
                "next": "Nothing was written to your env file. Call wallet_connect again to retry."}

    def _set_final(self, result: ConnectResult) -> None:
        """Caller holds self._mutex. The state comes from the result."""
        self._result = dict(result)
        self._state = self._result["state"]
        self._finished.set()

    def _finish_failed(self, code: str, error: str) -> None:
        with self._mutex:
            self._set_final(self._failure(code, error))

    def _cleanup(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.server_close()
            except OSError:
                pass
            self._httpd = None
        release_lock(self._lock_file)
        self._lock_file = None
        self.__key = None

    def _claim(self) -> Optional[str]:
        """Take the one submission. None when taken, else why not."""
        with self._mutex:
            if self._state != "waiting":
                return "this page was already used"
            if self._clock() >= self._deadline:
                return "this page expired"
            self._state = "working"
            return None

    def _process(self, body: Dict[str, Any]) -> ConnectResult:
        """Verify, submit, confirm, write. Runs in the request thread with
        the flow claimed. Returns the final result; never raises."""
        try:
            return self._process_inner(body)
        except ConnectError as e:
            return self._failure(e.code, e.message)
        except Exception as e:   # noqa: BLE001 - the message names the type only
            return self._failure("venue_unreachable", f"unexpected error ({type(e).__name__}); "
                                                      "nothing was written")

    def _process_inner(self, body: Dict[str, Any]) -> ConnectResult:
        address = str(body.get("address") or "")
        signature = str(body.get("signature") or "")
        chain_id = body.get("chainId")
        if not _ADDRESS_RE.match(address):
            raise ConnectError("bad_request", "the page sent no valid account address")
        if not _SIGNATURE_RE.match(signature):
            raise ConnectError("bad_request", "the page sent no valid signature")
        if isinstance(chain_id, bool) or not isinstance(chain_id, int) or not 0 < chain_id <= _MAX_CHAIN_ID:
            raise ConnectError("bad_request", "the page sent no valid chain id")
        typed = aa.build_typed_data(self.agent_address, self._nonce, contract.AGENT_NAME,
                                    signature_chain_id=chain_id, hyperliquid_chain=self._hl_chain)
        try:
            signer = aa.recover_signer(typed, signature)
        except Exception:   # noqa: BLE001
            raise ConnectError("bad_request", "the signature could not be read") from None
        if signer.lower() != address.lower():
            raise ConnectError("signer_mismatch",
                               f"the signature is from {signer}, not the account the page "
                               f"reported ({address}). Nothing was sent.")
        if signer.lower() == self.agent_address.lower():
            raise ConnectError("bad_request", "the agent cannot approve itself")
        master = signer
        if not self.replace and has_key(_read_env(Path(os.path.realpath(self.env_file)))):
            raise ConnectError("key_exists", f"a key was added to {self.env_file} while the page "
                                             "was open. Nothing was sent.")
        payload = aa.build_exchange_payload(self.agent_address, contract.AGENT_NAME, self._nonce,
                                            signature, signature_chain_id=chain_id,
                                            hyperliquid_chain=self._hl_chain)
        reply: Any = None
        post_failed = False
        try:
            reply = self._venue.exchange(payload)
        except Exception:   # noqa: BLE001 - the approval may still have landed; the read decides
            post_failed = True
        confirmed, read_failed = False, False
        for attempt in range(self._confirm_tries):
            try:
                confirmed = agent_is_approved(self._venue, master, self.agent_address)
                read_failed = False
            except Exception:   # noqa: BLE001
                read_failed = True
            if confirmed:
                break
            if isinstance(reply, dict) and reply.get("status") == "err" and not read_failed:
                break                                   # refused, and the read agrees
            if attempt + 1 < self._confirm_tries:
                self._sleep(self._confirm_wait_s)
        if not confirmed:
            if isinstance(reply, dict) and reply.get("status") == "err":
                why = str(reply.get("response", ""))[:300]
                raise ConnectError("venue_refused", f"Hyperliquid refused the approval: {why}")
            if post_failed or read_failed:
                raise ConnectError("venue_unreachable",
                                   "Hyperliquid could not be reached to send or confirm the "
                                   "approval. The new key was not saved; if Hyperliquid did "
                                   "approve it, it is unusable and connecting again replaces it.")
            raise ConnectError("not_confirmed",
                               "Hyperliquid did not list the new API wallet after the approval. "
                               "The key was not saved. Check the account in the Hyperliquid app "
                               "and connect again.")
        if not self.__key:
            raise ConnectError("write_failed", "the key is gone from memory; connect again")
        try:
            replaced = write_credentials(self.env_file, self.__key, self.agent_address, master,
                                         replace=self.replace, network=self.network)
        except OSError as e:
            raise ConnectError("write_failed",
                               f"Hyperliquid approved {self.agent_address}, but {self.env_file} "
                               f"could not be written ({errno.errorcode.get(e.errno, 'error')}). "
                               "That key is lost and harmless; connect again.") from None
        valid_until = agent_valid_until(self._venue, master, self.agent_address)
        note = contract.RECONNECT_NOTE
        if valid_until:
            note += (f" Hyperliquid keeps this API wallet approved until {valid_until} (UTC); "
                     "connect again before then to keep trading.")
        return {"ok": True, "state": "done", "network": self.network,
                "agent_address": self.agent_address, "master_address": master,
                "env_file": str(self.env_file), "replaced": replaced,
                "valid_until": valid_until, "next": note}


# ── HTTP ─────────────────────────────────────────────────────────────────────

# What the page gets back: no env file path, nothing it does not show.
_PAGE_KEYS = ("ok", "state", "network", "agent_address", "master_address", "valid_until",
              "code", "error")


def _make_server(flow: ConnectFlow) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pathiel"
        sys_version = ""
        timeout = REQUEST_SOCKET_TIMEOUT_S

        def log_message(self, format: str, *args: Any) -> None:   # noqa: A002 - stdlib name
            return                                                 # the path holds the token

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _text(self, code: int, text: str) -> None:
            self._send(code, (text + "\n").encode(), "text/plain; charset=utf-8")

        def _json(self, code: int, obj: Dict[str, Any]) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _host_ok(self) -> Optional[str]:
            host = self.headers.get("Host", "")
            allowed = (f"127.0.0.1:{flow.port}", f"localhost:{flow.port}")
            return host if host in allowed else None

        def do_GET(self) -> None:   # noqa: N802 - stdlib name
            if self._host_ok() is None:
                return self._text(421, "wrong host")
            if self.path not in (f"/{flow._token}/", f"/{flow._token}"):
                return self._text(404, "not found")
            if flow.state != "waiting":
                return self._text(410, "this page was already used or has expired; "
                                       "start again from Claude Code")
            flow._page_opened = True
            page = render_page(network=flow.network, agent_address=flow.agent_address,
                               typed_data=flow.typed_data_template(),
                               submit_path=f"/{flow._token}/submit")
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                       {"Content-Security-Policy": CSP})

        def do_POST(self) -> None:   # noqa: N802
            host = self._host_ok()
            if host is None:
                return self._text(421, "wrong host")
            if self.path != f"/{flow._token}/submit":
                return self._text(404, "not found")
            if self.headers.get("Origin", "") != f"http://{host}":
                return self._json(403, {"ok": False, "error": "wrong origin"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._json(411, {"ok": False, "error": "length required"})
            if length < 0 or length > MAX_BODY_BYTES:
                return self._json(413, {"ok": False, "error": "too large"})
            why = flow._claim()
            if why is not None:
                return self._json(409, {"ok": False, "error": why})
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError, OSError):
                result = flow._failure("bad_request", "the page sent something that is not JSON")
            else:
                result = flow._process(body)
            page_view = {k: result[k] for k in _PAGE_KEYS if k in result}
            try:
                self._json(200, page_view)
            except OSError:
                pass                                  # the tab closed: the result still stands
            finally:
                with flow._mutex:
                    flow._set_final(result)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    httpd.timeout = 0.1
    return httpd


# ── MCP tool ─────────────────────────────────────────────────────────────────

_CURRENT: Optional[ConnectFlow] = None
_CURRENT_MUTEX = threading.Lock()


def open_quietly(url: str) -> bool:
    """Open `url` in the default browser without a byte on this process's
    stdout (the MCP stream): the opener's stdio goes to /dev/null."""
    try:
        if sys.platform == "darwin":
            cmd = ["open", url]
        elif os.name == "nt":
            os.startfile(url)   # type: ignore[attr-defined]  # noqa: S606
            return True
        else:
            cmd = ["xdg-open", url]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except (OSError, ValueError):
        return False


# Replaced in tests; the MCP tool and the CLI share every other step.
_OPENER: Callable[[str], bool] = open_quietly
_VENUE_FACTORY: Optional[Callable[[], Venue]] = None


def _flag(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "y")


def start_background(env_file: Path, *, replace: bool, open_browser: bool) -> ConnectResult:
    """Start a flow in a daemon thread and return its first status. One flow
    per process: a second start while one waits returns that one."""
    global _CURRENT
    with _CURRENT_MUTEX:
        flow = _CURRENT
        if flow is not None and flow.state in ("waiting", "working"):
            out = flow.status()
            out["next"] = ("A connect is already in progress. " + out.get("next", "")).strip()
            return out
        try:
            venue = _VENUE_FACTORY() if _VENUE_FACTORY is not None else None
            flow = ConnectFlow(env_file, replace=replace, venue=venue)
            flow.start()
        except ConnectError as e:
            return {"ok": False, "state": "failed", "code": e.code, "error": e.message,
                    "network": runtime.network(), "next": _next_for(e.code)}
        threading.Thread(target=flow.serve, name="pathiel-wallet-connect", daemon=True).start()
        _CURRENT = flow
    if open_browser:
        _OPENER(flow.url)
    return flow.status()


def _next_for(code: str) -> str:
    return {
        "key_exists": "Ask the user whether to replace the key that is set; if yes, call "
                      "wallet_connect with replace=true.",
        "network_mismatch": contract.RECONNECT_NOTE,
        "busy": "Finish the open page, or call wallet_connect with cancel=true in the session "
                "that started it.",
    }.get(code, "Fix what the error names, then call wallet_connect again.")


def handle_wallet_connect(params: Dict[str, Any]) -> str:
    """The MCP tool. Never raises; never returns the key."""
    try:
        refusal = contract.operator_refusal()
        if refusal is not None:
            return json.dumps(refusal)
        params = params if isinstance(params, dict) else {}
        flow = _CURRENT
        if _flag(params.get("status")):
            if flow is None:
                return json.dumps({"ok": True, "state": "none", "network": runtime.network(),
                                   "next": "No connect has started. Call wallet_connect to start one."})
            return json.dumps(flow.status())
        if _flag(params.get("cancel")):
            if flow is None or not flow.cancel():
                return json.dumps({**(flow.status() if flow else {"ok": True, "state": "none"}),
                                   "next": "Nothing waiting to cancel."})
            return json.dumps(flow.status())
        open_browser = params.get("open_browser") is None or _flag(params.get("open_browser"))
        out = start_background(resolve_env_file(), replace=_flag(params.get("replace")),
                               open_browser=open_browser)
        return json.dumps(out)
    except Exception as e:   # noqa: BLE001 - the type only; a message could carry anything
        return json.dumps({"ok": False, "state": "failed", "code": "server_failed",
                           "error": f"wallet connect failed ({type(e).__name__})"})


def load_network_from_env_file(env_file: Path) -> None:
    """For a run outside the launcher: take PATHIEL_NETWORK from the env file
    when the process has none, as the launcher would. Reads only that line."""
    if os.environ.get(runtime.ENV_NETWORK):
        return
    raw = env_values(_read_env(Path(os.path.realpath(env_file)))).get(runtime.ENV_NETWORK, "")
    if raw:
        os.environ[runtime.ENV_NETWORK] = raw
        os.environ.setdefault(runtime.ENV_NETWORK_SOURCE, "env file")


def reset_for_tests() -> None:
    global _CURRENT
    with _CURRENT_MUTEX:
        flow = _CURRENT
        _CURRENT = None
    if flow is not None:
        flow.cancel()


__all__ = [
    "ConnectError", "ConnectFlow", "HttpVenue", "Venue", "CSP", "acquire_lock",
    "agent_is_approved", "env_values", "handle_wallet_connect", "has_key",
    "load_network_from_env_file", "merged_env_text", "open_quietly", "preflight",
    "release_lock", "render_page", "resolve_env_file", "start_background", "write_credentials",
]
