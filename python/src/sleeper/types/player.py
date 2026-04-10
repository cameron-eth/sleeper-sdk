from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class Player(BaseModel, extra="allow"):
    player_id: str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    position: str | None = None
    fantasy_positions: list[str] | None = None
    team: str | None = None
    number: int | None = None
    status: str | None = None
    sport: str | None = None
    age: int | None = None
    height: str | None = None
    weight: str | None = None
    college: str | None = None
    years_exp: int | None = None
    depth_chart_position: Any = None
    depth_chart_order: int | None = None
    injury_status: str | None = None
    injury_start_date: str | None = None
    practice_participation: str | None = None
    hashtag: str | None = None
    search_first_name: str | None = None
    search_last_name: str | None = None
    search_full_name: str | None = None
    search_rank: int | None = None
    birth_date: str | None = None
    birth_country: str | None = None
    # External IDs
    espn_id: Any = None
    yahoo_id: Any = None
    fantasy_data_id: Any = None
    rotowire_id: Any = None
    rotoworld_id: Any = None
    sportradar_id: str | None = None
    stats_id: Any = None


class TrendingPlayer(BaseModel):
    player_id: str
    count: int
