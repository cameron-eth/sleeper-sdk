"""Trade value estimation and buy-low/sell-high signals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sleeper.enrichment.id_bridge import PlayerIdBridge
from sleeper.enrichment.rankings import get_player_rankings, PlayerRanking
from sleeper.enrichment.stats import get_season_stats, SeasonStatLine
from sleeper.types.league import Roster


@dataclass
class TradeValue:
    sleeper_id: str
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    roster_id: int = 0
    ecr: Optional[float] = None
    positional_rank: Optional[int] = None
    ppg_ppr: float = 0.0
    value_tier: str = ""  # "elite", "starter", "flex", "bench", "droppable"


@dataclass
class BuySellSignal:
    sleeper_id: str
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    roster_id: int = 0
    signal: str = ""  # "buy_low" or "sell_high"
    ecr: Optional[float] = None
    positional_rank: Optional[int] = None
    ppg_ppr: float = 0.0
    rank_vs_performance_gap: float = 0.0  # positive = ranked higher than performing


def _assign_tier(ecr: Optional[float], position: Optional[str]) -> str:
    if ecr is None:
        return "unranked"

    # Tier thresholds based on overall ECR
    if ecr <= 24:
        return "elite"
    elif ecr <= 72:
        return "starter"
    elif ecr <= 120:
        return "flex"
    elif ecr <= 200:
        return "bench"
    else:
        return "droppable"


def get_trade_values(
    rosters: list[Roster],
    bridge: Optional[PlayerIdBridge] = None,
    ranking_type: Optional[str] = None,
) -> list[TradeValue]:
    """Estimate trade value for every rostered player using rankings + recent performance.

    Combines FantasyPros ECR with actual PPG for a more complete picture.
    """
    if bridge is None:
        bridge = PlayerIdBridge()

    rankings = get_player_rankings(bridge=bridge, ranking_type=ranking_type)
    rank_map: dict[str, PlayerRanking] = {r.sleeper_id: r for r in rankings}

    # Try to get most recent season stats for PPG context
    try:
        import nflreadpy as nfl_mod
        current = nfl_mod.get_current_season()
        stats = get_season_stats([current], bridge=bridge)
    except Exception:
        stats = {}

    result = []
    for roster in rosters:
        for pid in roster.players:
            ranking = rank_map.get(pid)
            ids = bridge.from_sleeper(pid)

            season_stats = stats.get(pid, {})
            latest_stat: Optional[SeasonStatLine] = None
            if season_stats:
                latest_season = max(season_stats.keys())
                latest_stat = season_stats[latest_season]

            result.append(TradeValue(
                sleeper_id=pid,
                name=ranking.name if ranking else (ids.name if ids else None),
                position=ranking.position if ranking else (ids.position if ids else None),
                team=ranking.team if ranking else (ids.team if ids else None),
                roster_id=roster.roster_id,
                ecr=ranking.ecr if ranking else None,
                positional_rank=ranking.positional_rank if ranking else None,
                ppg_ppr=latest_stat.ppg_ppr if latest_stat else 0.0,
                value_tier=_assign_tier(ranking.ecr if ranking else None, ranking.position if ranking else None),
            ))

    result.sort(key=lambda v: v.ecr if v.ecr is not None else 9999)
    return result


def get_buy_low_sell_high(
    rosters: list[Roster],
    seasons: list[int],
    bridge: Optional[PlayerIdBridge] = None,
    min_games: int = 4,
    gap_threshold: float = 0.15,
) -> list[BuySellSignal]:
    """Identify buy-low and sell-high candidates.

    Compares a player's ECR ranking against their actual fantasy production.
    Players ranked much higher than they've performed = sell high.
    Players ranked much lower than they've performed = buy low.

    Args:
        rosters: Rosters to analyze.
        seasons: Seasons to pull stats from (most recent is used).
        bridge: PlayerIdBridge instance.
        min_games: Minimum games played to be included.
        gap_threshold: Minimum gap (as fraction of roster size) to flag.
    """
    if bridge is None:
        bridge = PlayerIdBridge()

    rankings = get_player_rankings(bridge=bridge)
    rank_map: dict[str, PlayerRanking] = {r.sleeper_id: r for r in rankings}

    stats = get_season_stats(seasons, bridge=bridge)

    # Build PPG-based ranking for comparison
    ppg_list: list[tuple[str, float]] = []
    for pid, season_stats in stats.items():
        if not season_stats:
            continue
        latest = season_stats[max(season_stats.keys())]
        if latest.games >= min_games:
            ppg_list.append((pid, latest.ppg_ppr))

    ppg_list.sort(key=lambda x: x[1], reverse=True)
    ppg_rank_map = {pid: i + 1 for i, (pid, _) in enumerate(ppg_list)}

    total_ranked = max(len(ppg_list), 1)
    signals = []

    for roster in rosters:
        for pid in roster.players:
            ranking = rank_map.get(pid)
            ppg_rank = ppg_rank_map.get(pid)

            if ranking is None or ranking.ecr is None or ppg_rank is None:
                continue

            ids = bridge.from_sleeper(pid)
            season_stats = stats.get(pid, {})
            latest_stat = season_stats[max(season_stats.keys())] if season_stats else None

            if latest_stat is None or latest_stat.games < min_games:
                continue

            # Normalized gap: positive means ranked better than performing
            ecr_normalized = ranking.ecr / total_ranked
            ppg_normalized = ppg_rank / total_ranked
            gap = ppg_normalized - ecr_normalized  # positive = sell high, negative = buy low

            if abs(gap) < gap_threshold:
                continue

            signal = "sell_high" if gap > 0 else "buy_low"

            signals.append(BuySellSignal(
                sleeper_id=pid,
                name=ranking.name or (ids.name if ids else None),
                position=ranking.position or (ids.position if ids else None),
                team=ranking.team or (ids.team if ids else None),
                roster_id=roster.roster_id,
                signal=signal,
                ecr=ranking.ecr,
                positional_rank=ranking.positional_rank,
                ppg_ppr=latest_stat.ppg_ppr,
                rank_vs_performance_gap=round(gap, 4),
            ))

    # Sort: biggest buy-low opportunities first, then sell-high
    signals.sort(key=lambda s: s.rank_vs_performance_gap)
    return signals
