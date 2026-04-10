from __future__ import annotations

from sleeper.http.client import HttpClient
from sleeper.types.draft import Draft, DraftPick
from sleeper.types.transaction import TradedPick


class DraftsApi:
    def __init__(self, http: HttpClient):
        self._http = http

    async def get_drafts_for_user(self, user_id: str, sport: str = "nfl", season: str = "2024") -> list[Draft]:
        data = await self._http.get(f"/user/{user_id}/drafts/{sport}/{season}")
        return [Draft.model_validate(d) for d in data]

    async def get_drafts_for_league(self, league_id: str) -> list[Draft]:
        data = await self._http.get(f"/league/{league_id}/drafts")
        return [Draft.model_validate(d) for d in data]

    async def get_draft(self, draft_id: str) -> Draft:
        data = await self._http.get(f"/draft/{draft_id}")
        return Draft.model_validate(data)

    async def get_picks(self, draft_id: str) -> list[DraftPick]:
        data = await self._http.get(f"/draft/{draft_id}/picks")
        return [DraftPick.model_validate(d) for d in data]

    async def get_traded_picks(self, draft_id: str) -> list[TradedPick]:
        data = await self._http.get(f"/draft/{draft_id}/traded_picks")
        return [TradedPick.model_validate(d) for d in data]
