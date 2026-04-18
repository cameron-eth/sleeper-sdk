"""Sleeper SDK CLI."""
from __future__ import annotations

import argparse
import os
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
    print(f"Raw Net Difference:  {diff:+,}  ({pct:.1f}% of give side)" if pct else f"Raw Net Difference:  {diff:+,}")

    # Apply KTC-style value adjustment
    from sleeper.analytics.value_adjustment import apply_adjustment_to_delta
    give_vals = [_ktc_value(p, fmt) for p in give_found]
    get_vals = [_ktc_value(p, fmt) for p in get_found]
    adjusted_diff, adj = apply_adjustment_to_delta(diff, give_vals, get_vals)

    if adj.adjustment > 0:
        print(f"Value Adjustment:    {adj.adjustment:+,} KTC  (favors {adj.favors} side — {adj.stud_tier} stud tier)")
        print(f"  └─ {adj.rationale}")
        print(f"Adjusted Net:        {adjusted_diff:+,}")
    print()

    # Use adjusted_diff for the final verdict
    final = adjusted_diff
    if final > 500:
        verdict = "WIN  — you gain significant value (after Value Adjustment)"
    elif final > 0:
        verdict = "SLIGHT WIN — you gain minor value (after Value Adjustment)"
    elif final > -500:
        verdict = "SLIGHT LOSS — you give up minor value (after Value Adjustment)"
    else:
        verdict = "LOSS — you give up significant value (after Value Adjustment)"

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


def cmd_ktc_trend(args: argparse.Namespace) -> None:
    """KTC value history from local snapshot files."""
    from sleeper.enrichment.ktc_history import (
        load_player_history,
        top_movers,
        list_snapshot_dates,
    )

    sub = getattr(args, "kt_subcommand", None)
    snapshot_dir = getattr(args, "snapshot_dir", None) or "data/ktc"

    dates = list_snapshot_dates(snapshot_dir)
    if not dates:
        print(f"No KTC snapshots found in '{snapshot_dir}'.")
        print("Run: python scripts/snapshot_ktc.py")
        return

    if sub == "player":
        name = " ".join(args.player_name)
        trend = load_player_history(name, snapshot_dir=snapshot_dir, days=args.days)
        if not trend:
            print(f"No history found for '{name}' in {snapshot_dir}")
            return

        attr = "sf_value" if args.format == "sf" else "oqb_value"
        rank_attr = "sf_rank" if args.format == "sf" else "oqb_rank"

        print()
        print(f"{trend.name} ({trend.position} {trend.team}) — KTC {args.format.upper()} trend")
        print(f"Snapshots: {len(trend.points)} (from {trend.points[0].date} to {trend.points[-1].date})")
        print()

        headers = ["Date", "Value", "Rank", "Delta"]
        rows = []
        prev = None
        for pt in trend.points:
            val = getattr(pt, attr)
            rank = getattr(pt, rank_attr)
            delta = "" if prev is None else f"{val - prev:+d}"
            rows.append([pt.date, f"{val:,}", str(rank), delta])
            prev = val
        print(_format_table(headers, rows))

        total_delta = trend.delta(args.format)
        if total_delta is not None:
            pct = (total_delta / getattr(trend.points[0], attr)) * 100 if getattr(trend.points[0], attr) else 0
            arrow = "↑" if total_delta > 0 else ("↓" if total_delta < 0 else "→")
            print()
            print(f"Net: {arrow} {total_delta:+d} ({pct:+.1f}%)")
        return

    if sub == "movers":
        movers = top_movers(
            fmt=args.format,
            days=args.days,
            min_value=args.min_value,
            limit=args.top,
            snapshot_dir=snapshot_dir,
        )
        if not movers:
            print(f"Not enough snapshots in the last {args.days} days to compute movers.")
            print(f"Available snapshots: {dates}")
            return

        print()
        print(f"Top KTC movers ({args.format.upper()}, last {args.days}d, min value {args.min_value:,})")
        print()
        headers = ["Player", "Pos", "Team", "Start", "End", "Delta", "%"]
        rows = []
        attr = "sf_value" if args.format == "sf" else "oqb_value"
        for trend, delta in movers:
            start = getattr(trend.points[0], attr)
            end = getattr(trend.points[-1], attr)
            pct = (delta / start * 100) if start else 0
            rows.append([
                trend.name,
                trend.position,
                trend.team or "FA",
                f"{start:,}",
                f"{end:,}",
                f"{delta:+d}",
                f"{pct:+.1f}%",
            ])
        print(_format_table(headers, rows))
        return

    # No subcommand given — list what snapshots we have
    print(f"Snapshots in {snapshot_dir}: {len(dates)}")
    if dates:
        print(f"  Oldest: {dates[0]}")
        print(f"  Newest: {dates[-1]}")
    print()
    print("Usage:")
    print("  sleeper ktc-trend player 'Jayden Daniels' [--days 30] [--format sf|1qb]")
    print("  sleeper ktc-trend movers [--days 7] [--top 20] [--format sf|1qb]")


SUGGESTION_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".sleeper-sdk")


def _suggestion_cache_path(username: str, league_id: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in f"{username}__{league_id}")
    return os.path.join(SUGGESTION_CACHE_DIR, f"suggestions_{safe}.json")


