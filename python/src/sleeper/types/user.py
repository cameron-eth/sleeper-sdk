from __future__ import annotations
from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    username: str | None = None
    display_name: str | None = None
    avatar: str | None = None

    @property
    def avatar_url(self) -> str | None:
        if self.avatar:
            return f"https://sleepercdn.com/avatars/{self.avatar}"
        return None

    @property
    def avatar_thumb_url(self) -> str | None:
        if self.avatar:
            return f"https://sleepercdn.com/avatars/thumbs/{self.avatar}"
        return None
