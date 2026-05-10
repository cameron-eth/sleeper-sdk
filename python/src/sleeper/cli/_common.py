"""Shared helpers used across CLI command modules.

Single source of truth for table rendering, league resolution, roster +
player fetch, KTC ↔ sleeper_id mapping, and the small KTC accessor
helpers. Every command module imports from here — DRY by convention.
"""
from __future__ import annotations

import sys


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(no data)"
    col_widths = [
        max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, col_widths)),
        "  ".join("-" * w for w in col_widths),
    ]
    for row in rows:
        lines.append("  ".join(str(v).ljust(w) for v, w in zip(row, col_widths)))
    return "\n".join(lines)


def _resolve_league(username: str, league_filter: str | None) -> tuple:
    """Fetch user + leagues and resolve to a single league. Returns (user, league)."""
    import asyncio
    from datetime import datetime
    from sleeper.client import SleeperClient

    season = str(datetime.now().year)

    async def _fetch_all():
        async with SleeperClient() as client:
            user = await client.users.get_user(username)
            leagues = await client.leagues.get_leagues_for_user(user.user_id, "nfl", season)
            return user, leagues

    print(f"Fetching data for '{username}'...")
    try:
        user, leagues = asyncio.run(_fetch_all())
    except Exception:
        print(f"User '{username}' not found.")
        sys.exit(1)

    if not leagues:
        print(f"No leagues found for '{username}'.")
        sys.exit(1)

    if league_filter:
        matched = [lg for lg in leagues if league_filter.lower() in (lg.name or "").lower()]
        if not matched:
            print(f"No league matching '{league_filter}'. Available:")
            for lg in leagues:
                print(f"  - {lg.name}")
            sys.exit(1)
        league = matched[0]
    elif len(leagues) == 1:
        league = leagues[0]
    else:
        print("Multiple leagues found. Use --league to pick one:")
        for lg in leagues:
            print(f"  - {lg.name}")
        sys.exit(1)

    return user, league


def _fetch_roster_and_players(league_id: str) -> tuple:
    """Fetch rosters + all players for a league. Returns (rosters, sleeper_players)."""
    import asyncio
    from sleeper.client import SleeperClient

    async def _fetch():
        async with SleeperClient() as client:
            rosters = await client.leagues.get_rosters(league_id)
            sleeper_players = await client.get_all_players()
            return rosters, sleeper_players

    return asyncio.run(_fetch())


def _build_sleeper_to_ktc(ktc_players, sleeper_players) -> dict:
    """Build sleeper_id -> KTCPlayer reverse map."""
    from sleeper.enrichment.ktc import build_ktc_to_sleeper_map
    ktc_map = build_ktc_to_sleeper_map(ktc_players, sleeper_players)
    ktc_by_id = {p.ktc_id: p for p in ktc_players}
    sleeper_to_ktc = {}
    for ktc_id, sleeper_id in ktc_map.items():
        sleeper_to_ktc[sleeper_id] = ktc_by_id[ktc_id]
    return sleeper_to_ktc


def _ktc_value(ktc_p, fmt: str) -> int:
    if ktc_p is None:
        return 0
    return ktc_p.superflex.value if fmt == "sf" else ktc_p.one_qb.value


def _ktc_rank(ktc_p, fmt: str) -> int:
    if ktc_p is None:
        return 0
    return ktc_p.superflex.rank if fmt == "sf" else ktc_p.one_qb.rank


def _ktc_trend(ktc_p, fmt: str) -> int:
    """7-day overall trend for the given format."""
    if ktc_p is None:
        return 0
    pv = ktc_p.superflex if fmt == "sf" else ktc_p.one_qb
    return pv.overall_trend