def _save_suggestions_cache(path: str, payload: dict) -> None:
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _load_suggestions_cache(path: str) -> dict | None:
    import json
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def cmd_suggest_trades(args) -> None:
    """Suggest 1-for-1 trades that improve your roster's positional balance."""
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players

    fmt = args.format
    user, league = _resolve_league(args.username, args.league)
    print(f"League: {league.name}")

    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

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

    my_roster = next((r for r in rosters if r.owner_id == user.user_id), None)
    if my_roster is None:
        print(f"No roster found for '{args.username}' in {league.name}.")
        sys.exit(1)

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)

    pe_by_sid = {}
    if args.with_pe:
        try:
            from sleeper.enrichment.stats import get_season_stats, HAS_NFLREADPY
            if HAS_NFLREADPY:
                from datetime import datetime
                from sleeper.enrichment.ktc import build_ktc_to_sleeper_map
                # Need sleeper_id on each KTCPlayer for the valuation join
                ktc_to_sleeper = build_ktc_to_sleeper_map(ktc_players, sleeper_players)
                for p in ktc_players:
                    p.sleeper_id = ktc_to_sleeper.get(p.ktc_id)
                print(f"Computing P/E ratios for {datetime.now().year}...")
                stats = get_season_stats([datetime.now().year])
                # Lazy-load valuation by file path to dodge analytics/__init__ chain
                import importlib.util as _iu, sys as _sys
                _vp = os.path.join(os.path.dirname(__file__), "analytics", "valuation.py")
                _spec = _iu.spec_from_file_location("_sleeper_valuation_pe", _vp)
                _val = _iu.module_from_spec(_spec)
                _sys.modules["_sleeper_valuation_pe"] = _val
                _spec.loader.exec_module(_val)
                pes = _val.compute_pe_ratios(ktc_players, stats, seasons=[datetime.now().year], fmt=fmt)
                pe_by_sid = {p.sleeper_id: p.pe_ratio for p in pes if p.sleeper_id and p.pe_ratio}
        except Exception as e:
            print(f"(P/E enrichment failed: {e}; continuing without)")

    # Lazy-load trade_suggestions same way
    import importlib.util as _iu, sys as _sys
    _ts_path = os.path.join(os.path.dirname(__file__), "analytics", "trade_suggestions.py")
    _spec = _iu.spec_from_file_location("_sleeper_trade_suggestions", _ts_path)
    _ts = _iu.module_from_spec(_spec)
    _sys.modules["_sleeper_trade_suggestions"] = _ts
    _spec.loader.exec_module(_ts)

    suggestions = _ts.suggest_trades(
        my_roster=my_roster,
        all_rosters=rosters,
        sleeper_players=sleeper_players,
        sleeper_to_ktc=sleeper_to_ktc,
        user_display=user_display,
        pe_by_sleeper_id=pe_by_sid,
        fmt=fmt,
        top=args.top,
        max_per_partner=args.max_per_partner,
        value_tolerance_pct=args.tolerance,
        position_filter=args.position,
    )

    if not suggestions:
        print("\nNo trades found at this tolerance. Try `--tolerance 15` or `--top 25`.")
        return

    headers = ["#", "Partner", "Send", "KTC", "Receive", "KTC", "Raw Δ", "Val Adj", "Adj Δ", "PE Δ", "Rationale"]
    rows = []
    for i, s in enumerate(suggestions, 1):
        send = s.send_players[0]
        recv = s.receive_players[0]
        val_adj_display = f"{s.value_adjustment:+,}" if s.value_adjustment else "0"
        rows.append([
            str(i),
            s.to_owner[:18],
            f"{send.name} ({send.position})",
            f"{send.ktc_value:,}",
            f"{recv.name} ({recv.position})",
            f"{recv.ktc_value:,}",
            f"{s.value_delta:+,}",
            val_adj_display,
            f"{s.adjusted_delta:+,}",
            f"{s.pe_arbitrage:+.2f}" if s.pe_arbitrage else "-",
            s.rationale[:55],
        ])

    print()
    print(f"Trade Suggestions for {args.username} ({fmt.upper()})")
    print()
    print(_format_table(headers, rows))
    print()
    print(f"To send a suggestion: sleeper send-trade {args.username} --league \"{league.name}\" --suggestion N")

    # Cache for send-trade --suggestion N
    cache_path = _suggestion_cache_path(args.username, league.league_id)
    cache_payload = {
        "username": args.username,
        "league_id": league.league_id,
        "league_name": league.name,
        "format": fmt,
        "my_roster_id": my_roster.roster_id,
        "saved_at": int(__import__("time").time()),
        "suggestions": [
            {
                "to_roster_id": s.to_roster_id,
                "to_owner": s.to_owner,
                "send": [{"sleeper_id": p.sleeper_id, "name": p.name, "position": p.position,
                          "team": p.team, "ktc_value": p.ktc_value} for p in s.send_players],
                "receive": [{"sleeper_id": p.sleeper_id, "name": p.name, "position": p.position,
                             "team": p.team, "ktc_value": p.ktc_value} for p in s.receive_players],
                "value_delta": s.value_delta,
                "rationale": s.rationale,
            }
            for s in suggestions
        ],
    }
    _save_suggestions_cache(cache_path, cache_payload)


