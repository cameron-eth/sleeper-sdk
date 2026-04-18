"""GM Mode: Comprehensive team archetype analysis and strategic recommendations.

Classifies teams into archetypes:
- CONTENDER: Top tier (1-3) in both value and production
- RELOADING: Mid-tier (3-6) in value but trending up with young assets
- REBUILDING: Bottom tier (7-12) actively accumulating picks/young players
- PRETENDER: Mid-tier value (3-6) but bottom-tier production (6-9) — often older rosters

Also identifies:
- Positional balance (strength/depth/production gaps)
- Age curve (average starter age, young/old asset breakdown)
- Pick capital relative to league
- Strategic trade targets based on archetype
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PositionBreakdown:
    """Position-level value and depth analysis."""
    position: str
    starters_value: int  # Top N by position
    bench_value: int     # Rest
    total_value: int
    league_avg_total: int
    rank: int            # 1 = best in league
    strength_score: float  # -1.0 (weakness) to +1.0 (strength)
    depth_score: float     # -1.0 (shallow) to +1.0 (deep)


@dataclass
class TeamArchetype:
    """Overall team classification with supporting metrics."""
    roster_id: int
    owner: str
    archetype: str  # "CONTENDER", "RELOADING", "REBUILDING", "PRETENDER"
    confidence: float  # 0-1, how clearly the team fits this archetype
    reasoning: str

    # Value metrics
    total_ktc_value: int
    pick_capital: int
    value_rank: int

    # Production proxy (wins/points for past seasons, or current season)
    production_rank: Optional[int] = None
    record_str: Optional[str] = None

    # Age & roster construction
    avg_starter_age: Optional[float] = None
    young_asset_pct: Optional[float] = None  # % of value from players < 26

    # Positional breakdown
    positions: list[PositionBreakdown] = field(default_factory=list)

    # Strategic recommendations
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    trade_strategy: str = ""  # "Buy win-now", "Sell veterans", etc.


@dataclass
class GMReport:
    """Full GM Mode report for a single team."""
    archetype: TeamArchetype
    league_context: dict  # {name, size, format, scoring, season}
    roster_summary: dict  # {qb_count, rb_count, wr_count, te_count, total_players}
    top_assets: list[dict]  # Top 5 players by KTC
    liabilities: list[dict]  # Declining or over-aged players
    targets: list[dict]  # Recommended trade targets based on archetype


def _safe_age(player) -> Optional[float]:
    """Get player age if available."""
    if not player:
        return None
    age = getattr(player, "age", None)
    if age is None:
        return None
    try:
        return float(age)
    except (ValueError, TypeError):
        return None


def _position_rank(
    my_roster,
    all_rosters,
    sleeper_players,
    sleeper_to_ktc,
    position: str,
    starters: int = 2,
    fmt: str = "sf",
) -> PositionBreakdown:
    """Calculate positional breakdown for a single position."""

    def _get_ktc_val(pid):
        ktc_p = sleeper_to_ktc.get(pid)
        if not ktc_p:
            return 0
        val = ktc_p.superflex.value if fmt == "sf" else ktc_p.one_qb.value
        return val or 0

    # Compute for all rosters
    roster_totals = []
    for r in all_rosters:
        starters_v = 0
        bench_v = 0
        pos_players = []
        for pid in (r.players or []):
            p = sleeper_players.get(pid)
            if p and p.position == position:
                val = _get_ktc_val(pid)
                pos_players.append(val)
        pos_players.sort(reverse=True)
        for i, val in enumerate(pos_players):
            if i < starters:
                starters_v += val
            else:
                bench_v += val
        roster_totals.append((r.roster_id, starters_v, bench_v, starters_v + bench_v))

    # Sort by total value desc
    roster_totals.sort(key=lambda x: -x[3])

    # Find my team
    my_entry = next((x for x in roster_totals if x[0] == my_roster.roster_id), None)
    if not my_entry:
        return PositionBreakdown(
            position=position, starters_value=0, bench_value=0, total_value=0,
            league_avg_total=0, rank=len(all_rosters), strength_score=0, depth_score=0
        )

    my_rank = next(i for i, x in enumerate(roster_totals) if x[0] == my_roster.roster_id) + 1
    league_avg = sum(x[3] for x in roster_totals) / len(roster_totals) if roster_totals else 0

    # Normalize score: rank-based, -1 (worst) to +1 (best)
    n = len(roster_totals)
    strength_score = 1.0 - (2.0 * (my_rank - 1) / max(n - 1, 1))

    # Depth score based on bench value vs league
    avg_bench = sum(x[2] for x in roster_totals) / n if n else 0
    depth_score = 0.0
    if avg_bench > 0:
        depth_score = min(max((my_entry[2] - avg_bench) / max(avg_bench, 1), -1.0), 1.0)

    return PositionBreakdown(
        position=position,
        starters_value=my_entry[1],
        bench_value=my_entry[2],
        total_value=my_entry[3],
        league_avg_total=int(league_avg),
        rank=my_rank,
        strength_score=strength_score,
        depth_score=depth_score,
    )


def _classify_archetype(
    value_rank: int,
    production_rank: Optional[int],
    avg_age: Optional[float],
    young_asset_pct: Optional[float],
    league_size: int,
) -> tuple[str, float, str]:
    """Classify team into CONTENDER / RELOADING / REBUILDING / PRETENDER.

    Returns (archetype, confidence, reasoning).
    """
    # Define tier boundaries based on league size
    top_tier_max = max(3, league_size // 4)  # 1-3 for 12-team
    mid_tier_max = max(6, league_size // 2)  # 4-6 for 12-team
    low_tier_max = league_size  # 7-12 for 12-team

    v = value_rank  # 1 = best
    p = production_rank

    # Default older = 27+, young = <26
    is_old = avg_age is not None and avg_age >= 27.0
    is_young = avg_age is not None and avg_age < 25.5
    young_heavy = young_asset_pct is not None and young_asset_pct >= 0.55

    # CONTENDER: Top-tier in value AND (good production OR older veterans with proven production)
    if v <= top_tier_max and (p is None or p <= mid_tier_max):
        return (
            "CONTENDER",
            0.9,
            f"Top {top_tier_max} in KTC value (rank {v})"
            + (f" with production rank {p}" if p else "")
            + ". Ready to compete for a championship."
        )

    # PRETENDER: Mid-value tier with poor production AND older roster
    if top_tier_max < v <= mid_tier_max and p is not None and p > mid_tier_max and is_old:
        return (
            "PRETENDER",
            0.85,
            f"Rank {v} in value but only rank {p} in production with aging roster "
            f"(avg age {avg_age:.1f}). Dangerous position — value may erode fast."
        )

    # REBUILDING: Low-tier in value OR young-heavy roster in bottom half
    if v > mid_tier_max or (v > top_tier_max and young_heavy):
        return (
            "REBUILDING",
            0.85,
            f"Rank {v} in value"
            + (f", {young_asset_pct*100:.0f}% of value in players under 26" if young_heavy else "")
            + ". Accumulating future assets for a multi-year window."
        )

    # RELOADING: Mid-tier value but young assets and/or pick capital (between CONTENDER & REBUILD)
    if top_tier_max < v <= mid_tier_max:
        return (
            "RELOADING",
            0.75,
            f"Mid-tier (rank {v}) with "
            + (f"young core (avg age {avg_age:.1f})" if is_young else "balanced roster")
            + ". Retooling for a contention window in 1-2 years."
        )

    # Fallback
    return (
        "RELOADING",
        0.5,
        f"Rank {v} in value — unclear archetype. Consider targeted moves based on positional needs."
    )


def _trade_strategy_for_archetype(archetype: str, positions: list[PositionBreakdown]) -> str:
    """Generate a 1-line trade strategy based on archetype and positional gaps."""
    weakest = min(positions, key=lambda p: p.strength_score) if positions else None
    strongest = max(positions, key=lambda p: p.strength_score) if positions else None

    if archetype == "CONTENDER":
        return (
            f"Buy-Win-Now: Trade picks + young depth for proven {weakest.position} starters. "
            f"Push for a title while your window is open."
            if weakest else "Buy-Win-Now: Consolidate depth into elite starters."
        )
    elif archetype == "PRETENDER":
        return (
            f"Sell-High URGENT: Your {strongest.position} surplus has value but aging pieces "
            f"will fall off. Sell vets for youth + picks before production drops."
            if strongest else "Sell-High: Liquidate aging pieces before they depreciate."
        )
    elif archetype == "REBUILDING":
        return (
            f"Accumulate Youth: Target {weakest.position} upside (rookies, breakouts) + picks. "
            f"Sell any over-25 veterans for future value."
            if weakest else "Accumulate picks and young breakouts."
        )
    elif archetype == "RELOADING":
        return (
            f"Selective Upgrades: Strengthen {weakest.position} while keeping young core. "
            f"You're 1-2 moves from contention — be patient."
            if weakest else "Selective upgrades — 1-2 moves from contention."
        )
    return "Evaluate positional needs and target specific upgrades."


def generate_gm_report(
    my_roster,
    all_rosters,
    sleeper_players,
    sleeper_to_ktc,
    user_display: dict[str, str],
    production_rank: Optional[int] = None,
    record_str: Optional[str] = None,
    pick_capital: int = 0,
    fmt: str = "sf",
) -> GMReport:
    """Main entry point for GM Mode analysis.

    Args:
        my_roster: Sleeper Roster object for the target team
        all_rosters: All rosters in the league
        sleeper_players: Dict of sleeper_id -> Player
        sleeper_to_ktc: Dict of sleeper_id -> KTCPlayer
        user_display: Dict of user_id -> display_name
        production_rank: Optional rank 1-N by actual scoring (wins/points)
        record_str: Optional "W-L" string
        pick_capital: Total KTC value of owned picks
        fmt: "sf" or "1qb"
    """

    def _get_ktc_val(pid):
        ktc_p = sleeper_to_ktc.get(pid)
        if not ktc_p:
            return 0
        val = ktc_p.superflex.value if fmt == "sf" else ktc_p.one_qb.value
        return val or 0

    # Compute total value for all rosters (for value_rank)
    roster_values = []
    for r in all_rosters:
        total = sum(_get_ktc_val(pid) for pid in (r.players or []))
        roster_values.append((r.roster_id, total))
    roster_values.sort(key=lambda x: -x[1])
    value_rank = next(i for i, x in enumerate(roster_values) if x[0] == my_roster.roster_id) + 1
    my_total_value = next(x[1] for x in roster_values if x[0] == my_roster.roster_id)

    # Positional breakdowns (QB, RB, WR, TE)
    positions = []
    for pos, starters in [("QB", 2), ("RB", 2), ("WR", 3), ("TE", 1)]:
        pb = _position_rank(my_roster, all_rosters, sleeper_players, sleeper_to_ktc, pos, starters, fmt)
        positions.append(pb)

    # Age & young asset analysis
    ages = []
    young_value = 0
    total_player_value = 0
    for pid in (my_roster.players or []):
        p = sleeper_players.get(pid)
        age = _safe_age(p)
        val = _get_ktc_val(pid)
        total_player_value += val
        if age is not None:
            ages.append(age)
            if age < 26:
                young_value += val

    avg_age = sum(ages) / len(ages) if ages else None
    young_pct = young_value / total_player_value if total_player_value > 0 else None

    # Get avg starter age (top QB + top 2 RB + top 3 WR + top TE by value)
    starter_ages = []
    by_pos_players = {"QB": [], "RB": [], "WR": [], "TE": []}
    for pid in (my_roster.players or []):
        p = sleeper_players.get(pid)
        if p and p.position in by_pos_players:
            by_pos_players[p.position].append((pid, _get_ktc_val(pid), _safe_age(p)))

    starter_config = {"QB": 2, "RB": 2, "WR": 3, "TE": 1}
    for pos, count in starter_config.items():
        sorted_players = sorted(by_pos_players[pos], key=lambda x: -x[1])[:count]
        for _, _, age in sorted_players:
            if age is not None:
                starter_ages.append(age)
    avg_starter_age = sum(starter_ages) / len(starter_ages) if starter_ages else avg_age

    # Classify archetype
    archetype_label, confidence, reasoning = _classify_archetype(
        value_rank=value_rank,
        production_rank=production_rank,
        avg_age=avg_starter_age,
        young_asset_pct=young_pct,
        league_size=len(all_rosters),
    )

    # Strengths / weaknesses
    strengths = [f"{p.position} (rank {p.rank})" for p in positions if p.strength_score >= 0.4]
    weaknesses = [f"{p.position} (rank {p.rank})" for p in positions if p.strength_score <= -0.4]

    trade_strategy = _trade_strategy_for_archetype(archetype_label, positions)

    owner_name = user_display.get(str(my_roster.owner_id), "?")

    archetype = TeamArchetype(
        roster_id=my_roster.roster_id,
        owner=owner_name,
        archetype=archetype_label,
        confidence=confidence,
        reasoning=reasoning,
        total_ktc_value=my_total_value + pick_capital,
        pick_capital=pick_capital,
        value_rank=value_rank,
        production_rank=production_rank,
        record_str=record_str,
        avg_starter_age=avg_starter_age,
        young_asset_pct=young_pct,
        positions=positions,
        strengths=strengths,
        weaknesses=weaknesses,
        trade_strategy=trade_strategy,
    )

    # Top assets
    all_players = []
    for pid in (my_roster.players or []):
        p = sleeper_players.get(pid)
        val = _get_ktc_val(pid)
        if p and val > 0:
            all_players.append({
                "name": p.full_name,
                "position": p.position,
                "team": p.team,
                "ktc": val,
                "age": _safe_age(p),
            })
    all_players.sort(key=lambda x: -x["ktc"])
    top_assets = all_players[:5]

    # Liabilities: high-KTC, high-age (>28) that may depreciate
    liabilities = [p for p in all_players if p["age"] and p["age"] >= 28 and p["ktc"] >= 3000]
    liabilities.sort(key=lambda x: (-x["age"], -x["ktc"]))
    liabilities = liabilities[:5]

    # Roster summary
    by_pos_count = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    for pid in (my_roster.players or []):
        p = sleeper_players.get(pid)
        if p and p.position in by_pos_count:
            by_pos_count[p.position] += 1

    roster_summary = {
        "qb_count": by_pos_count["QB"],
        "rb_count": by_pos_count["RB"],
        "wr_count": by_pos_count["WR"],
        "te_count": by_pos_count["TE"],
        "total_players": len(my_roster.players or []),
    }

    # Generate strategic targets based on archetype
    targets = []
    if archetype_label == "CONTENDER":
        targets = [{"type": "Buy", "description": f"Proven {w.position} starters (age 24-28)"} for w in positions if w.strength_score < 0]
    elif archetype_label == "PRETENDER":
        targets = [{"type": "Sell", "description": f"Aging {s.position} assets before depreciation"} for s in positions if s.strength_score > 0.3]
    elif archetype_label == "REBUILDING":
        targets = [
            {"type": "Buy", "description": "Rookies, 2026+ picks, breakout candidates"},
            {"type": "Sell", "description": "Any player over 26 for future assets"},
        ]
    elif archetype_label == "RELOADING":
        targets = [{"type": "Buy", "description": f"Ascending {w.position} age 23-26"} for w in positions if w.strength_score < 0]

    return GMReport(
        archetype=archetype,
        league_context={"size": len(all_rosters)},
        roster_summary=roster_summary,
        top_assets=top_assets,
        liabilities=liabilities,
        targets=targets,
    )
