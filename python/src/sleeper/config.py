"""Zero-dependency ``.env`` loader.

The SDK reads secrets (notably ``SLEEPER_TOKEN``) from the process
environment. To make local use ergonomic without pulling in a dependency
like python-dotenv, this module loads a ``.env`` file into ``os.environ``
on demand.

Lookup order (first file found wins):
    1. ``$SLEEPER_ENV_FILE`` if set (explicit override)
    2. ``.env`` walking up from the current working directory to the
       filesystem root (nearest one wins)
    3. ``~/.sleeper-sdk/.env``

Real environment variables always take precedence: a key already present
in ``os.environ`` is never overwritten by the file. Values may be quoted
(``KEY="eyJ..."``) and lines starting with ``#`` are ignored.

The token never touches disk via this module — it only *reads* a file the
user created. Keep ``.env`` gitignored (it already is).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_loaded = False


def _find_env_file() -> Optional[Path]:
    override = os.environ.get("SLEEPER_ENV_FILE")
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None

    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate

    home_env = Path.home() / ".sleeper-sdk" / ".env"
    if home_env.is_file():
        return home_env

    return None


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # Support an optional leading `export `
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip a single matching pair of surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_env(*, override: bool = False) -> bool:
    """Load the nearest ``.env`` into ``os.environ``.

    Idempotent: subsequent calls are no-ops unless ``override`` is True.
    Existing environment variables win unless ``override`` is True.

    Returns True if a file was found and applied, else False.
    """
    global _loaded
    if _loaded and not override:
        return False

    env_file = _find_env_file()
    if env_file is None:
        _loaded = True
        return False

    try:
        pairs = _parse(env_file.read_text(encoding="utf-8"))
    except OSError:
        _loaded = True
        return False

    for key, value in pairs.items():
        if override or key not in os.environ:
            os.environ[key] = value

    _loaded = True
    return True
