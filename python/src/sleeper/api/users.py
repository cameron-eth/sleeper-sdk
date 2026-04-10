from __future__ import annotations

from sleeper.http.client import HttpClient
from sleeper.types.user import User


class UsersApi:
    def __init__(self, http: HttpClient):
        self._http = http

    async def get_user(self, username_or_id: str) -> User:
        data = await self._http.get(f"/user/{username_or_id}")
        return User.model_validate(data)
