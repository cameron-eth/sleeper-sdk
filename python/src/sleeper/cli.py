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


def _resolve_league(username: str, league_filter: str | None) -> tuple:
    """Fetch user + leagues and resolve to a single league. Returns (user, league, sleeper_players, rosters)."""
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
        print(f"Multiple leagues found. Use --league to pick one:")
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


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
    from sleeper.enrichment.ktc import fetch_ktc_players

    fmt = args.format
    user, league = _resolve_league(args.username, args.league)
    print(f"League: {league.name}")

    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

    # Find user's roster
    user_roster = None
    for r in rosters:
        if r.owner_id == user.user_id:
            user_roster = r
            break

    if user_roster is None:
        print(f"No roster found for '{args.username}' in {league.name}.")
        sys.exit(1)

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)

    headers = ["Player", "Pos", "Team", "KTC Value", "KTC Rank"]
    rows = []
    total_value = 0

    for pid in user_roster.players:
        sp = sleeper_players.get(pid)
        name = (sp.full_name if sp else None) or pid
        pos = (sp.position if sp else None) or "?"
        team = (sp.team if sp else None) or "FA"

        ktc_p = sleeper_to_ktc.get(pid)
        val = _ktc_value(ktc_p, fmt)
        rank = _ktc_rank(ktc_p, fmt)

        total_value += val
        rows.append([name, pos, team, str(val), str(rank) if rank else "-"])

    rows.sort(key=lambda r: int(r[3]), reverse=True)

    print()
    print(f"Roster Values ({fmt.upper()}):")
    print()
    print(_format_table(headers, rows))
    print()
    print(f"Total Roster Value: {total_value:,}")


def cmd_roster_rank(args: argparse.Namespace) -> None:
    """Rank all teams in a league by total KTC roster value."""
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players

    fmt = args.format
    league_filter = args.league

    # Need username OR league_id — use username approach
    user, league = _resolve_league(args.username, league_filter)
    print(f"League: {league.name}")

    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

    # Fetch league users to get display names
    async def _get_users():
        async with SleeperClient() as client:
            return await client.leagues.get_users(league.league_id)

    league_users = asyncio.run(_get_users())
    user_display: dict[str, str] = {}
    for u in (league_users or []):
        uid = str(u.user_id) if hasattr(u, "user_id") else ""
        disp = u.display_name if hasattr(u, "display_name") else ""
        if uid and disp:
            user_display[uid] = disp

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)

    rows = []
    for roster in rosters:
        owner_id = str(roster.owner_id or "")
        display = user_display.get(owner_id) or owner_id or f"Roster {roster.roster_id}"

        players = roster.players or []
        total = sum(_ktc_value(sleeper_to_ktc.get(pid), fmt) for pid in players)

        # Best player
        best_pid = max(players, key=lambda pid: _ktc_value(sleeper_to_ktc.get(pid), fmt), default=None)
        best_name = ""
        if best_pid:
            sp = sleeper_players.get(best_pid)
            best_name = (sp.full_name if sp else None) or best_pid

        rows.append([display, str(len(players)), f"{total:,}", best_name])

    rows.sort(key=lambda r: int(r[2].replace(",", "")), reverse=True)

    # Add rank column
    ranked = [[str(i + 1)] + row for i, row in enumerate(rows)]

    print()
    print(f"League Roster Rankings ({fmt.upper()}):")
    print()
    print(_format_table(["Rank", "Team", "Players", "Total Value", "Best Player"], ranked))


