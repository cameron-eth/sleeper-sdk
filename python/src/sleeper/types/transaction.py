from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class TradedPick(BaseModel):
    season: str
    round: int
    roster_id: int
    previous_owner_id: int
    owner_id: int


class WaiverBudget(BaseModel):
    sender: int
    receiver: int
    amount: int


class Transaction(BaseModel):
    transaction_id: str
    type: str  # "trade", "free_agent", "waiver", "commissioner"
    status: str
    status_updated: int | None = None
    created: int | None = None
    creator: str | None = None
    roster_ids: list[int] = []
    consenter_ids: list[int] = []
    adds: dict[str, int] | None = None  # player_id -> roster_id
    drops: dict[str, int] | None = None  # player_id -> roster_id
    draft_picks: list[TradedPick] = []
    waiver_budget: list[WaiverBudget] = []
    settings: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    leg: int | None = None
