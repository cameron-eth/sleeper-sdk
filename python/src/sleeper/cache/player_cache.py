from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL = 86400  # 24 hours in seconds


class PlayerCache:
    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ttl: float = DEFAULT_TTL,
        filesystem_enabled: bool = True,
    ):
        self._memory: dict[str, dict[str, Any]] = {}
        self._memory_timestamps: dict[str, float] = {}
        self._ttl = ttl
        self._filesystem_enabled = filesystem_enabled

        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            import tempfile
            self._cache_dir = Path(tempfile.gettempdir()) / "sleeper_sdk_cache"

        if self._filesystem_enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _is_fresh(self, timestamp: float) -> bool:
        return (time.time() - timestamp) < self._ttl

    def _fs_path(self, sport: str) -> Path:
        return self._cache_dir / f"players_{sport}.json"

    def get(self, sport: str) -> dict[str, Any] | None:
        # Check memory first
        if sport in self._memory and self._is_fresh(self._memory_timestamps[sport]):
            return self._memory[sport]

        # Check filesystem
        if self._filesystem_enabled:
            path = self._fs_path(sport)
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    cached_at = data.get("_cached_at", 0)
                    if self._is_fresh(cached_at):
                        players = data.get("players", {})
                        self._memory[sport] = players
                        self._memory_timestamps[sport] = cached_at
                        return players
                except (json.JSONDecodeError, KeyError):
                    pass

        return None

    def set(self, sport: str, players: dict[str, Any]) -> None:
        now = time.time()
        self._memory[sport] = players
        self._memory_timestamps[sport] = now

        if self._filesystem_enabled:
            path = self._fs_path(sport)
            data = {"_cached_at": now, "players": players}
            path.write_text(json.dumps(data))

    def clear(self, sport: str | None = None) -> None:
        if sport:
            self._memory.pop(sport, None)
            self._memory_timestamps.pop(sport, None)
            if self._filesystem_enabled:
                path = self._fs_path(sport)
                path.unlink(missing_ok=True)
        else:
            self._memory.clear()
            self._memory_timestamps.clear()
            if self._filesystem_enabled:
                for path in self._cache_dir.glob("players_*.json"):
                    path.unlink(missing_ok=True)
