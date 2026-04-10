"""FantasyPros consensus rankings mapped to Sleeper IDs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import nflreadpy as nfl
    import polars as pl
    HAS_NFLREADPY = True
except ImportError:
    HAS_NFLREADPY = False

from sleeper.enrichment.id_bridge import PlayerIdBridge


@dataclass
class PlayerRanking:
    sleeper_id: str
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    ecr: Optional[float] = None  # expert consensus ranking
    ecr_type: Optional[str] = None  # "ros", "draft", "week", etc.
    positional_rank: Optional[int] = None
    best: Optional[int] = None
    worst: Optional[int] = None
    std_dev: Optional[float] = None


def get_player_rankings(
    bridge: Optional[PlayerIdBridge] = None,
    ranking_type: Optional[str] = None,
) -> list[PlayerRanking]:
    """Load FantasyPros rankings mapped to Sleeper IDs.

    Args:
        bridge: PlayerIdBridge instance (created if not provided).
        ranking_type: Filter to a specific ecr_type (e.g. "ros", "draft", "week").
            If None, returns the first available ranking per player.

    Returns:
        List of PlayerRanking sorted by ECR.
    """
    if not HAS_NFLREADPY:
        raise ImportError("nflreadpy is required. Install with: pip install sleeper-sdk[nfl-data]")

    if bridge is None:
        bridge = PlayerIdBridge()

    rankings_df = nfl.load_ff_rankings()

    # Filter by ranking type if specified
    if ranking_type:
        rankings_df = rankings_df.filter(pl.col("ecr_type") == ranking_type)

    # The rankings use sportsdata_id to identify players.
    # We need to join with the ID bridge via fantasypros_id or sportsdata_id.
    # Load the ID mapping to get fantasypros_id -> sleeper_id
    ids_df = nfl.load_ff_playerids()

    # Build fantasypros_id -> sleeper_id map
    fp_to_sleeper: dict[int, str] = {}
    for row in ids_df.iter_rows(named=True):
        fp_id = row.get("fantasypros_id")
        sleeper_id = row.get("sleeper_id")
        if fp_id is not None and sleeper_id is not None:
            sid = str(int(sleeper_id)) if isinstance(sleeper_id, float) else str(sleeper_id)
            fp_to_sleeper[int(fp_id)] = sid

    # Track positional rank counters
    pos_counters: dict[str, int] = {}
    seen_sleeper: set[str] = set()
    result = []

    # Sort by ECR
    rankings_df = rankings_df.sort("ecr")

    for row in rankings_df.iter_rows(named=True):
        fp_id = row.get("id")
        if fp_id is None:
            continue

        sleeper_id = fp_to_sleeper.get(int(fp_id))
        if sleeper_id is None or sleeper_id in seen_sleeper:
            continue
        seen_sleeper.add(sleeper_id)

        pos = row.get("pos")
        if pos:
            pos_counters[pos] = pos_counters.get(pos, 0) + 1
            pos_rank = pos_counters[pos]
        else:
            pos_rank = None

        ids = bridge.from_sleeper(sleeper_id)

        result.append(PlayerRanking(
            sleeper_id=sleeper_id,
            name=ids.name if ids else row.get("player"),
            position=pos,
            team=row.get("tm") or (ids.team if ids else None),
            ecr=row.get("ecr"),
            ecr_type=row.get("ecr_type"),
            positional_rank=pos_rank,
            best=row.get("best"),
            worst=row.get("worst"),
            std_dev=row.get("sd"),
        ))

    return result
