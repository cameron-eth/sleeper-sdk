from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sleeper.types.draft import DraftPick
from sleeper.types.transaction import Transaction, TradedPick
from sleeper.types.league import LeagueUser


@dataclass
class DraftMapEntry:
    player_id: str
    pick_no: int
    round: int
    draft_slot: Optional[int] = None
    roster_id: Optional[str] = None
    picked_by: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None


@dataclass
class PlayerTradeVolume:
    player_id: str
    times_traded: int = 0
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None


@dataclass
class TeamTradeVolume:
    roster_id: int
    display_name: Optional[str] = None
    team_name: Optional[str] = None
    total_trades: int = 0
    players_acquired: int = 0
    players_sent: int = 0
    picks_acquired: int = 0
    picks_sent: int = 0
    faab_spent: int = 0
    faab_received: int = 0


@dataclass
class FuturePickOwnership:
    season: str
    round: int
    original_roster_id: int
    current_owner_id: int


def get_initial_draft_map(picks: list[DraftPick]) -> list[DraftMapEntry]:
    """Map every player to their original draft pick. Sorted by pick number."""
    entries = []
    for p in picks:
        m = p.metadata
        entries.append(DraftMapEntry(
            player_id=p.player_id or "",
            pick_no=p.pick_no,
            round=p.round,
            draft_slot=p.draft_slot,
            roster_id=p.roster_id,
            picked_by=p.picked_by,
            first_name=m.first_name if m else None,
            last_name=m.last_name if m else None,
            position=m.position if m else None,
            team=m.team if m else None,
        ))
    entries.sort(key=lambda e: e.pick_no)
    return entries


def get_trade_volume_by_player(
    transactions: list[Transaction],
) -> list[PlayerTradeVolume]:
    """Count how many times each player has been traded. Sorted by most traded."""
    counts: dict[str, int] = {}

    for tx in transactions:
        if tx.type != "trade":
            continue
        if tx.adds:
            for pid in tx.adds:
                counts[pid] = counts.get(pid, 0) + 1

    result = [
        PlayerTradeVolume(player_id=pid, times_traded=count)
        for pid, count in counts.items()
    ]
    result.sort(key=lambda v: v.times_traded, reverse=True)
    return result


def get_trade_volume_by_team(
    transactions: list[Transaction],
    users: list[LeagueUser],
) -> list[TeamTradeVolume]:
    """Aggregate trade activity per roster. Sorted by total trades desc."""
    # Map owner_id to roster_ids involved
    volumes: dict[int, TeamTradeVolume] = {}

    for tx in transactions:
        if tx.type != "trade":
            continue

        for rid in (tx.roster_ids or []):
            if rid not in volumes:
                volumes[rid] = TeamTradeVolume(roster_id=rid)
            volumes[rid].total_trades += 1

        # Count player adds/drops per roster
        if tx.adds:
            for pid, rid in tx.adds.items():
                if rid in volumes:
                    volumes[rid].players_acquired += 1
        if tx.drops:
            for pid, rid in tx.drops.items():
                if rid in volumes:
                    volumes[rid].players_sent += 1

        # Count pick trades
        for pick in tx.draft_picks:
            if pick.owner_id in volumes:
                volumes[pick.owner_id].picks_acquired += 1
            if pick.previous_owner_id in volumes:
                volumes[pick.previous_owner_id].picks_sent += 1

        # FAAB
        for wb in tx.waiver_budget:
            if wb.sender in volumes:
                volumes[wb.sender].faab_spent += wb.amount
            if wb.receiver in volumes:
                volumes[wb.receiver].faab_received += wb.amount

    # Attach user info by matching roster_ids
    # Note: we can't directly map roster_id to user without rosters,
    # but users list is available for display names
    result = list(volumes.values())
    result.sort(key=lambda v: v.total_trades, reverse=True)
    return result


def get_future_pick_ownership(
    traded_picks: list[TradedPick],
) -> list[FuturePickOwnership]:
    """Show current ownership of all traded future picks. Sorted by season, round."""
    result = [
        FuturePickOwnership(
            season=tp.season,
            round=tp.round,
            original_roster_id=tp.roster_id,
            current_owner_id=tp.owner_id,
        )
        for tp in traded_picks
        if tp.roster_id != tp.owner_id  # only show picks that moved
    ]
    result.sort(key=lambda p: (p.season, p.round, p.original_roster_id))
    return result
