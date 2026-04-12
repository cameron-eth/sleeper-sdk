#!/usr/bin/env python3
"""
Individual examples for each new module.

Run any section standalone or use as a reference.
"""

import asyncio
from sleeper import SleeperClient


# ─────────────────────────────────────────────────────────────────────
# 1. enrichment/ktc.py — KTC Dynasty Values
# ─────────────────────────────────────────────────────────────────────

def example_ktc():
    """Scrape KTC values and match to Sleeper player IDs."""
    from sleeper.enrichment.ktc import (
        fetch_ktc_values,
        match_ktc_to_sleeper,
        detect_scoring_type,
    )

    # Fetch current KTC values
    ktc_players = fetch_ktc_values()
    print(f"Scraped {len(ktc_players)} players from KTC\n")

    # Show top 10 by superflex value
    top_sf = sorted(ktc_players, key=lambda p: p.value_sf, reverse=True)[:10]
    print("Top 10 KTC Superflex Values:")
    print(f"  {'Player':<25} {'Pos':<4} {'Team':<5} {'SF Value':>8} {'1QB Value':>9}")
    print(f"  {'-'*25} {'-'*4} {'-'*5} {'-'*8} {'-'*9}")
    for p in top_sf:
        print(f"  {p.player_name:<25} {p.position:<4} {p.team:<5} "
              f"{p.value_sf:>8,} {p.value_1qb:>9,}")

    # Match to Sleeper IDs (requires async client for player data)
    async def match():
        async with SleeperClient() as client:
            sleeper_players = await client.get_all_players()
            matched = match_ktc_to_sleeper(ktc_players, sleeper_players)
            print(f"\nMatched {len(matched)}/{len(ktc_players)} KTC players to Sleeper IDs")

            # Show a few matches
            print("\nSample matches:")
            for sid, kp in list(matched.items())[:5]:
                sp = sleeper_players[sid]
                print(f"  Sleeper: {sp.first_name} {sp.last_name} ({sid}) "
                      f"→ KTC: {kp.player_name} (SF: {kp.value_sf})")

            # Detect scoring type for a league
            leagues = await client.users.get_user_leagues("1328460395249172480")
            if leagues:
                league = leagues[0]
                scoring = detect_scoring_type(league)
                print(f"\nLeague '{league.name}' scoring type: {scoring.upper()}")

    asyncio.run(match())


# ─────────────────────────────────────────────────────────────────────
# 2. enrichment/marketplace.py — Marketplace Value Engine
# ─────────────────────────────────────────────────────────────────────

