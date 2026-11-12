"""Eval: does the public-install test kit do what the other units rely on?

Outcome measured: a test that uses the kit cannot be fooled by the
operator's machine. Concretely, on this checkout:

  planted return claims caught               12/12
  clean disclaimer and rule lines passed      6/6
  subprocess loaded env file == fixture path  yes
  live key variables non-empty in subprocess  0/4
  venue connections that succeeded            0/2
  venue attempts that failed in under 2 s     2/2
  non-loopback sockets the subprocess opened  0

Threshold: all met. Offline: the venue subprocess opens sockets only to the
dead loopback proxy; a guard inside it refuses and records any other
destination, so a broken proxy setup fails this eval instead of reaching
Hyperliquid.

    .venv/bin/python -m pytest -q -s tests/test_eval_public_kit.py
    PATHIEL_EVAL_OUT=/tmp/evals .venv/bin/python -m pytest -q -s tests/test_eval_public_kit.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KEY_VARS = ("HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_MASTER_PRIVATE_KEY",
            "HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_MASTER_ADDRESS")
VENUE_URLS = ("https://api.hyperliquid.xyz/info",
              "https://api.hyperliquid-testnet.xyz/info")

PLANTED_CLAIMS = [
    "+51% per month", "ROI", "The\nedge is the thesis", "18.7% a day", "+6.03R",
    "$64 ends $487.66", "guaranteed returns", "risk-free profit", "beats the market",
    "makes money", "passive income", "profitable",
]
CLEAN_LINES = [
    "no guaranteed returns", "not risk-free",
    "There is no demonstrated edge and no return is promised",
    "refuses any bracket paying under 2R", "Sizing any account, $10 to $100k",
    "a 0.01% fee per fill",
]

_ENV_CODE = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, os.path.join(sys.argv[1], "scripts"))
    import _state_env
    loaded = _state_env.load_env_local(sys.argv[1])
    keys = json.loads(sys.argv[2])
    print(json.dumps({"loaded": loaded,
                      "non_empty": [k for k in keys if os.environ.get(k, "") != ""]}))
""")

# Default requests session, as pathiel's clients use it (trust_env on). The
# socket guard records every destination and refuses anything not loopback,
# so the venue is unreachable even if the proxies were ignored.
_VENUE_CODE = textwrap.dedent("""
    import json, socket, sys, time
    dests, refused = [], []
    _connect = socket.socket.connect
    _gai = socket.getaddrinfo
    def _loop(h):
        h = str(h)
        return h in ("localhost", "::1") or h.startswith("127.")
    def connect(self, addr, *a, **k):
        if isinstance(addr, tuple):
            dests.append([str(addr[0]), addr[1]])
            if not _loop(addr[0]):
                refused.append(str(addr[0]))
                raise ConnectionRefusedError("eval guard: not loopback")
        return _connect(self, addr, *a, **k)
    def gai(host, *a, **k):
        h = host.decode() if isinstance(host, bytes) else str(host or "")
        if h and not _loop(h):
            refused.append(h)
            raise socket.gaierror(socket.EAI_NONAME, "eval guard: not loopback")
        return _gai(host, *a, **k)
    socket.socket.connect = connect
    socket.getaddrinfo = gai
    import requests
    out = []
    for url in json.loads(sys.argv[1]):
        t0 = time.monotonic()
        try:
            r = requests.get(url, timeout=3)
            ok, err = True, f"HTTP {r.status_code}"
        except Exception as exc:
            ok, err = False, type(exc).__name__
        out.append({"url": url, "succeeded": ok, "error": err,
                    "seconds": round(time.monotonic() - t0, 3)})
    print(json.dumps({"attempts": out, "dests": dests, "refused": refused}))
""")


def _subprocess_json(code: str, args, env) -> dict:
    proc = subprocess.run([sys.executable, "-c", code, *args], env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def run(public_env, copy_lint) -> list:
    caught = sum(1 for t in PLANTED_CLAIMS if copy_lint.claim_hits(t))
    passed_clean = sum(1 for t in CLEAN_LINES if not copy_lint.claim_hits(t))

    env = public_env()
    seen = _subprocess_json(_ENV_CODE, [str(ROOT), json.dumps(KEY_VARS)], env)
    venue = _subprocess_json(_VENUE_CODE, [json.dumps(VENUE_URLS)], env)
    attempts = venue["attempts"]
    succeeded = sum(1 for a in attempts if a["succeeded"])
    fast_fail = sum(1 for a in attempts if not a["succeeded"] and a["seconds"] < 2.0)
    outside = [d for d in venue["dests"] if not str(d[0]).startswith("127.")]
    via_dead_proxy = all(d[1] == 9 for d in venue["dests"]) and bool(venue["dests"])

    n_claims, n_clean, n_urls = len(PLANTED_CLAIMS), len(CLEAN_LINES), len(VENUE_URLS)
    return [
        {"check": "planted claims caught", "got": f"{caught}/{n_claims}",
         "want": f"{n_claims}/{n_claims}", "ok": caught == n_claims},
        {"check": "clean lines passed", "got": f"{passed_clean}/{n_clean}",
         "want": f"{n_clean}/{n_clean}", "ok": passed_clean == n_clean},
        {"check": "subprocess loaded env file == fixture path",
         "got": seen["loaded"] == str(public_env.env_file), "want": True,
         "ok": seen["loaded"] == str(public_env.env_file)},
        {"check": "live key variables non-empty in subprocess",
         "got": f"{len(seen['non_empty'])}/{len(KEY_VARS)}", "want": f"0/{len(KEY_VARS)}",
         "ok": not seen["non_empty"]},
        {"check": "venue connections that succeeded", "got": f"{succeeded}/{n_urls}",
         "want": f"0/{n_urls}", "ok": succeeded == 0},
        {"check": "venue attempts that failed in under 2 s",
         "got": f"{fast_fail}/{n_urls}", "want": f"{n_urls}/{n_urls}",
         "ok": fast_fail == n_urls},
        {"check": "non-loopback destinations or lookups",
         "got": len(outside) + len(venue["refused"]), "want": 0,
         "ok": not outside and not venue["refused"]},
        {"check": "sockets went only to the dead proxy (127.0.0.1:9)",
         "got": via_dead_proxy, "want": True, "ok": via_dead_proxy},
    ]


def test_eval_public_kit(public_env, copy_lint, scorecard):
    card = scorecard("public_kit", run(public_env, copy_lint))
    failed = [r for r in card["rows"] if not r["ok"]]
    assert card["passed"] == card["total"], f"public_kit eval below threshold: {failed}"
