"""Marketplace-derived trade valuations from observed trade activity.

Builds empirical player values by analyzing real trades across a user's
leagues. Each trade implies that both sides are roughly equal in value,
creating constraints that allow us to derive what players are actually
trading for — not just what KTC or ECR says they *should* trade for.

The key metric is **median acquisition cost**: across every trade where
a player was acquired, what was the total KTC value of the other side?
That is the real price you have to pay to get that player in your leagues.

Algorithm:
    1. Seed values from KTC dynasty values (or ECR as fallback).
    2. Decompose each trade into two sides of assets (players + picks).
    3. For each player, record the total value of the opposite side in
       every trade they appeared in — that's one acquisition cost sample.
    4. The **median** of those samples is the actual market price.
    5. Compare to KTC to find overvalued/undervalued players.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from sleeper.types.league import Roster
from sleeper.types.player import Player
from sleeper.types.transaction import Transaction, TradedPick
from sleeper.enrichment.ktc import KtcPlayer


# ── Types ──


@dataclass
class PickAsset:
    """A draft pick as a tradeable asset."""

    season: str
    round: int
    original_roster_id: int
    estimated_position: str = "mid"  # "early", "mid", "late"

    @property
    def asset_key(self) -> str:
        return f"pick_{self.season}_rd{self.round}_{self.estimated_position}"


@dataclass
class TradeObservation:
    """A decomposed trade with two sides."""

    transaction_id: str
    league_id: str
    season: str
    week: Optional[int] = None
    side_a_players: list[str] = field(default_factory=list)
    side_a_picks: list[PickAsset] = field(default_factory=list)
    side_b_players: list[str] = field(default_factory=list)
    side_b_picks: list[PickAsset] = field(default_factory=list)


@dataclass
class MarketplaceValue:
    """Empirically derived trade value for a player or pick."""

    asset_id: str
    asset_type: str  # "player" or "pick"
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    market_value: float = 0.0
    ktc_value: Optional[float] = None
    median_acquisition_cost: Optional[float] = None
    min_acquisition_cost: Optional[float] = None
    max_acquisition_cost: Optional[float] = None
    observation_count: int = 0
    confidence: float = 0.0  # 0.0-1.0
    trade_partners: list[str] = field(default_factory=list)
    acquisition_costs: list[float] = field(default_factory=list)

    @property
    def ktc_vs_actual(self) -> Optional[float]:
        """Difference: KTC value minus actual acquisition cost.

        Positive = KTC overvalues (costs less to acquire than KTC says).
        Negative = KTC undervalues (costs more to acquire).
        """
        if self.ktc_value is not None and self.median_acquisition_cost is not None:
            return round(self.ktc_value - self.median_acquisition_cost, 1)
        return None

    @property
    def ktc_vs_actual_pct(self) -> Optional[float]:
        """Percentage difference: KTC value vs actual cost.

        +20% = KTC overvalues by 20% (you can get them cheaper).
        -20% = KTC undervalues by 20% (costs more than KTC says).
        """
        if (self.ktc_value is not None
                and self.median_acquisition_cost is not None
                and self.ktc_value > 0):
            return round(
                (self.ktc_value - self.median_acquisition_cost) / self.ktc_value * 100,
                1,
            )
        return None


@dataclass
class PlayerMarketComparison:
    """Side-by-side comparison of KTC value vs actual market cost."""

    sleeper_id: str
    name: str
    position: str
    team: str
    ktc_value: int
    actual_cost: float
    delta: float        # ktc - actual (positive = overvalued by KTC)
    delta_pct: float    # percentage difference
    times_traded: int
    confidence: float
    signal: str         # "buy" (cheaper than KTC), "sell" (pricier), "fair"


# ── Default pick values (KTC-scale, roughly 0-9999) ──

DEFAULT_PICK_VALUES: dict[int, dict[str, float]] = {
    1: {"early": 6500.0, "mid": 4500.0, "late": 3000.0},
    2: {"early": 2500.0, "mid": 1800.0, "late": 1200.0},
    3: {"early": 1000.0, "mid": 700.0, "late": 400.0},
    4: {"early": 300.0, "mid": 200.0, "late": 100.0},
}


# ── Trade decomposition ──


def decompose_trade(
    tx: Transaction,
    league_id: str,
    season: str,
    rosters: list[Roster] | None = None,
) -> TradeObservation | None:
    """Decompose a Transaction into a TradeObservation with two sides.

    Side A = assets received by ``roster_ids[0]``.
    Side B = assets received by ``roster_ids[1]``.
    """
    if tx.type != "trade" or not tx.roster_ids or len(tx.roster_ids) < 2:
        return None

    rid_a, rid_b = tx.roster_ids[0], tx.roster_ids[1]

    side_a_players: list[str] = []
    side_b_players: list[str] = []
    if tx.adds:
        for player_id, receiving_roster in tx.adds.items():
            if receiving_roster == rid_a:
                side_a_players.append(player_id)
            elif receiving_roster == rid_b:
                side_b_players.append(player_id)

    side_a_picks: list[PickAsset] = []
    side_b_picks: list[PickAsset] = []
    for pick in tx.draft_picks:
        asset = PickAsset(
            season=pick.season,
            round=pick.round,
            original_roster_id=pick.roster_id,
            estimated_position=_estimate_pick_position(pick, rosters),
        )
        if pick.owner_id == rid_a:
            side_a_picks.append(asset)
        elif pick.owner_id == rid_b:
            side_b_picks.append(asset)

    return TradeObservation(
        transaction_id=tx.transaction_id,
        league_id=league_id,
        season=season,
        week=tx.leg,
        side_a_players=side_a_players,
        side_a_picks=side_a_picks,
        side_b_players=side_b_players,
        side_b_picks=side_b_picks,
    )


def _estimate_pick_position(
    pick: TradedPick,
    rosters: list[Roster] | None,
) -> str:
    """Estimate early/mid/late based on the original owner's record."""
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