def cmd_gm_mode(args) -> None:
    """GM Mode: full team archetype analysis with strategic recommendations."""
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players

    user, league = _resolve_league(args.username, args.league)
    print(f"League: {league.name}")
    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

    async def _get_users_and_picks():
        async with SleeperClient() as client:
            users = await client.leagues.get_users(league.league_id)
            try:
                picks = await client.leagues.get_traded_picks(league.league_id)
            except Exception:
                picks = []
            return users, picks

    league_users, traded_picks = asyncio.run(_get_users_and_picks())
    user_display: dict[str, str] = {}
    for u in (league_users or []):
        uid = str(u.user_id) if hasattr(u, "user_id") else ""
        disp = u.display_name if hasattr(u, "display_name") else ""
        if uid and disp:
            user_display[uid] = disp

    # Find target roster (by username, or --owner override for another team)
    target_user_id = user.user_id
    if args.owner and args.owner != args.username:
        for uid, disp in user_display.items():
            if disp.lower() == args.owner.lower():
                target_user_id = uid
                break

    my_roster = next((r for r in rosters if str(r.owner_id) == str(target_user_id)), None)
    if my_roster is None:
        print(f"No roster found for '{args.owner or args.username}' in {league.name}.")
        sys.exit(1)

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)

    # Pick capital is approximated from traded picks (rough; a future enhancement is to
    # pull the full draft-pick list from Sleeper and value each with KTC pick values)
    pick_capital = 0

    # Production rank from roster settings (fpts)
    production_rank = None
    record_str = None
    try:
        roster_records = []
        for r in rosters:
            wins = getattr(r.settings, "wins", 0) if r.settings else 0
            losses = getattr(r.settings, "losses", 0) if r.settings else 0
            pf = getattr(r.settings, "fpts", 0) if r.settings else 0
            roster_records.append((r.roster_id, wins, losses, pf))
        roster_records.sort(key=lambda x: -x[3])
        for i, rec in enumerate(roster_records):
            if rec[0] == my_roster.roster_id:
                production_rank = i + 1
                record_str = f"{rec[1]}-{rec[2]}"
                break
    except Exception:
        pass

    # Lazy-load gm_mode module
    import importlib.util as _iu, sys as _sys
    _gm_path = os.path.join(os.path.dirname(__file__), "analytics", "gm_mode.py")
    _spec = _iu.spec_from_file_location("_sleeper_gm_mode", _gm_path)
    _gm = _iu.module_from_spec(_spec)
    _sys.modules["_sleeper_gm_mode"] = _gm
    _spec.loader.exec_module(_gm)

    report = _gm.generate_gm_report(
        my_roster=my_roster,
        all_rosters=rosters,
        sleeper_players=sleeper_players,
        sleeper_to_ktc=sleeper_to_ktc,
        user_display=user_display,
        production_rank=production_rank,
        record_str=record_str,
        pick_capital=pick_capital,
        fmt=args.format,
    )

    arch = report.archetype
    print()
    print("=" * 78)
    print(f"  GM MODE REPORT — {arch.owner} ({league.name})")
    print("=" * 78)
    print()
    print(f"  ARCHETYPE:      {arch.archetype}   (confidence {arch.confidence*100:.0f}%)")
    print(f"  REASONING:      {arch.reasoning}")
    print()
    print(f"  Total Value:    {arch.total_ktc_value:,} KTC  (rank {arch.value_rank} of {report.league_context['size']})")
    if arch.production_rank:
        print(f"  Production:     rank {arch.production_rank} of {report.league_context['size']}  ({arch.record_str})")
    if arch.avg_starter_age:
        print(f"  Starter Age:    {arch.avg_starter_age:.1f} avg")
    if arch.young_asset_pct is not None:
        print(f"  Young Value:    {arch.young_asset_pct*100:.0f}% from players under 26")
    print()

    print("-" * 78)
    print("  POSITIONAL BREAKDOWN")
    print("-" * 78)
    print(f"  {'Pos':<5} {'Starters':>10} {'Bench':>10} {'Total':>10} {'Lg Avg':>12} {'Rank':>6} {'Strength':>10} {'Depth':>8}")
    for p in arch.positions:
        strength_tag = "STRONG" if p.strength_score >= 0.4 else ("WEAK" if p.strength_score <= -0.4 else "AVG")
        depth_tag = "DEEP" if p.depth_score >= 0.3 else ("SHALLOW" if p.depth_score <= -0.3 else "AVG")
        print(f"  {p.position:<5} {p.starters_value:>10,} {p.bench_value:>10,} {p.total_value:>10,} "
              f"{p.league_avg_total:>12,} {p.rank:>6} {strength_tag:>10} {depth_tag:>8}")
    print()

    if arch.strengths:
        print(f"  STRENGTHS:      {', '.join(arch.strengths)}")
    if arch.weaknesses:
        print(f"  WEAKNESSES:     {', '.join(arch.weaknesses)}")
    print()

    print("-" * 78)
    print("  TOP 5 ASSETS")
    print("-" * 78)
    for p in report.top_assets:
        age_str = f"age {p['age']:.0f}" if p["age"] else "age N/A"
        print(f"  {p['name']:<25} {p['position']:<3} {p['team']:<4} KTC {p['ktc']:>6,}  ({age_str})")
    print()

    if report.liabilities:
        print("-" * 78)
        print("  LIABILITIES (high-value aging players)")
        print("-" * 78)
        for p in report.liabilities:
            print(f"  {p['name']:<25} {p['position']:<3} KTC {p['ktc']:>6,}  (age {p['age']:.0f})")
        print()

    print("-" * 78)
    print("  STRATEGIC RECOMMENDATION")
    print("-" * 78)
    print(f"  {arch.trade_strategy}")
    print()

    if report.targets:
        print("  TRADE TARGETS:")
        for t in report.targets:
            print(f"    - {t['type']}: {t['description']}")
        print()

    print("=" * 78)
    print(f"  Next steps:")
    print(f"    sleeper find-trades {args.username} --league \"{league.name}\"")
    print(f"    sleeper suggest-trades {args.username} --league \"{league.name}\"")
    print("=" * 78)


