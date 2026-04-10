from __future__ import annotations
from pydantic import BaseModel


class Matchup(BaseModel):
    roster_id: int
    matchup_id: int | None = None
    starters: list[str] = []
    players: list[str] = []
    starters_points: list[float] | None = None
    points: float | None = None
    custom_points: float | None = None