def example_marketplace():
    """Build marketplace values from mock trade data.

    Shows how multi-player trades are isolated:
    - 1-for-N trades: the solo player's cost = full other side.
    - N-for-N trades: each player's cost = other_side - companions.
      e.g. Chase + Zay Jones traded for Derrick Henry + 2025 1st:
      Chase's cost = (Henry + 1st) - value(Zay Jones)
      Zay's cost   = (Henry + 1st) - value(Chase)
    """
    from sleeper.enrichment.marketplace import (
        TradeObservation,
        PickAsset,
        build_marketplace_values,
    )

    # Real players with KTC-scale seed values (superflex)
    NAMES = {
        "mahomes":    "Patrick Mahomes",
        "jamarr":     "Ja'Marr Chase",
        "d_henry":    "Derrick Henry",
        "g_wilson":   "Garrett Wilson",
        "devonta":    "DeVonta Smith",
        "zay_jones":  "Zay Jones",
    }
    seed_values = {
        "mahomes":   9500.0,
        "jamarr":    8800.0,
        "d_henry":   4000.0,
        "g_wilson":  2500.0,
        "devonta":   3200.0,
        "zay_jones":  400.0,  # low-value throw-in
    }

    observations = [
        # Trade 1 (1-for-N): Mahomes traded for Ja'Marr + 2025 mid 1st
        # Mahomes cost = jamarr(8800) + pick(4500) = 13300.  Clean signal.
        TradeObservation(
            transaction_id="t1", league_id="Dynasty Bros", season="2024",
            side_a_players=["mahomes"],
            side_b_players=["jamarr"],
            side_b_picks=[PickAsset(season="2025", round=1,
                                    original_roster_id=1, estimated_position="mid")],
        ),
        # Trade 2 (2-for-2): Ja'Marr + Zay Jones traded for Henry + 2025 early 1st
        # This is where isolation matters:
        #   Ja'Marr's cost  = (henry + pick) - zay_jones = (4000+6500) - 400 = 10100
        #   Zay Jones' cost = (henry + pick) - jamarr    = (4000+6500) - 8800 = 1700
        # Without isolation (proportional), Ja'Marr would get 96% of the pie
        # regardless of what actually happened.
        TradeObservation(
            transaction_id="t2", league_id="Keeper League", season="2024",
            side_a_players=["jamarr", "zay_jones"],
            side_b_players=["d_henry"],
            side_b_picks=[PickAsset(season="2025", round=1,
                                    original_roster_id=3, estimated_position="early")],
        ),
        # Trade 3 (1-for-2): Henry traded for Wilson + DeVonta
        # Henry cost = wilson(2500) + devonta(3200) = 5700.  Clean signal.
        TradeObservation(
            transaction_id="t3", league_id="Dynasty Bros", season="2024",
            side_a_players=["d_henry"],
            side_b_players=["g_wilson", "devonta"],
        ),
    ]

    marketplace = build_marketplace_values(observations, seed_values)

    print("Marketplace Values (from 3 observed trades):\n")
    print(f"  {'Player':<22} {'Market':>8} {'KTC':>6} {'Actual':>8} "
          f"{'KTC vs Actual':>15} {'Trades':>7}")
    print(f"  {'-'*22} {'-'*8} {'-'*6} {'-'*8} {'-'*15} {'-'*7}")
    for asset_id, mv in sorted(marketplace.items(), key=lambda x: x[1].market_value, reverse=True):
        name = NAMES.get(asset_id, asset_id)
        ktc_str = f"{mv.ktc_value:.0f}" if mv.ktc_value else "N/A"
        actual_str = f"{mv.median_acquisition_cost:.0f}" if mv.median_acquisition_cost is not None else "N/A"
        diff_str = f"{mv.ktc_vs_actual:+.0f} ({mv.ktc_vs_actual_pct:+.1f}%)" if mv.ktc_vs_actual is not None else "N/A"
        print(f"  {name:<22} {mv.market_value:>8,.0f} {ktc_str:>6} {actual_str:>8} "
              f"{diff_str:>15} {mv.observation_count:>7}")

    print()
    print("  How isolation works per trade:\n")
    for asset_id, mv in sorted(marketplace.items(), key=lambda x: x[1].market_value, reverse=True):
        if mv.acquisition_costs:
            name = NAMES.get(asset_id, asset_id)
            costs_str = ", ".join(f"{c:.0f}" for c in mv.acquisition_costs)
            print(f"    {name}:")
            print(f"      Acquisition costs across trades: [{costs_str}]")
            print(f"      Min: {mv.min_acquisition_cost:.0f}  "
                  f"Median: {mv.median_acquisition_cost:.0f}  "
                  f"Max: {mv.max_acquisition_cost:.0f}")
            print()


# ─────────────────────────────────────────────────────────────────────
# 3. analytics/user_collector.py — Cross-League Data Collection
# ─────────────────────────────────────────────────────────────────────