def cmd_trade_check(args: argparse.Namespace) -> None:
    """Evaluate a proposed trade between two sets of players."""
    from sleeper.enrichment.ktc import fetch_ktc_players, _find_ktc_player, _normalize_name

    fmt = args.format

    # Parse --give and --get player lists
    give_names = args.give  # list of strings (possibly multi-word joined)
    get_names = args.get

    print(f"Fetching KTC values ({fmt.upper()})...")
    print()
    ktc_players = fetch_ktc_players()

    def resolve_players(names: list[str]) -> list:
        found = []
        not_found = []
        for name in names:
            p = _find_ktc_player(name, ktc_players)
            if p:
                found.append(p)
            else:
                not_found.append(name)
        return found, not_found

    give_found, give_missing = resolve_players(give_names)
    get_found, get_missing = resolve_players(get_names)

    if give_missing:
        print(f"Warning: Not found in KTC — {', '.join(give_missing)}")
    if get_missing:
        print(f"Warning: Not found in KTC — {', '.join(get_missing)}")

    give_total = sum(_ktc_value(p, fmt) for p in give_found)
    get_total = sum(_ktc_value(p, fmt) for p in get_found)
    diff = get_total - give_total
    pct = round(get_total / give_total * 100, 1) if give_total > 0 else None

    print("YOU GIVE:")
    headers_side = ["Player", "Pos", "Team", "KTC Value"]
    give_rows = [[p.player_name, p.position, p.team, f"{_ktc_value(p, fmt):,}"] for p in give_found]
    print(_format_table(headers_side, give_rows) if give_rows else "  (none)")
    print(f"  Subtotal: {give_total:,}")
    print()
    print("YOU GET:")
    get_rows = [[p.player_name, p.position, p.team, f"{_ktc_value(p, fmt):,}"] for p in get_found]
    print(_format_table(headers_side, get_rows) if get_rows else "  (none)")
    print(f"  Subtotal: {get_total:,}")
    print()
    print(f"Net Difference:  {diff:+,}  ({pct:.1f}% of give side)" if pct else f"Net Difference:  {diff:+,}")
    print()

    if diff > 500:
        verdict = "WIN  — you gain significant value"
    elif diff > 0:
        verdict = "SLIGHT WIN — you gain minor value"
    elif diff > -500:
        verdict = "SLIGHT LOSS — you give up minor value"
    else:
        verdict = "LOSS — you give up significant value"

    print(f"Verdict: {verdict}")