# ── Marketplace value engine ──


def build_marketplace_values(
    observations: list[TradeObservation],
    seed_values: dict[str, float] | None = None,
    iterations: int = 3,
) -> dict[str, MarketplaceValue]:
    """Build marketplace-derived values from observed trade activity.

    For every asset that was traded, this function computes:
    - **market_value**: blended KTC-seed + marketplace-implied value.
    - **median_acquisition_cost**: the median total value of the other
      side across all trades where this asset was acquired. This is the
      real-world price to get this player.

    Args:
        observations: Decomposed trade observations.
        seed_values: Dict of asset_id to seed value (typically KTC values).
        iterations: Number of value propagation passes.

    Returns:
        Dict of asset_id -> MarketplaceValue.
    """
    if seed_values is None:
        seed_values = {}

    # Collect all unique assets
    all_assets: set[str] = set()
    for obs in observations:
        all_assets.update(obs.side_a_players)
        all_assets.update(obs.side_b_players)
        for p in obs.side_a_picks:
            all_assets.add(p.asset_key)
        for p in obs.side_b_picks:
            all_assets.add(p.asset_key)

    # Seed initial values
    current: dict[str, float] = {}
    for asset in all_assets:
        if asset in seed_values:
            current[asset] = float(seed_values[asset])
        elif asset.startswith("pick_"):
            current[asset] = _default_pick_value(asset)
        else:
            current[asset] = 100.0

    # ── Pass 1: Compute raw acquisition costs ──
    #
    # For each asset, the "acquisition cost" is the value it took to
    # get that specific player. How we isolate it depends on the trade
    # structure:
    #
    #   1-for-N:  Player X received for A + B + C.
    #             X's cost = value(A) + value(B) + value(C).
    #             Clean signal, full confidence.
    #
    #   N-for-N:  Player X + Y received for A + B + C.
    #             We isolate X by subtracting the other assets on the
    #             SAME side: X's cost = other_side_total - value(Y).
    #             If that goes negative (X is the "throw-in"), clamp to 0.
    #
    # This is better than proportional attribution because it doesn't
    # assume the KTC ratios are correct — it lets the real trade data
    # reveal who the centerpiece is.
    raw_costs: dict[str, list[float]] = defaultdict(list)
    trade_partner_map: dict[str, set[str]] = defaultdict(set)

    def _value_of(asset_id: str) -> float:
        return seed_values.get(asset_id, current.get(asset_id, 100.0))

    for obs in observations:
        a_assets = obs.side_a_players + [p.asset_key for p in obs.side_a_picks]
        b_assets = obs.side_b_players + [p.asset_key for p in obs.side_b_picks]

        total_a = sum(_value_of(a) for a in a_assets)
        total_b = sum(_value_of(b) for b in b_assets)

        # Record trade partnerships
        for a in a_assets:
            trade_partner_map[a].update(b_assets)
        for b in b_assets:
            trade_partner_map[b].update(a_assets)

        # Attribute acquisition costs for side A assets
        _attribute_costs(a_assets, total_b, _value_of, raw_costs)
        # Attribute acquisition costs for side B assets
        _attribute_costs(b_assets, total_a, _value_of, raw_costs)

    # ── Pass 2: Iterative propagation for blended market_value ──
    observation_counts: dict[str, int] = defaultdict(int)

    for _iteration in range(iterations):
        implied: dict[str, list[float]] = defaultdict(list)

        for obs in observations:
            a_assets = obs.side_a_players + [p.asset_key for p in obs.side_a_picks]
            b_assets = obs.side_b_players + [p.asset_key for p in obs.side_b_picks]

            total_a = sum(current.get(a, 100.0) for a in a_assets)
            total_b = sum(current.get(a, 100.0) for a in b_assets)

            if total_a == 0 and total_b == 0:
                continue

            if a_assets and total_a > 0:
                for asset in a_assets:
                    share = current.get(asset, 100.0) / total_a
                    implied[asset].append(total_b * share)

            if b_assets and total_b > 0:
                for asset in b_assets:
                    share = current.get(asset, 100.0) / total_b
                    implied[asset].append(total_a * share)

        for asset, implied_values in implied.items():
            seed = seed_values.get(asset, current.get(asset, 100.0))
            avg_implied = sum(implied_values) / len(implied_values)
            count = len(implied_values)
            observation_counts[asset] = count

            market_weight = min(0.7, 0.3 + count * 0.08)
            current[asset] = seed * (1 - market_weight) + avg_implied * market_weight

    # ── Build final results ──
    result: dict[str, MarketplaceValue] = {}
    for asset in all_assets:
        obs_count = observation_counts.get(asset, 0)
        costs = raw_costs.get(asset, [])
        median_cost = statistics.median(costs) if costs else None
        min_cost = min(costs) if costs else None
        max_cost = max(costs) if costs else None

        result[asset] = MarketplaceValue(
            asset_id=asset,
            asset_type="pick" if asset.startswith("pick_") else "player",
            market_value=round(current.get(asset, 0.0), 1),
            ktc_value=seed_values.get(asset),
            median_acquisition_cost=round(median_cost, 1) if median_cost is not None else None,
            min_acquisition_cost=round(min_cost, 1) if min_cost is not None else None,
            max_acquisition_cost=round(max_cost, 1) if max_cost is not None else None,
            observation_count=obs_count,
            confidence=round(min(1.0, obs_count / 10.0), 2),
            trade_partners=sorted(trade_partner_map.get(asset, set()))[:10],
            acquisition_costs=[round(c, 1) for c in costs],
        )

    return result


