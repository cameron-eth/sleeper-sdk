"""Re-score raw Sleeper projections under a league's own scoring settings.

Sleeper ships three precomputed totals on every projection — `pts_std`,
`pts_half_ppr`, `pts_ppr` — and none of them is necessarily *your* league.
A 6-point passing TD, TE premium, or a bonus-heavy setup can move a QB or TE
several points, which is exactly the margin a start/sit question turns on.

The fix is cheap because the projection `stats` keys and the league's
`scoring_settings` keys are **the same vocabulary**: `pass_yd`, `rec`,
`bonus_rec_te`, `fgm_40_49`, `pts_allow_21_27`, and so on. So league points
are a dot product over the keys the two dicts share::

    points = sum(stats[k] * scoring_settings[k] for k in stats & scoring_settings)

Two consequences of taking the intersection, both deliberate:

* Non-scoring stat keys (`gp`, `cmp_pct`, `adp_*`, and Sleeper's own
  `pts_*` totals) never appear in a league's `scoring_settings`, so they drop
  out on their own. `NON_SCORING_STAT_KEYS` exists only to document them and
  to guard the (never yet observed) case of a settings key colliding with one.
* Scoring rules with no matching projection key contribute nothing. Sleeper
  projects `fgmiss_30_39` and `fgmiss_40_49` but a league scores plain
  `fgmiss`, so that rule is silently unpriced. This is real and unavoidable
  — the projection simply does not carry that number. `unmatched_scoring_keys`
  reports it instead of hiding it, and it is why a re-scored kicker total can
  drift from what the Sleeper app shows. Skill positions are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from sleeper.types.projection import PlayerProjection

#: Keys that appear in a projection's `stats` but are not scoring events.
#: Excluded defensively; a league's `scoring_settings` has never been observed
#: to contain any of them, in which case the intersection already drops them.
NON_SCORING_STAT_KEYS = frozenset({
    "gp",            # games projected (1.0 for a weekly row)
    "gs",            # games started
    "gms_active",
    "cmp_pct",       # derived rate, not an event
    "pass_ypa",
    "pass_ypc",
    "pass_rtg",
    "off_snp",
    "pts_std",       # Sleeper's own totals — including these would double-count
    "pts_half_ppr",
    "pts_ppr",
    "pos_rank_std",
    "pos_rank_half_ppr",
    "pos_rank_ppr",
})

#: Sleeper's three built-in formats, as `scoring` string -> stats key.
BASE_FORMAT_KEYS = {
    "ppr": "pts_ppr",
    "half_ppr": "pts_half_ppr",
    "std": "pts_std",
}


def is_scoring_key(key: str) -> bool:
    """True when a stat key represents a scorable event.

    ADP fields are matched by prefix because Sleeper keeps adding variants
    (`adp_ppr`, `adp_dynasty_2qb`, `pos_adp_dd_ppr`, ...).
    """
    if key in NON_SCORING_STAT_KEYS:
        return False
    return not (key.startswith("adp_") or key.startswith("pos_adp_"))


@dataclass
class ScoringComponent:
    """One line of the arithmetic behind a projected total."""

    stat: str
    projected: float      # e.g. 258.4 passing yards
    per_unit: float       # e.g. 0.04 points per yard
    points: float         # projected * per_unit

    def __str__(self) -> str:
        return f"{self.stat}: {self.projected:g} x {self.per_unit:g} = {self.points:+.2f}"


@dataclass
class ScoredProjection:
    """A projection with league-specific points and the math that produced it."""

    player_id: str
    points: float
    components: list[ScoringComponent] = field(default_factory=list)
    #: Scoring rules the league has that the projection cannot price. Non-empty
    #: usually means kickers (`fgmiss`) or IDP; see the module docstring.
    unmatched_scoring_keys: list[str] = field(default_factory=list)

    def top_components(self, n: int = 5) -> list[ScoringComponent]:
        """The n components contributing the most absolute points."""
        return sorted(self.components, key=lambda c: -abs(c.points))[:n]


def score_stats(
    stats: Mapping[str, float],
    scoring_settings: Optional[Mapping[str, float]],
) -> float:
    """Dot-product a stat line with a league's scoring settings.

    Returns 0.0 for an empty stat line or absent settings — callers that need
    to distinguish "no projection" from "projected zero" should check
    `PlayerProjection.has_projection` first.
    """
    if not stats or not scoring_settings:
        return 0.0
    total = 0.0
    for key, value in stats.items():
        if not is_scoring_key(key):
            continue
        per_unit = scoring_settings.get(key)
        if per_unit:
            total += value * per_unit
    return total


def score_projection(
    projection: PlayerProjection,
    scoring_settings: Optional[Mapping[str, float]] = None,
    *,
    scoring: str = "ppr",
) -> ScoredProjection:
    """Score one projection, with the per-stat breakdown.

    With `scoring_settings`, points are re-derived from the stat line. Without
    them, falls back to Sleeper's precomputed `scoring` total (`ppr`,
    `half_ppr` or `std`) and returns no components, since there is no
    breakdown to report.
    """
    if not scoring_settings:
        fallback = projection.points(scoring) or 0.0
        return ScoredProjection(player_id=projection.player_id, points=round(fallback, 2))

    components: list[ScoringComponent] = []
    total = 0.0
    for key, value in projection.stats.items():
        if not is_scoring_key(key):
            continue
        per_unit = scoring_settings.get(key)
        if not per_unit or not value:
            continue
        points = value * per_unit
        total += points
        components.append(
            ScoringComponent(stat=key, projected=value, per_unit=per_unit, points=round(points, 3))
        )

    unmatched = sorted(
        k for k, v in scoring_settings.items()
        if v and k not in projection.stats and is_scoring_key(k)
    )
    return ScoredProjection(
        player_id=projection.player_id,
        points=round(total, 2),
        components=components,
        unmatched_scoring_keys=unmatched,
    )


def build_projection_lookup(
    projections: Iterable[PlayerProjection],
    scoring_settings: Optional[Mapping[str, float]] = None,
    *,
    scoring: str = "ppr",
    include_byes: bool = False,
) -> dict[str, float]:
    """Flatten projections into `{player_id: points}`.

    This is the shape `agent.helpers.optimal_lineup` wants for its
    `projections` argument.

    By default players with no game this week (bye, or no NFL team) are
    **omitted** rather than mapped to 0.0. Omitting them keeps "0 points" and
    "not playing" distinguishable downstream; `optimal_lineup` treats a
    missing id as 0.0 anyway, so a bye player still sorts to the bottom.
    """
    out: dict[str, float] = {}
    for proj in projections:
        if not include_byes and not proj.has_game:
            continue
        out[proj.player_id] = score_projection(
            proj, scoring_settings, scoring=scoring
        ).points
    return out


def rank_projections(
    projections: Sequence[PlayerProjection],
    scoring_settings: Optional[Mapping[str, float]] = None,
    *,
    scoring: str = "ppr",
    position: Optional[str] = None,
    min_points: Optional[float] = None,
    projected_only: bool = True,
    top: Optional[int] = None,
) -> list[tuple[PlayerProjection, float]]:
    """Sort projections by league points, highest first.

    Needed because a multi-position fetch merges independently-sorted batches,
    and because most rows in a position sweep are filler.

    Args:
        projected_only: Drop rows Sleeper published no forecast for (see
            `PlayerProjection.has_projection`). On by default — without it a
            list of the "worst" players is just alphabetical noise.
    """
    rows: list[tuple[PlayerProjection, float]] = []
    want = position.upper() if position else None
    for proj in projections:
        if projected_only and not proj.has_projection:
            continue
        if want and want not in {p.upper() for p in proj.fantasy_positions}:
            continue
        pts = score_projection(proj, scoring_settings, scoring=scoring).points
        if min_points is not None and pts < min_points:
            continue
        rows.append((proj, pts))

    rows.sort(key=lambda r: (-r[1], r[0].name or r[0].player_id))
    return rows[:top] if top else rows
