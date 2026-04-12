"""Cross-league data aggregation for a single user.

Collects rosters, users, and transactions across all leagues and seasons
for a given user. This is the async equivalent of the
``collect_all_league_data`` function from the original sleeper_wrapper.py,
built on top of the typed SDK.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sleeper.api.leagues import LeaguesApi
from sleeper.api.users import UsersApi
from sleeper.enrichment.ktc import detect_scoring_type
from sleeper.types.league import League, LeagueUser, Roster
from sleeper.types.transaction import Transaction


@dataclass
class LeagueSnapshot:
    """All relevant data from a single league-season for a user."""

    league_id: str
    league_name: str
    season: str
    user_roster_id: int
    scoring_type: str  # "sf" or "1qb"
    league: League | None = None
    rosters: list[Roster] = field(default_factory=list)
    users: list[LeagueUser] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    roster_to_owner: dict[int, str] = field(default_factory=dict)
    owner_to_name: dict[str, str] = field(default_factory=dict)


async def collect_user_league_snapshots(
    users_api: UsersApi,
    leagues_api: LeaguesApi,
    user_id: str,
    seasons: list[str] | None = None,
    max_transaction_week: int = 18,
) -> list[LeagueSnapshot]:
    """Collect all league data across seasons for a user.

    For each league the user was in, gathers rosters, users, and all
    transactions. Identifies the user's roster_id in each league and
    auto-detects the scoring format (SF vs 1QB).

    Args:
        users_api: UsersApi instance from SleeperClient.
        leagues_api: LeaguesApi instance from SleeperClient.
        user_id: The Sleeper user_id.
        seasons: List of season strings (e.g. ``["2023", "2024"]``).
            Defaults to 2017-2025.
        max_transaction_week: Max week to fetch transactions for.

    Returns:
        List of LeagueSnapshot, one per league-season.
    """
    if seasons is None:
        seasons = [str(y) for y in range(2017, 2026)]

    # Phase 1: Discover all leagues across seasons
    all_leagues: list[tuple[League, str]] = []
    for season in seasons:
        leagues = await users_api.get_user_leagues(user_id, season=season)
        for league in leagues:
            all_leagues.append((league, season))

    # Phase 2: For each league, gather data
    snapshots: list[LeagueSnapshot] = []
    for league, season in all_leagues:
        lid = league.league_id

        # Fetch rosters and users concurrently
        rosters, users = await asyncio.gather(
            leagues_api.get_rosters(lid),
            leagues_api.get_users(lid),
        )

        # Find user's roster_id
        user_roster_id = _find_user_roster_id(user_id, rosters)
        if user_roster_id is None:
            continue

        # Build lookup maps
        roster_to_owner: dict[int, str] = {}
        for r in rosters:
            if r.owner_id:
                roster_to_owner[r.roster_id] = r.owner_id

        owner_to_name: dict[str, str] = {}
        for u in users:
            name = u.team_name or u.display_name or u.username or u.user_id
            owner_to_name[u.user_id] = name

        # Detect scoring type
        scoring_type = detect_scoring_type(league)

        # Fetch all weeks of transactions
        all_txns: list[Transaction] = []
        for week in range(1, max_transaction_week + 1):
            txns = await leagues_api.get_transactions(lid, week)
            all_txns.extend(txns)

        snapshots.append(LeagueSnapshot(
            league_id=lid,
            league_name=league.name or "Unknown",
            season=season,
            user_roster_id=user_roster_id,
            scoring_type=scoring_type,
            league=league,
            rosters=rosters,
            users=users,
            transactions=all_txns,
            roster_to_owner=roster_to_owner,
            owner_to_name=owner_to_name,
        ))

    return snapshots


def extract_trades_only(
    snapshots: list[LeagueSnapshot],
) -> list[tuple[Transaction, LeagueSnapshot]]:
    """Extract all completed trade transactions across all snapshots.

    Args:
        snapshots: List of LeagueSnapshot from :func:`collect_user_league_snapshots`.

    Returns:
        List of ``(Transaction, LeagueSnapshot)`` tuples for completed trades.
    """
    trades = []
    for snap in snapshots:
        for tx in snap.transactions:
            if tx.type == "trade" and tx.status == "complete":
                trades.append((tx, snap))
    return trades


def _find_user_roster_id(user_id: str, rosters: list[Roster]) -> int | None:
    """Find which roster_id belongs to the given user_id."""
    for r in rosters:
        if r.owner_id == user_id:
            return r.roster_id
    return None
