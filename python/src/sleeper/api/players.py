from __future__ import annotations

from sleeper.http.client import HttpClient
from sleeper.types.player import Player, TrendingPlayer


class PlayersApi:
    def __init__(self, http: HttpClient):
        self._http = http

    async def get_all_players(self, sport: str = "nfl") -> dict[str, Player]:
        data = await self._http.get(f"/players/{sport}")
        return {pid: Player.model_validate(pdata) for pid, pdata in data.items()}

    async def get_trending(
        self,
        type: str = "add",
        sport: str = "nfl",
        lookback_hours: int = 24,
        limit: int = 25,
    ) -> list[TrendingPlayer]:
        params = {"lookback_hours": lookback_hours, "limit": limit}
        data = await self._http.get(f"/players/{sport}/trending/{type}", params=params)
        return [TrendingPlayer.model_validate(d) for d in data]
