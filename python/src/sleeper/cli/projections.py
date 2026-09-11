"""Weekly projection commands.

Commands:
    projections      Sleeper's weekly projections, optionally league-scored
    start-sit        "Start X or Y?" — ranked, with confidence

Both are read-only and need no auth. `start-sit` takes a username so it can
score with the league's own `scoring_settings` rather than generic PPR; plain
`projections` works without a league.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from sleeper.cli._common import _format_table, _resolve_league


def _resolve_season_week(season: str | None, week: int | None) -> tuple[str, int]:
    """Fill in season/week from Sleeper's own state when not given.

    Never defaults the season to a hardcoded year — league IDs roll over
    annually and a stale season silently returns another league's data.
    """
    if season and week:
        return season, week

    from sleeper.client import SleeperClient

    async def _state():
        async with SleeperClient() as client:
            return await client.state.get_state()

    try:
        state = asyncio.run(_state())
    except Exception:
        return season or str(datetime.now().year), week or 1

    return (
        season or str(getattr(state, "season", None) or datetime.now().year),
        week or int(getattr(state, "week", 1) or 1),
    )


def cmd_projections(args: argparse.Namespace) -> None:
    """Print Sleeper's weekly projections for one or more positions."""
    from sleeper.api.projections import DEFAULT_POSITIONS
    from sleeper.client import SleeperClient
    from sleeper.enrichment.projections import rank_projections

    season, week = _resolve_season_week(args.season, args.week)
    # No --position means the skill positions, not literally every position —
    # the unfiltered response is ~9.4k rows / 5.7 MB including linemen.
    positions: list[str] = [p.upper() for p in (args.position or [])] or list(DEFAULT_POSITIONS)

    scoring_settings = None
    scoring_label = args.scoring
    if args.username:
        _user, league = _resolve_league(args.username, args.league)
        scoring_settings = league.scoring_settings
        if scoring_settings:
            scoring_label = f"{league.name} scoring"

    async def _fetch():
        async with SleeperClient() as client:
            return await client.projections.get_week(season, week, position=positions)

    print(f"Fetching {season} week {week} projections...")
    try:
        projections = asyncio.run(_fetch())
    except Exception as e:
        print(f"Could not fetch projections: {e}")
        sys.exit(1)

    rows = rank_projections(
        projections,
        scoring_settings,
        scoring=args.scoring,
        min_points=args.min_points,
        top=args.top,
    )
    if not rows:
        print("(no projections — check the season/week/position)")
        return

    print()
    print(f"{season} Week {week} projections — {scoring_label}")
    print()
    table = [
        [
            str(i),
            p.name or p.player_id,
            p.position or "?",
            p.team or "FA",
            ("BYE" if p.is_bye else (f"vs {p.opponent}" if p.opponent else "-")),
            f"{pts:.1f}",
            p.injury_status or "",
        ]
        for i, (p, pts) in enumerate(rows, 1)
    ]
    print(_format_table(
        ["#", "Player", "Pos", "Team", "Opp", "Proj", "Inj"], table
    ))

    if not scoring_settings:
        print()
        print(
            f"Note: generic {args.scoring} scoring. Pass a username "
            "(+ --league) to score with your league's settings."
        )


def cmd_start_sit(args: argparse.Namespace) -> None:
    """Answer a start/sit question between two or more players."""
    from sleeper.agent.helpers import start_sit

    names = list(args.players)
    if len(names) <= args.slots:
        print(f"Pass more players than slots — {len(names)} player(s) for "
              f"{args.slots} slot(s) is not a decision. Example:\n"
              "  sleeper start-sit camfleety --players 'Jalen Hurts' 'Josh Allen'")
        sys.exit(1)

    json_mode = getattr(args, "json", False)
    if not json_mode:
        print(f"Comparing {', '.join(names)}...")

    try:
        result = start_sit(
            args.username,
            names,
            args.league,
            week=args.week,
            slots=args.slots,
        )
    except Exception as e:
        if json_mode:
            from sleeper.agent.envelope import error_envelope
            from sleeper.errors import ErrorCode, SleeperError
            code = e.code if isinstance(e, SleeperError) else ErrorCode.INTERNAL
            print(json.dumps(error_envelope(
                command="start-sit", code=code, message=str(e),
                args={"username": args.username, "players": names},
            ), indent=2, default=str))
            sys.exit(1)
        print(f"Could not answer: {e}")
        sys.exit(1)

    if json_mode:
        from sleeper.agent.envelope import ok_envelope
        print(json.dumps(ok_envelope(
            command="start-sit", data=result,
            args={"username": args.username, "players": names, "slots": args.slots},
        ), indent=2, default=str))
        return

    league = result.get("league") or {}
    print()
    print(f"{league.get('name') or '?'} — week {result.get('week')} "
          f"({result.get('scoring')})")
    print()

    # Slots are filled even when nobody can play, so don't print START next to
    # a bye — it would contradict the recommendation printed below.
    rows = []
    for row in result.get("start", []):
        rows.append(["START" if row.get("available") else "(n/a)", row])
    for row in result.get("sit", []):
        rows.append(["sit", row])

    table = [
        [
            verdict,
            row.get("name") or row.get("player_id"),
            row.get("position") or "?",
            row.get("team") or "FA",
            ("BYE" if row.get("status") == "bye"
             else (f"vs {row['opponent']}" if row.get("opponent") else "-")),
            ("-" if row.get("status") == "no_projection"
             else f"{row.get('projected_points', 0.0):.1f}"),
            row.get("injury_status") or "",
        ]
        for verdict, row in rows
    ]
    print(_format_table(
        ["", "Player", "Pos", "Team", "Opp", "Proj", "Inj"], table
    ))

    print()
    print(result.get("recommendation") or "")
    print(f"Confidence: {result.get('confidence')} "
          f"(margin {result.get('margin')}, score {result.get('confidence_score')})")

    reasons = result.get("reasons") or []
    if reasons:
        print()
        print("Why:")
        for r in reasons:
            print(f"  - {r}")

    if args.verbose:
        for row in result.get("start", []) + result.get("sit", []):
            drivers = row.get("drivers") or []
            if drivers:
                print()
                print(f"{row.get('name')} projection drivers:")
                for d in drivers:
                    print(f"  {d}")

    warnings = result.get("warnings") or []
    if warnings:
        print()
        for w in warnings:
            print(f"⚠ {w}", file=sys.stderr)
