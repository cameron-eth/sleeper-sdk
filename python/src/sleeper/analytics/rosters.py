from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sleeper.types.league import Roster
from sleeper.types.player import Player


@dataclass
class RosterComposition:
    roster_id: int
    owner_id: Optional[str] = None
    total_players: int = 0
    by_position: dict[str, int] = field(default_factory=dict)
    starters_count: int = 0
    bench_count: int = 0


@dataclass
class PlayerTeamMapping:
    player_id: str
    player_name: Optional[str] = None
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    roster_id: int = 0


def get_roster_composition(
    rosters: list[Roster],
    players: dict[str, Player],
) -> list[RosterComposition]:
    """Break down each roster by position."""
    result = []
    for r in rosters:
        comp = RosterComposition(
            roster_id=r.roster_id,
            owner_id=r.owner_id,
            total_players=len(r.players),
            starters_count=len(r.starters),
            bench_count=len(r.bench),
        )

        for pid in r.players:
            player = players.get(pid)
            if player and player.position:
                pos = player.position
                comp.by_position[pos] = comp.by_position.get(pos, 0) + 1

        result.append(comp)
    return result


def get_player_to_team_map(
    rosters: list[Roster],
    players: dict[str, Player],
) -> list[PlayerTeamMapping]:
    """Map every rostered player to their NFL team and fantasy roster."""
    result = []
    for r in rosters:
        for pid in r.players:
            player = players.get(pid)
            name = None
            if player:
                parts = [player.first_name or "", player.last_name or ""]
                name = " ".join(p for p in parts if p) or None

            result.append(PlayerTeamMapping(
                player_id=pid,
                player_name=name,
                position=player.position if player else None,
                nfl_team=player.team if player else None,
                roster_id=r.roster_id,
            ))

    result.sort(key=lambda p: (p.nfl_team or "", p.position or "", p.player_name or ""))
    return result
