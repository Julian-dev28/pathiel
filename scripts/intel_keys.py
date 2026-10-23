#!/usr/bin/env python3
"""Which free API keys services/intel's sources need, which are set, and
where to get each. Run this before asking "why did a desk's HARD DATA
section come back thin" — a missing key skips its source cleanly (see
sources/eia.py, sources/fred.py, sources/policy.py's congress.gov path)
and shows up in IntelPacket.sources_failed / DeskBrief.stale as
"<key>_missing", but this script is the one place that lists every key
services/intel can use, whether it's set, and the free registration URL
for the ones that are not.

    .venv/bin/python scripts/intel_keys.py           # human-readable report
    .venv/bin/python scripts/intel_keys.py --check    # exit 1 if any key is missing

Reads the same `.env.local` at the repo root that
services/intel/env.py's `_load_dotenv` reads (a key already exported in
the real shell environment always wins — `.env.local` only fills gaps),
so this script sees exactly what a real `build_packet` call would see.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = ROOT / ".env.local"


class KeyInfo(NamedTuple):
    env_var: str
    used_by: str
    register_url: str
    required: bool  # False = the source works without it (no key needed at all)


# Every env var a services/intel source reads, in the order a cold `build_packet`
# would need them. Keep this in sync with sources/*.py's own `os.environ.get(...)`
# calls — there is no other single place that lists them all.
KEYS = (
    KeyInfo("EIA_API_KEY", "sources/eia.py (EIA weekly crude stocks + nat gas storage)",
            "https://www.eia.gov/opendata/register.php", required=True),
    KeyInfo("FRED_API_KEY", "sources/fred.py (DGS10/DFII10/T10YIE/DTWEXBGS)",
            "https://fred.stlouisfed.org/docs/api/api_key.html", required=True),
    KeyInfo("CONGRESS_API_KEY", "sources/policy.py (congress.gov bill search)",
            "https://api.congress.gov/sign-up/", required=True),
)

# Sources that need NO key at all, listed here so "intel_keys.py says nothing
# about X" never reads as "X might need a key I haven't set" — it is just not
# in the table because it genuinely needs nothing.
NO_KEY_NEEDED = (
    "sources/venue.py (Hyperliquid /info — public, no auth)",
    "sources/cboe.py (cdn.cboe.com daily CSVs — public, no auth)",
    "sources/edgar.py (SEC EDGAR — public, asks only for a contact User-Agent)",
    "sources/positioning.py (CFTC COT Socrata API — public, no auth)",
    "sources/news.py, sources/catalysts.py (GDELT/Google News/RSS/NHC/DeFiLlama — public)",
)


def _load_dotenv_into(environ: Dict[str, str]) -> None:
    """Mirrors services/intel/env.py's `_load_dotenv`: fills gaps in
    `environ`, never overrides a key already set. Re-implemented rather
    than imported so this script has zero dependency on services/intel's
    internals and keeps working even if that module's private helper is
    renamed or removed."""
    if not ENV_LOCAL.exists():
        return
    try:
        for line in ENV_LOCAL.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def key_status(environ: Dict[str, str]) -> Dict[str, bool]:
    """{env_var: is_set} — pure function of an environ mapping, so tests
    never have to touch the real process environment or `.env.local`."""
    merged = dict(environ)
    _load_dotenv_into(merged)
    return {k.env_var: bool(merged.get(k.env_var)) for k in KEYS}


def render_report(status: Dict[str, bool]) -> str:
    lines = ["services/intel API keys", "=" * 24, ""]
    for k in KEYS:
        mark = "SET    " if status[k.env_var] else "MISSING"
        lines.append(f"[{mark}] {k.env_var:<18} used by {k.used_by}")
        if not status[k.env_var]:
            lines.append(f"           free, register at: {k.register_url}")
    lines.append("")
    lines.append("No key needed (public endpoints):")
    for s in NO_KEY_NEEDED:
        lines.append(f"  - {s}")
    missing = [k.env_var for k in KEYS if not status[k.env_var]]
    lines.append("")
    if missing:
        lines.append(f"{len(missing)} of {len(KEYS)} key(s) missing: {', '.join(missing)}")
        lines.append(f"Set them in {ENV_LOCAL} (KEY=value, one per line) or export them "
                      f"in the shell before running the desk loop.")
    else:
        lines.append(f"All {len(KEYS)} keys are set.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any key is missing, print nothing on success")
    args = ap.parse_args()

    status = key_status(dict(os.environ))
    missing = [k.env_var for k in KEYS if not status[k.env_var]]

    if args.check:
        if missing:
            print(f"missing: {', '.join(missing)}")
        return 1 if missing else 0

    print(render_report(status))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
