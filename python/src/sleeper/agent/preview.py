"""Preview / execute pattern for safe agent writes.

Flow:
    1. Agent runs `... --preview`. SDK builds the full payload it would send,
       returns it with a `preview_id` and `expires_at`.
    2. Agent shows the preview to the human (or applies its own rules).
    3. Agent runs `sleeper execute <preview_id>` (or calls `consume_preview`
       in Python). SDK pulls the cached payload and fires the actual write.

Why disk-cached: lets a separate process (the human's browser, a Telegram
bot callback, a queued job) execute by id without re-fetching state and
without race conditions.

Storage: ~/.sleeper-sdk/previews/<preview_id>.json (10 minute TTL).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sleeper.errors import ErrorCode, SleeperPreviewError

DEFAULT_TTL_SECONDS = 600   # 10 minutes
PREVIEW_DIR = Path(
    os.environ.get("SLEEPER_PREVIEW_DIR")
    or (Path.home() / ".sleeper-sdk" / "previews")
)


@dataclass
class Preview:
    preview_id: str
    command: str
    payload: dict           # the exact body the live call would send
    summary: str            # human-readable one-liner
    warnings: list
    created_at: float
    expires_at: float
    metadata: dict          # league_id, league_name, idempotency_key, etc.

    def to_dict(self) -> dict:
        return {
            "preview_id": self.preview_id,
            "command": self.command,
            "payload": self.payload,
            "summary": self.summary,
            "warnings": self.warnings,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }


def _path(preview_id: str) -> Path:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    return PREVIEW_DIR / f"{preview_id}.json"


def _gen_id() -> str:
    return "prv_" + uuid.uuid4().hex[:12]


def idempotency_key(*parts: str) -> str:
    """Deterministic idempotency key from any string parts.

    Convention: pass `(league_id, action, target_id, date_str)`. The same
    inputs hash to the same key; agents can safely retry without doubling.
    """
    raw = "|".join(str(p) for p in parts)
    return "idem_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_preview(
    command: str,
    payload: dict,
    *,
    summary: str,
    warnings: Optional[list] = None,
    metadata: Optional[dict] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Preview:
    """Persist a preview and return its handle."""
    now = time.time()
    p = Preview(
        preview_id=_gen_id(),
        command=command,
        payload=payload,
        summary=summary,
        warnings=warnings or [],
        created_at=now,
        expires_at=now + ttl_seconds,
        metadata=metadata or {},
    )
    _path(p.preview_id).write_text(json.dumps(p.to_dict(), indent=2))
    return p


def load_preview(preview_id: str) -> Preview:
    """Read a preview by id. Raises if missing or expired."""
    path = _path(preview_id)
    if not path.exists():
        raise SleeperPreviewError(
            f"No preview found for id {preview_id}",
            code=ErrorCode.PREVIEW_NOT_FOUND,
        )
    raw = json.loads(path.read_text())
    p = Preview(**raw)
    if time.time() >= p.expires_at:
        raise SleeperPreviewError(
            f"Preview {preview_id} expired at "
            f"{time.strftime('%H:%M:%S', time.localtime(p.expires_at))}",
            code=ErrorCode.PREVIEW_EXPIRED,
        )
    return p


def consume_preview(preview_id: str) -> Preview:
    """Load + delete a preview (one-shot, post-execute)."""
    p = load_preview(preview_id)
    try:
        _path(preview_id).unlink()
    except FileNotFoundError:
        pass
    return p


def gc_expired(now: Optional[float] = None) -> int:
    """Garbage-collect expired previews. Returns count removed."""
    now = now or time.time()
    n = 0
    if not PREVIEW_DIR.exists():
        return 0
    for f in PREVIEW_DIR.glob("prv_*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("expires_at", 0) < now:
                f.unlink()
                n += 1
        except Exception:
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
    return n


class PreviewStore:
    """OO wrapper for callers that prefer it."""
    create = staticmethod(create_preview)
    load = staticmethod(load_preview)
    consume = staticmethod(consume_preview)
    gc = staticmethod(gc_expired)
