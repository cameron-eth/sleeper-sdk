from __future__ import annotations
from pydantic import BaseModel


class SportState(BaseModel):
    week: int
    season: str
    season_type: str | None = None
    season_start_date: str | None = None
    previous_season: str | None = None
    leg: int | None = None
    league_season: str | None = None
    league_create_season: str | None = None
    display_week: int | None = None