def example_collector():
    """Collect all league data for a user."""
    from sleeper.analytics.user_collector import (
        collect_user_league_snapshots,
        extract_trades_only,
    )

    async def collect():
        async with SleeperClient() as client:
            user = await client.users.get_user("1328460395249172480")
            print(f"Collecting data for: {user.display_name}\n")

            # Collect just recent seasons to keep it quick
            snapshots = await collect_user_league_snapshots(
                users_api=client.users,
                leagues_api=client.leagues,
                user_id=user.user_id,
                seasons=["2024"],
            )

            print(f"Found {len(snapshots)} leagues in 2024:\n")
            for snap in snapshots:
                trade_count = sum(1 for tx in snap.transactions if tx.type == "trade")
                print(f"  {snap.league_name}")
                print(f"    ID: {snap.league_id}")
                print(f"    Scoring: {snap.scoring_type.upper()}")
                print(f"    Your roster ID: {snap.user_roster_id}")
                print(f"    Total transactions: {len(snap.transactions)}")
                print(f"    Trades: {trade_count}")
                print(f"    League members: {len(snap.users)}")
                print()

            # Extract just the trades
            trades = extract_trades_only(snapshots)
            print(f"Total completed trades across all leagues: {len(trades)}")

    asyncio.run(collect())


# ─────────────────────────────────────────────────────────────────────
# 4. analytics/user_trades.py — Trade Evaluation & Report
# ─────────────────────────────────────────────────────────────────────

def example_user_trades():
    """Evaluate a user's trades with mock marketplace values."""
    from sleeper.analytics.user_trades import (
        evaluate_user_trades,
        build_user_trade_report,
    )
    from sleeper.enrichment.marketplace import MarketplaceValue
    from sleeper.types.transaction import Transaction, TradedPick

    NAMES = {
        "jamarr": "Ja'Marr Chase",
        "breece": "Breece Hall",
        "devonta": "DeVonta Smith",
        "tlaw": "Trevor Lawrence",
    }

    # Mock marketplace values (KTC-scale)
    marketplace = {
        "jamarr": MarketplaceValue(
            asset_id="jamarr", asset_type="player",
            market_value=8800.0, observation_count=5, confidence=0.5,
        ),
        "breece": MarketplaceValue(
            asset_id="breece", asset_type="player",
            market_value=6200.0, observation_count=3, confidence=0.3,
        ),
        "devonta": MarketplaceValue(
            asset_id="devonta", asset_type="player",
            market_value=3200.0, observation_count=2, confidence=0.2,
        ),
        "tlaw": MarketplaceValue(
            asset_id="tlaw", asset_type="player",
            market_value=4500.0, observation_count=4, confidence=0.4,
        ),
    }

    # Mock transactions — Cameron (roster_id=1) made 2 trades
    transactions = [
        # Trade 1: Cameron received Ja'Marr, sent Breece + DeVonta
        # Good trade: 8800 received vs 9400 sent → net -600
        Transaction(
            transaction_id="tx1", type="trade", status="complete",
            roster_ids=[1, 2], leg=3,
            adds={"jamarr": 1, "breece": 2, "devonta": 2},
            drops={"jamarr": 2, "breece": 1, "devonta": 1},
        ),
        # Trade 2: Cameron received T-Law, sent Ja'Marr
        # Bad trade: 4500 received vs 8800 sent → net -4300
        Transaction(
            transaction_id="tx2", type="trade", status="complete",
            roster_ids=[1, 3], leg=8,
            adds={"tlaw": 1, "jamarr": 3},
            drops={"tlaw": 3, "jamarr": 1},
        ),
    ]

    evaluated = evaluate_user_trades(
        user_id="user_123",
        user_roster_id=1,
        transactions=transactions,
        league_id="league_1",
        league_name="Dynasty Bros",
        season="2024",
        marketplace_values=marketplace,
        roster_to_owner={1: "user_123", 2: "user_456", 3: "user_789"},
        owner_to_name={"user_123": "Cameron", "user_456": "Marcus", "user_789": "Trey"},
    )

    print(f"Evaluated {len(evaluated)} trades:\n")
    for e in evaluated:
        received = [NAMES.get(p, p) for p in e.user_side.players]
        sent = [NAMES.get(p, p) for p in e.other_side.players]
        print(f"  [{e.season} W{e.week}] {e.league_name} (vs {e.other_side.display_name}):")
        print(f"    Received: {', '.join(received)} (value: {e.user_side.total_value:,.0f})")
        print(f"    Sent:     {', '.join(sent)} (value: {e.other_side.total_value:,.0f})")
        print(f"    Net:      {e.value_gained:+,.0f}")
        print()

    report = build_user_trade_report("user_123", evaluated, display_name="Cameron")
    print(f"Trade Report for {report.display_name}:")
    print(f"  Total trades:  {report.total_trades}")
    print(f"  Net value:     {report.net_value:+,.0f}")
    print(f"  Avg per trade: {report.avg_value_per_trade:+,.0f}")
    print(f"  Win rate:      {report.win_rate:.0%}")


