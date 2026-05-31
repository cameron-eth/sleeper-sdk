"""Higher-level league analysis commands.

Commands:
    picks            All future pick assets in a league with KTC
    gm-mode          Team archetype classification (contender/rebuild/etc)
    proposed-trades  League-wide trade history with KTC verdicts (auth)

`proposed-trades` requires SLEEPER_TOKEN; the others are read-only.
"""
from __future__ import annotations

import argparse
import os
import sys

from sleeper.cli._common import (
    _build_sleeper_to_ktc,
    _build_user_display,
    _fetch_roster_and_players,
    _format_table,
    _ktc_value,
    _lazy_load_analytics,
    _player_view,
    _resolve_league,
    _verdict_from_delta,
)



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
    user_display = _build_user_display(league_users)

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
    user_display = _build_user_display(league_users)

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

    # Lazy-load gm_mode module via file path (bypasses analytics/__init__.py
    # which has a pre-existing broken import on user_collector).
    # __file__ is src/sleeper/cli/analysis.py — walk up one to src/sleeper/
    # before descending into analytics/.
    _gm = _lazy_load_analytics("gm_mode")

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


def cmd_trade_partners(args) -> None:
    """Rank other league owners by trade-partner compatibility.

    For each rival roster, classify their archetype with gm_mode, derive
    their positional strengths/weaknesses, optionally fold in past
    completed-trade history with the user, and produce a single
    "engagement priority" score. Output is a ranked table with rationale.

    Auth: optional. With SLEEPER_TOKEN, the history factor uses real
    league-wide completed trades. Without, history defaults to zero.
    """
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players
    from sleeper.analytics.partner_match import (
        PartnerScore,
        TradeHistory,
        rank_partners,
        score_partner,
    )
    from sleeper.analytics.find_trades_engine import package_overpay

    user, league = _resolve_league(args.username, args.league)
    print(f"League: {league.name}")
    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

    async def _get_users():
        async with SleeperClient() as client:
            return await client.leagues.get_users(league.league_id)
    league_users = asyncio.run(_get_users())
    user_display = _build_user_display(league_users)

    my_roster = next((r for r in rosters if r.owner_id == user.user_id), None)
    if my_roster is None:
        print(f"No roster found for '{args.username}' in {league.name}.")
        sys.exit(1)

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)

    _gm = _lazy_load_analytics("gm_mode")

    def _classify(roster):
        """Run gm_mode on a roster and return (archetype, strong_set, weak_set)."""
        try:
            report = _gm.generate_gm_report(
                my_roster=roster,
                all_rosters=rosters,
                sleeper_players=sleeper_players,
                sleeper_to_ktc=sleeper_to_ktc,
                user_display=user_display,
                fmt=args.format,
            )
            arch = report.archetype.archetype if report.archetype else "UNKNOWN"
            strong = {p.position for p in report.positions if p.strength_score >= 0.4}
            weak = {p.position for p in report.positions if p.strength_score <= -0.4}
            return arch, strong, weak
        except Exception:
            return "UNKNOWN", set(), set()

    print(f"Classifying {len(rosters)} rosters...")
    user_arch, user_strong, user_weak = _classify(my_roster)

    # Optional: pull league-wide trade history
    histories: dict[int, TradeHistory] = {}
    try:
        from sleeper.auth import SleeperAuthClient
        with SleeperAuthClient() as auth:
            trades = auth.get_trades(
                league.league_id,
                statuses=["complete"],
                roster_ids=[r.roster_id for r in rosters],
                limit=500,
            )
        seen: set[str] = set()
        my_rid = my_roster.roster_id
        for t in trades:
            tid = t.get("transaction_id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            rids = t.get("roster_ids") or []
            if my_rid not in rids:
                continue
            # Build per-side KTC totals from `adds`
            adds = t.get("adds") or {}
            per_side: dict[int, list[int]] = {int(r): [] for r in rids}
            for pid, rid in adds.items():
                try:
                    rid_i = int(rid)
                except (TypeError, ValueError):
                    continue
                ktc_p = sleeper_to_ktc.get(pid)
                val = (ktc_p.superflex.value if ktc_p and ktc_p.superflex else 0) or 0
                per_side.setdefault(rid_i, []).append(val)
            # Pair vs other rosters (skip 3-way trades from scoring)
            others = [r for r in rids if r != my_rid]
            if len(others) != 1:
                continue
            other = int(others[0])
            send_vals = [v for v in per_side.get(other, []) if v > 0]
            recv_vals = [v for v in per_side.get(my_rid, []) if v > 0]
            if not send_vals and not recv_vals:
                continue
            h = histories.setdefault(other, TradeHistory())
            h.total += 1
            # Score from user's perspective: user sends recv_vals... wait,
            # `adds` shows who RECEIVED each player. So per_side[my_rid] is
            # what user RECEIVED. The OTHER's per_side is what user GAVE.
            send_for_score = recv_vals or [0]   # what user "spent" to receive
            target_total = sum(per_side.get(other, []))
            if recv_vals and per_side.get(other):
                # User sent per_side[other] (what other received), received per_side[my_rid]
                # Use package_overpay from user's perspective: user sent A, got B target
                sent = per_side.get(other, [])
                got = sum(per_side.get(my_rid, []))
                if got > 0 and sent:
                    score = package_overpay(send_values=sent, target_ktc=got)
                    # adjusted_overpay POSITIVE = user overpaid (gave more value)
                    # Flip for user-net: negative overpay = user net win
                    user_net = -score.adjusted_overpay
                    h.user_net_ktc += user_net
                    if user_net > 800:
                        h.user_wins += 1
                    elif user_net < -800:
                        h.user_losses += 1
                    else:
                        h.fair += 1
    except Exception as e:
        print(f"  (history fetch skipped: {e})")

    # Score each non-user owner
    scores: list[PartnerScore] = []
    for roster in rosters:
        if roster.owner_id == user.user_id:
            continue
        owner = user_display.get(str(roster.owner_id), f"Roster {roster.roster_id}")
        p_arch, p_strong, p_weak = _classify(roster)
        history = histories.get(roster.roster_id, TradeHistory())
        scores.append(score_partner(
            owner=owner,
            roster_id=roster.roster_id,
            user_archetype=user_arch,
            partner_archetype=p_arch,
            user_strong=user_strong,
            user_weak=user_weak,
            partner_strong=p_strong,
            partner_weak=p_weak,
            history=history,
        ))

    ranked = rank_partners(scores)
    top_n = args.top if hasattr(args, "top") else 12

    print()
    print("=" * 78)
    print(f"  TRADE PARTNERS for {args.username} in {league.name}")
    print(f"  Your archetype: {user_arch}")
    if user_strong:
        print(f"  Your strengths:  {', '.join(sorted(user_strong))}")
    if user_weak:
        print(f"  Your weaknesses: {', '.join(sorted(user_weak))}")
    print("=" * 78)
    print()
    headers = ["#", "Owner", "Score", "Arch", "Syn", "Pos", "Hist", "Rationale"]
    rows = []
    for i, s in enumerate(ranked[:top_n], 1):
        rows.append([
            str(i),
            s.owner[:22],
            f"{s.total:+d}",
            s.archetype[:9],
            f"{s.synergy:+d}",
            f"{s.positional.score:+d}",
            f"{s.history_pts:+d}",
            s.rationale[:60],
        ])
    print(_format_table(headers, rows))
    print()
    if ranked:
        top = ranked[0]
        print(f"🎯 Top engage target: {top.owner} (score {top.total:+d})")
        print(f"   {top.rationale}")


def cmd_proposed_trades(args) -> None:
    """List every pending/proposed trade in the league with KTC valuation.

    Hits the private `league_transactions_filtered` GraphQL endpoint via
    SleeperAuthClient and renders adds + traded picks per side, with the
    same value adjustment used in `find-trades` and `trade-check`.
    """
    import asyncio
    from sleeper.client import SleeperClient
    from sleeper.enrichment.ktc import fetch_ktc_players
    from sleeper.analytics.value_adjustment import compute_value_adjustment

    user, league = _resolve_league(args.username, args.league)
    print(f"League: {league.name}")
    rosters, sleeper_players = _fetch_roster_and_players(league.league_id)

    async def _get_users():
        async with SleeperClient() as client:
            return await client.leagues.get_users(league.league_id)
    league_users = asyncio.run(_get_users())

    # roster_id (int) -> display name
    user_display = _build_user_display(league_users)
    roster_to_owner: dict[int, str] = {}
    for r in rosters:
        oid = str(r.owner_id or "")
        roster_to_owner[r.roster_id] = user_display.get(oid) or f"Roster {r.roster_id}"

    print("Fetching KTC values...")
    ktc_players = fetch_ktc_players()
    sleeper_to_ktc = _build_sleeper_to_ktc(ktc_players, sleeper_players)
    pick_ktc_by_name = {p.player_name: p for p in ktc_players if p.position == "RDP"}

    def _ktc_for_player(pid: str) -> int:
        ktc_p = sleeper_to_ktc.get(pid)
        if not ktc_p or not ktc_p.superflex:
            return 0
        return ktc_p.superflex.value or 0

    # Pick KTC lookup: delegates to analytics.pick_value (Mid → Early → Late
    # tier fallback). Pure function, unit-testable without network.
    from sleeper.analytics.pick_value import lookup_pick_ktc

    def _ktc_for_pick(season: str, rnd: int) -> int:
        return lookup_pick_ktc(season, rnd, pick_ktc_by_name, fmt="sf")

    statuses = args.status if args.status else None  # None = all statuses
    limit = args.limit
    print(f"Fetching trades (auth required, statuses={statuses or 'ALL'}, limit={limit})...")
    try:
        from sleeper.auth import SleeperAuthClient
    except Exception as e:
        print(f"Auth client unavailable: {e}")
        sys.exit(1)

    # The GraphQL endpoint defaults to scoping results to the authenticated
    # session's own trades. To get a TRUE league-wide view, we explicitly
    # pass every roster ID in the league and dedupe by transaction_id.
    all_roster_ids = sorted({r.roster_id for r in rosters})
    try:
        with SleeperAuthClient() as auth:
            raw = auth.get_trades(
                league.league_id,
                statuses=statuses,
                roster_ids=all_roster_ids,
                limit=max(limit, 500),
            )
    except Exception as e:
        print(f"Failed to fetch trades: {e}")
        print("Set SLEEPER_TOKEN env var with a valid session token.")
        sys.exit(1)

    seen: set[str] = set()
    trades: list[dict] = []
    for t in raw:
        tid = t.get("transaction_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        trades.append(t)

    if not trades:
        print("No trades found.")
        return

    # Sort newest first by created timestamp
    trades.sort(key=lambda t: t.get("created") or 0, reverse=True)

    # League-wide status histogram (computed BEFORE the user filter so the
    # totals reflect the actual league activity, not just the filtered slice).
    by_status: dict[str, int] = {}
    for t in trades:
        s = t.get("status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    summary = "  ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
    print(f"\nLeague-wide: {len(trades)} trade(s) — {summary}")

    # Optional per-user filter — case-insensitive substring match against
    # display names of any roster involved in the trade.
    user_filter = None
    if args.user:
        user_filter = [u.lower() for u in args.user]
        before = len(trades)
        filtered = []
        for t in trades:
            rids = t.get("roster_ids") or []
            owners = " ".join(roster_to_owner.get(int(r), "").lower() for r in rids)
            if any(u in owners for u in user_filter):
                filtered.append(t)
        trades = filtered
        # Filtered status histogram
        f_status: dict[str, int] = {}
        for t in trades:
            s = t.get("status") or "unknown"
            f_status[s] = f_status.get(s, 0) + 1
        f_summary = "  ".join(f"{k}={v}" for k, v in sorted(f_status.items()))
        print(f"User filter {args.user}: {before} → {len(trades)} trades — {f_summary}")

    print("=" * 88)

    for idx, t in enumerate(trades, 1):
        roster_ids: list[int] = t.get("roster_ids") or []
        consenter_ids: list[int] = t.get("consenter_ids") or []
        # Sleeper returns adds/drops as TOP-LEVEL fields {player_id: roster_id}.
        # Fall back to metadata.adds for shapes where they're nested instead.
        adds_raw = t.get("adds")
        if not isinstance(adds_raw, dict):
            meta = t.get("metadata") if isinstance(t.get("metadata"), dict) else {}
            adds_raw = (meta.get("adds") if meta else None) or {}
        adds: dict[str, int] = adds_raw or {}
        draft_picks: list = t.get("draft_picks") or []

        # Build per-roster bundles of what they RECEIVE
        per_side: dict[int, dict] = {rid: {"players": [], "picks": [], "ktc": 0}
                                      for rid in roster_ids}

        for pid, rid in adds.items():
            try:
                rid_int = int(rid)
            except (TypeError, ValueError):
                continue
            if rid_int not in per_side:
                per_side[rid_int] = {"players": [], "picks": [], "ktc": 0}
            p = sleeper_players.get(pid)
            name = (p.full_name if p else None) or pid
            pos = p.position if p else "?"
            val = _ktc_for_player(pid)
            per_side[rid_int]["players"].append((name, pos, val))
            per_side[rid_int]["ktc"] += val

        for dp in draft_picks:
            # GraphQL response format varies:
            #   - dict: {season, round, owner_id (receiver), previous_owner_id, roster_id (original)}
            #   - string: "original_roster,season,round,from_roster,to_roster"
            season: str = ""
            rnd: int = 0
            receiver: int | None = None
            if isinstance(dp, dict):
                try:
                    receiver = int(dp.get("owner_id"))
                    season = str(dp.get("season"))
                    rnd = int(dp.get("round"))
                except (TypeError, ValueError):
                    continue
            elif isinstance(dp, str):
                parts = dp.split(",")
                if len(parts) < 5:
                    continue
                try:
                    season = str(parts[1])
                    rnd = int(parts[2])
                    receiver = int(parts[4])  # to_roster
                except (ValueError, IndexError):
                    continue
            else:
                continue

            if receiver is None:
                continue
            val = _ktc_for_pick(season, rnd)
            label = f"{season} R{rnd} pick"
            if receiver not in per_side:
                per_side[receiver] = {"players": [], "picks": [], "ktc": 0}
            per_side[receiver]["picks"].append((label, val))
            per_side[receiver]["ktc"] += val

        # Render
        from datetime import datetime
        creator = t.get("creator")
        created_ts = t.get("created")
        date_str = ""
        if created_ts:
            try:
                # Sleeper timestamps are ms-epoch
                date_str = datetime.fromtimestamp(int(created_ts) / 1000).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                date_str = ""
        status = t.get("status") or "?"
        status_emoji = {"proposed": "⏳", "complete": "✅", "rejected": "❌",
                        "cancelled": "🚫", "vetoed": "🛑"}.get(status, "•")
        print(f"\n#{idx}  {status_emoji} {status.upper()}  ({date_str})  Trade ID {t.get('transaction_id')}")
        print(f"     Creator user_id {creator}  |  Consenters: {consenter_ids or 'none'}")
        sides = list(per_side.items())
        for rid, bundle in sides:
            owner = roster_to_owner.get(rid, f"Roster {rid}")
            consent_mark = "✓" if rid in consenter_ids else "·"
            print(f"  [{consent_mark}] {owner} (roster {rid}) RECEIVES — KTC {bundle['ktc']:,}")
            for name, pos, val in bundle["players"]:
                print(f"        {pos:3s}  {name:28s}  {val:>6,}")
            for label, val in bundle["picks"]:
                print(f"        PICK {label:24s}  {val:>6,}")

        # Two-sided value adjustment (only meaningful when exactly 2 sides
        # AND both sides actually receive something — Sleeper sometimes
        # records a trade with one side getting only picks the other side
        # didn't formally surrender).
        if len(sides) == 2:
            (rid_a, side_a), (rid_b, side_b) = sides
            send_vals = [v for _, _, v in side_a["players"]] + [v for _, v in side_a["picks"]]
            recv_vals = [v for _, _, v in side_b["players"]] + [v for _, v in side_b["picks"]]
        else:
            send_vals, recv_vals = [], []

        if len(sides) == 2 and send_vals and recv_vals:
            # NB: from side A's POV, side_a "sends" and side_b "receives".
            adj = compute_value_adjustment(send_values=send_vals, receive_values=recv_vals)
            raw_delta = side_b["ktc"] - side_a["ktc"]   # positive = B "wins" raw
            if adj.favors == "receive":
                # B is the multi-receiver of the stud; A consolidates onto B
                adjusted = raw_delta - adj.adjustment
            elif adj.favors == "send":
                adjusted = raw_delta + adj.adjustment
            else:
                adjusted = raw_delta

            owner_a = roster_to_owner.get(rid_a, f"Roster {rid_a}")
            owner_b = roster_to_owner.get(rid_b, f"Roster {rid_b}")
            print(f"\n     Raw KTC delta: {raw_delta:+,}  (positive favors {owner_b})")
            if adj.adjustment > 0:
                print(f"     Stud premium:  {adj.adjustment:+,} ({adj.stud_tier} tier, favors {adj.favors})")
            print(f"     Adjusted:      {adjusted:+,}")
            # Per-trade verdict reuses the same band constants as
            # _verdict_from_delta but renders "WIN for <owner>" since both
            # sides are named in this context.
            from sleeper.cli._common import VERDICT_FAIR_BAND, VERDICT_WIN_THRESHOLD
            if adjusted > VERDICT_FAIR_BAND:
                verdict = f"WIN for {owner_b}"
            elif adjusted < -VERDICT_FAIR_BAND:
                verdict = f"WIN for {owner_a}"
            elif abs(adjusted) <= VERDICT_WIN_THRESHOLD:
                verdict = "FAIR"
            else:
                verdict = f"slight edge to {owner_b if adjusted > 0 else owner_a}"
            print(f"     Verdict:       {verdict}")
        print("-" * 88)


