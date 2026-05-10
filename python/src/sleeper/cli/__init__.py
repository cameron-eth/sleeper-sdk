"""Sleeper SDK CLI package.

The CLI is split into command-group modules (values, trades, send_trade,
analysis) plus shared helpers (_common). Argparse setup and dispatch live
in _main. This file exists so `sleeper.cli:main` (the pyproject entry
point) and `python -m sleeper.cli` both resolve correctly.
"""
from sleeper.cli._main import main

__all__ = ["main"]