def cmd_find_trades(args) -> None:
    """Find trades targeting specific positions, with include/exclude filters.

    Modes:
    - Normal: Find trades with overpay tolerance (default: 300-3500)
    - upgrade: Find trades where you get more value back (negative overpay)
    - downtiering: Find trades where you liquidate for lower tier (positive overpay)
    """
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players, build_ktc_to_sleeper_map

    # Auto-adjust thresholds based on mode if not explicitly set
    if args.min_overpay is None:
        if args.mode == "upgrade":
            args.min_overpay = -5000
        else:
            args.min_overpay = 300

    if args.max_overpay is None:
        if args.mode == "upgrade":
            args.max_overpay = 0
        elif args.mode == "downtiering":
            args.max_overpay = 5000
        else:
            args.max_overpay = 3500

    user, league = _resolve_league(args.username, args.league)
    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

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

    my_roster = next((r for r in rosters if r.owner_id == user.user_id), None)
    if my_roster is None:
        print(f"No roster found for '{args.username}' in {league.name}.")
        sys.exit(1)

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)

    # Parse include/exclude player lists
    include_set = set(n.lower().replace(".", "").replace("'", "").strip() for n in (args.include or []))
    exclude_set = set(n.lower().replace(".", "").replace("'", "").strip() for n in (args.exclude or []))
    target_positions = set(args.position or [])

    mode_str = f" ({args.mode} mode)" if args.mode != "normal" else ""
    print(f"\nSearching for {target_positions if target_positions else 'any position'} trades{mode_str}...")
    print(f"  Overpay range: {args.min_overpay:+,} to {args.max_overpay:+,}")
    if include_set:
        print(f"  Include: {', '.join(args.include)}")
    if exclude_set:
        print(f"  Exclude: {', '.join(args.exclude)}")

    # Build list of trade targets: RBs/WRs/TEs from other rosters matching filters
    trade_targets = []
    for roster in rosters:
        if roster.owner_id == user.user_id:
            continue
        for pid in (roster.players or []):
            p = sleeper_players.get(pid)
            if not p or not p.position:
                continue

            # Filter by target position
            if target_positions and p.position not in target_positions:
                continue

            # Check include/exclude
            pname = (p.full_name or "").lower().replace(".", "").replace("'", "")
            if include_set and pname not in include_set:
                continue
            if exclude_set and pname in exclude_set:
                continue

            ktc_p = sleeper_to_ktc.get(pid)
            ktc_val = (ktc_p.superflex.value if ktc_p and ktc_p.superflex else 0) or 0

            if ktc_val >= (args.min_ktc or 0):
                owner = user_display.get(str(roster.owner_id), "?")
                trade_targets.append({
                    "owner": owner,
                    "roster_id": roster.roster_id,
                    "name": p.full_name,
                    "position": p.position,
                    "ktc": ktc_val,
                })

    if not trade_targets:
        print("\nNo matching players found. Try adjusting filters.")
        return

    trade_targets.sort(key=lambda x: -x["ktc"])

    # Build list of my trade chips
    my_chips = []
    for pid in (my_roster.players or []):
        p = sleeper_players.get(pid)
        if not p or p.position not in ("WR", "TE", "QB", "RB"):
            continue

        ktc_p = sleeper_to_ktc.get(pid)
        val = (ktc_p.superflex.value if ktc_p and ktc_p.superflex else 0) or 0

        # Skip excluded players
        pname = (p.full_name or "").lower().replace(".", "").replace("'", "")
        if pname in exclude_set:
            continue

        if val > 0:
            my_chips.append({
                "name": p.full_name,
                "position": p.position,
                "ktc": val,
                "pid": pid,
            })

    my_chips.sort(key=lambda x: -x["ktc"])

    # Find trades: target player vs combinations of my chips
    from sleeper.analytics.value_adjustment import compute_value_adjustment

    trades = []
    for target in trade_targets:
        # Single chip (1-for-1: no value adjustment needed, count is equal)
        for chip in my_chips:
            overpay = chip["ktc"] - target["ktc"]
            if args.min_overpay <= overpay <= args.max_overpay:
                adj = compute_value_adjustment([chip["ktc"]], [target["ktc"]])
                trades.append({
                    "target": target,
                    "chips": [chip],
                    "overpay": overpay,
                    "adj": adj,
                    "adjusted_overpay": overpay,  # 1-for-1 has 0 adjustment
                    "score": target["ktc"] * 1.5,
                })

        # Chip + secondary (if different position)
        if not args.single_only:
            for i, chip1 in enumerate(my_chips):
                for chip2 in my_chips[i+1:]:
                    total = chip1["ktc"] + chip2["ktc"]
                    overpay = total - target["ktc"]
                    # Value adjustment: sending 2 chips for 1 target means we give
                    # up a roster spot. If target is a stud, we owe *less* overpay
                    # in effective terms (stud side gets credit). From our POV as
                    # the multi-sender, favors="receive" (we're getting the stud),
                    # so effective cost to us is HIGHER.
                    adj = compute_value_adjustment(
                        send_values=[chip1["ktc"], chip2["ktc"]],
                        receive_values=[target["ktc"]],
                    )
                    # adjusted_overpay = raw_overpay + adj (we pay more when they
                    # have the stud). Cast to positive adjustment on our overpay.
                    adjusted_overpay = overpay + (adj.adjustment if adj.favors == "receive" else -adj.adjustment)
                    if args.min_overpay <= adjusted_overpay <= args.max_overpay:
                        trades.append({
                            "target": target,
                            "chips": [chip1, chip2],
                            "overpay": overpay,
                            "adj": adj,
                            "adjusted_overpay": adjusted_overpay,
                            # Score using the ADJUSTED overpay so value-adjustment
                            # pushes lopsided-but-unfair trades down the list.
                            "score": target["ktc"] * 1.5 - adjusted_overpay * 0.3,
                        })

    if not trades:
        print("\nNo trades found within overpay range. Try adjusting --min-overpay/--max-overpay.")
        return

    trades.sort(key=lambda t: -t["score"])

    # Display results (now with Value Adjustment column)
    headers = ["#", "Partner", "Target", "Pos", "KTC", "You Give", "Total KTC", "Raw Δ", "Val Adj", "Adj Δ"]
    rows = []
    for i, trade in enumerate(trades[:args.top], 1):
        target = trade["target"]
        chips_str = " + ".join(c["name"] for c in trade["chips"])
        chips_ktc = sum(c["ktc"] for c in trade["chips"])
        adj = trade["adj"]
        # Display adjustment from the stud side's perspective (always positive)
        # Show sign based on who it favors relative to you (the sender).
        if adj.favors == "receive":
            adj_display = f"+{adj.adjustment:,}"   # you owe more (they have stud)
        elif adj.favors == "send":
            adj_display = f"-{adj.adjustment:,}"   # you're owed more (you have stud)
        else:
            adj_display = "0"
        rows.append([
            str(i),
            target["owner"][:15],
            target["name"][:20],
            target["position"],
            f"{target['ktc']:,}",
            chips_str[:30],
            f"{chips_ktc:,}",
            f"{trade['overpay']:+,}",
            adj_display,
            f"{trade['adjusted_overpay']:+,}",
        ])

    print()
    print(f"Found {len(trades)} trades. Showing top {min(args.top, len(trades))}:\n")
    print(_format_table(headers, rows))
    print()
    print(f"To send a trade: sleeper send-trade {args.username} --league \"{league.name}\" \\")
    print(f"  --to-roster <roster_id> --send <player> --get <target>")


