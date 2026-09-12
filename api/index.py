"""Vercel entrypoint: the pathia dashboard, serving synthetic data.

This is a demo deployment. It runs the real dashboard code — the same
`pathia/dashboard.py` the live system serves — against a generated session log,
positions snapshot and agent config. No exchange connection, no credentials, no
trading loop.

Why the real code rather than a static mock: the dashboard's value is in what it
computes (drawdown, fee drag, the funnel, the book league table), and a static
mock demonstrates none of it. Feeding invented inputs to the production
renderers shows the actual product.

Three things this file exists to guarantee, in order of how badly they would
hurt if they were wrong:

  1. **No credentials, ever.** `PATHIA_PUBLIC_DASHBOARD=1` deliberately opens
     every account endpoint to anonymous readers — that is what makes the demo
     viewable. It is also exactly the flag the 2026-09-04 audit called a
     privacy leak when there is a real account behind it. So this module
     refuses to boot if any exchange credential is present in the environment.
     A demo that can reach the live account is not a demo.
  2. **Read-only.** `PATHIA_DASHBOARD_READONLY=1` makes every POST a 403, so
     the STOP TRADING button and the operator token field are inert.
  3. **Env before import.** `pathia.dashboard` resolves the session-log,
     snapshot and config paths at module scope, and `pathia.client.universe`
     mkdirs a cache under `Path.home()` at import — a hard crash on a
     read-only filesystem. Everything has to be set before the import at the
     bottom, which is why the imports are not at the top.
"""

from __future__ import annotations

import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── 1. refuse to run anywhere near a real account ────────────────────────────
#
# Names taken from pathia/client/exchange.py and .env.local.example. Presence
# is enough to abort: this process must have no path to signing anything.
_FORBIDDEN = (
    "HYPERLIQUID_PRIVATE_KEY",
    "PRIVATE_KEY_HEX",
    "HYPERLIQUID_ACCOUNT_ADDRESS",
    "HL_ACCOUNT_ADDRESS",
    "PATHIA_OPERATOR_TOKEN",
)
_present = [k for k in _FORBIDDEN if os.environ.get(k)]

# `pathia/server.py` loads `.env.local` into the environment on import, before
# anything here gets a second look. Checking os.environ alone would therefore
# pass on a developer box and then boot the demo against real keys, so the file
# itself counts as a credential.
if os.path.exists(os.path.join(_REPO_ROOT, ".env.local")):
    _present.append(".env.local (file present; server.py loads it on import)")

if _present:
    raise RuntimeError(
        "demo deployment refuses to boot with exchange credentials in the "
        f"environment: {', '.join(_present)}. This deployment serves a public, "
        "unauthenticated dashboard; with a real key present it would be a "
        "window onto the live account. Remove these from the Vercel project "
        "environment and redeploy."
    )

# ── 2. give the process a writable home ──────────────────────────────────────
#
# `pathia/client/universe.py` calls `Path.home() / ".pathia" / "universe_cache"`
# and mkdirs it AT IMPORT, which is a hard crash on Vercel: everything outside
# /tmp is read-only, and the traceback is `OSError: [Errno 30] Read-only file
# system: '/home/sbx_user1051'` before a single route is registered.
#
# Two other modules read the same home — `server.py`'s PID file and
# `session_log.py`'s default path — so pointing HOME at /tmp fixes all three at
# once and needs no change to the trading code. Set before any pathia import,
# because `Path.home()` is resolved at module scope.
_HOME = os.path.join(tempfile.gettempdir(), "pathia-home")
os.makedirs(_HOME, exist_ok=True)
os.environ["HOME"] = _HOME

# ── 3. generate the demo data ────────────────────────────────────────────────
#
# /tmp is the only writable path on Vercel, and the data has to be minted at
# boot regardless: the dashboard calls a heartbeat older than 300s "offline"
# and a snapshot older than 120s missing, so committed fixtures would show a
# dead bot. Regenerated on every cold start, deterministic within one.
from services.demo.generator import materialize  # noqa: E402

_DATA_DIR = os.path.join(tempfile.gettempdir(), "pathia-demo")
for _key, _path in materialize(_DATA_DIR).items():
    os.environ[_key] = _path

# ── 4. open the read APIs, close every write ─────────────────────────────────
os.environ["PATHIA_PUBLIC_DASHBOARD"] = "1"
os.environ["PATHIA_DASHBOARD_READONLY"] = "1"
# services/auth hands the operator role to the first account that signs in,
# which is the right bootstrap for a private box with a durable database. Here
# the database is SQLite in /tmp on an ephemeral instance, so the users table is
# empty again after every cold start and every visitor is "the first". Nothing
# follows from it while the read-only flag above holds, and that is exactly why
# it should not be the only thing standing between a stranger and the kill
# switch.
os.environ["PATHIA_AUTH_NO_BOOTSTRAP_OPERATOR"] = "1"

# The domain inside the SIWE message, which services/auth deliberately takes
# from config rather than the Host header (an attacker controls Host, so a
# domain derived from it asserts nothing). Its default is "localhost:8000", so
# an unset value here made the wallet display
#
#     localhost:8000 wants you to sign in with your Ethereum account
#
# on a page served from vercel.app. That is precisely the shape of a phishing
# prompt, and a user who signs it anyway has been taught to ignore the one
# field that makes SIWE worth anything. Vercel supplies the real host.
_host = (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
         or os.environ.get("VERCEL_URL"))
if _host:
    os.environ.setdefault("PATHIA_AUTH_DOMAIN", _host)
    os.environ.setdefault("PATHIA_AUTH_URI", f"https://{_host}")
# The loop is not running here and never will be; say so rather than letting a
# background task get scheduled by the server's lifespan hook.
os.environ.setdefault("PATHIA_DISABLE_TRADING_LOOP", "1")

from pathia.server import app  # noqa: E402,F401
