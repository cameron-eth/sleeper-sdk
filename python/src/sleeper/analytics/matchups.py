from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sleeper.types.matchup import Matchup
from sleeper.types.league import LeagueUser


@dataclass
class HeadToHeadRecord:
    roster_id_1: int
    roster_id_2: int
    wins_1: int = 0
    wins_2: int = 0
    ties: int = 0
    total_points_1: float = 0.0
    total_points_2: float = 0.0


@dataclass
class ClosestGame:
    week: int
    matchup_id: int
    roster_id_1: int
    roster_id_2: int
    points_1: float
    points_2: float
    margin: float


@dataclass
class HighScoringWeek:
    week: int
    roster_id: int
    points: float
    display_name: Optional[str] = None


def get_head_to_head(
    matchups_by_week: dict[int, list[Matchup]],
    roster_id_1: int,
    roster_id_2: int,
) -> HeadToHeadRecord:
    """All-time head-to-head record between two rosters."""
    record = HeadToHeadRecord(roster_id_1=roster_id_1, roster_id_2=roster_id_2)

    for week, matchups in matchups_by_week.items():
        by_matchup: dict[int, list[Matchup]] = {}
        for m in matchups:
            if m.matchup_id is not None:
                by_matchup.setdefault(m.matchup_id, []).append(m)

        for mid, teams in by_matchup.items():
            ids = {t.roster_id for t in teams}
            if roster_id_1 in ids and roster_id_2 in ids:
                t1 = next(t for t in teams if t.roster_id == roster_id_1)
                t2 = next(t for t in teams if t.roster_id == roster_id_2)
                p1 = t1.points or 0.0
                p2 = t2.points or 0.0
                record.total_points_1 += p1
                record.total_points_2 += p2
                if p1 > p2:
                    record.wins_1 += 1
                elif p2 > p1:
                    record.wins_2 += 1
                else:
                    record.ties += 1

    return record


def get_closest_games(
    matchups_by_week: dict[int, list[Matchup]],
    limit: int = 10,
) -> list[ClosestGame]:
    """Find the closest matchups across all weeks. Sorted by smallest margin."""
    games = []

    for week, matchups in matchups_by_week.items():
        by_matchup: dict[int, list[Matchup]] = {}
        for m in matchups:
            if m.matchup_id is not None:
                by_matchup.setdefault(m.matchup_id, []).append(m)

        for mid, teams in by_matchup.items():
            if len(teams) == 2:
                p1 = teams[0].points or 0.0
                p2 = teams[1].points or 0.0
                margin = abs(p1 - p2)
                games.append(ClosestGame(
                    week=week,
                    matchup_id=mid,
                    roster_id_1=teams[0].roster_id,
                    roster_id_2=teams[1].roster_id,
                    points_1=p1,
                    points_2=p2,
                    margin=round(margin, 2),
                ))

    games.sort(key=lambda g: g.margin)
    return games[:limit]


def get_highest_scoring_weeks(
    matchups_by_week: dict[int, list[Matchup]],
    users: list[LeagueUser],
    limit: int = 10,
) -> list[HighScoringWeek]:
    """Find the highest individual team scores across all weeks."""
    # We don't have a direct roster_id -> user mapping here,
    # so we return roster_id and let the caller resolve names
    scores = []

    for week, matchups in matchups_by_week.items():
        for m in matchups:
            pts = m.points or 0.0
            if pts > 0:
                scores.append(HighScoringWeek(
                    week=week,
                    roster_id=m.roster_id,
                    points=pts,
                ))

    scores.sort(key=lambda s: s.points, reverse=True)
    return scores[:limit]