# ─────────────────────────────────────────────────────────────────────
# 5. Full Pipeline (all modules together)
# ─────────────────────────────────────────────────────────────────────

def example_full_pipeline():
    """Run the complete pipeline end-to-end."""
    from sleeper.enrichment.ktc import fetch_ktc_values, match_ktc_to_sleeper
    from sleeper.enrichment.marketplace import get_marketplace_values
    from sleeper.analytics.user_collector import (
        collect_user_league_snapshots,
        extract_trades_only,
    )
    from sleeper.analytics.user_trades import (
        evaluate_user_trades,
        build_user_trade_report,
    )

    async def pipeline():
        async with SleeperClient() as client:
            user = await client.users.get_user("1328460395249172480")
            print(f"=== Full Pipeline for {user.display_name} ===\n")

            # KTC values
            ktc_players = fetch_ktc_values()
            sleeper_players = await client.get_all_players()
            ktc_matched = match_ktc_to_sleeper(ktc_players, sleeper_players)

            # Build seed values (superflex)
            ktc_seed = {sid: float(kp.value_sf) for sid, kp in ktc_matched.items()}
            print(f"KTC seed: {len(ktc_seed)} players valued")

            # Collect data (just 2024 for speed)
            snapshots = await collect_user_league_snapshots(
                client.users, client.leagues,
                user.user_id, seasons=["2024"],
            )
            print(f"Snapshots: {len(snapshots)} leagues")

            # Marketplace values
            trades = extract_trades_only(snapshots)
            trade_tuples = [(tx, s.league_id, s.season, s.rosters) for tx, s in trades]
            marketplace = get_marketplace_values(trade_tuples, ktc_seed)
            print(f"Marketplace: {len(marketplace)} assets valued from {len(trades)} trades")

            # Evaluate
            all_evals = []
            for snap in snapshots:
                evals = evaluate_user_trades(
                    user.user_id, snap.user_roster_id,
                    snap.transactions, snap.league_id,
                    snap.league_name, snap.season, marketplace,
                    snap.roster_to_owner, snap.owner_to_name, snap.rosters,
                )
                all_evals.extend(evals)

            report = build_user_trade_report(user.user_id, all_evals, user.display_name)
            print(f"\nResult: {report.total_trades} trades, "
                  f"net {report.net_value:+.0f}, "
                  f"win rate {report.win_rate:.0%}")

    asyncio.run(pipeline())


# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    examples = {
        "ktc": ("KTC Dynasty Values", example_ktc),
        "marketplace": ("Marketplace Value Engine (mock data)", example_marketplace),
        "collector": ("Cross-League Data Collection", example_collector),
        "trades": ("Trade Evaluation (mock data)", example_user_trades),
        "full": ("Full Pipeline (live API + KTC)", example_full_pipeline),
    }

    if len(sys.argv) < 2 or sys.argv[1] not in examples:
        print("Usage: python examples/module_examples.py <example>\n")
        print("Available examples:")
        for key, (desc, _) in examples.items():
            print(f"  {key:<15} {desc}")
        print()
        print("Examples using mock data (no API calls):")
        print("  python examples/module_examples.py marketplace")
        print("  python examples/module_examples.py trades")
        print()
        print("Examples using live API:")
        print("  python examples/module_examples.py ktc")
        print("  python examples/module_examples.py collector")
        print("  python examples/module_examples.py full")
        sys.exit(1)

    name = sys.argv[1]
    desc, fn = examples[name]
    print(f"{'='*60}")
    print(f"  Example: {desc}")
    print(f"{'='*60}\n")
    fn()
