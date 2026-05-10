"""Agent-friendly CLI commands.

These commands all support `--json` and follow the standard envelope
defined in `sleeper.agent.envelope`. They're separated from the main
`cli.py` so the human-friendly command surface stays uncluttered.

Commands added:
  whoami           — who am I, what leagues, current week
  status           — one-shot snapshot per league
  context          — full agent context bundle (the recommended LLM input)
  changes          — delta of league activity since a timestamp
  auth-check       — verify SLEEPER_TOKEN works + when it expires

  inbox            — pending incoming trades w/ ktc context
  outbox           — pending outgoing trades I proposed
  trade-respond    — accept|reject a specific transaction (preview/execute)

  roster           — my roster (starters/bench/taxi/IR) w/ ktc
  matchup          — this week's matchup
  lineup           — current vs optimal starters w/ projections
  lineup-set       — set starters (preview/execute)
  lineup-health    — injury/bye/empty-slot scan for the active week

  waivers          — ranked FA pool
  waiver-claim     — submit a waiver claim (preview/execute)
  drop             — drop a player (preview/execute)
  add              — FA add w/ optional drop (preview/execute)
  taxi-move        — move player to taxi (preview/execute)
  ir-move          — move player to IR (preview/execute)
  activate         — activate from IR (preview/execute)

  execute          — execute a previously-created preview by id
  preview-show     — inspect a preview by id

Convention: every write supports `--preview` (default) and `--execute`.
A preview prints the would-be call payload + a preview_id, and ALSO
writes it to ~/.sleeper-sdk/previews/. `--execute` (or `execute <id>`)
fires the live mutation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from sleeper.agent import (
    create_preview,
    consume_preview,
    load_preview,
)
from sleeper.agent.envelope import ok_envelope, error_envelope
from sleeper.agent.helpers import (
    build_context,
    summarize_inbox,
    optimal_lineup,
    check_lineup_health,
    rank_drop_candidates,
    rank_waiver_targets,
)
from sleeper.errors import ErrorCode, SleeperError


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit(env: dict, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(env, indent=2, default=str))
    else:
        if env.get("ok"):
            data = env.get("data")
            if isinstance(data, (dict, list)):
                print(json.dumps(data, indent=2, default=str))
            else:
                print(data)
            for w in env.get("warnings") or []:
                print(f"⚠ {w}", file=sys.stderr)
        else:
            for e in env.get("errors") or []:
                print(f"❌ [{e.get('code')}] {e.get('message')}", file=sys.stderr)
            sys.exit(1)


def _wrap(command: str, fn, args_dict: dict, json_mode: bool):
    try:
        data = fn()
    except SleeperError as e:
        _emit(error_envelope(
            command=command, code=e.code, message=str(e.message or str(e)),
            retryable=e.retryable, args=args_dict, details=e.details,
        ), json_mode=json_mode)
        return
    except Exception as e:
        _emit(error_envelope(
            command=command, code=ErrorCode.INTERNAL, message=str(e), args=args_dict,
        ), json_mode=json_mode)
        return
    _emit(ok_envelope(command=command, data=data, args=args_dict), json_mode=json_mode)


# ---------------------------------------------------------------------------
# Auth bootstrap helpers
# ---------------------------------------------------------------------------

def _resolve_my_roster_id(ctx: dict) -> Optional[int]:
    mr = ctx.get("my_roster") or {}
    return mr.get("roster_id")


def _ktc_lookup_for_context(ctx: dict, format_: str = "sf") -> dict:
    """Build {sleeper_player_id: ktc_value} for everyone on my roster + matchup
    + opponents. Lazy-import KTC enrichment to avoid hard dependency."""
    try:
        from sleeper.enrichment.ktc import (
            fetch_ktc_players,
            build_ktc_to_sleeper_map,
        )
        from sleeper.client import SleeperClient
        client = SleeperClient()
        sleeper_players = client.sync(client.get_all_players())
        ktc = fetch_ktc_players()
        ktc_map = build_ktc_to_sleeper_map(ktc, sleeper_players)
        # ktc_map is {sleeper_id: ktc_record}; pull the requested format value.
        out = {}
        for sid, rec in ktc_map.items():
            v = (rec.get("value_sf") if format_ == "sf" else rec.get("value_1qb"))
            if v is not None:
                out[sid] = int(v)
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def cmd_whoami(args) -> None:
    def _do():
        from datetime import datetime
        from sleeper.client import SleeperClient

        async def _fetch():
            async with SleeperClient() as c:
                user = await c.users.get_user(args.username)
                leagues = await c.users.get_user_leagues(user.user_id, season=str(datetime.now().year))
                state = await c.state.get_state()
                return user, leagues, state

        import asyncio
        user, leagues, state = asyncio.run(_fetch())
        return {
            "user": {"user_id": user.user_id, "username": user.username, "display_name": user.display_name},
            "season": str(datetime.now().year),
            "current_week": getattr(state, "week", None),
            "leagues": [
                {"league_id": lg.league_id, "name": lg.name, "total_rosters": lg.total_rosters}
                for lg in leagues
            ],
        }
    _wrap("whoami", _do, {"username": args.username}, args.json)


def cmd_context(args) -> None:
    def _do():
        return build_context(args.username, args.league)
    _wrap("context", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_status(args) -> None:
    def _do():
        ctx = build_context(args.username, args.league)
        ktc = _ktc_lookup_for_context(ctx, args.format)
        my_rid = _resolve_my_roster_id(ctx)

        # Inbox
        inbox_count = 0
        try:
            from sleeper.auth import SleeperAuthClient
            with SleeperAuthClient() as auth:
                inbox = auth.get_inbox(ctx["league"]["league_id"], my_roster_id=my_rid)
                inbox_count = len(inbox)
        except Exception:
            inbox_count = -1   # signal "auth not available"

        health = check_lineup_health(ctx.get("my_roster") or {}, ctx.get("week") or 1,
                                     ctx.get("roster_positions"))
        roster_value = sum(ktc.get(p.get("player_id"), 0) for p in (ctx.get("my_roster") or {}).get("players") or [])

        return {
            "league": ctx["league"]["name"],
            "week": ctx.get("week"),
            "my_roster_id": my_rid,
            "inbox_pending": inbox_count,
            "lineup_health": health,
            "roster_ktc_total": roster_value,
            "ktc_format": args.format,
        }
    _wrap("status", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_auth_check(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient, inspect_token
        token = os.environ.get("SLEEPER_TOKEN")
        if not token:
            return {"ok": False, "reason": "SLEEPER_TOKEN not set"}
        info = inspect_token(token)
        return {
            "ok": not info.is_expired,
            "user_id": info.user_id,
            "display_name": info.display_name,
            "expires_at": info.expires_at,
            "seconds_remaining": info.seconds_remaining,
        }
    _wrap("auth-check", _do, {}, args.json)


def cmd_inbox(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        from sleeper.client import SleeperClient
        ctx = build_context(args.username, args.league)
        my_rid = _resolve_my_roster_id(ctx)
        ktc = _ktc_lookup_for_context(ctx, args.format)

        with SleeperClient() as c:
            sleeper_players = c.sync(c.get_all_players())

        with SleeperAuthClient() as auth:
            trades = auth.get_inbox(ctx["league"]["league_id"], my_roster_id=my_rid)

        rows = summarize_inbox(trades, my_roster_id=my_rid,
                                sleeper_players=sleeper_players, ktc_lookup=ktc)
        return {"league": ctx["league"]["name"], "my_roster_id": my_rid, "trades": rows}
    _wrap("inbox", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_outbox(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        from sleeper.client import SleeperClient
        ctx = build_context(args.username, args.league)
        my_rid = _resolve_my_roster_id(ctx)
        ktc = _ktc_lookup_for_context(ctx, args.format)
        with SleeperClient() as c:
            sleeper_players = c.sync(c.get_all_players())
        with SleeperAuthClient() as auth:
            trades = auth.get_outbox(ctx["league"]["league_id"], my_roster_id=my_rid)
        rows = summarize_inbox(trades, my_roster_id=my_rid,
                                sleeper_players=sleeper_players, ktc_lookup=ktc)
        return {"league": ctx["league"]["name"], "my_roster_id": my_rid, "trades": rows}
    _wrap("outbox", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_roster(args) -> None:
    def _do():
        ctx = build_context(args.username, args.league)
        ktc = _ktc_lookup_for_context(ctx, args.format)
        roster = ctx.get("my_roster") or {}
        for p in roster.get("players") or []:
            p["ktc"] = ktc.get(p.get("player_id"))
        return {"league": ctx["league"]["name"], "roster": roster}
    _wrap("roster", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_matchup(args) -> None:
    def _do():
        ctx = build_context(args.username, args.league)
        return {"league": ctx["league"]["name"], "week": ctx.get("week"), "matchup": ctx.get("matchup")}
    _wrap("matchup", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_waivers(args) -> None:
    def _do():
        from sleeper.client import SleeperClient
        ctx = build_context(args.username, args.league)
        # Build FA pool: all players minus everyone rostered.
        with SleeperClient() as c:
            sleeper_players = c.sync(c.get_all_players())
            rosters = c.sync(c.leagues.get_rosters(ctx["league"]["league_id"]))
        rostered = set()
        for r in rosters:
            for pid in (r.players or []):
                if pid:
                    rostered.add(pid)
        skill = {"QB", "RB", "WR", "TE"}
        fa = []
        for pid, p in sleeper_players.items():
            if pid in rostered:
                continue
            if p.position not in skill:
                continue
            if not p.team:
                continue
            fa.append({
                "player_id": pid,
                "name": getattr(p, "full_name", None) or f"{p.first_name} {p.last_name}",
                "position": p.position,
                "team": p.team,
                "age": getattr(p, "age", None),
            })
        ktc = _ktc_lookup_for_context(ctx, args.format)
        ranked = rank_waiver_targets(
            fa, ktc_lookup=ktc,
            position_priority=(args.position_priority or "").split(",") if args.position_priority else None,
            age_max=args.age_max, top=args.top,
        )
        return {"league": ctx["league"]["name"], "candidates": ranked}
    _wrap("waivers", _do, {"username": args.username, "league": args.league, "top": args.top}, args.json)


def cmd_lineup(args) -> None:
    def _do():
        ctx = build_context(args.username, args.league)
        roster = ctx.get("my_roster") or {}
        # Projections: agent should pass them in via --projections JSON file; default zero.
        projections: dict = {}
        if args.projections:
            try:
                with open(args.projections) as f:
                    projections = json.load(f)
            except Exception as e:
                return {"error": f"Could not load projections: {e}"}
        opt = optimal_lineup(roster, ctx.get("roster_positions") or [], projections=projections)
        return {"league": ctx["league"]["name"], "week": ctx.get("week"), "lineup": opt}
    _wrap("lineup", _do, {"username": args.username, "league": args.league}, args.json)


def cmd_lineup_health(args) -> None:
    def _do():
        ctx = build_context(args.username, args.league)
        return {
            "league": ctx["league"]["name"],
            "week": ctx.get("week"),
            "health": check_lineup_health(ctx.get("my_roster") or {}, ctx.get("week") or 1,
                                          ctx.get("roster_positions")),
        }
    _wrap("lineup-health", _do, {"username": args.username, "league": args.league}, args.json)


# ---------------------------------------------------------------------------
# Writes — all use preview / execute
# ---------------------------------------------------------------------------

def _exec_or_preview(args, *, command: str, payload: dict, summary: str, executor):
    """Standard preview-vs-execute decision wrapper.

    `executor` is a 0-arg callable that performs the actual mutation and
    returns its result dict. It is only invoked when --execute is passed.
    """
    if not args.execute:
        prv = create_preview(command, payload, summary=summary,
                             metadata={"args": vars(args)})
        return {
            "preview_id": prv.preview_id,
            "expires_at": prv.expires_at,
            "summary": summary,
            "would_call": payload,
            "execute_command": f"sleeper execute {prv.preview_id}",
        }
    return executor()


def cmd_trade_respond(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        action = "accept" if args.accept else "reject"
        payload = {
            "league_id": league_id,
            "transaction_id": args.transaction_id,
            "leg": args.leg,
            "action": action,
        }
        summary = f"{action.upper()} trade {args.transaction_id} in {ctx['league']['name']}"

        def _do_write():
            with SleeperAuthClient() as auth:
                if args.accept:
                    return auth.accept_trade(league_id, args.transaction_id, args.leg)
                return auth.reject_trade(league_id, args.transaction_id, args.leg)

        return _exec_or_preview(args, command="trade-respond", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("trade-respond", _do, vars(args), args.json)


def cmd_lineup_set(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        starters = [s.strip() for s in (args.starters or "").split(",") if s.strip()]
        payload = {"league_id": league_id, "roster_id": roster_id, "starters": starters}
        summary = f"Set {len(starters)} starters in {ctx['league']['name']} week {ctx.get('week')}"

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.set_starters(league_id, roster_id, starters)

        return _exec_or_preview(args, command="lineup-set", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("lineup-set", _do, vars(args), args.json)


def cmd_waiver_claim(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        payload = {
            "league_id": league_id, "roster_id": roster_id,
            "add_player_id": args.add, "drop_player_id": args.drop,
            "faab_bid": args.faab,
        }
        summary = f"Waiver: add {args.add}" + (f", drop {args.drop}" if args.drop else "") + f" (FAAB ${args.faab})"

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.submit_waiver_claim(
                    league_id, roster_id,
                    add_player_id=args.add, drop_player_id=args.drop, faab_bid=args.faab,
                )

        return _exec_or_preview(args, command="waiver-claim", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("waiver-claim", _do, vars(args), args.json)


def cmd_drop(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        payload = {"league_id": league_id, "roster_id": roster_id, "drop_player_id": args.player}
        summary = f"Drop player {args.player} from {ctx['league']['name']}"

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.add_drop(league_id, roster_id, drop_player_id=args.player)

        return _exec_or_preview(args, command="drop", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("drop", _do, vars(args), args.json)


def cmd_add(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        payload = {
            "league_id": league_id, "roster_id": roster_id,
            "add_player_id": args.player, "drop_player_id": args.drop,
        }
        summary = f"Add {args.player}" + (f", drop {args.drop}" if args.drop else "")

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.add_drop(league_id, roster_id,
                                     add_player_id=args.player, drop_player_id=args.drop)

        return _exec_or_preview(args, command="add", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("add", _do, vars(args), args.json)


def cmd_taxi_move(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        payload = {"league_id": league_id, "roster_id": roster_id, "player_id": args.player}
        summary = f"Move {args.player} to taxi"

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.move_to_taxi(league_id, roster_id, args.player)

        return _exec_or_preview(args, command="taxi-move", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("taxi-move", _do, vars(args), args.json)


def cmd_ir_move(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        payload = {"league_id": league_id, "roster_id": roster_id, "player_id": args.player}
        summary = f"Move {args.player} to IR"

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.move_to_ir(league_id, roster_id, args.player)

        return _exec_or_preview(args, command="ir-move", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("ir-move", _do, vars(args), args.json)


def cmd_activate(args) -> None:
    def _do():
        from sleeper.auth import SleeperAuthClient
        ctx = build_context(args.username, args.league)
        league_id = ctx["league"]["league_id"]
        roster_id = _resolve_my_roster_id(ctx)
        payload = {"league_id": league_id, "roster_id": roster_id, "player_id": args.player}
        summary = f"Activate {args.player} from IR"

        def _do_write():
            with SleeperAuthClient() as auth:
                return auth.activate_from_ir(league_id, roster_id, args.player)

        return _exec_or_preview(args, command="activate", payload=payload,
                                summary=summary, executor=_do_write)
    _wrap("activate", _do, vars(args), args.json)


def cmd_execute(args) -> None:
    """Consume a previously-created preview and fire its mutation."""
    def _do():
        from sleeper.auth import SleeperAuthClient
        prv = consume_preview(args.preview_id)
        cmd = prv.command
        p = prv.payload
        with SleeperAuthClient() as auth:
            if cmd == "trade-respond":
                if p["action"] == "accept":
                    return auth.accept_trade(p["league_id"], p["transaction_id"], p["leg"])
                return auth.reject_trade(p["league_id"], p["transaction_id"], p["leg"])
            if cmd == "lineup-set":
                return auth.set_starters(p["league_id"], p["roster_id"], p["starters"])
            if cmd == "waiver-claim":
                return auth.submit_waiver_claim(
                    p["league_id"], p["roster_id"],
                    add_player_id=p["add_player_id"], drop_player_id=p.get("drop_player_id"),
                    faab_bid=p.get("faab_bid", 0),
                )
            if cmd == "drop":
                return auth.add_drop(p["league_id"], p["roster_id"], drop_player_id=p["drop_player_id"])
            if cmd == "add":
                return auth.add_drop(p["league_id"], p["roster_id"],
                                     add_player_id=p["add_player_id"], drop_player_id=p.get("drop_player_id"))
            if cmd == "taxi-move":
                return auth.move_to_taxi(p["league_id"], p["roster_id"], p["player_id"])
            if cmd == "ir-move":
                return auth.move_to_ir(p["league_id"], p["roster_id"], p["player_id"])
            if cmd == "activate":
                return auth.activate_from_ir(p["league_id"], p["roster_id"], p["player_id"])
        raise SleeperError(f"Unknown preview command: {cmd}", code=ErrorCode.UNSUPPORTED)
    _wrap("execute", _do, {"preview_id": args.preview_id}, args.json)


def cmd_preview_show(args) -> None:
    def _do():
        prv = load_preview(args.preview_id)
        return prv.to_dict()
    _wrap("preview-show", _do, {"preview_id": args.preview_id}, args.json)


# ---------------------------------------------------------------------------
# argparse wiring — called from main cli.py
# ---------------------------------------------------------------------------

def add_subparsers(subparsers) -> dict:
    """Register all agent commands. Returns name → handler mapping."""
    handlers = {}

    def _common_user_league(p):
        p.add_argument("username")
        p.add_argument("--league", help="League name filter")
        p.add_argument("--format", choices=["sf", "1qb"], default="sf")
        p.add_argument("--json", action="store_true", help="Emit JSON envelope")

    def _common_write(p):
        p.add_argument("--execute", action="store_true",
                       help="Fire the live mutation. Default is preview-only.")
        p.add_argument("--json", action="store_true")

    # --- reads ---
    p = subparsers.add_parser("whoami")
    p.add_argument("username"); p.add_argument("--json", action="store_true")
    handlers["whoami"] = cmd_whoami

    p = subparsers.add_parser("context", help="Full agent context bundle (recommended LLM input)")
    _common_user_league(p)
    handlers["context"] = cmd_context

    p = subparsers.add_parser("status", help="One-shot snapshot for a league")
    _common_user_league(p)
    handlers["status"] = cmd_status

    p = subparsers.add_parser("auth-check", help="Verify SLEEPER_TOKEN works")
    p.add_argument("--json", action="store_true")
    handlers["auth-check"] = cmd_auth_check

    p = subparsers.add_parser("inbox", help="Pending incoming trades")
    _common_user_league(p)
    handlers["inbox"] = cmd_inbox

    p = subparsers.add_parser("outbox", help="Pending outgoing trades I proposed")
    _common_user_league(p)
    handlers["outbox"] = cmd_outbox

    p = subparsers.add_parser("roster", help="My roster with KTC values")
    _common_user_league(p)
    handlers["roster"] = cmd_roster

    p = subparsers.add_parser("matchup", help="This week's matchup view")
    _common_user_league(p)
    handlers["matchup"] = cmd_matchup

    p = subparsers.add_parser("waivers", help="Ranked free-agent pool")
    _common_user_league(p)
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--age-max", type=int, default=None)
    p.add_argument("--position-priority", default=None,
                   help="Comma-separated positions in priority order, e.g. RB,WR")
    handlers["waivers"] = cmd_waivers

    p = subparsers.add_parser("lineup", help="Current vs optimal starters")
    _common_user_league(p)
    p.add_argument("--projections", help="Path to JSON {player_id: projected_points}")
    handlers["lineup"] = cmd_lineup

    p = subparsers.add_parser("lineup-health", help="Injury / bye / empty-slot scan")
    _common_user_league(p)
    handlers["lineup-health"] = cmd_lineup_health

    # --- writes ---
    p = subparsers.add_parser("trade-respond", help="Accept or reject a pending trade")
    p.add_argument("transaction_id")
    p.add_argument("--username", required=True)
    p.add_argument("--league")
    p.add_argument("--leg", type=int, required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--reject", action="store_true")
    _common_write(p)
    handlers["trade-respond"] = cmd_trade_respond

    p = subparsers.add_parser("lineup-set", help="Set starters")
    _common_user_league(p)
    p.add_argument("--starters", required=True,
                   help="Comma-separated player_ids in slot order")
    p.add_argument("--execute", action="store_true")
    handlers["lineup-set"] = cmd_lineup_set

    p = subparsers.add_parser("waiver-claim", help="Submit a waiver claim")
    _common_user_league(p)
    p.add_argument("--add", required=True, help="player_id to add")
    p.add_argument("--drop", help="player_id to drop")
    p.add_argument("--faab", type=int, default=0)
    p.add_argument("--execute", action="store_true")
    handlers["waiver-claim"] = cmd_waiver_claim

    p = subparsers.add_parser("drop", help="Drop a player")
    _common_user_league(p)
    p.add_argument("--player", required=True, help="player_id to drop")
    p.add_argument("--execute", action="store_true")
    handlers["drop"] = cmd_drop

    p = subparsers.add_parser("add", help="Free-agent add (with optional drop)")
    _common_user_league(p)
    p.add_argument("--player", required=True, help="player_id to add")
    p.add_argument("--drop", help="player_id to drop alongside")
    p.add_argument("--execute", action="store_true")
    handlers["add"] = cmd_add

    p = subparsers.add_parser("taxi-move", help="Move a player to taxi")
    _common_user_league(p)
    p.add_argument("--player", required=True)
    p.add_argument("--execute", action="store_true")
    handlers["taxi-move"] = cmd_taxi_move

    p = subparsers.add_parser("ir-move", help="Move a player to IR")
    _common_user_league(p)
    p.add_argument("--player", required=True)
    p.add_argument("--execute", action="store_true")
    handlers["ir-move"] = cmd_ir_move

    p = subparsers.add_parser("activate", help="Activate a player from IR")
    _common_user_league(p)
    p.add_argument("--player", required=True)
    p.add_argument("--execute", action="store_true")
    handlers["activate"] = cmd_activate

    # --- preview ops ---
    p = subparsers.add_parser("execute", help="Execute a previously-created preview")
    p.add_argument("preview_id")
    p.add_argument("--json", action="store_true")
    handlers["execute"] = cmd_execute

    p = subparsers.add_parser("preview-show", help="Inspect a preview by id")
    p.add_argument("preview_id")
    p.add_argument("--json", action="store_true")
    handlers["preview-show"] = cmd_preview_show

    return handlers
