from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class DraftSettings(BaseModel):
    teams: int | None = None
    rounds: int | None = None
    pick_timer: int | None = None
    slots_qb: int | None = None
    slots_rb: int | None = None
    slots_wr: int | None = None
    slots_te: int | None = None
    slots_flex: int | None = None
    slots_def: int | None = None
    slots_k: int | None = None
    slots_bn: int | None = None


class DraftPickMetadata(BaseModel):
    player_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    team: str | None = None
    position: str | None = None
    number: str | None = None
    sport: str | None = None
    status: str | None = None
    injury_status: str | None = None
    news_updated: str | None = None


class DraftPick(BaseModel):
    draft_id: str
    player_id: str | None = None
    picked_by: str | None = None
    roster_id: str | None = None
    round: int
    draft_slot: int | None = None
    pick_no: int
    is_keeper: bool | None = None
    metadata: DraftPickMetadata | None = None


class Draft(BaseModel):
    draft_id: str
    league_id: str | None = None
    type: str | None = None  # "snake", "auction", "linear"
    status: str | None = None  # "pre_draft", "drafting", "complete"
    sport: str | None = None
    season: str | None = None
    season_type: str | None = None
    start_time: int | None = None
    created: int | None = None
    last_picked: int | None = None
    last_message_time: int | None = None
    last_message_id: str | None = None
    creators: list[str] | None = None
    settings: DraftSettings | None = None
    metadata: dict[str, Any] | None = None
    draft_order: dict[str, int] | None = None  # user_id -> draft_slot
    slot_to_roster_id: dict[str, int] | None = None  # slot -> roster_id
