"""Enrich Sleeper rosters with real NFL stats from nflreadpy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

try:
    import nflreadpy as nfl
    import polars as pl
    HAS_NFLREADPY = True
except ImportError:
    HAS_NFLREADPY = False

from sleeper.enrichment.id_bridge import PlayerIdBridge
from sleeper.types.league import Roster


@dataclass
class SeasonStatLine:
    season: int
    games: int = 0
    fantasy_points: float = 0.0
    fantasy_points_ppr: float = 0.0
    ppg: float = 0.0
    ppg_ppr: float = 0.0
    # Passing
    passing_yards: int = 0
    passing_tds: int = 0
    interceptions: int = 0
    # Rushing
    rushing_yards: int = 0
    rushing_tds: int = 0
    carries: int = 0
    # Receiving
    receptions: int = 0
    targets: int = 0
    receiving_yards: int = 0
    receiving_tds: int = 0
    target_share: float = 0.0


@dataclass
class EnrichedPlayer:
    sleeper_id: str
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    roster_id: int = 0
    seasons: dict[int, SeasonStatLine] = field(default_factory=dict)


def get_season_stats(
    seasons: list[int],
    bridge: Optional[PlayerIdBridge] = None,
) -> dict[str, dict[int, SeasonStatLine]]:
    """Load season stats for given years, keyed by sleeper_id.

    Returns {sleeper_id: {season: SeasonStatLine}}.
    """
    if not HAS_NFLREADPY:
        raise ImportError("nflreadpy is required. Install with: pip install sleeper-sdk[nfl-data]")

    if bridge is None:
        bridge = PlayerIdBridge()

    stats_df = nfl.load_player_stats(seasons)

    # Aggregate weekly stats to season totals per player
    # player_id in nflreadpy stats is gsis_id
    result: dict[str, dict[int, SeasonStatLine]] = {}

    # Group by player_id and season
    grouped = stats_df.group_by(["player_id", "season"]).agg([
        pl.col("week").count().alias("games"),
        pl.col("fantasy_points").sum().alias("fantasy_points"),
        pl.col("fantasy_points_ppr").sum().alias("fantasy_points_ppr"),
        pl.col("passing_yards").sum().alias("passing_yards"),
        pl.col("passing_tds").sum().alias("passing_tds"),
        pl.col("passing_interceptions").sum().alias("interceptions"),
        pl.col("rushing_yards").sum().alias("rushing_yards"),
        pl.col("rushing_tds").sum().alias("rushing_tds"),
        pl.col("carries").sum().alias("carries"),
        pl.col("receptions").sum().alias("receptions"),
        pl.col("targets").sum().alias("targets"),
        pl.col("receiving_yards").sum().alias("receiving_yards"),
        pl.col("receiving_tds").sum().alias("receiving_tds"),
        pl.col("target_share").mean().alias("target_share"),
    ])

    for row in grouped.iter_rows(named=True):
        gsis_id = row["player_id"]
        sleeper_id = bridge.gsis_to_sleeper(gsis_id)
        if sleeper_id is None:
            continue

        season = row["season"]
        games = row["games"] or 0
        fp = row["fantasy_points"] or 0.0
        fp_ppr = row["fantasy_points_ppr"] or 0.0

        stat = SeasonStatLine(
            season=season,
            games=games,
            fantasy_points=round(fp, 2),
            fantasy_points_ppr=round(fp_ppr, 2),
            ppg=round(fp / games, 2) if games > 0 else 0.0,
            ppg_ppr=round(fp_ppr / games, 2) if games > 0 else 0.0,
            passing_yards=int(row["passing_yards"] or 0),
            passing_tds=int(row["passing_tds"] or 0),
            interceptions=int(row["interceptions"] or 0),
            rushing_yards=int(row["rushing_yards"] or 0),
            rushing_tds=int(row["rushing_tds"] or 0),
            carries=int(row["carries"] or 0),
            receptions=int(row["receptions"] or 0),
            targets=int(row["targets"] or 0),
            receiving_yards=int(row["receiving_yards"] or 0),
            receiving_tds=int(row["receiving_tds"] or 0),
            target_share=round(row["target_share"] or 0.0, 4),
        )

        if sleeper_id not in result:
            result[sleeper_id] = {}
        result[sleeper_id][season] = stat

    return result


def enrich_rosters_with_stats(
    rosters: list[Roster],
    seasons: list[int],
    bridge: Optional[PlayerIdBridge] = None,
) -> list[EnrichedPlayer]:
    """Attach real NFL stats to every player on the given rosters."""
    if bridge is None:
        bridge = PlayerIdBridge()

    all_stats = get_season_stats(seasons, bridge)

    enriched = []
    for roster in rosters:
        for pid in roster.players:
            ids = bridge.from_sleeper(pid)
            player = EnrichedPlayer(
                sleeper_id=pid,
                name=ids.name if ids else None,
                position=ids.position if ids else None,
                team=ids.team if ids else None,
                roster_id=roster.roster_id,
                seasons=all_stats.get(pid, {}),
            )
            enriched.append(player)

    return enriched
