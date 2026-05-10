"""Authenticated write — propose a trade against Sleeper's GraphQL.

The only command in this module is `send-trade`. It requires
SLEEPER_TOKEN and is the only CLI surface that mutates league state.
Intentionally NOT exposed as a high-level skill — write ops require
explicit user invocation, never natural-language triggers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from sleeper.cli._common import (
    _build_sleeper_to_ktc,
    _fetch_roster_and_players,
    _format_table,
    _ktc_value,
    _resolve_league,
)
# send-trade pulls suggestion cache helpers from trades.py
from sleeper.cli.trades import (
    _load_suggestions_cache,
    _suggestion_cache_path,
)



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


