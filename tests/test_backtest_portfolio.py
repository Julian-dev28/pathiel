"""Regression tests for the portfolio backtest command-line entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backtest_portfolio.py"


def test_help_starts_without_retired_rotation_module() -> None:
    """The CLI must not import the capital-rotation feature removed in f967e6b."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--max-concurrent" in result.stdout
    assert "--sweep-concurrent" in result.stdout
    assert "--rotate" not in result.stdout