def cmd_send_trade(args) -> None:
    """Fire a propose_trade mutation against Sleeper. Always previews + asks for confirmation."""
    import asyncio
    import json as _json
    import time as _time
    from sleeper.auth.client import SleeperAuthClient, SleeperAuthError
    from sleeper.client import SleeperClient

    if not os.environ.get("SLEEPER_TOKEN") and not getattr(args, "dry_run", False):
        print("ERROR: SLEEPER_TOKEN env var not set.")
        print("Capture it from sleeper.com DevTools -> Network -> graphql -> 'authorization' header.")
        print("Then: export SLEEPER_TOKEN='eyJ...'")
        sys.exit(1)

    user, league = _resolve_league(args.username, args.league)

    # Build adds/drops either from cached suggestion or explicit flags
    adds: list[tuple[str, int]] = []
    drops: list[tuple[str, int]] = []
    preview_lines: list[str] = []
    to_owner = ""
    to_roster_id = 0
    # Track per-side KTC values for Value Adjustment preview
    send_values_for_adj: list[int] = []
    receive_values_for_adj: list[int] = []
    # Track per-side player metadata for dry-run (P/E lookup)
    send_players_meta: list[dict] = []     # [{sleeper_id, name, position, ktc}]
    receive_players_meta: list[dict] = []

    if args.suggestion is not None:
        cache_path = _suggestion_cache_path(args.username, league.league_id)
        cached = _load_suggestions_cache(cache_path)
        if not cached:
            print(f"No cached suggestions for {args.username} in {league.name}.")
            print(f"Run `sleeper suggest-trades {args.username} --league \"{league.name}\"` first.")
            sys.exit(1)
        age_hr = (_time.time() - cached.get("saved_at", 0)) / 3600.0
        if age_hr > 4:
            print(f"WARNING: cached suggestions are {age_hr:.1f}h old. Consider re-running suggest-trades.")
        sugs = cached.get("suggestions", [])
        idx = args.suggestion - 1
        if idx < 0 or idx >= len(sugs):
            print(f"Suggestion #{args.suggestion} not found. Cache has {len(sugs)} suggestions.")
            sys.exit(1)
        chosen = sugs[idx]
        to_roster_id = chosen["to_roster_id"]
        to_owner = chosen["to_owner"]
        my_roster_id = cached["my_roster_id"]
        for p in chosen["send"]:
            drops.append((p["sleeper_id"], my_roster_id))
            adds.append((p["sleeper_id"], to_roster_id))
            send_values_for_adj.append(p["ktc_value"])
            send_players_meta.append({"sleeper_id": p["sleeper_id"], "name": p["name"], "position": p["position"], "ktc": p["ktc_value"]})
            preview_lines.append(f"  YOU GIVE:  {p['name']} ({p['position']}, KTC {p['ktc_value']:,}) -> roster {to_roster_id} ({to_owner})")
        for p in chosen["receive"]:
            drops.append((p["sleeper_id"], to_roster_id))
            adds.append((p["sleeper_id"], my_roster_id))
            receive_values_for_adj.append(p["ktc_value"])
            receive_players_meta.append({"sleeper_id": p["sleeper_id"], "name": p["name"], "position": p["position"], "ktc": p["ktc_value"]})
            preview_lines.append(f"  YOU GET:   {p['name']} ({p['position']}, KTC {p['ktc_value']:,}) -> you (roster {my_roster_id})")
        net_send = sum(p["ktc_value"] for p in chosen["send"])
        net_recv = sum(p["ktc_value"] for p in chosen["receive"])
        preview_lines.append(f"  NET:       {net_recv - net_send:+,} KTC (raw)")
    else:
        # Explicit mode: --to-roster N --send "Player A" --get "Player B"
        if not args.to_roster or not args.send or not args.get:
            print("Explicit mode needs --to-roster, --send, and --get (or use --suggestion N).")
            sys.exit(1)
        to_roster_id = args.to_roster
        rosters, sleeper_players = _fetch_roster_and_players(league.league_id)
        my_roster = next((r for r in rosters if r.owner_id == user.user_id), None)
        if not my_roster:
            print(f"No roster found for '{args.username}'.")
            sys.exit(1)
        their_roster = next((r for r in rosters if r.roster_id == to_roster_id), None)
        if not their_roster:
            print(f"No roster_id={to_roster_id} in this league.")
            sys.exit(1)

        async def _users():
            async with SleeperClient() as c:
                return await c.leagues.get_users(league.league_id)
        lus = asyncio.run(_users())
        udisp = {str(u.user_id): u.display_name for u in lus}
        to_owner = udisp.get(str(their_roster.owner_id) or "", f"roster {to_roster_id}")

        def resolve(name: str, roster, label: str):
            n = name.lower().replace(".", "").replace("'", "").strip()
            for pid in (roster.players or []):
                sp = sleeper_players.get(pid)
                if not sp:
                    continue
                full = (sp.full_name or "").lower().replace(".", "").replace("'", "")
                if n in full:
                    return pid, sp
            print(f"ERROR: '{name}' not on {label}'s roster.")
            print(f"  Available {label} players (top by name match):")
            for pid in (roster.players or [])[:8]:
                sp = sleeper_players.get(pid)
                if sp:
                    print(f"    {sp.full_name} ({sp.position})")
            sys.exit(1)

        for nm in args.send:
            pid, sp = resolve(nm, my_roster, args.username)
            from sleeper.enrichment.ktc import fetch_ktc_players
            ktc = fetch_ktc_players()
            s2k = _build_sleeper_to_ktc(ktc, sleeper_players)
            ktc_p = s2k.get(pid)
            val = _ktc_value(ktc_p, "sf")
            drops.append((pid, my_roster.roster_id))
            adds.append((pid, to_roster_id))
            send_values_for_adj.append(val)
            send_players_meta.append({"sleeper_id": pid, "name": sp.full_name, "position": sp.position, "ktc": val})
            preview_lines.append(f"  YOU GIVE:  {sp.full_name} ({sp.position}, KTC {val:,}) -> roster {to_roster_id} ({to_owner})")
        for nm in args.get:
            pid, sp = resolve(nm, their_roster, to_owner)
            from sleeper.enrichment.ktc import fetch_ktc_players
            ktc = fetch_ktc_players()
            s2k = _build_sleeper_to_ktc(ktc, sleeper_players)
            ktc_p = s2k.get(pid)
            val = _ktc_value(ktc_p, "sf")
            drops.append((pid, to_roster_id))
            adds.append((pid, my_roster.roster_id))
            receive_values_for_adj.append(val)
            receive_players_meta.append({"sleeper_id": pid, "name": sp.full_name, "position": sp.position, "ktc": val})
            preview_lines.append(f"  YOU GET:   {sp.full_name} ({sp.position}, KTC {val:,}) -> you (roster {my_roster.roster_id})")

    print()
    print("=" * 70)
    print("PROPOSED TRADE")
    print("=" * 70)
    print(f"  League: {league.name}")
    print(f"  Partner: {to_owner} (roster {to_roster_id})")
    print()
    for line in preview_lines:
        print(line)

    # Value Adjustment preview
    raw_delta = 0
    adj_delta = 0
    adj = None
    if send_values_for_adj and receive_values_for_adj:
        from sleeper.analytics.value_adjustment import apply_adjustment_to_delta
        raw_delta = sum(receive_values_for_adj) - sum(send_values_for_adj)
        adj_delta, adj = apply_adjustment_to_delta(raw_delta, send_values_for_adj, receive_values_for_adj)
        if adj.adjustment > 0:
            print(f"  VAL ADJ:   {adj.adjustment:+,} KTC (favors {adj.favors} side — {adj.stud_tier} stud)")
            print(f"             {adj.rationale}")
            print(f"  ADJUSTED:  {adj_delta:+,} KTC")
    print("=" * 70)

    # --dry-run: show winner/loser on KTC + P/E and exit
    if getattr(args, "dry_run", False):
        print()
        print("=" * 70)
        print("DRY RUN — KTC + P/E WINNER / LOSER ANALYSIS")
        print("=" * 70)

        # KTC verdict
        send_ktc_total = sum(send_values_for_adj)
        recv_ktc_total = sum(receive_values_for_adj)
        print(f"\n  KTC:")
        print(f"    You send:    {send_ktc_total:,}")
        print(f"    You receive: {recv_ktc_total:,}")
        print(f"    Raw delta:   {raw_delta:+,}")
        if adj and adj.adjustment > 0:
            print(f"    + Val Adj:   {adj_delta:+,} (after {adj.stud_tier}-tier stud compensation)")
        final_ktc = adj_delta if (adj and adj.adjustment > 0) else raw_delta
        if final_ktc > 500:
            print(f"    KTC Winner:  YOU ({final_ktc:+,})")
        elif final_ktc < -500:
            print(f"    KTC Winner:  PARTNER ({final_ktc:+,})")
        else:
            print(f"    KTC Verdict: EVEN ({final_ktc:+,})")

        # P/E verdict — compute on the fly from KTC + stats
        print(f"\n  P/E RATIO:")
        try:
            import datetime as _dt
            from sleeper.enrichment.ktc import fetch_ktc_players, build_ktc_to_sleeper_map
            from sleeper.enrichment.stats import get_season_stats
            from sleeper.analytics.valuation import compute_pe_ratios
            ktc_all = fetch_ktc_players()
            if 'sleeper_players' not in dir():
                _, _sp = _fetch_roster_and_players(league.league_id)
            else:
                _sp = sleeper_players
            mapping = build_ktc_to_sleeper_map(ktc_all, _sp)
            for p in ktc_all:
                p.sleeper_id = mapping.get(p.ktc_id)
            year = _dt.date.today().year
            stats = None
            # Try current year, fall back to last completed season
            for y in [year - 1, year - 2]:
                try:
                    stats = get_season_stats([y])
                    if stats:
                        year = y
                        break
                except Exception:
                    continue
            if not stats:
                raise RuntimeError("no recent NFL stats available")
            pes = compute_pe_ratios(ktc_all, stats, seasons=[year], fmt="sf")
            pe_by_sid = {r.sleeper_id: r for r in pes if r.sleeper_id}

            def _pe_line(p: dict, direction: str) -> None:
                r = pe_by_sid.get(p["sleeper_id"])
                if r and r.pe_ratio is not None:
                    signal = r.signal.upper() if r.signal else "-"
                    print(f"    {direction}  {p['name']:<22} {p['position']:<3} PE={r.pe_ratio:.2f}  FFPG={r.ffpg:.1f}  [{signal}]")
                else:
                    print(f"    {direction}  {p['name']:<22} {p['position']:<3} PE=?  (no production sample)")

            send_pe_sum = 0.0
            recv_pe_sum = 0.0
            send_pe_count = 0
            recv_pe_count = 0
            for p in send_players_meta:
                _pe_line(p, "SEND   ")
                r = pe_by_sid.get(p["sleeper_id"])
                if r and r.pe_ratio is not None:
                    send_pe_sum += r.pe_ratio
                    send_pe_count += 1
            for p in receive_players_meta:
                _pe_line(p, "RECEIVE")
                r = pe_by_sid.get(p["sleeper_id"])
                if r and r.pe_ratio is not None:
                    recv_pe_sum += r.pe_ratio
                    recv_pe_count += 1

            if send_pe_count and recv_pe_count:
                avg_send = send_pe_sum / send_pe_count
                avg_recv = recv_pe_sum / recv_pe_count
                pe_delta = avg_send - avg_recv  # positive = you're sending higher PE (overpriced) = win
                print(f"\n    Avg PE sent:     {avg_send:.2f}  (lower = more undervalued)")
                print(f"    Avg PE received: {avg_recv:.2f}")
                print(f"    PE delta:        {pe_delta:+.2f}")
                if pe_delta > 0.15:
                    print(f"    P/E Winner:      YOU — receiving cheaper production per KTC")
                elif pe_delta < -0.15:
                    print(f"    P/E Winner:      PARTNER — you're paying hype premium")
                else:
                    print(f"    P/E Verdict:     EVEN on production vs price")
            else:
                print(f"    (insufficient production sample — at least one side is speculative)")
        except Exception as e:
            print(f"    (P/E analysis unavailable: {e})")

        print("\n" + "=" * 70)
        print("DRY RUN COMPLETE — no proposal sent. Remove --dry-run to fire for real.")
        print("=" * 70)
        return

    if args.yes:
        print("(--yes flag: skipping confirmation)")
    else:
        if not sys.stdin.isatty():
            print("\nERROR: not a TTY. Use --yes to confirm non-interactively.")
            sys.exit(1)
        try:
            answer = input("\nSend this proposal? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    print("\nSending to Sleeper...")
    try:
        with SleeperAuthClient() as c:
            result = c.propose_trade(
                league_id=league.league_id,
                adds=adds,
                drops=drops,
                draft_picks=[],
                waiver_budget=[],
                expires_at=None,
            )
    except SleeperAuthError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    print()
    print(f"  Transaction ID: {result.get('transaction_id', '?')}")
    print(f"  Status:         {result.get('status', '?')}")
    print(f"  Created:        {result.get('created', '?')}")
    print()
    print("View it in Sleeper: https://sleeper.com/leagues/" + league.league_id + "/trades")


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

    # ktc-trend
    kt = subparsers.add_parser("ktc-trend", help="Player KTC value history from local daily snapshots")
    kt_sub = kt.add_subparsers(dest="kt_subcommand")

    kt_player = kt_sub.add_parser("player", help="Show one player's value over time")
    kt_player.add_argument("player_name", nargs="+", help="Player name (or ktc_id)")
    kt_player.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    kt_player.add_argument("--days", type=int, default=None, help="Limit to last N days")
    kt_player.add_argument("--snapshot-dir", default="data/ktc", dest="snapshot_dir")

    kt_movers = kt_sub.add_parser("movers", help="Biggest value changes in a window")
    kt_movers.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    kt_movers.add_argument("--days", type=int, default=7, help="Window size in days (default: 7)")
    kt_movers.add_argument("--top", type=int, default=20, help="Number of players (default: 20)")
    kt_movers.add_argument("--min-value", type=int, default=2000, dest="min_value",
                           help="Minimum current value to include (default: 2000)")
    kt_movers.add_argument("--snapshot-dir", default="data/ktc", dest="snapshot_dir")

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

    # suggest-trades
    st = subparsers.add_parser("suggest-trades",
                               help="Suggest 1-for-1 trades that improve positional balance")
    st.add_argument("username", help="Sleeper username")
    st.add_argument("--league", help="League name filter")
    st.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")
    st.add_argument("--top", type=int, default=10, help="Max suggestions to show (default: 10)")
    st.add_argument("--max-per-partner", type=int, default=2, dest="max_per_partner",
                    help="Max suggestions per trade partner (default: 2)")
    st.add_argument("--tolerance", type=float, default=10.0,
                    help="KTC value match tolerance percent (default: 10)")
    st.add_argument("--position", help="Filter to suggestions involving this position (QB/RB/WR/TE)")
    st.add_argument("--with-pe", action="store_true", dest="with_pe",
                    help="Also compute P/E ratios for arbitrage scoring (slower; needs nflreadpy)")

    # gm-mode
    gm = subparsers.add_parser("gm-mode",
                               help="Full team archetype analysis (contender/reloading/rebuilding/pretender)")
    gm.add_argument("username", help="Sleeper username (authed user)")
    gm.add_argument("--league", help="League name filter")
    gm.add_argument("--owner", help="Analyze another owner in the same league (by display name)")
    gm.add_argument("--format", choices=["sf", "1qb"], default="sf", help="Format (default: sf)")

    # find-trades
    ft = subparsers.add_parser("find-trades",
                               help="Find trades targeting specific positions with filters")
    ft.add_argument("username", help="Sleeper username")
    ft.add_argument("--league", help="League name filter")
    ft.add_argument("--mode", choices=["normal", "upgrade", "downtiering"], default="normal",
                    help="Trade mode: normal (balanced overpay), upgrade (get more value), downtiering (liquidate)")
    ft.add_argument("--position", nargs="+", default=[], dest="position",
                    help="Target position(s) to search for (QB RB WR TE)")
    ft.add_argument("--include", nargs="+", default=None,
                    help="Only consider these players as targets")
    ft.add_argument("--exclude", nargs="+", default=None,
                    help="Exclude these players from targets")
    ft.add_argument("--min-overpay", type=int, default=None, dest="min_overpay",
                    help="Minimum KTC overpay threshold (auto-set by mode if not specified)")
    ft.add_argument("--max-overpay", type=int, default=None, dest="max_overpay",
                    help="Maximum KTC overpay threshold (auto-set by mode if not specified)")
    ft.add_argument("--min-ktc", type=int, default=0, dest="min_ktc",
                    help="Filter targets by minimum KTC value (default: 0)")
    ft.add_argument("--top", type=int, default=15,
                    help="Max trades to show (default: 15)")
    ft.add_argument("--single-only", action="store_true", dest="single_only",
                    help="Only show single-player trades (don't combine chips)")

    # send-trade
    sd = subparsers.add_parser("send-trade",
                               help="Fire a propose_trade mutation against Sleeper (auth required)")
    sd.add_argument("username", help="Sleeper username")
    sd.add_argument("--league", help="League name filter")
    sd.add_argument("--suggestion", type=int, default=None,
                    help="Use suggestion #N from the last `suggest-trades` run for this user+league")
    sd.add_argument("--to-roster", type=int, default=None, dest="to_roster",
                    help="(explicit mode) target roster_id")
    sd.add_argument("--send", nargs="+", default=None,
                    help="(explicit mode) player names you give up")
    sd.add_argument("--get", nargs="+", default=None,
                    help="(explicit mode) player names you receive")
    sd.add_argument("--yes", action="store_true",
                    help="Skip the confirmation prompt (still prints preview)")
    sd.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Preview KTC + P/E winner/loser analysis and exit without sending")

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
    elif args.command == "ktc-trend":
        cmd_ktc_trend(args)
    elif args.command == "suggest-trades":
        cmd_suggest_trades(args)
    elif args.command == "find-trades":
        cmd_find_trades(args)
    elif args.command == "send-trade":
        cmd_send_trade(args)
    elif args.command == "gm-mode":
        cmd_gm_mode(args)


if __name__ == "__main__":
    main()
