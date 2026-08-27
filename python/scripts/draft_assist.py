#!/usr/bin/env python3
"""Live draft co-pilot: poll a Sleeper draft and print a KTC-valued board.

Usage:
    python scripts/draft_assist.py USERNAME [--league NAME] [--draft-id ID]
                                   [--interval 8] [--top 12] [--once]

Polls the draft, announces new picks with their market value, and reprints
the best-available board (overall + by position, with value-cliff gaps)
plus your roster so far. Read-only: no SLEEPER_TOKEN needed.

Values are KTC 1QB or SF (auto-detected from roster positions) read from
data/ktc/latest.json. K and DEF have no KTC values — they are listed
unvalued. KTC is a dynasty market; in redraft leagues treat it as a
market signal, not gospel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from sleeper import SleeperClient

REPO_ROOT = Path(__file__).resolve().parents[2]
KTC_LATEST = REPO_ROOT / "data" / "ktc" / "latest.json"
VALUED_POSITIONS = ("QB", "RB", "WR", "TE")


def norm(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?$", "", name.strip())
    return re.sub(r"[^a-z]", "", name)


def load_board(value_key: str) -> dict[tuple[str, str], dict]:
    """(norm_name, position) -> KTC record, using the requested value column."""
    snap = json.loads(KTC_LATEST.read_text())
    board = {}
    for rec in snap["players"]:
        if rec["position"] not in VALUED_POSITIONS:
            continue
        val = rec.get(value_key)
        if not val or val <= 0:
            continue
        board[(norm(rec["name"]), rec["position"])] = {**rec, "value": val}
    return board


def snake_slot(pick_no: int, teams: int) -> int:
    idx = (pick_no - 1) % teams
    rnd = (pick_no - 1) // teams + 1
    return idx + 1 if rnd % 2 == 1 else teams - idx


def fmt_pick_no(pick_no: int, teams: int) -> str:
    rnd = (pick_no - 1) // teams + 1
    return f"{rnd}.{(pick_no - 1) % teams + 1:02d}"


def print_board(available: list[dict], top: int) -> None:
    print(f"\n  BEST AVAILABLE (top {top} overall)")
    for i, p in enumerate(available[:top], 1):
        print(f"   {i:>2}. {p['name']:<24} {p['position']:<3} {p['team'] or 'FA':<4} {p['value']:>5}")
    for pos in VALUED_POSITIONS:
        pool = [p for p in available if p["position"] == pos][:5]
        if not pool:
            continue
        parts = []
        for j, p in enumerate(pool):
            gap = pool[j - 1]["value"] - p["value"] if j else 0
            cliff = " |CLIFF" if j and gap >= 400 else ""
            parts.append(f"{p['name']} {p['value']}{cliff}")
        print(f"   {pos:<3}: " + "  ·  ".join(parts))


async def run(args: argparse.Namespace) -> None:
    async with SleeperClient() as client:
        user = await client.users.get_user(args.username)
        state = await client.state.get_state()

        draft_id = args.draft_id
        league = None
        if not draft_id:
            leagues = await client.users.get_user_leagues(user.user_id, season=state.season)
            if args.league:
                leagues = [l for l in leagues if args.league.lower() in l.name.lower()]
            if len(leagues) != 1:
                names = ", ".join(l.name for l in leagues) or "none"
                sys.exit(f"Ambiguous league (matched: {names}) — use --league or --draft-id.")
            league = leagues[0]
            draft_id = league.draft_id

        draft = await client.drafts.get_draft(draft_id)
        if league is None and draft.league_id:
            league = await client.leagues.get_league(draft.league_id)
        teams = league.total_rosters if league else 12
        positions = league.roster_positions if league else []
        superflex = "SUPER_FLEX" in (positions or [])
        value_key = "sf_value" if superflex else "oqb_value"

        lg_users = await client.leagues.get_users(league.league_id) if league else []
        names_by_id = {u.user_id: u.display_name for u in lg_users}
        my_slot = (draft.draft_order or {}).get(user.user_id)

        board = load_board(value_key)
        board_by_key = dict(board)  # (norm_name, pos) -> rec
        print(f"Draft co-pilot — {league.name if league else draft_id} | {teams} teams | "
              f"{'SF' if superflex else '1QB'} values | your slot: {my_slot or '?'}")
        print(f"KTC snapshot: {json.loads(KTC_LATEST.read_text())['date']} "
              f"({len(board)} valued players). Ctrl-C to stop.")

        seen: set[int] = set()
        my_picks: list[str] = []
        while True:
            picks = await client.drafts.get_picks(draft_id)
            drafted_keys = set()
            new = []
            for p in sorted(picks, key=lambda x: x.pick_no):
                meta = p.metadata
                pname = f"{meta.first_name} {meta.last_name}" if meta else "?"
                key = (norm(pname), meta.position if meta else "")
                drafted_keys.add(key)
                if p.pick_no not in seen:
                    seen.add(p.pick_no)
                    new.append((p, pname, key))

            available = sorted(
                (rec for key, rec in board_by_key.items() if key not in drafted_keys),
                key=lambda r: -r["value"],
            )
            ranks = {(norm(r["name"]), r["position"]): i + 1 for i, r in enumerate(
                sorted(board.values(), key=lambda r: -r["value"]))}

            for p, pname, key in new:
                who = names_by_id.get(p.picked_by or "", f"slot {p.draft_slot}")
                rec = board_by_key.get(key)
                val = f"KTC {rec['value']} (board #{ranks.get(key, '?')})" if rec else "unvalued"
                mine = p.picked_by == user.user_id or (my_slot and p.draft_slot == my_slot)
                if mine:
                    my_picks.append(f"{p.metadata.position if p.metadata else '?'} {pname}")
                print(f"  {fmt_pick_no(p.pick_no, teams)} {who:<18} {pname:<24} {val}"
                      f"{'   <== YOU' if mine else ''}")

            if new or not seen:
                next_no = (max(seen) if seen else 0) + 1
                on_clock = snake_slot(next_no, teams)
                until_me = ""
                if my_slot:
                    n = next_no
                    while snake_slot(n, teams) != my_slot:
                        n += 1
                    until_me = f" — your turn in {n - next_no} picks" if n != next_no else " — YOU ARE ON THE CLOCK"
                print(f"\n  Next: pick {fmt_pick_no(next_no, teams)} (slot {on_clock}){until_me}")
                if my_picks:
                    print(f"  Your roster: {', '.join(my_picks)}")
                print_board(available, args.top)

            if args.once:
                break
            if draft.status == "complete" or (seen and len(seen) >= teams * 20):
                print("Draft complete.")
                break
            await asyncio.sleep(args.interval)
            draft = await client.drafts.get_draft(draft_id)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("username")
    ap.add_argument("--league", help="league name filter")
    ap.add_argument("--draft-id")
    ap.add_argument("--interval", type=float, default=8.0, help="poll seconds (default 8)")
    ap.add_argument("--top", type=int, default=12, help="board depth (default 12)")
    ap.add_argument("--once", action="store_true", help="print board once and exit")
    try:
        asyncio.run(run(ap.parse_args()))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
