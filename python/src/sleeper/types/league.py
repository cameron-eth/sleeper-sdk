from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class RosterSettings(BaseModel):
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0
    fpts_decimal: float = 0
    fpts_against: float = 0
    fpts_against_decimal: float = 0
    waiver_position: int = 0
    waiver_budget_used: int = 0
    total_moves: int = 0


class Roster(BaseModel):
    roster_id: int
    owner_id: str | None = None
    league_id: str | None = None
    starters: list[str] = []
    players: list[str] = []
    reserve: list[str] | None = None
    settings: RosterSettings | None = None

    @property
    def bench(self) -> list[str]:
        starter_set = set(self.starters)
        return [p for p in self.players if p not in starter_set]


class LeagueUser(BaseModel):
    user_id: str
    username: str | None = None
    display_name: str | None = None
    avatar: str | None = None
    metadata: dict[str, Any] | None = None
    is_owner: bool | None = None

    @property
    def team_name(self) -> str | None:
        if self.metadata:
            return self.metadata.get("team_name")
        return None


class League(BaseModel):
    league_id: str
    name: str | None = None
    status: str | None = None
    sport: str | None = None
    season: str | None = None
    season_type: str | None = None
    total_rosters: int | None = None
    draft_id: str | None = None
    previous_league_id: str | None = None
    avatar: str | None = None
    settings: dict[str, Any] | None = None
    scoring_settings: dict[str, float] | None = None
    roster_positions: list[str] | None = None
