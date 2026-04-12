from __future__ import annotations

from sleeper.http.client import HttpClient
from sleeper.types.state import SportState


class StateApi:
    def __init__(self, http: HttpClient):
        self._http = http

    async def get_state(self, sport: str = "nfl") -> SportState:
        data = await self._http.get(f"/state/{sport}")
        return SportState.model_validate(data)
