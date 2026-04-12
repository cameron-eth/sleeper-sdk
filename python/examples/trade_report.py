#!/usr/bin/env python3
"""
Example: Full trade analysis pipeline.

Demonstrates every new module:
  - enrichment/ktc.py          → fetch KTC values, match to Sleeper IDs
  - enrichment/marketplace.py  → build marketplace values, KTC vs Actual
  - analytics/user_collector.py → collect all league data for a user
  - analytics/user_trades.py    → evaluate trades, build report

Usage:
    python examples/trade_report.py <username_or_id> [--seasons 2023 2024]
"""

import argparse
import asyncio
import sys

from sleeper import SleeperClient
from sleeper.enrichment.ktc import (
    detect_scoring_type,
    fetch_ktc_values,
    match_ktc_to_sleeper,
)
from sleeper.enrichment.marketplace import (
    compare_ktc_vs_actual,
    get_marketplace_values,
)
from sleeper.analytics.user_collector import (
    collect_user_league_snapshots,
    extract_trades_only,
)
from sleeper.analytics.user_trades import (
    evaluate_user_trades,
    build_user_trade_report,
)


async def run(username_or_id: str, seasons: list[str] | None = None):
    async with SleeperClient() as client:

        # ── Step 1: Resolve user ──
        print(f"Looking up user: {username_or_id}")
        user = await client.users.get_user(username_or_id)
        print(f"  Found: {user.display_name} (ID: {user.user_id})")
        print()

        # ── Step 2: Fetch KTC dynasty values ──
        print("Fetching KTC dynasty values...")
        ktc_players = fetch_ktc_values()
        print(f"  Scraped {len(ktc_players)} players from KTC")

        # Match KTC to Sleeper IDs
        print("Fetching Sleeper player database...")
        sleeper_players = await client.get_all_players()
        print(f"  {len(sleeper_players)} players in Sleeper database")

        ktc_matched = match_ktc_to_sleeper(ktc_players, sleeper_players)
        print(f"  Matched {len(ktc_matched)} KTC players to Sleeper IDs")
        print()

        # ── Step 3: Collect all league data ──
        print("Collecting league data across seasons...")
        snapshots = await collect_user_league_snapshots(
            users_api=client.users,
            leagues_api=client.leagues,
            user_id=user.user_id,
            seasons=seasons,
        )
        print(f"  Found {len(snapshots)} league-seasons")
        for snap in snapshots:
            print(f"    [{snap.season}] {snap.league_name} ({snap.scoring_type.upper()}) "
                  f"- {len(snap.transactions)} transactions")
        print()

        # ── Step 4: Extract trades and build marketplace values ──
        trades = extract_trades_only(snapshots)
        print(f"Found {len(trades)} completed trades across all leagues")

        if not trades:
            print("No trades found. Nothing to evaluate.")
            return

        # Build KTC seed values using the most common scoring type
        sf_count = sum(1 for s in snapshots if s.scoring_type == "sf")
        primary_scoring = "sf" if sf_count > len(snapshots) / 2 else "1qb"
        print(f"Primary scoring format: {primary_scoring.upper()}")

        ktc_seed: dict[str, float] = {}
        for sid, kp in ktc_matched.items():
            ktc_seed[sid] = float(kp.value_sf if primary_scoring == "sf" else kp.value_1qb)

        # Build marketplace values from real trade activity
        trade_tuples = [
            (tx, snap.league_id, snap.season, snap.rosters)
            for tx, snap in trades
        ]
        print(f"Building marketplace values from {len(trade_tuples)} trades...")
        marketplace = get_marketplace_values(trade_tuples, seed_values=ktc_seed)
        print(f"  Derived values for {len(marketplace)} assets")
        print()

        # ── Step 5: KTC vs Actual Market Price ──
        comparisons = compare_ktc_vs_actual(
            marketplace_values=marketplace,
            ktc_matched=ktc_matched,
            sleeper_players=sleeper_players,
            scoring_type=primary_scoring,
            min_trades=1,
            delta_threshold_pct=10.0,
        )
        _print_ktc_vs_actual(comparisons)

        # ── Step 6: Evaluate user's trades ──
        print("Evaluating your trades...")
        all_evaluated = []
        for snap in snapshots:
            evals = evaluate_user_trades(
                user_id=user.user_id,
                user_roster_id=snap.user_roster_id,
                transactions=snap.transactions,
                league_id=snap.league_id,
                league_name=snap.league_name,
                season=snap.season,
                marketplace_values=marketplace,
                roster_to_owner=snap.roster_to_owner,
                owner_to_name=snap.owner_to_name,
                rosters=snap.rosters,
            )
            all_evaluated.extend(evals)

        # ── Step 7: Build the report ──
        report = build_user_trade_report(
            user_id=user.user_id,
            all_evaluated_trades=all_evaluated,
            display_name=user.display_name,
        )

        _print_report(report, sleeper_players)


