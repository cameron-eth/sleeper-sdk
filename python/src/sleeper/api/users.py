from __future__ import annotations

from sleeper.http.client import HttpClient
from sleeper.types.draft import Draft
from sleeper.types.league import League
from sleeper.types.user import User


class UsersApi:
    def __init__(self, http: HttpClient):
        self._http = http

    async def get_user(self, username_or_id: str) -> User:
        data = await self._http.get(f"/user/{username_or_id}")
        return User.model_validate(data)

    async def get_user_leagues(
        self, user_id: str, sport: str = "nfl", season: str = "2024"
    ) -> list[League]:
        data = await self._http.get(f"/user/{user_id}/leagues/{sport}/{season}")
        return [League.model_validate(d) for d in data]

    async def get_user_drafts(
        self, user_id: str, sport: str = "nfl", season: str = "2024"
    ) -> list[Draft]:
        data = await self._http.get(f"/user/{user_id}/drafts/{sport}/{season}")
        return [Draft.model_validate(d) for d in data]
