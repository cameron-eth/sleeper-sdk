from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sleeper.types.league import Roster, LeagueUser
from sleeper.types.matchup import Matchup


@dataclass
class TeamStanding:
    roster_id: int
    owner_id: Optional[str] = None
    display_name: Optional[str] = None
    team_name: Optional[str] = None
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0.0
    fpts_against: float = 0.0
    streak: int = 0  # positive = win streak, negative = loss streak
    median_wins: int = 0
    median_losses: int = 0


@dataclass
class WeekRecord:
    week: int
    roster_id: int
    points: float = 0.0
    opponent_points: float = 0.0
    won: Optional[bool] = None
    matchup_id: Optional[int] = None


@dataclass
class PowerRanking:
    roster_id: int
    display_name: Optional[str] = None
    team_name: Optional[str] = None
    rank: int = 0
    score: float = 0.0  # composite score
    wins: int = 0
    fpts: float = 0.0
    median_wins: int = 0


def get_standings(
    rosters: list[Roster],
    users: list[LeagueUser],
) -> list[TeamStanding]:
    """Build standings from rosters and users. Sorted by wins desc, then points desc."""
    user_map = {u.user_id: u for u in users}

    standings = []
    for r in rosters:
        s = r.settings
        user = user_map.get(r.owner_id or "")
        standings.append(TeamStanding(
            roster_id=r.roster_id,
            owner_id=r.owner_id,
            display_name=user.display_name if user else None,
            team_name=user.team_name if user else None,
            wins=s.wins if s else 0,
            losses=s.losses if s else 0,
            ties=s.ties if s else 0,
            fpts=(s.fpts + s.fpts_decimal / 100) if s else 0.0,
            fpts_against=(s.fpts_against + s.fpts_against_decimal / 100) if s else 0.0,
        ))

    standings.sort(key=lambda t: (t.wins, t.fpts), reverse=True)
    return standings


def get_points_per_week(
    matchups_by_week: dict[int, list[Matchup]],
) -> dict[int, dict[int, float]]:
    """Returns {roster_id: {week: points}} for all weeks."""
    result: dict[int, dict[int, float]] = {}
    for week, matchups in matchups_by_week.items():
        for m in matchups:
            if m.roster_id not in result:
                result[m.roster_id] = {}
            result[m.roster_id][week] = m.points or 0.0
    return result


def get_record_by_week(
    matchups_by_week: dict[int, list[Matchup]],
) -> dict[int, list[WeekRecord]]:
    """Returns {roster_id: [WeekRecord, ...]} with opponent info per week."""
    result: dict[int, list[WeekRecord]] = {}

    for week, matchups in sorted(matchups_by_week.items()):
        # Group matchups by matchup_id to find opponents
        by_matchup: dict[int, list[Matchup]] = {}
        for m in matchups:
            mid = m.matchup_id
            if mid is not None:
                by_matchup.setdefault(mid, []).append(m)

        for mid, teams in by_matchup.items():
            if len(teams) == 2:
                for i in range(2):
                    me = teams[i]
                    opp = teams[1 - i]
                    my_pts = me.points or 0.0
                    opp_pts = opp.points or 0.0
                    won = my_pts > opp_pts if my_pts != opp_pts else None

                    record = WeekRecord(
                        week=week,
                        roster_id=me.roster_id,
                        points=my_pts,
                        opponent_points=opp_pts,
                        won=won,
                        matchup_id=mid,
                    )
                    result.setdefault(me.roster_id, []).append(record)

    return result


def get_median_record(
    matchups_by_week: dict[int, list[Matchup]],
) -> dict[int, tuple[int, int]]:
    """Calculate W/L record vs the league median each week.
    Returns {roster_id: (median_wins, median_losses)}.
    """
    median_record: dict[int, list[int]] = {}  # roster_id -> [wins, losses]

    for week, matchups in matchups_by_week.items():
        scores = [(m.roster_id, m.points or 0.0) for m in matchups]
        if not scores:
            continue

        scores.sort(key=lambda x: x[1])
        n = len(scores)
        if n % 2 == 0:
            median = (scores[n // 2 - 1][1] + scores[n // 2][1]) / 2
        else:
            median = scores[n // 2][1]

        for roster_id, pts in scores:
            if roster_id not in median_record:
                median_record[roster_id] = [0, 0]
            if pts > median:
                median_record[roster_id][0] += 1
            elif pts < median:
                median_record[roster_id][1] += 1

    return {rid: (wl[0], wl[1]) for rid, wl in median_record.items()}


def get_power_rankings(
    rosters: list[Roster],
    users: list[LeagueUser],
    matchups_by_week: dict[int, list[Matchup]],
) -> list[PowerRanking]:
    """Composite power ranking: 40% win%, 40% points rank, 20% median record."""
    standings = get_standings(rosters, users)
    median = get_median_record(matchups_by_week)
    user_map = {u.user_id: u for u in users}

    n = len(standings)
    if n == 0:
        return []

    # Rank by points
    by_pts = sorted(standings, key=lambda t: t.fpts, reverse=True)
    pts_rank = {t.roster_id: i for i, t in enumerate(by_pts)}

    rankings = []
    for t in standings:
        total_games = t.wins + t.losses + t.ties
        win_pct = t.wins / total_games if total_games > 0 else 0
        pts_pct = 1 - (pts_rank[t.roster_id] / max(n - 1, 1))
        mw, ml = median.get(t.roster_id, (0, 0))
        median_total = mw + ml
        median_pct = mw / median_total if median_total > 0 else 0

        score = (win_pct * 0.4) + (pts_pct * 0.4) + (median_pct * 0.2)

        user = user_map.get(t.owner_id or "")
        rankings.append(PowerRanking(
            roster_id=t.roster_id,
            display_name=user.display_name if user else None,
            team_name=user.team_name if user else None,
            score=round(score, 4),
            wins=t.wins,
            fpts=t.fpts,
            median_wins=mw,
        ))

    rankings.sort(key=lambda r: r.score, reverse=True)
    for i, r in enumerate(rankings):
        r.rank = i + 1

    return rankings