def cmd_trending(args: argparse.Namespace) -> None:
    """Show players with the biggest KTC value movement (7-day trend)."""
    from sleeper.enrichment.ktc import fetch_ktc_players

    fmt = args.format
    n = args.top
    direction = args.direction  # "up", "down", or "both"
    pos_filter = args.position.upper() if args.position else None

    print(f"Fetching KTC trends ({fmt.upper()})...")
    print()
    ktc_players = fetch_ktc_players()

    scored = []
    for p in ktc_players:
        if "Pick" in p.player_name:
            continue
        if pos_filter and p.position.upper() != pos_filter:
            continue
        val = _ktc_value(p, fmt)
        if val == 0:
            continue
        trend = _ktc_trend(p, fmt)
        scored.append((trend, p, val))

    if direction == "up":
        scored = [(t, p, v) for t, p, v in scored if t > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        label = "Rising"
    elif direction == "down":
        scored = [(t, p, v) for t, p, v in scored if t < 0]
        scored.sort(key=lambda x: x[0])
        label = "Falling"
    else:
        scored.sort(key=lambda x: abs(x[0]), reverse=True)
        label = "Moving"

    top = scored[:n]

    rows = []
    for trend, p, val in top:
        trend_str = f"+{trend}" if trend > 0 else str(trend)
        pct = round(trend / (val - trend) * 100, 1) if (val - trend) != 0 else 0.0
        pct_str = f"+{pct}%" if pct > 0 else f"{pct}%"
        rows.append([p.player_name, p.position, p.team, f"{val:,}", trend_str, pct_str])

    title = f"Top {n} {label} Players — 7-Day Trend ({fmt.upper()})"
    if pos_filter:
        title += f" [{pos_filter}]"
    print(title)
    print()
    print(_format_table(["Player", "Pos", "Team", "KTC Value", "Δ Value", "Δ %"], rows))


def cmd_buy_sell(args: argparse.Namespace) -> None:
    """Players trading below (buy-low) or above (sell-high) their KTC value."""
    from sleeper.enrichment.ktc import fetch_ktc_players, fetch_ktc_trades, _compute_implied_price

    fmt = args.format
    n = args.top
    mode = args.mode  # "buy" or "sell"
    pos_filter = args.position.upper() if args.position else None
    min_trades = args.min_trades

    print(f"Fetching KTC data ({fmt.upper()})... this may take a moment.")
    print()
    ktc_players = fetch_ktc_players()
    trades = fetch_ktc_trades()

    ktc_by_id = {p.ktc_id: p for p in ktc_players}
    ktc_by_name = {p.player_name: p for p in ktc_players}

    # For each player, compute their market value across all trades
    from collections import defaultdict
    import statistics as _stats
    player_implied: dict[str, list[int]] = defaultdict(list)

    for trade in trades:
        is_sf = (trade.settings.qbs or 1) >= 2
        if fmt == "sf" and not is_sf:
            continue
        if fmt == "1qb" and is_sf:
            continue

        all_ids = set(trade.side_one.player_ids) | set(trade.side_two.player_ids)
        for pid in all_ids:
            p = ktc_by_id.get(pid)
            if not p or not p.player_name or "Pick" in p.player_name:
                continue
            if pos_filter and p.position.upper() != pos_filter:
                continue

            detail = _compute_implied_price(trade, pid, ktc_by_id, ktc_by_name, fmt)
            if detail and detail.implied_price > 0:
                player_implied[pid].append(detail.implied_price)

    rows = []
    for pid, values in player_implied.items():
        if len(values) < min_trades:
            continue
        p = ktc_by_id.get(pid)
        if not p:
            continue
        ktc_val = _ktc_value(p, fmt)
        if ktc_val < 500:  # Skip low-value players
            continue

        median_val = int(_stats.median(values))
        pct = round(median_val / ktc_val * 100, 1) if ktc_val > 0 else None
        if pct is None:
            continue

        rows.append((pct, p, ktc_val, median_val, len(values)))

    if mode == "buy":
        # Buy-low: median market value significantly BELOW KTC listed
        filtered = [(pct, p, kv, mv, nt) for pct, p, kv, mv, nt in rows if pct < 90]
        filtered.sort(key=lambda x: x[0])  # most underpriced first
        label = "BUY LOW — Trading Below KTC Value"
    else:
        # Sell-high: median market value significantly ABOVE KTC listed
        filtered = [(pct, p, kv, mv, nt) for pct, p, kv, mv, nt in rows if pct > 110]
        filtered.sort(key=lambda x: x[0], reverse=True)  # most overpriced first
        label = "SELL HIGH — Trading Above KTC Value"

    top = filtered[:n]
    if not top:
        print(f"No players found matching criteria. Try --min-trades 1 or --position.")
        return

    out_rows = []
    for pct, p, ktc_val, median_val, num_t in top:
        diff = median_val - ktc_val
        pct_str = f"{pct:.1f}%"
        diff_str = f"{diff:+,}"
        out_rows.append([p.player_name, p.position, p.team,
                         f"{ktc_val:,}", f"{median_val:,}", pct_str, diff_str, str(num_t)])

    print(f"{label} ({fmt.upper()})")
    if pos_filter:
        print(f"Position filter: {pos_filter}")
    print()
    print(_format_table(
        ["Player", "Pos", "Team", "KTC Listed", "Market Val", "% of KTC", "Diff", "Trades"],
        out_rows,
    ))


def cmd_picks(args: argparse.Namespace) -> None:
    """Show all future pick assets in a league with KTC values."""
    from sleeper.enrichment.ktc import fetch_ktc_players, _find_ktc_player

    fmt = args.format
    user, league = _resolve_league(args.username, args.league)
    print(f"League: {league.name}")

    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

    # Fetch traded picks via Sleeper API
    import asyncio
    from sleeper.client import SleeperClient

    async def _get_picks_and_users():
        async with SleeperClient() as client:
            traded_picks = await client.leagues.get_traded_picks(league.league_id)
            league_users = await client.leagues.get_users(league.league_id)
            return traded_picks, league_users

    traded_picks, league_users = asyncio.run(_get_picks_and_users())

    # Build lookup maps
    user_display: dict[str, str] = {}
    for u in (league_users or []):
        uid = str(u.user_id) if hasattr(u, "user_id") else ""
        disp = u.display_name if hasattr(u, "display_name") else ""
        if uid and disp:
            user_display[uid] = disp

    # roster_id (int) -> display name, via owner_id
    roster_to_owner: dict[str, str] = {}
    user_id_to_roster: dict[str, str] = {}
    for r in rosters:
        rid = str(r.roster_id)
        oid = str(r.owner_id or "")
        display = user_display.get(oid) or oid or f"Roster {rid}"
        roster_to_owner[rid] = display
        if oid:
            user_id_to_roster[oid] = rid

    print("Fetching KTC pick values...")
    ktc_players = fetch_ktc_players()
    ktc_by_name = {p.player_name: p for p in ktc_players}

    from datetime import datetime
    current_year = datetime.now().year

    # TradedPick fields: season, round, roster_id (original slot), previous_owner_id, owner_id (current holder ROSTER_ID)
    # Note: in Sleeper's TradedPick, owner_id is actually a roster_id (not user_id)
    # Build: (season, round, original_roster_id) -> current_holder_display
    traded_pick_map: dict[tuple, str] = {}
    for tp in (traded_picks or []):
        season = str(tp.season)
        rnd = int(tp.round)
        orig_roster = str(tp.roster_id)  # original pick slot (roster_id)
        curr_roster_id = str(tp.owner_id)  # current holder's roster_id
        curr_display = roster_to_owner.get(curr_roster_id, f"Roster {curr_roster_id}")
        traded_pick_map[(season, rnd, orig_roster)] = curr_display

    # Determine league size + years to show (current + 2 future)
    num_teams = len(rosters)
    num_rounds = 4
    years = [str(y) for y in range(current_year, current_year + 3)]

    from sleeper.enrichment.ktc import _classify_pick_tier, _get_pick_ktc_value

    rows = []
    for year in years:
        for rnd in range(1, num_rounds + 1):
            for pick_num in range(1, num_teams + 1):
                orig_roster = str(pick_num)
                tier = _classify_pick_tier(pick_num, num_teams)
                rnd_suffix = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(rnd, f"{rnd}th")
                pick_label = f"{year} {tier} {rnd_suffix}"
                pick_str = f"{year} Pick {rnd}.{pick_num:02d}"

                ktc_val = _get_pick_ktc_value(pick_str, ktc_by_name, fmt, num_teams)

                # Current holder: check traded map, else original roster owner
                current_holder_display = traded_pick_map.get((year, rnd, orig_roster))
                orig_holder = roster_to_owner.get(orig_roster, f"Roster {orig_roster}")
                traded_flag = "Y" if current_holder_display else "N"
                holder = current_holder_display if current_holder_display else orig_holder

                rows.append([
                    pick_label,
                    f"P{pick_num:02d}",
                    f"{ktc_val:,}",
                    holder,
                    orig_holder if traded_flag == "Y" else "",
                    traded_flag,
                ])

    # Filter to only traded picks if --traded-only
    if args.traded_only:
        rows = [r for r in rows if r[5] == "Y"]

    # Filter to specific owner if --owner
    if args.owner:
        owner_filter = args.owner.lower()
        rows = [r for r in rows if owner_filter in r[3].lower()]

    rows.sort(key=lambda r: int(r[2].replace(",", "")), reverse=True)

    print()
    print(f"Future Pick Assets ({fmt.upper()}) — {league.name}")
    print()
    if rows:
        print(_format_table(
            ["Pick", "Slot", "KTC Value", "Current Holder", "Original Owner", "Traded"],
            rows,
        ))
    else:
        print("(no picks found matching filters)")


def cmd_pe_ratio(args: argparse.Namespace) -> None:
    """P/E ratio: KTC price vs real fantasy production (FFPG)."""
    from datetime import datetime
    from sleeper.enrichment.ktc import fetch_ktc_players, build_ktc_to_sleeper_map
    # Load valuation module by file path to bypass analytics/__init__.py
    # (which has a pre-existing broken import on user_collector).
    import importlib.util
    import sys as _sys
    import os
    _val_path = os.path.join(os.path.dirname(__file__), "analytics", "valuation.py")
    _spec = importlib.util.spec_from_file_location("_sleeper_valuation", _val_path)
    valuation = importlib.util.module_from_spec(_spec)
    _sys.modules["_sleeper_valuation"] = valuation
    _spec.loader.exec_module(valuation)
    compute_pe_ratios = valuation.compute_pe_ratios

    fmt = args.format
    scoring = args.scoring
    min_games = args.min_games
    n = args.top
    sort_key = args.sort
    pos_filter = args.position.upper() if args.position else None

    # Parse seasons
    if args.seasons:
        try:
            seasons = [int(s.strip()) for s in args.seasons.split(",")]
        except ValueError:
            print(f"Invalid --seasons '{args.seasons}'. Use a comma-separated list of years.")
            sys.exit(1)
    else:
        seasons = [datetime.now().year]

    # Stats source (optional dep)
    try:
        from sleeper.enrichment.stats import get_season_stats, HAS_NFLREADPY
    except ImportError:
        print("nflreadpy is required for pe-ratio. Install with: pip install 'sleeper-sdk[nfl-data]'")
        sys.exit(1)
    if not HAS_NFLREADPY:
        print("nflreadpy is required for pe-ratio. Install with: pip install 'sleeper-sdk[nfl-data]'")
        sys.exit(1)

    print(f"Fetching KTC values ({fmt.upper()})...")
    ktc_players = fetch_ktc_players()

    # Need sleeper IDs on each KTC player so we can join to stats.
    print("Loading Sleeper player metadata...")
    import asyncio
    from sleeper.client import SleeperClient

    async def _get_sleeper_players():
        async with SleeperClient() as client:
            return await client.get_all_players()

    sleeper_players = asyncio.run(_get_sleeper_players())
    ktc_to_sleeper = build_ktc_to_sleeper_map(ktc_players, sleeper_players)
    for p in ktc_players:
        p.sleeper_id = ktc_to_sleeper.get(p.ktc_id)

    print(f"Loading NFL stats for seasons {seasons} (this may take a moment)...")
    season_stats = get_season_stats(seasons)

    pes = compute_pe_ratios(
        ktc_players,
        season_stats,
        seasons=seasons,
        fmt=fmt,
        scoring=scoring,
        min_games=min_games,
    )

    # Filter
    if pos_filter:
        pes = [r for r in pes if r.position == pos_filter]
    if args.max_age is not None:
        pes = [r for r in pes if r.age is not None and r.age <= args.max_age]
    if args.min_age is not None:
        pes = [r for r in pes if r.age is not None and r.age >= args.min_age]
    if args.min_ppg is not None:
        pes = [r for r in pes if r.ffpg >= args.min_ppg]
    if args.min_ktc is not None:
        pes = [r for r in pes if r.ktc_value >= args.min_ktc]
    if args.exclude_speculative:
        pes = [r for r in pes if r.signal != "speculative"]

    # Sort
    if sort_key == "pe":
        pes.sort(key=lambda r: (r.pe_ratio is None, r.pe_ratio if r.pe_ratio is not None else 0))
    elif sort_key == "pe-desc":
        pes.sort(key=lambda r: (r.pe_ratio is None, -(r.pe_ratio or 0)))
    elif sort_key == "value":
        pes.sort(key=lambda r: -r.ktc_value)
    elif sort_key == "ffpg":
        pes.sort(key=lambda r: -r.ffpg)

    top = pes[:n]
    if not top:
        print("(no players matched)")
        return

    headers = ["Player", "Pos", "Team", "Age", "KTC", "FFPG", "Games", "PE", "Signal"]
    rows = []
    for r in top:
        age_str = f"{r.age:.0f}" if r.age else "-"
        pe_str = f"{r.pe_ratio:.2f}" if r.pe_ratio is not None else "—"
        rows.append([
            r.name,
            r.position,
            r.team,
            age_str,
            f"{r.ktc_value:,}",
            f"{r.ffpg:.1f}" if r.ffpg else "-",
            str(r.games) if r.games else "-",
            pe_str,
            r.signal,
        ])

    title = f"Player P/E Ratios ({fmt.upper()}, seasons={seasons}, scoring={scoring.upper()})"
    if pos_filter:
        title += f" [{pos_filter}]"
    print()
    print(title)
    print()
    print(_format_table(headers, rows))
    print()
    print("PE < 0.7 = undervalued | ~1.0 = fair | > 1.5 = overvalued | — = speculative (insufficient games)")


# ---------------------------------------------------------------------------
# Main / argparse
# ---------------------------------------------------------------------------


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

    # roster-rank
    rr = subparsers.add_parser("roster-rank", help="Rank all teams by total KTC roster value")
    rr.add_argument("username", help="Sleeper username")
    rr.add_argument("--league", help="League name filter")
    rr.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")

    # trade-check
    tc = subparsers.add_parser("trade-check", help="Evaluate a proposed trade")
    tc.add_argument("--give", nargs="+", required=True, metavar="PLAYER",
                    help="Players you give up (quoted names, e.g. 'Ja Marr Chase')")
    tc.add_argument("--get", nargs="+", required=True, metavar="PLAYER",
                    help="Players you receive (quoted names)")
    tc.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")

    # trending
    tr = subparsers.add_parser("trending", help="Players with biggest 7-day KTC value movement")
    tr.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    tr.add_argument("--top", type=int, default=20, help="Number of players to show (default: 20)")
    tr.add_argument("--direction", choices=["up", "down", "both"], default="both",
                    help="Rising, falling, or both (default: both)")
    tr.add_argument("--position", help="Filter by position (QB, RB, WR, TE)")

    # buy-sell
    bs = subparsers.add_parser("buy-sell", help="Players trading below/above their KTC value")
    bs.add_argument("mode", choices=["buy", "sell"], help="'buy' = buy-low candidates, 'sell' = sell-high")
    bs.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    bs.add_argument("--top", type=int, default=15, help="Number of players to show (default: 15)")
    bs.add_argument("--position", help="Filter by position (QB, RB, WR, TE)")
    bs.add_argument("--min-trades", type=int, default=2, dest="min_trades",
                    help="Minimum trades required (default: 2)")

    # pe-ratio
    pe = subparsers.add_parser("pe-ratio", help="Player P/E ratio: KTC price vs real production (FFPG)")
    pe.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    pe.add_argument("--seasons", help="Comma-separated season years (default: current year)")
    pe.add_argument("--scoring", choices=["ppr", "standard"], default="ppr", help="Scoring (default: ppr)")
    pe.add_argument("--position", help="Filter by position (QB, RB, WR, TE)")
    pe.add_argument("--min-games", type=int, default=4, dest="min_games",
                    help="Minimum games for non-speculative PE (default: 4)")
    pe.add_argument("--max-age", type=float, default=None, dest="max_age",
                    help="Exclude players older than this (e.g. 26 for dynasty targets)")
    pe.add_argument("--min-age", type=float, default=None, dest="min_age",
                    help="Exclude players younger than this")
    pe.add_argument("--min-ppg", type=float, default=None, dest="min_ppg",
                    help="Minimum FFPG to include (e.g. 8 to skip irrelevant scrubs)")
    pe.add_argument("--min-ktc", type=int, default=None, dest="min_ktc",
                    help="Minimum KTC value to include (e.g. 3000 to skip deep bench)")
    pe.add_argument("--exclude-speculative", action="store_true", dest="exclude_speculative",
                    help="Hide players with no real production sample (rookies/IR)")
    pe.add_argument("--top", type=int, default=25, help="Number of players to show (default: 25)")
    pe.add_argument("--sort", choices=["pe", "pe-desc", "value", "ffpg"], default="pe",
                    help="Sort order (default: pe = cheapest multiples first)")

    # picks
    pk = subparsers.add_parser("picks", help="Show future pick assets in a league with KTC values")
    pk.add_argument("username", help="Sleeper username")
    pk.add_argument("--league", help="League name filter")
    pk.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    pk.add_argument("--owner", help="Filter by owner name")
    pk.add_argument("--traded-only", action="store_true", dest="traded_only",
                    help="Show only traded picks")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "market-value":
        cmd_market_value(args)
    elif args.command == "league-values":
        cmd_league_values(args)
    elif args.command == "roster-rank":
        cmd_roster_rank(args)
    elif args.command == "trade-check":
        cmd_trade_check(args)
    elif args.command == "trending":
        cmd_trending(args)
    elif args.command == "buy-sell":
        cmd_buy_sell(args)
    elif args.command == "picks":
        cmd_picks(args)
    elif args.command == "pe-ratio":
        cmd_pe_ratio(args)


if __name__ == "__main__":
    main()
