"""Trade evaluation and search commands.

Commands:
    trade-check       Score a hypothetical 2-sided trade
    suggest-trades    1-for-1 league-wide trade scan
    find-trades       Targeted search with positional filters + chip combos

All read-only; no auth required.
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
    _ktc_rank,
    _ktc_trend,
    _ktc_value,
    _resolve_league,
)

# Cache directory for `suggest-trades` output — `send-trade --suggestion N`
# reads from here so users can fire a previously-shown suggestion by index.
SUGGESTION_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".sleeper-sdk")



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

        # Aging-QB chip discount: in Superflex, KTC face value for QBs 28+
        # overstates what they actually transact for as a trade chip. A 31yo
        # QB at 4,700 KTC doesn't return 4,700 of WR/RB value — apply a
        # reality discount so the package math reflects market behavior.
        age = getattr(p, "age", None) or 0
        if p.position == "QB":
            if age >= 32:
                val = int(val * 0.45)
            elif age >= 30:
                val = int(val * 0.55)
            elif age >= 28:
                val = int(val * 0.75)

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
                    # up a roster spot. If target is a stud, the receive side
                    # (us) owes a stud premium ON TOP OF face value. So the
                    # effective fair price is target_ktc + adj.adjustment, and
                    # our effective overpay is what we sent minus that fair price.
                    adj = compute_value_adjustment(
                        send_values=[chip1["ktc"], chip2["ktc"]],
                        receive_values=[target["ktc"]],
                    )
                    # When favors=="receive": fair_price = target + adj. We need
                    # to send (face + premium) to break even; sending less means
                    # we underpay. Subtract adj to reflect this debit.
                    if adj.favors == "receive":
                        adjusted_overpay = overpay - adj.adjustment
                    elif adj.favors == "send":
                        # We're consolidating — partner owes us premium. Adding
                        # to our overpay reflects that we deserve more credit.
                        adjusted_overpay = overpay + adj.adjustment
                    else:
                        adjusted_overpay = overpay
                    if args.min_overpay <= adjusted_overpay <= args.max_overpay:
                        trades.append({
                            "target": target,
                            "chips": [chip1, chip2],
                            "overpay": overpay,
                            "adj": adj,
                            "adjusted_overpay": adjusted_overpay,
                            # Score: prefer trades where adjusted overpay is small
                            # and positive (a "fair" overpay). Penalize anything
                            # far from the fair-price midpoint.
                            "score": target["ktc"] * 1.5 - abs(adjusted_overpay) * 0.5,
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


