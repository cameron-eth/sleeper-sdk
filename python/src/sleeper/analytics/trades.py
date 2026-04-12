from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sleeper.types.transaction import Transaction


@dataclass
class TradePartnerFrequency:
    roster_id_1: int
    roster_id_2: int
    trade_count: int = 0


@dataclass
class MostTradedPlayer:
    player_id: str
    times_traded: int = 0


@dataclass
class WaiverActivitySummary:
    total_waiver_moves: int = 0
    total_free_agent_moves: int = 0
    total_faab_spent: int = 0
    most_active_roster_id: Optional[int] = None
    most_active_moves: int = 0


def get_transaction_summary(
    transactions: list[Transaction],
) -> dict[str, int]:
    """Count transactions by type."""
    counts: dict[str, int] = {}
    for tx in transactions:
        counts[tx.type] = counts.get(tx.type, 0) + 1
    return counts


def get_most_traded_players(
    transactions: list[Transaction],
    limit: int = 20,
) -> list[MostTradedPlayer]:
    """Players involved in the most trades."""
    counts: dict[str, int] = {}
    for tx in transactions:
        if tx.type != "trade":
            continue
        seen: set[str] = set()
        if tx.adds:
            seen.update(tx.adds.keys())
        if tx.drops:
            seen.update(tx.drops.keys())
        for pid in seen:
            counts[pid] = counts.get(pid, 0) + 1

    result = [MostTradedPlayer(player_id=pid, times_traded=c) for pid, c in counts.items()]
    result.sort(key=lambda p: p.times_traded, reverse=True)
    return result[:limit]


def get_trade_partners(
    transactions: list[Transaction],
) -> list[TradePartnerFrequency]:
    """Which pairs of teams trade together most often."""
    pairs: dict[tuple[int, int], int] = {}
    for tx in transactions:
        if tx.type != "trade" or not tx.roster_ids or len(tx.roster_ids) < 2:
            continue
        rids = sorted(tx.roster_ids)
        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                key = (rids[i], rids[j])
                pairs[key] = pairs.get(key, 0) + 1

    result = [
        TradePartnerFrequency(roster_id_1=k[0], roster_id_2=k[1], trade_count=v)
        for k, v in pairs.items()
    ]
    result.sort(key=lambda p: p.trade_count, reverse=True)
    return result


def get_waiver_activity(
    transactions: list[Transaction],
) -> WaiverActivitySummary:
    """Summarize waiver and free agent activity."""
    summary = WaiverActivitySummary()
    roster_moves: dict[int, int] = {}

    for tx in transactions:
        if tx.type == "waiver":
            summary.total_waiver_moves += 1
            for rid in (tx.roster_ids or []):
                roster_moves[rid] = roster_moves.get(rid, 0) + 1
            if tx.settings and "waiver_bid" in tx.settings:
                summary.total_faab_spent += tx.settings["waiver_bid"]
        elif tx.type == "free_agent":
            summary.total_free_agent_moves += 1
            for rid in (tx.roster_ids or []):
                roster_moves[rid] = roster_moves.get(rid, 0) + 1

    if roster_moves:
        most_active = max(roster_moves, key=roster_moves.get)  # type: ignore[arg-type]
        summary.most_active_roster_id = most_active
        summary.most_active_moves = roster_moves[most_active]

    return summary