def get_marketplace_values(
    trade_tuples: list[tuple[Transaction, str, str, list[Roster] | None]],
    seed_values: dict[str, float] | None = None,
    iterations: int = 3,
) -> dict[str, MarketplaceValue]:
    """Convenience: decompose trades and build marketplace values."""
    observations = []
    for tx, league_id, season, rosters in trade_tuples:
        obs = decompose_trade(tx, league_id, season, rosters)
        if obs is not None:
            observations.append(obs)

    return build_marketplace_values(observations, seed_values, iterations)


# ── KTC vs Actual comparison ──


def compare_ktc_vs_actual(
    marketplace_values: dict[str, MarketplaceValue],
    ktc_matched: dict[str, KtcPlayer],
    sleeper_players: dict[str, Player],
    scoring_type: str = "sf",
    min_trades: int = 1,
    delta_threshold_pct: float = 10.0,
) -> list[PlayerMarketComparison]:
    """Compare KTC values to actual acquisition costs across all leagues.

    For every player that was traded at least ``min_trades`` times, shows
    what KTC says they are worth vs the **median value required to
    actually acquire them** in real trades.

    Args:
        marketplace_values: Output from :func:`build_marketplace_values`.
        ktc_matched: Dict of sleeper_id -> KtcPlayer from
            :func:`~sleeper.enrichment.ktc.match_ktc_to_sleeper`.
        sleeper_players: Sleeper player database.
        scoring_type: ``"sf"`` or ``"1qb"`` — which KTC column to use.
        min_trades: Minimum number of trade observations to include.
        delta_threshold_pct: % difference to flag as buy/sell (default 10%).

    Returns:
        List of PlayerMarketComparison sorted by delta_pct descending
        (most overvalued by KTC first).
    """
    comparisons: list[PlayerMarketComparison] = []

    for asset_id, mv in marketplace_values.items():
        if mv.asset_type != "player":
            continue
        if mv.observation_count < min_trades:
            continue
        if mv.median_acquisition_cost is None:
            continue

        kp = ktc_matched.get(asset_id)
        if kp is None:
            continue

        sp = sleeper_players.get(asset_id)
        ktc_val = kp.value_sf if scoring_type == "sf" else kp.value_1qb
        if ktc_val <= 0:
            continue

        actual = mv.median_acquisition_cost
        delta = ktc_val - actual
        delta_pct = (delta / ktc_val) * 100

        if delta_pct > delta_threshold_pct:
            signal = "buy"   # KTC overvalues → you can get them cheaper
        elif delta_pct < -delta_threshold_pct:
            signal = "sell"  # KTC undervalues → they cost more than KTC says
        else:
            signal = "fair"

        name = f"{sp.first_name} {sp.last_name}" if sp else kp.player_name
        position = sp.position if sp else kp.position
        team = sp.team if sp else kp.team

        comparisons.append(PlayerMarketComparison(
            sleeper_id=asset_id,
            name=name,
            position=position or "",
            team=team or "",
            ktc_value=ktc_val,
            actual_cost=round(actual, 1),
            delta=round(delta, 1),
            delta_pct=round(delta_pct, 1),
            times_traded=mv.observation_count,
            confidence=mv.confidence,
            signal=signal,
        ))

    comparisons.sort(key=lambda c: c.delta_pct, reverse=True)
    return comparisons


