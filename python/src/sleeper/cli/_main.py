"""Argparse setup + dispatch.

Wires every command in the CLI: assembles the subparser tree and routes
parsed args to the right handler. Command implementations live in:
    - values.py    (read-only KTC + valuation commands)
    - trades.py    (trade-check, suggest-trades, find-trades)
    - send_trade.py (the only write op — needs SLEEPER_TOKEN)
    - analysis.py  (picks, gm-mode, proposed-trades)

Agent-only commands (whoami/inbox/lineup/...) are loaded lazily from
sleeper.cli_agent if that module is available — it's not required.
"""
from __future__ import annotations

import argparse
import sys

from sleeper.cli.analysis import (
    cmd_gm_mode,
    cmd_picks,
    cmd_proposed_trades,
)
from sleeper.cli.send_trade import cmd_send_trade
from sleeper.cli.trades import (
    cmd_find_trades,
    cmd_suggest_trades,
    cmd_trade_check,
)
from sleeper.cli.values import (
    cmd_buy_sell,
    cmd_ktc_trend,
    cmd_league_values,
    cmd_market_value,
    cmd_pe_ratio,
    cmd_roster_rank,
    cmd_trending,
)


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

    # proposed-trades
    pt = subparsers.add_parser("proposed-trades",
                               help="List every trade in the league (any status) with KTC valuation")
    pt.add_argument("username", help="Sleeper username")
    pt.add_argument("--league", help="League name filter")
    pt.add_argument("--status", nargs="+", default=None,
                    help="Status filter (proposed, complete, rejected, cancelled, vetoed). "
                         "Default: all statuses.")
    pt.add_argument("--limit", type=int, default=200,
                    help="Max trades to fetch (default: 200)")
    pt.add_argument("--user", nargs="+", default=None,
                    help="Only show trades involving any of these usernames "
                         "(case-insensitive substring match against display names)")

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

    # Agent-friendly commands (whoami/status/context/inbox/lineup/waivers/...).
    # The cli_agent module is optional — when it's not present (e.g. in the
    # base public install), the public CLI still works without those commands.
    try:
        from sleeper.cli_agent import add_subparsers as _add_agent_subparsers
        agent_handlers = _add_agent_subparsers(subparsers)
    except ImportError:
        agent_handlers = {}

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
    elif args.command == "proposed-trades":
        cmd_proposed_trades(args)
    elif args.command == "send-trade":
        cmd_send_trade(args)
    elif args.command == "gm-mode":
        cmd_gm_mode(args)
    elif args.command in agent_handlers:
        agent_handlers[args.command](args)
    else:
        parser.print_help()
        sys.exit(1)
