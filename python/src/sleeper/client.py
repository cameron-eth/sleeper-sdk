from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sleeper.api.drafts import DraftsApi
from sleeper.api.leagues import LeaguesApi
from sleeper.api.players import PlayersApi
from sleeper.api.state import StateApi
from sleeper.api.users import UsersApi
from sleeper.cache.player_cache import PlayerCache
from sleeper.http.client import HttpClient
from sleeper.types.player import Player


class SleeperClient:
    """Main entry point for the Sleeper SDK.

    Usage (async):
        async with SleeperClient() as client:
            league = await client.leagues.get_league("123456")

    Usage (sync):
        client = SleeperClient()
        league = client.sync(client.leagues.get_league("123456"))
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        cache_ttl: float = 86400,
        cache_filesystem: bool = True,
        timeout: float = 30.0,
    ):
        self._http = HttpClient(timeout=timeout)
        self._player_cache = PlayerCache(
            cache_dir=cache_dir,
            ttl=cache_ttl,
            filesystem_enabled=cache_filesystem,
        )

        self.users = UsersApi(self._http)
        self.leagues = LeaguesApi(self._http)
        self.drafts = DraftsApi(self._http)
        self.players = PlayersApi(self._http)
        self.state = StateApi(self._http)

    async def get_all_players(self, sport: str = "nfl", force_refresh: bool = False) -> dict[str, Player]:
        """Get all players with caching. Uses cached data if available and fresh."""
        if not force_refresh:
            cached = self._player_cache.get(sport)
            if cached is not None:
                return {pid: Player.model_validate(pdata) for pid, pdata in cached.items()}

        players = await self.players.get_all_players(sport)
        raw = {pid: p.model_dump() for pid, p in players.items()}
        self._player_cache.set(sport, raw)
        return players

    def sync(self, coro: Any) -> Any:
        """Run an async method synchronously. Convenience for scripts and notebooks."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return asyncio.run(coro)

    async def close(self) -> None:
        await self._http.close()

    async def __aenter__(self) -> SleeperClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