# ── Helpers ──


def _attribute_costs(
    this_side: list[str],
    other_side_total: float,
    value_fn: callable,
    raw_costs: dict[str, list[float]],
) -> None:
    """Attribute acquisition costs to each asset on one side of a trade.

    Strategy:
    - **1-for-N**: The single asset's cost = entire other side. Clean signal.
    - **N-for-N**: For each asset, subtract the KTC value of the OTHER
      assets on the SAME side from the other side total. This isolates
      what was "really" paid for this specific asset.
      Example: You trade FOR (Chase + bench player). Other side = 10000.
      Chase's isolated cost = 10000 - value(bench player).
      Bench player's isolated cost = 10000 - value(Chase).
      If isolated cost < 0, this asset was a throw-in (cost = 0).
    """
    if not this_side:
        return

    if len(this_side) == 1:
        # Clean 1-for-N: full other side is the cost
        raw_costs[this_side[0]].append(other_side_total)
        return

    # N-for-N: isolate each asset by subtracting same-side companions
    for asset in this_side:
        companions_value = sum(
            value_fn(a) for a in this_side if a != asset
        )
        isolated_cost = max(0.0, other_side_total - companions_value)
        raw_costs[asset].append(isolated_cost)


def _default_pick_value(asset_key: str) -> float:
    """Parse a pick asset key and return its default value."""
    parts = asset_key.split("_")
    if len(parts) >= 4:
        try:
            rd = int(parts[2].replace("rd", ""))
            pos = parts[3]
            return DEFAULT_PICK_VALUES.get(rd, {}).get(pos, 200.0)
        except (ValueError, IndexError):
            pass
    return 200.0
