"""Read-only KTC valuation commands.

Commands:
    market-value     Player KTC vs actual trade price
    league-values    KTC values for your roster
    roster-rank      Rank league teams by total KTC
    trending         Biggest 7-day KTC movers
    buy-sell         Players trading below/above their KTC
    pe-ratio         KTC price ÷ FFPG (price/earnings analog)
    ktc-trend        Local snapshot history (player or movers)

All commands here are read-only and require no auth.
"""
from __future__ import annotations

import argparse

from sleeper.cli._common import (
    _build_sleeper_to_ktc,
    _fetch_roster_and_players,
    _format_table,
    _ktc_rank,
    _ktc_trend,
    _ktc_value,
    _resolve_league,
)



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

