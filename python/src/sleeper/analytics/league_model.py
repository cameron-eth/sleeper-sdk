"""L1 — League model (talent distribution, per-team window & needs).

Consumes L0 base values (`analytics.base_value.Asset`) plus league structure
(rosters, roster_positions) and produces the shared context every layer above
reads from:

  * **Talent distribution** — league-wide value curve per position, and a
    *replacement level* per position (the value of the last league-wide
    starter). Replacement level embeds scarcity: in Superflex, QBs run out
    fast so their replacement value is high.
  * **Starter-weighted strength** — an optimal-lineup value, NOT a flat roster
    sum. This is what "how good are you *now*" should mean; a flat sum rewards
    hoarding bench bodies.
  * **Quality-weighted needs / surplus** — per position, how far your starting
    floor sits below (need) or above (surplus) league replacement. Scarcity is
    baked in via replacement level, so a hole at a scarce position scores
    larger than the same body-count hole at a deep one.
  * **Window score** — a continuous −1 (deep rebuild) … +1 (all-in contender)
    scalar plus a trajectory axis, replacing gm_mode's 4-bucket tree.

Everything here is a **pure function of explicit inputs** so it can be
validated numerically (see tests/test_league_model.py). The live adapter
`build_league_model` wires Sleeper data in; it's a thin shell over the pure core.

NOT in this layer yet (deliberately deferred): owner trade tendencies (sparse,
needs hierarchical shrinkage) and the fingerprint versioning / caching /
re-validate-before-execute machinery (design captured separately). This module
is the pure computation; persistence wraps it later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from sleeper.analytics.base_value import Asset

# ---------------------------------------------------------------------------
# Slot vocabulary
# ---------------------------------------------------------------------------

SLOT_ELIGIBILITY: dict[str, frozenset[str]] = {
    "QB": frozenset({"QB"}),
    "RB": frozenset({"RB"}),
    "WR": frozenset({"WR"}),
    "TE": frozenset({"TE"}),
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
    "K": frozenset({"K"}),
    "DEF": frozenset({"DEF"}),
}
_NON_STARTING = frozenset({"BN", "IR", "TAXI"})
_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Age below which a player's value counts as "young" (ascending asset).
YOUNG_AGE_MAX = 25.0
# Starter age at/above which a starter counts as "aging" (window-closing).
AGING_STARTER_AGE = 28.0


def starting_slots(roster_positions: Sequence[str]) -> list[str]:
    """Just the slots that field a starter (drop BN/IR/TAXI)."""
    return [s.upper() for s in roster_positions if s.upper() not in _NON_STARTING]


def dedicated_slot_counts(roster_positions: Sequence[str]) -> dict[str, int]:
    """Count of dedicated single-position starting slots per position."""
    counts: dict[str, int] = {}
    for slot in starting_slots(roster_positions):
        if slot in _SKILL_POSITIONS:
            counts[slot] = counts.get(slot, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Optimal lineup — starter-weighted strength
# ---------------------------------------------------------------------------


def optimal_starters(
    assets: Sequence[Asset],
    roster_positions: Sequence[str],
    fmt: str = "sf",
) -> tuple[list[Asset], int]:
    """Fill the starting lineup to maximize total base value.

    Greedy, most-restrictive-slot-first: dedicated position slots (which have
    no substitute) are filled before flex slots, each taking the highest-value
    eligible unused player. For value maximization this greedy order is optimal
    because dedicated slots cannot be served by anyone else, and every flex
    then takes the best remaining eligible asset.

    Picks and non-skill assets are ignored (they don't start). Returns
    (chosen_starters, total_base_value).
    """
    players = [a for a in assets if not a.is_pick and a.position in SLOT_ELIGIBILITY]
    slots = starting_slots(roster_positions)
    # Most restrictive (smallest eligibility set) first; stable for determinism.
    slots_sorted = sorted(
        (s for s in slots if s in SLOT_ELIGIBILITY),
        key=lambda s: len(SLOT_ELIGIBILITY[s]),
    )
    used: set[int] = set()
    chosen: list[Asset] = []
    for slot in slots_sorted:
        elig = SLOT_ELIGIBILITY[slot]
        best_i: Optional[int] = None
        best_val = -1
        for i, a in enumerate(players):
            if i in used or a.position not in elig:
                continue
            v = a.base_value(fmt)
            if v > best_val:
                best_val, best_i = v, i
        if best_i is not None:
            used.add(best_i)
            chosen.append(players[best_i])
    total = sum(a.base_value(fmt) for a in chosen)
    return chosen, total


# ---------------------------------------------------------------------------
# League talent distribution & replacement levels
# ---------------------------------------------------------------------------


def positional_values(
    teams_assets: Mapping[int, Sequence[Asset]],
    fmt: str = "sf",
) -> dict[str, list[int]]:
    """League-wide sorted (desc) player values per skill position."""
    out: dict[str, list[int]] = {p: [] for p in _SKILL_POSITIONS}
    for assets in teams_assets.values():
        for a in assets:
            if not a.is_pick and a.position in out:
                out[a.position].append(a.base_value(fmt))
    for p in out:
        out[p].sort(reverse=True)
    return out


def replacement_levels(
    teams_assets: Mapping[int, Sequence[Asset]],
    roster_positions: Sequence[str],
    fmt: str = "sf",
) -> dict[str, int]:
    """Replacement value per position = the last league-wide starter's value.

    For each position, the number of league-wide starting slots is
    `n_teams * dedicated_slots[pos]`. The value of the player at that rank
    (1-indexed) in the sorted league pool is the replacement level — the
    quality a fresh starter at that position clears. Scarce positions (few
    quality bodies vs slots) get a high replacement level.
    """
    n_teams = len(teams_assets)
    dedicated = dedicated_slot_counts(roster_positions)
    pos_vals = positional_values(teams_assets, fmt)
    repl: dict[str, int] = {}
    for pos in _SKILL_POSITIONS:
        slots_leaguewide = n_teams * dedicated.get(pos, 0)
        vals = pos_vals[pos]
        if slots_leaguewide <= 0 or not vals:
            repl[pos] = 0
            continue
        idx = min(slots_leaguewide, len(vals)) - 1
        repl[pos] = vals[idx]
    return repl


# ---------------------------------------------------------------------------
# Per-team needs / surplus (quality- & scarcity-weighted)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionNeed:
    position: str
    starter_floor: int   # value of your weakest required starter at this pos (0 if short)
    replacement: int     # league replacement value at this pos
    need: int            # max(0, replacement - starter_floor): a hole
    surplus_count: int   # players above replacement beyond your dedicated slots


def team_position_needs(
    team_assets: Sequence[Asset],
    roster_positions: Sequence[str],
    repl: Mapping[str, int],
    fmt: str = "sf",
) -> dict[str, PositionNeed]:
    """Per-position need/surplus vs league replacement level."""
    dedicated = dedicated_slot_counts(roster_positions)
    out: dict[str, PositionNeed] = {}
    for pos in _SKILL_POSITIONS:
        k = dedicated.get(pos, 0)
        vals = sorted(
            (a.base_value(fmt) for a in team_assets
             if not a.is_pick and a.position == pos),
            reverse=True,
        )
        r = repl.get(pos, 0)
        # starter floor = value of your k-th best (weakest required starter)
        floor = vals[k - 1] if k > 0 and len(vals) >= k else 0
        need = max(0, r - floor)
        surplus_count = sum(1 for v in vals[k:] if v >= r) if k >= 0 else 0
        out[pos] = PositionNeed(
            position=pos, starter_floor=floor, replacement=r,
            need=need, surplus_count=surplus_count,
        )
    return out


# ---------------------------------------------------------------------------
# Window score — continuous contention × trajectory
# ---------------------------------------------------------------------------


def contention_strength(team_strength: int, all_strengths: Sequence[int]) -> float:
    """Gap-aware position of a team's starter strength in the league, [0, 1].

    0 = weakest starting lineup, 1 = strongest. Uses min-max over the field so
    it reflects the *gap* to the field, not just ordinal rank (a lineup far
    ahead of everyone scores near 1, not merely "rank 1").
    """
    if not all_strengths:
        return 0.5
    lo, hi = min(all_strengths), max(all_strengths)
    if hi == lo:
        return 0.5
    return (team_strength - lo) / (hi - lo)


def trajectory(team_assets: Sequence[Asset], fmt: str = "sf") -> float:
    """Asset-age direction, [-1, 1]. + = ascending (young + picks), − = aging.

    Blends the share of player value in young (<=25) players and the share in
    picks against the share tied up in aging (>=28) players. This is the
    "which way is the roster pointing" axis, distinct from how good it is now.
    """
    player_val = sum(a.base_value(fmt) for a in team_assets if not a.is_pick)
    pick_val = sum(a.base_value(fmt) for a in team_assets if a.is_pick)
    total = player_val + pick_val
    if total <= 0:
        return 0.0
    young_val = sum(
        a.base_value(fmt) for a in team_assets
        if not a.is_pick and a.age is not None and a.age <= YOUNG_AGE_MAX
    )
    aging_val = sum(
        a.base_value(fmt) for a in team_assets
        if not a.is_pick and a.age is not None and a.age >= AGING_STARTER_AGE
    )
    ascending = (young_val + pick_val) / total
    declining = aging_val / total
    return max(-1.0, min(1.0, ascending - declining))


def window_score(contention: float, traj: float) -> float:
    """Continuous window in [-1, 1]. +1 = all-in contender, −1 = deep rebuild.

    Contention (how good now) is the spine, mapped [0,1] -> [-1,1]. Trajectory
    nudges it: a strong-but-aging team is pushed *harder* toward win-now (its
    window is closing), while young assets on a weak team deepen the rebuild
    lean. The nudge is bounded so contention always dominates the sign.
    """
    spine = 2.0 * contention - 1.0
    # Aging (traj<0) on a contender → more urgent win-now; youth (traj>0) on a
    # rebuilder → lean further into the rebuild. Both are captured by pushing
    # the spine slightly *away from center* in the direction it already leans
    # when trajectory disagrees with the spine.
    nudge = -0.25 * traj  # young assets lower the window (toward rebuild)
    return max(-1.0, min(1.0, spine + nudge))


def archetype_label(window: float) -> str:
    """Discrete label for display continuity, derived from the window scalar."""
    if window >= 0.5:
        return "CONTENDER"
    if window >= 0.15:
        return "RELOADING"
    if window > -0.15:
        return "FRINGE"
    if window > -0.5:
        return "RETOOLING"
    return "REBUILDING"


# ---------------------------------------------------------------------------
# Assembled per-team profile & league model
# ---------------------------------------------------------------------------


@dataclass
class TeamProfile:
    roster_id: int
    owner: str
    starter_strength: int
    pick_capital: int
    total_base: int
    avg_starter_age: Optional[float]
    young_value_pct: float
    contention: float
    trajectory: float
    window: float
    archetype: str
    needs: dict[str, PositionNeed]


@dataclass
class LeagueModel:
    fmt: str
    roster_positions: list[str]
    replacement: dict[str, int]
    teams: dict[int, TeamProfile]
    distribution: dict[str, list[int]] = field(default_factory=dict)


def build_from_assets(
    teams_assets: Mapping[int, Sequence[Asset]],
    owners: Mapping[int, str],
    roster_positions: Sequence[str],
    fmt: str = "sf",
) -> LeagueModel:
    """Pure assembly of a LeagueModel from per-team Asset lists.

    `teams_assets` maps roster_id -> that team's Assets (players + picks).
    `owners` maps roster_id -> display name.
    """
    repl = replacement_levels(teams_assets, roster_positions, fmt)
    dist = positional_values(teams_assets, fmt)

    # First pass: starter strength per team (needed for contention percentile).
    strengths: dict[int, int] = {}
    starters_by_team: dict[int, list[Asset]] = {}
    for rid, assets in teams_assets.items():
        starters, strength = optimal_starters(assets, roster_positions, fmt)
        strengths[rid] = strength
        starters_by_team[rid] = starters

    all_strengths = list(strengths.values())
    teams: dict[int, TeamProfile] = {}
    for rid, assets in teams_assets.items():
        starters = starters_by_team[rid]
        strength = strengths[rid]
        pick_capital = sum(a.base_value(fmt) for a in assets if a.is_pick)
        total_base = sum(a.base_value(fmt) for a in assets)
        starter_ages = [s.age for s in starters if s.age is not None]
        avg_age = sum(starter_ages) / len(starter_ages) if starter_ages else None
        player_val = sum(a.base_value(fmt) for a in assets if not a.is_pick)
        young_val = sum(
            a.base_value(fmt) for a in assets
            if not a.is_pick and a.age is not None and a.age <= YOUNG_AGE_MAX
        )
        young_pct = (young_val / player_val) if player_val > 0 else 0.0
        cont = contention_strength(strength, all_strengths)
        traj = trajectory(assets, fmt)
        win = window_score(cont, traj)
        needs = team_position_needs(assets, roster_positions, repl, fmt)
        teams[rid] = TeamProfile(
            roster_id=rid,
            owner=owners.get(rid, f"Roster {rid}"),
            starter_strength=strength,
            pick_capital=pick_capital,
            total_base=total_base,
            avg_starter_age=avg_age,
            young_value_pct=young_pct,
            contention=cont,
            trajectory=traj,
            window=win,
            archetype=archetype_label(win),
            needs=needs,
        )

    return LeagueModel(
        fmt=fmt,
        roster_positions=list(roster_positions),
        replacement=repl,
        teams=teams,
        distribution=dist,
    )
