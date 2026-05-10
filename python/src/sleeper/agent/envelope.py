"""Standard JSON envelope for every CLI / agent-helper response.

Shape (always present):

    {
      "schema_version": "1",
      "ok": true|false,
      "command": "<cli command name or helper name>",
      "args": { ... echoed for traceability ... },
      "data": { ... command-specific payload ... },     # present when ok
      "warnings": [ "..." ],                            # zero or more
      "errors": [ {"code": "...", "message": "...", "retryable": bool}, ... ],
      "ts": "<ISO-8601 UTC>",
      "cache": { "hit": false, "age_seconds": 0 }
    }

Agents read `ok` first, branch on it. On error, every entry has a stable
`code` from `sleeper.errors.ErrorCode`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope(
    ok: bool,
    *,
    command: str,
    args: Optional[dict] = None,
    data: Any = None,
    warnings: Optional[Iterable[str]] = None,
    errors: Optional[Iterable[dict]] = None,
    cache_hit: bool = False,
    cache_age_seconds: int = 0,
) -> dict:
    """Construct the standard envelope. Prefer `ok_envelope` / `error_envelope`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok),
        "command": command,
        "args": args or {},
        "data": data,
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "ts": _now_iso(),
        "cache": {"hit": bool(cache_hit), "age_seconds": int(cache_age_seconds)},
    }


def ok_envelope(
    *,
    command: str,
    data: Any = None,
    args: Optional[dict] = None,
    warnings: Optional[Iterable[str]] = None,
    cache_hit: bool = False,
    cache_age_seconds: int = 0,
) -> dict:
    return envelope(
        True,
        command=command,
        args=args,
        data=data,
        warnings=warnings,
        cache_hit=cache_hit,
        cache_age_seconds=cache_age_seconds,
    )


def error_envelope(
    *,
    command: str,
    code: str,
    message: str,
    retryable: bool = False,
    args: Optional[dict] = None,
    details: Optional[dict] = None,
) -> dict:
    err = {
        "code": code,
        "message": message,
        "retryable": bool(retryable),
    }
    if details:
        err["details"] = details
    return envelope(
        False,
        command=command,
        args=args,
        errors=[err],
    )
