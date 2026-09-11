"""Answer "start X or Y?" from Sleeper projections.

Pure functions only — callers fetch the projections (see
`agent.helpers.fetch_projections`) and hand them in, which keeps this module
testable without network access.

Design notes
------------

**Availability outranks the projection.** The decisive fact in most real
start/sit questions is not a point spread, it is that one guy is on bye or was
ruled out on Friday. Sleeper's feed still returns a row for those players, and
a bye row carries no ``pts_ppr`` at all — so ranking on
``stats.get("pts_ppr", 0.0)`` gets the right order by luck while reporting
"projected 0.0" instead of "BYE". :class:`StartSitCandidate` keeps the reason
alongside the number, and the sort is lexicographic on
``(availability_rank, -points)`` so no projection can promote a player who
cannot play.

**A projection edge is not a fact.** Weekly fantasy projections carry several
points of RMSE at the skill positions, so a 0.4-point edge is noise dressed as
a decision. Confidence is therefore a continuous function of the margin::

    confidence_score(margin) = margin / (margin + PROJECTION_NOISE_POINTS)

which is monotonic, bounded in [0, 1), and 0 at a dead tie — it has no
threshold to sit exactly on. The labels are bands of that score, and the band
edges land on round margins by construction: with
``PROJECTION_NOISE_POINTS = 3.0``, a score of 0.25 is a 1.0-point margin and a
score of 0.5 is a 3.0-point margin. Recalibrating means moving one constant,
not rewriting the bands.

**Say "coin-flip" when it is one.** A confident answer to a genuine tie is
worse than no answer, because it spends the user's trust on a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from sleeper.enrichment.projections import score_projection
from sleeper.types.projection import PlayerProjection

# ---------------------------------------------------------------------------
# Injury handling
# ---------------------------------------------------------------------------

#: Designations that make a player unstartable regardless of projection.
#: `Doubtful` is included: it is ~75% out in practice, and Sleeper's
#: projection is usually not yet marked down when the tag lands.
INJURY_OUT = frozenset({"Out", "IR", "PUP", "NA", "Suspended", "Doubtful"})

#: Designations worth flagging but not deciding on.
INJURY_WATCH = frozenset({"Questionable", "Probable", "DTD", "Sus"})

# ---------------------------------------------------------------------------
# Confidence calibration
# ---------------------------------------------------------------------------

#: Points of margin that a projection edge must clear to be worth trusting.
#: Sets the whole confidence curve — see the module docstring. Roughly the
#: half-width of weekly projection error at RB/WR; deliberately conservative,
#: because the cost of a falsely confident call is higher than the cost of
#: saying "coin-flip".
PROJECTION_NOISE_POINTS = 3.0

#: Band edges on `confidence_score`. 0.25 <=> a 1.0-point margin,
#: 0.50 <=> a 3.0-point margin, given the noise constant above.
COIN_FLIP_BELOW = 0.25
LEAN_BELOW = 0.50

# Availability ranks — lower starts first. A player with no published
# projection sorts behind anyone who has one (his 0.0 means "unknown", not
# "zero") but ahead of anyone who cannot play at all.
_RANK_PROJECTED = 0
_RANK_UNKNOWN = 1
_RANK_UNAVAILABLE = 2


def confidence_score(margin: float) -> float:
    """Map a point margin to a continuous confidence in [0, 1).

    Monotonically increasing, 0.0 at a tie, never reaching 1.0. Negative
    margins are clamped to 0 — a caller asking about a negative margin has
    the pair backwards, and a negative confidence is meaningless.
    """
    m = max(0.0, float(margin))
    return m / (m + PROJECTION_NOISE_POINTS)


def confidence_label(margin: float) -> str:
    """Band `confidence_score` into `coin-flip` / `lean` / `clear`."""
    score = confidence_score(margin)
    if score < COIN_FLIP_BELOW:
        return "coin-flip"
    if score < LEAN_BELOW:
        return "lean"
    return "clear"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StartSitCandidate:
    """One player under consideration, with the reason behind his ranking."""

    player_id: str
    name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    opponent: Optional[str] = None
    projected_points: float = 0.0
    #: `ok` | `no_projection` | `bye` | `no_team` | `out`
    status: str = "ok"
    injury_status: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    #: Biggest contributors to the projected total, pre-formatted for display.
    #: Empty unless league `scoring_settings` were supplied.
    drivers: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        """False when the player cannot play at all this week."""
        return self.status not in ("bye", "no_team", "out")

    @property
    def has_projection(self) -> bool:
        return self.status != "no_projection"

    @property
    def availability_rank(self) -> int:
        if not self.available:
            return _RANK_UNAVAILABLE
        return _RANK_PROJECTED if self.has_projection else _RANK_UNKNOWN

    @property
    def label(self) -> str:
        """Short human string: `Jalen Hurts (QB, PHI vs WAS) 22.8`."""
        who = self.name or self.player_id
        where = self.team or "FA"
        if self.status == "bye":
            matchup = f"{where} BYE"
        elif self.opponent:
            matchup = f"{where} vs {self.opponent}"
        else:
            matchup = where
        bits = f"{who} ({self.position or '?'}, {matchup})"
        if self.status == "no_projection":
            return f"{bits} no projection"
        return f"{bits} {self.projected_points:.1f}"


@dataclass
class StartSitVerdict:
    """The answer: who to start, who to sit, and how sure we are."""

    week: Optional[int]
    slots: int
    scoring_label: str
    start: list[StartSitCandidate] = field(default_factory=list)
    sit: list[StartSitCandidate] = field(default_factory=list)
    #: Points between the last starter and the best player left on the bench.
    #: 0.0 when there is nobody to compare against.
    margin: float = 0.0
    confidence: str = "coin-flip"
    confidence_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ranked(self) -> list[StartSitCandidate]:
        """Every candidate, best first."""
        return self.start + self.sit

    @property
    def recommendation(self) -> str:
        """One-line answer, e.g. `Start Jalen Hurts over Josh Allen (clear, +4.2)`.

        Slots get filled even when nobody can play — you still have to field a
        lineup — so this checks `available` rather than just whether `start` is
        non-empty. Otherwise a roster of bye-week players would produce a
        confident "Start <player on bye>".
        """
        startable = [c for c in self.start if c.available]
        if not startable:
            return (
                "No startable candidate — every option is on bye, ruled out, "
                "or not on an NFL roster. Look to waivers."
            )

        starters = ", ".join(c.name or c.player_id for c in startable)
        prefix = ""
        if len(startable) < len(self.start):
            unplayable = ", ".join(
                c.name or c.player_id for c in self.start if not c.available
            )
            prefix = (
                f"Only {len(startable)} of {self.slots} slots can be filled "
                f"({unplayable} cannot play). "
            )

        if not self.sit:
            return f"{prefix}Start {starters}."
        benched = ", ".join(c.name or c.player_id for c in self.sit)
        if prefix:
            return f"{prefix}Start {starters} over {benched}."
        if self.confidence == "coin-flip" and self.margin < PROJECTION_NOISE_POINTS:
            return (
                f"Coin-flip: {starters} over {benched} by only {self.margin:.1f} — "
                "inside projection noise, either is defensible."
            )
        return f"Start {starters} over {benched} ({self.confidence}, +{self.margin:.1f})."

    def to_dict(self) -> dict:
        """JSON-serializable form, matching the agent-layer convention."""
        def row(c: StartSitCandidate) -> dict:
            return {
                "player_id": c.player_id,
                "name": c.name,
                "position": c.position,
                "team": c.team,
                "opponent": c.opponent,
                "projected_points": c.projected_points,
                "status": c.status,
                "available": c.available,
                "injury_status": c.injury_status,
                "notes": c.notes,
                "drivers": c.drivers,
            }

        return {
            "week": self.week,
            "slots": self.slots,
            "scoring": self.scoring_label,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "confidence_score": round(self.confidence_score, 3),
            "margin": self.margin,
            "start": [row(c) for c in self.start],
            "sit": [row(c) for c in self.sit],
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Building candidates
# ---------------------------------------------------------------------------

def build_candidate(
    projection: PlayerProjection,
    scoring_settings: Optional[Mapping[str, float]] = None,
    *,
    scoring: str = "ppr",
) -> StartSitCandidate:
    """Turn one projection into a ranked candidate with its reason attached."""
    scored = score_projection(projection, scoring_settings, scoring=scoring)
    notes: list[str] = []
    injury = projection.injury_status

    if projection.is_bye:
        status = "bye"
        notes.append(f"{projection.team} on bye in week {projection.week}")
    elif projection.team is None:
        status = "no_team"
        notes.append("No NFL team — free agent or practice squad")
    elif injury in INJURY_OUT:
        status = "out"
        notes.append(f"Ruled {injury}")
    elif not projection.has_projection:
        status = "no_projection"
        notes.append("Sleeper published no projection — likely a deep-bench role")
    else:
        status = "ok"
        if injury in INJURY_WATCH:
            notes.append(f"{injury} — confirm active before kickoff")

    # An out/bye player's stale projection is misleading; report 0.
    points = 0.0 if status in ("bye", "no_team", "out") else scored.points

    return StartSitCandidate(
        player_id=projection.player_id,
        name=projection.name,
        position=projection.position,
        team=projection.team,
        opponent=projection.opponent,
        projected_points=points,
        status=status,
        injury_status=injury,
        notes=notes,
        drivers=[str(c) for c in scored.top_components(3)],
    )


def compare_projections(
    projections: Sequence[PlayerProjection],
    scoring_settings: Optional[Mapping[str, float]] = None,
    *,
    slots: int = 1,
    scoring: str = "ppr",
    week: Optional[int] = None,
) -> StartSitVerdict:
    """Rank candidates and fill `slots` of them.

    Args:
        projections: The players being weighed against each other, as returned
            by `client.projections.get_week`.
        scoring_settings: The league's `scoring_settings`. Strongly
            recommended — without it the verdict falls back to Sleeper's
            generic `scoring` total, which mis-prices 6-point passing TDs,
            TE premium and bonus scoring.
        slots: How many of these candidates can start. 1 answers "X or Y?";
            2 answers "which two of these three flex plays?".

    Raises:
        ValueError: if `slots` < 1.
    """
    if slots < 1:
        raise ValueError(f"slots must be >= 1, got {slots}")

    candidates = [
        build_candidate(p, scoring_settings, scoring=scoring) for p in projections
    ]
    # Availability first, then points. Name breaks ties so the output is
    # deterministic for equal projections.
    candidates.sort(
        key=lambda c: (c.availability_rank, -c.projected_points, c.name or c.player_id)
    )

    start = candidates[:slots]
    sit = candidates[slots:]

    # The margin is the decision that was actually made: last man in versus
    # best man out. Comparing first-to-last would overstate a close call.
    margin = 0.0
    if start and sit:
        margin = round(start[-1].projected_points - sit[0].projected_points, 2)

    reasons: list[str] = []
    warnings: list[str] = []

    for c in candidates:
        for note in c.notes:
            reasons.append(f"{c.name or c.player_id}: {note}")

    if start and sit and start[-1].availability_rank > sit[0].availability_rank:
        # Cannot happen via the sort; guards a future edit to the sort key.
        warnings.append("Ranking put a less-available player in a starting slot")

    if not scoring_settings:
        warnings.append(
            f"No league scoring_settings supplied — using Sleeper's generic "
            f"{scoring} total, which ignores custom scoring."
        )

    if week is None:
        week = next((p.week for p in projections if p.week is not None), None)

    if margin < 0:
        # Reachable: a player with a *negative* league projection (mostly
        # interceptions and fumbles in a harsh scoring setup) still ranks
        # ahead of one with no projection at all, whose points read 0.0. The
        # ordering is right, but the arithmetic gap is negative and a negative
        # margin would feed a meaningless confidence.
        margin = 0.0

    return StartSitVerdict(
        week=week,
        slots=slots,
        scoring_label="league scoring_settings" if scoring_settings else scoring,
        start=start,
        sit=sit,
        margin=margin,
        confidence=confidence_label(margin),
        confidence_score=confidence_score(margin),
        reasons=reasons,
        warnings=warnings,
    )