def _print_ktc_vs_actual(comparisons):
    """Print the KTC vs Actual Market Price table."""
    if not comparisons:
        print("No players with enough trade data for KTC comparison.\n")
        return

    buys = [c for c in comparisons if c.signal == "buy"]
    sells = [c for c in comparisons if c.signal == "sell"]
    fairs = [c for c in comparisons if c.signal == "fair"]

    print("=" * 80)
    print("  KTC VALUE vs ACTUAL ACQUISITION COST")
    print("  (What KTC says vs what it actually takes to get them in your leagues)")
    print("=" * 80)
    print()
    print(f"  {'Player':<22} {'Pos':<4} {'Team':<5} {'KTC':>6} {'Actual':>7} "
          f"{'Delta':>7} {'%':>6}  {'Trades':>6}  Signal")
    print(f"  {'-'*22} {'-'*4} {'-'*5} {'-'*6} {'-'*7} {'-'*7} {'-'*6}  {'-'*6}  {'-'*6}")

    for c in comparisons:
        delta_str = f"{c.delta:+.0f}"
        pct_str = f"{c.delta_pct:+.1f}%"
        if c.signal == "buy":
            marker = "BUY"
        elif c.signal == "sell":
            marker = "SELL"
        else:
            marker = "fair"
        print(f"  {c.name:<22} {c.position:<4} {c.team:<5} {c.ktc_value:>6,} "
              f"{c.actual_cost:>7,.0f} {delta_str:>7} {pct_str:>6}  {c.times_traded:>6}  {marker}")

    print()
    print(f"  Summary: {len(buys)} buy targets (cheaper than KTC), "
          f"{len(sells)} sell targets (pricier than KTC), "
          f"{len(fairs)} fairly priced")
    print()
    print(f"  BUY  = KTC overvalues them. They cost LESS than KTC says in your leagues.")
    print(f"  SELL = KTC undervalues them. They cost MORE than KTC says. Sell high.")
    print(f"  fair = Market price is close to KTC value.")
    print()


def _print_report(report, sleeper_players):
    """Pretty-print the trade report."""
    print()
    print("=" * 80)
    print(f"  TRADE REPORT: {report.display_name}")
    print("=" * 80)
    print(f"  Total trades:        {report.total_trades}")
    print(f"  Net value:           {report.net_value:+.0f}")
    print(f"  Avg value per trade: {report.avg_value_per_trade:+.0f}")
    print(f"  Win rate:            {report.win_rate:.0%}")
    print()

    if report.best_trades:
        print("-" * 80)
        print("  BEST TRADES")
        print("-" * 80)
        for i, trade in enumerate(report.best_trades[:5], 1):
            _print_trade(i, trade, sleeper_players)

    if report.worst_trades:
        print("-" * 80)
        print("  WORST TRADES")
        print("-" * 80)
        for i, trade in enumerate(report.worst_trades[:5], 1):
            _print_trade(i, trade, sleeper_players)

    print("=" * 80)


def _print_trade(rank, trade, sleeper_players):
    """Print a single evaluated trade."""
    def _names(player_ids):
        names = []
        for pid in player_ids:
            sp = sleeper_players.get(pid)
            if sp:
                names.append(f"{sp.first_name} {sp.last_name}")
            else:
                names.append(pid)
        return names

    def _pick_strs(picks):
        return [f"{p.season} Rd{p.round} ({p.estimated_position})" for p in picks]

    received = _names(trade.user_side.players) + _pick_strs(trade.user_side.picks)
    sent = _names(trade.other_side.players) + _pick_strs(trade.other_side.picks)

    print(f"  {rank}. [{trade.season} W{trade.week or '?'}] {trade.league_name}")
    print(f"     Received: {', '.join(received) or 'nothing'} "
          f"(value: {trade.user_side.total_value:.0f})")
    print(f"     Sent:     {', '.join(sent) or 'nothing'} "
          f"(value: {trade.other_side.total_value:.0f})")
    print(f"     Net:      {trade.value_gained:+.0f}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Sleeper trade analysis report")
    parser.add_argument("user", help="Sleeper username or user ID")
    parser.add_argument(
        "--seasons", nargs="+", default=None,
        help="Seasons to analyze (e.g. 2023 2024). Defaults to all.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.user, args.seasons))


if __name__ == "__main__":
    main()
