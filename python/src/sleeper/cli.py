"""Sleeper SDK CLI."""
from __future__ import annotations

import argparse
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


def cmd_market_value(args: argparse.Namespace) -> None:
    """Analyze a player's market value: KTC listed vs actual trade price."""
    from sleeper.enrichment.ktc import get_player_market_value

    player_name = " ".join(args.player_name)
    fmt = args.format

    print(f"Analyzing market value for '{player_name}' ({fmt.upper()})...")
    print()

    report = get_player_market_value(player_name, fmt=fmt)

    if report.num_trades == 0:
        if report.ktc_value:
            print(f"{report.player_name} ({report.position}, {report.team})")
            print(f"KTC Value: {report.ktc_value}")
            print(f"No matching trades found in KTC trade database.")
        else:
            print(f"Player '{player_name}' not found in KTC database.")
        return

    # Summary
    print(f"Player:              {report.player_name}")
    print(f"Position:            {report.position}")
    print(f"Team:                {report.team}")
    print(f"Format:              {report.format.upper()}")
    print()
    print(f"KTC Listed Value:    {report.ktc_value:,}")
    print(f"Median Market Value: {report.median_market_value:,}")
    print(f"Mean Market Value:   {report.mean_market_value:,}")
    print(f"Trades Analyzed:     {report.num_trades}")

    pct_str = f"{report.pct_of_ktc:.1f}%" if report.pct_of_ktc else "N/A"
    diff = (report.median_market_value or 0) - report.ktc_value
    print(f"% of KTC Value:      {pct_str}")
    print(f"Difference:          {diff:+,}")

    if report.pct_of_ktc is not None:
        if report.pct_of_ktc > 105:
            print(f"Signal:              UNDERVALUED by KTC (trades above listed)")
        elif report.pct_of_ktc < 95:
            print(f"Signal:              OVERVALUED by KTC (trades below listed)")
        else:
            print(f"Signal:              FAIRLY VALUED")

    print()
    print("Trade Breakdown:")
    print()

    headers = ["#", "Date", "Gave (player side)", "Got (other side)", "Implied", "Solo"]
    rows = []
    for i, t in enumerate(report.trades, 1):
        gave = ", ".join(t.player_side)
        got = ", ".join(t.other_side)
        if len(gave) > 45:
            gave = gave[:42] + "..."
        if len(got) > 45:
            got = got[:42] + "..."
        rows.append([
            str(i),
            t.date[:10] if len(t.date) > 10 else t.date,
            gave,
            got,
            f"{t.implied_price:,}",
            "Y" if t.is_solo else "N",
        ])

    print(_format_table(headers, rows))


def cmd_league_values(args: argparse.Namespace) -> None:
    """Show KTC values for all players on a user's roster."""
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players, build_ktc_to_sleeper_map

    username = args.username
    league_filter = args.league
    fmt = args.format

    async def _run() -> tuple:
        from datetime import datetime
        season = str(datetime.now().year)
        async with SleeperClient() as client:
            user = await client.users.get_user(username)
            leagues = await client.leagues.get_leagues_for_user(user.user_id, "nfl", season)
            return user, leagues, client

    print(f"Fetching data for '{username}'...")

    try:
        user, leagues, _ = asyncio.run(_run())
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
        print(f"Multiple leagues found. Use --league to pick one:")
        for lg in leagues:
            print(f"  - {lg.name}")
        sys.exit(1)

    print(f"League: {league.name}")

    async def _fetch_roster_data() -> tuple:
        async with SleeperClient() as client:
            rosters = await client.leagues.get_rosters(league.league_id)
            sleeper_players = await client.get_all_players()
            return rosters, sleeper_players

    rosters, sleeper_players = asyncio.run(_fetch_roster_data())

    # Find user's roster
    user_roster = None
    for r in rosters:
        if r.owner_id == user.user_id:
            user_roster = r
            break

    if user_roster is None:
        print(f"No roster found for '{username}' in {league.name}.")
        sys.exit(1)

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    ktc_map = build_ktc_to_sleeper_map(ktc_players, sleeper_players)

    # Reverse map: sleeper_id -> KTCPlayer
    ktc_by_id = {p.ktc_id: p for p in ktc_players}
    sleeper_to_ktc: dict[str, object] = {}
    for ktc_id, sleeper_id in ktc_map.items():
        sleeper_to_ktc[sleeper_id] = ktc_by_id[ktc_id]

    headers = ["Player", "Pos", "Team", "KTC Value", "KTC Rank"]
    rows = []
    total_value = 0

    for pid in user_roster.players:
        sp = sleeper_players.get(pid)
        name = (sp.full_name if sp else None) or pid
        pos = (sp.position if sp else None) or "?"
        team = (sp.team if sp else None) or "FA"

        ktc_p = sleeper_to_ktc.get(pid)
        if ktc_p:
            val = ktc_p.superflex.value if fmt == "sf" else ktc_p.one_qb.value  # type: ignore[union-attr]
            rank = ktc_p.superflex.rank if fmt == "sf" else ktc_p.one_qb.rank  # type: ignore[union-attr]
        else:
            val = 0
            rank = 0

        total_value += val
        rows.append([name, pos, team, str(val), str(rank) if rank else "-"])

    rows.sort(key=lambda r: int(r[3]), reverse=True)

    print()
    print(f"Roster Values ({fmt.upper()}):")
    print()
    print(_format_table(headers, rows))
    print()
    print(f"Total Roster Value: {total_value:,}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sleeper",
        description="Sleeper Fantasy Football SDK CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # market-value
    mv = subparsers.add_parser("market-value", help="KTC value vs actual trade price")
    mv.add_argument("player_name", nargs="+", help="Player name")
    mv.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")

    # league-values
    lv = subparsers.add_parser("league-values", help="KTC values for your roster")
    lv.add_argument("username", help="Sleeper username")
    lv.add_argument("--league", help="League name filter")
    lv.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "market-value":
        cmd_market_value(args)
    elif args.command == "league-values":
        cmd_league_values(args)


if __name__ == "__main__":
    main()
