#!/usr/bin/env python3
"""Move the master key out of the env file into the macOS Keychain.

HYPERLIQUID_MASTER_PRIVATE_KEY is the one key that can withdraw. Since
2026-11-15 no long-running pathiel process loads it, but while it sits in
.env.local anything that can read the file can read it. This moves it into
the login Keychain (built in, encrypted at rest) and removes it from the file:

  1. read the key from the env file pathiel uses (_state_env precedence);
  2. store it as generic password "pathiel-master-key", sent to `security -i`
     on stdin, so it never appears in a process list or shell history;
  3. read it back and compare, in memory;
  4. only then remove the line from the env file (same mode, atomic replace).

It prints names and paths, never the key. Run it yourself, in your terminal:

    .venv/bin/python scripts/master_key_to_keychain.py            # do it
    .venv/bin/python scripts/master_key_to_keychain.py --dry-run  # say what it would do

Then pass the key to the one script that needs it, for one run:

    HYPERLIQUID_MASTER_PRIVATE_KEY="$(security find-generic-password -a "$USER" -s pathiel-master-key -w)" \\
      .venv/bin/python scripts/treasury.py
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _state_env  # noqa: E402

KEY_NAME = "HYPERLIQUID_MASTER_PRIVATE_KEY"
SERVICE = "pathiel-master-key"
KEY_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


def split_env(text: str, name: str = KEY_NAME) -> Tuple[Optional[str], str]:
    """(value of `name` or None, the text without its lines). Pure. Keeps every
    other line, comment and blank exactly; drops every `name=` line."""
    value: Optional[str] = None
    kept: List[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, val = stripped.partition("=")
            if key.strip() == name:
                if val.strip():
                    value = val.strip().strip('"').strip("'")
                continue
        kept.append(line)
    return value, "".join(kept)


def _quote(arg: str) -> str:
    """Double-quote one argument for `security -i`'s command line parser."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def keychain_store(account: str, service: str, secret: str) -> None:
    """Add or update (-U) a generic password. The command, secret included,
    goes to `security -i` on stdin: never in argv, so never in `ps`."""
    cmd = f"add-generic-password -U -a {_quote(account)} -s {_quote(service)} -w {_quote(secret)}\n"
    proc = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or "error" in (proc.stderr or "").lower():
        raise RuntimeError(f"security add-generic-password failed (exit {proc.returncode})")


def keychain_read(account: str, service: str) -> Optional[str]:
    proc = subprocess.run(["security", "find-generic-password", "-a", account, "-s", service, "-w"],
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else None


def write_atomic(path: str, text: str) -> None:
    """Replace path with text, keeping its permission bits."""
    mode = os.stat(path).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".env.local.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def move(env_path: str, account: str, service: str, dry_run: bool = False,
         store: Callable[[str, str, str], None] = keychain_store,
         read: Callable[[str, str], Optional[str]] = keychain_read) -> Tuple[int, str]:
    """(exit code, one-line report). The key never appears in the report."""
    with open(env_path) as fh:
        text = fh.read()
    value, rest = split_env(text)
    if value is None:
        there = read(account, service) is not None
        return 0, (f"{KEY_NAME} is not in {env_path}; nothing to move."
                   + (f" The Keychain holds {service}." if there else ""))
    if not KEY_RE.match(value):
        return 2, f"{KEY_NAME} in {env_path} is not 64 hex characters; left as it is."
    if dry_run:
        return 0, f"would store {KEY_NAME} in the Keychain as {service} and remove it from {env_path}"
    store(account, service, value)
    if read(account, service) != value:
        return 1, f"the Keychain copy did not read back the same; {env_path} left unchanged."
    write_atomic(env_path, rest)
    return 0, (f"stored in the login Keychain as {service} (account {account}) and removed from "
               f"{env_path}. Use it for one run: "
               f'{KEY_NAME}="$(security find-generic-password -a "$USER" -s {service} -w)" '
               ".venv/bin/python scripts/treasury.py")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    ap.add_argument("--env-file", help="env file to read (default: the one pathiel loads)")
    ap.add_argument("--service", default=SERVICE, help=f"Keychain item name (default {SERVICE})")
    a = ap.parse_args(argv)
    if sys.platform != "darwin" or not shutil.which("security"):
        print("macOS only: the Keychain is read with the `security` tool.")
        return 2
    env_path = a.env_file or next((p for p in _state_env.env_file_candidates() if os.path.isfile(p)), None)
    if not env_path:
        print("no env file found (.env.local or ~/.pathiel/.env.local)")
        return 2
    code, report = move(env_path, getpass.getuser(), a.service, dry_run=a.dry_run)
    print(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
