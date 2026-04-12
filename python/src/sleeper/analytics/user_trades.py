"""User-level trade history analytics.

Evaluates every trade a user has made using marketplace-derived values,
producing best/worst trade lists, net value gained, and win rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sleeper.enrichment.marketplace import (
    MarketplaceValue,
    PickAsset,
    decompose_trade,
)
from sleeper.types.league import Roster
from sleeper.types.transaction import Transaction


@dataclass
class TradeSideEvaluation:
    """Value assessment of one side of a trade."""

    roster_id: int
    owner_id: Optional[str] = None
    display_name: Optional[str] = None
    players: list[str] = field(default_factory=list)
    player_names: list[str] = field(default_factory=list)
    picks: list[PickAsset] = field(default_factory=list)
    total_value: float = 0.0


@dataclass
class EvaluatedTrade:
    """A fully evaluated trade with value differential."""

    transaction_id: str
    league_id: str
    league_name: Optional[str] = None
    season: str = ""
    week: Optional[int] = None
    user_side: TradeSideEvaluation = field(default_factory=TradeSideEvaluation)
    other_side: TradeSideEvaluation = field(default_factory=TradeSideEvaluation)
    value_gained: float = 0.0  # positive = user won the trade


@dataclass
class UserTradeReport:
    """Complete trade history report for a single user."""

    user_id: str
    display_name: Optional[str] = None
    total_trades: int = 0
    net_value: float = 0.0
    best_trades: list[EvaluatedTrade] = field(default_factory=list)
    worst_trades: list[EvaluatedTrade] = field(default_factory=list)
    all_trades: list[EvaluatedTrade] = field(default_factory=list)
    avg_value_per_trade: float = 0.0
    win_rate: float = 0.0  # fraction of trades "won"


def evaluate_user_trades(
    user_id: str,
    user_roster_id: int,
    transactions: list[Transaction],
    league_id: str,
    league_name: str,
    season: str,
    marketplace_values: dict[str, MarketplaceValue],
    roster_to_owner: dict[int, str] | None = None,
    owner_to_name: dict[str, str] | None = None,
    rosters: list[Roster] | None = None,
) -> list[EvaluatedTrade]:
    """Evaluate all trades involving a specific user in a single league-season.

    Args:
        user_id: The Sleeper user_id.
        user_roster_id: The user's roster_id in this league.
        transactions: All transactions from this league-season.
        league_id: The league identifier.
        league_name: Display name of the league.
        season: Season string.
        marketplace_values: Pre-computed marketplace values from
            :func:`~sleeper.enrichment.marketplace.build_marketplace_values`.
        roster_to_owner: ``roster_id -> owner_id`` mapping.
        owner_to_name: ``owner_id -> display_name`` mapping.
        rosters: Roster list for pick position estimation.

    Returns:
        List of EvaluatedTrade sorted by value_gained descending.
    """
    if roster_to_owner is None:
        roster_to_owner = {}
    if owner_to_name is None:
        owner_to_name = {}

    evaluated: list[EvaluatedTrade] = []

    for tx in transactions:
        if tx.type != "trade":
            continue
        if not tx.roster_ids or user_roster_id not in tx.roster_ids:
            continue

        # Determine which assets went to which side
        user_received: list[str] = []
        other_received: list[str] = []
        user_picks: list[PickAsset] = []
        other_picks: list[PickAsset] = []
        other_roster_id: int | None = None

        for rid in tx.roster_ids:
            if rid != user_roster_id:
                other_roster_id = rid
                break

        if tx.adds:
            for pid, rid in tx.adds.items():
                if rid == user_roster_id:
                    user_received.append(pid)
                else:
                    other_received.append(pid)

        for pick in tx.draft_picks:
            asset = PickAsset(
                season=pick.season,
                round=pick.round,
                original_roster_id=pick.roster_id,
                estimated_position=_estimate_pick_pos(pick, rosters),
            )
            if pick.owner_id == user_roster_id:
                user_picks.append(asset)
            else:
                other_picks.append(asset)

        user_value = _sum_values(user_received, user_picks, marketplace_values)
        other_value = _sum_values(other_received, other_picks, marketplace_values)

        other_owner = roster_to_owner.get(other_roster_id or 0, "")

        evaluated.append(EvaluatedTrade(
            transaction_id=tx.transaction_id,
            league_id=league_id,
            league_name=league_name,
            season=season,
            week=tx.leg,
            user_side=TradeSideEvaluation(
                roster_id=user_roster_id,
                owner_id=user_id,
                display_name=owner_to_name.get(user_id, user_id),
                players=user_received,
                picks=user_picks,
                total_value=user_value,
            ),
            other_side=TradeSideEvaluation(
                roster_id=other_roster_id or 0,
                owner_id=other_owner,
                display_name=owner_to_name.get(other_owner, str(other_roster_id)),
                players=other_received,
                picks=other_picks,
                total_value=other_value,
            ),
            value_gained=round(user_value - other_value, 1),
        ))

    evaluated.sort(key=lambda e: e.value_gained, reverse=True)
    return evaluated


def build_user_trade_report(
    user_id: str,
    all_evaluated_trades: list[EvaluatedTrade],
    display_name: Optional[str] = None,
    top_n: int = 10,
) -> UserTradeReport:
    """Aggregate evaluated trades into a user-level report.

    Args:
        user_id: The Sleeper user_id.
        all_evaluated_trades: All evaluated trades across leagues/seasons.
        display_name: User's display name.
        top_n: Number of best/worst trades to include.

    Returns:
        Complete UserTradeReport with best/worst trades, net value, win rate.
    """
    if not all_evaluated_trades:
        return UserTradeReport(user_id=user_id, display_name=display_name)

    sorted_trades = sorted(all_evaluated_trades, key=lambda t: t.value_gained)
    wins = sum(1 for t in sorted_trades if t.value_gained > 0)
    total = len(sorted_trades)
    net = sum(t.value_gained for t in sorted_trades)

    return UserTradeReport(
        user_id=user_id,
        display_name=display_name,
        total_trades=total,
        net_value=round(net, 1),
        best_trades=sorted_trades[-top_n:][::-1],
        worst_trades=sorted_trades[:top_n],
        all_trades=sorted_trades[::-1],
        avg_value_per_trade=round(net / total, 1) if total > 0 else 0.0,
        win_rate=round(wins / total, 4) if total > 0 else 0.0,
    )


# ── Helpers ──


def _sum_values(
    player_ids: list[str],
    picks: list[PickAsset],
    marketplace_values: dict[str, MarketplaceValue],
) -> float:
    """Sum marketplace values for a set of players and picks."""
    total = 0.0
    for pid in player_ids:
        mv = marketplace_values.get(pid)
        total += mv.market_value if mv else 50.0
    for pick in picks:
        mv = marketplace_values.get(pick.asset_key)
        total += mv.market_value if mv else 200.0
    return total


def _estimate_pick_pos(pick, rosters: list[Roster] | None) -> str:
    """Estimate pick position from original owner's record."""
    if rosters is None:
        return "mid"
    for r in rosters:
        if r.roster_id == pick.roster_id and r.settings is not None:
            total = r.settings.wins + r.settings.losses + r.settings.ties
            if total == 0:
                return "mid"
            win_pct = r.settings.wins / total
            if win_pct <= 0.35:
                return "early"
            if win_pct >= 0.65:
                return "late"
            return "mid"
    return "mid"
