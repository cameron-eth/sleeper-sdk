"""CLI smoke tests — ensure every subcommand registers and `--help` works.

These are pure import / argparse tests; no network, no auth, no Sleeper API
calls. They guard against regressions where someone removes a command,
forgets to wire a dispatch case, or breaks argparse with a typo.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

# Subcommands the public CLI must always expose.
EXPECTED_SUBCOMMANDS = [
    "market-value",
    "league-values",
    "roster-rank",
    "trade-check",
    "trending",
    "buy-sell",
    "ktc-trend",
    "pe-ratio",
    "picks",
    "suggest-trades",
    "find-trades",
    "send-trade",
    "gm-mode",
    "proposed-trades",   # added May 2026
    "trade-partners",    # added May 2026
]


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke `python -m sleeper.cli <args>` and return the completed process."""
    return subprocess.run(
        [sys.executable, "-m", "sleeper.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_help_exits_zero():
    """`sleeper --help` must exit cleanly and list every subcommand."""
    result = _run_cli("--help")
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    for cmd in EXPECTED_SUBCOMMANDS:
        assert cmd in result.stdout, f"Subcommand `{cmd}` missing from --help output"


@pytest.mark.parametrize("cmd", EXPECTED_SUBCOMMANDS)
def test_subcommand_help_exits_zero(cmd: str):
    """Each subcommand's `--help` must exit cleanly without imports failing."""
    result = _run_cli(cmd, "--help")
    assert result.returncode == 0, (
        f"{cmd} --help failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Help text should mention the command name somewhere
    assert cmd in result.stdout or cmd in result.stderr


def test_no_args_prints_help_nonzero():
    """Calling the CLI with no command should show help and exit non-zero."""
    result = _run_cli()
    assert result.returncode != 0
    # argparse prints help to stdout when explicitly called
    assert "usage" in (result.stdout + result.stderr).lower()


def test_proposed_trades_has_status_and_user_filters():
    """The new proposed-trades subcommand must expose --status and --user."""
    result = _run_cli("proposed-trades", "--help")
    assert result.returncode == 0
    assert "--status" in result.stdout
    assert "--user" in result.stdout
    assert "--limit" in result.stdout


def test_find_trades_has_mode_options():
    """find-trades must expose its three modes."""
    result = _run_cli("find-trades", "--help")
    assert result.returncode == 0
    for mode in ("normal", "upgrade", "downtiering"):
        assert mode in result.stdout
