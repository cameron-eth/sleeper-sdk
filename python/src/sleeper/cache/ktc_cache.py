"""Cache for KTC scraped data (players and trades)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_KTC_TTL = 3600  # 1 hour


class KTCCache:
    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ttl: float = DEFAULT_KTC_TTL,
        filesystem_enabled: bool = True,
    ):
        self._memory: dict[str, Any] = {}
        self._memory_timestamps: dict[str, float] = {}
        self._ttl = ttl
        self._filesystem_enabled = filesystem_enabled

        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            import tempfile
            self._cache_dir = Path(tempfile.gettempdir()) / "sleeper_sdk_cache" / "ktc"

        if self._filesystem_enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _is_fresh(self, timestamp: float) -> bool:
        return (time.time() - timestamp) < self._ttl

    def _fs_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(" ", "_")
        return self._cache_dir / f"ktc_{safe_key}.json"

    def get(self, key: str) -> Any | None:
        if key in self._memory and self._is_fresh(self._memory_timestamps[key]):
            return self._memory[key]

        if self._filesystem_enabled:
            path = self._fs_path(key)
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    cached_at = data.get("_cached_at", 0)
                    if self._is_fresh(cached_at):
                        payload = data.get("payload")
                        self._memory[key] = payload
                        self._memory_timestamps[key] = cached_at
                        return payload
                except (json.JSONDecodeError, KeyError):
                    pass

        return None

    def set(self, key: str, payload: Any) -> None:
        now = time.time()
        self._memory[key] = payload
        self._memory_timestamps[key] = now

        if self._filesystem_enabled:
            path = self._fs_path(key)
            data = {"_cached_at": now, "payload": payload}
            path.write_text(json.dumps(data))

    def clear(self, key: str | None = None) -> None:
        if key:
            self._memory.pop(key, None)
            self._memory_timestamps.pop(key, None)
            if self._filesystem_enabled:
                path = self._fs_path(key)
                path.unlink(missing_ok=True)
        else:
            self._memory.clear()
            self._memory_timestamps.clear()
            if self._filesystem_enabled:
                for path in self._cache_dir.glob("ktc_*.json"):
                    path.unlink(missing_ok=True)
