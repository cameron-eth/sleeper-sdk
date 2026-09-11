"""Tests for the start/sit decision layer.

Follows the repo convention of asserting *properties* — ordering, monotonicity,
boundedness, correct precedence — rather than pinning the current calibration
constants. Retuning `PROJECTION_NOISE_POINTS` should not require editing these.

The invariants that matter, and the bugs they guard:

* Availability outranks points. A ruled-out or bye player must never be
  slotted ahead of someone who can actually play, no matter what stale number
  the feed still carries for him.
* Confidence is continuous and monotonic in the margin, so there is no cliff
  for a real decision to land exactly on.
* A coin-flip is reported as a coin-flip.
"""
from __future__ import annotations

import pytest

from sleeper.analytics.start_sit import (
    COIN_FLIP_BELOW,
    INJURY_OUT,
    INJURY_WATCH,
    LEAN_BELOW,
    PROJECTION_NOISE_POINTS,
    build_candidate,
    compare_projections,
    confidence_label,
    confidence_score,
)
from sleeper.types.projection import PlayerProjection

PPR = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rush_yd": 0.1, "rush_td": 6.0}


def player(
    name: str,
    points: float | None = 10.0,
    *,
    position: str = "WR",
    team: str | None = "PHI",
    playing: bool = True,
    injury: str | None = None,
    week: int = 1,
) -> PlayerProjection:
    """Build a projection. `points` of None means Sleeper published none.

    The whole name goes in `first_name` so `full_name` round-trips exactly and
    assertions can use the same string they passed in.
    """
    stats = {} if points is None else {"pts_ppr": points, "rec_yd": points * 10}
    return PlayerProjection.model_validate({
        "player_id": name.replace(" ", "_").lower(),
        "week": week,
        "team": team,
        "opponent": "WAS" if playing and team else None,
        "game_id": "g1" if playing and team else None,
        "stats": stats,
        "player": {
            "first_name": name, "last_name": None,
            "position": position, "injury_status": injury,
        },
    })


# ---------------------------------------------------------------------------
# Confidence curve
# ---------------------------------------------------------------------------

def test_confidence_is_zero_at_a_dead_tie():
    assert confidence_score(0.0) == 0.0


def test_confidence_is_bounded_below_one():
    for margin in (0, 0.5, 3, 10, 50, 1_000, 1e9):
        assert 0.0 <= confidence_score(margin) < 1.0


def test_confidence_is_strictly_monotonic_in_margin():
    margins = [0.0, 0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 20.0]
    scores = [confidence_score(m) for m in margins]
    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores)


def test_confidence_has_no_cliff():
    """Continuity: a hair more margin never jumps the score.

    The whole reason confidence is a curve rather than a lookup table is that
    a real decision must not hinge on landing a thousandth of a point either
    side of a threshold.
    """
    step = 1e-6
    for margin in (0.0, 0.999, 1.0, 2.999, 3.0, 7.5):
        assert confidence_score(margin + step) - confidence_score(margin) < 1e-5


def test_negative_margin_clamps_to_zero():
    assert confidence_score(-5.0) == 0.0
    assert confidence_label(-5.0) == "coin-flip"


def test_band_edges_sit_on_the_documented_margins():
    """The docstring claims 0.25 <=> 1.0 pts and 0.50 <=> 3.0 pts.

    Derived from PROJECTION_NOISE_POINTS rather than hardcoded, so retuning
    the constant keeps this honest instead of breaking it.
    """
    one_point = PROJECTION_NOISE_POINTS / 3.0
    assert confidence_score(one_point) == pytest.approx(COIN_FLIP_BELOW)
    assert confidence_score(PROJECTION_NOISE_POINTS) == pytest.approx(LEAN_BELOW)


def test_labels_are_monotonic_in_margin():
    rank = {"coin-flip": 0, "lean": 1, "clear": 2}
    seen = [rank[confidence_label(m)] for m in [0, 0.5, 1, 2, 3, 4, 6, 12, 40]]
    assert seen == sorted(seen)


def test_tiny_margins_are_coin_flips_and_large_ones_are_clear():
    assert confidence_label(0.1) == "coin-flip"
    assert confidence_label(12.0) == "clear"


# ---------------------------------------------------------------------------
# Candidate classification
# ---------------------------------------------------------------------------

def test_healthy_playing_candidate_is_available():
    c = build_candidate(player("Puka Nacua", 18.0), PPR)
    assert c.status == "ok"
    assert c.available and c.has_projection
    assert c.projected_points > 0


def test_bye_candidate_is_unavailable_and_zeroed():
    c = build_candidate(player("Bye Guy", 15.0, playing=False), PPR)
    assert c.status == "bye"
    assert not c.available
    assert c.projected_points == 0.0, "a stale bye-week projection must not survive"
    assert any("bye" in n.lower() for n in c.notes)


def test_unsigned_candidate_is_not_labelled_a_bye():
    c = build_candidate(player("Free Agent", None, team=None, playing=False), PPR)
    assert c.status == "no_team"
    assert not c.available


@pytest.mark.parametrize("status", sorted(INJURY_OUT))
def test_ruled_out_designations_are_unavailable(status):
    c = build_candidate(player("Hurt Guy", 20.0, injury=status), PPR)
    assert c.status == "out"
    assert not c.available
    assert c.projected_points == 0.0


@pytest.mark.parametrize("status", sorted(INJURY_WATCH))
def test_watch_designations_stay_startable_but_flagged(status):
    c = build_candidate(player("Maybe Guy", 14.0, injury=status), PPR)
    assert c.status == "ok"
    assert c.available
    assert c.projected_points > 0
    assert any(status in n for n in c.notes)


def test_playing_player_with_no_published_projection_is_flagged_unknown():
    """0.0 here means 'unknown', which is not the same as 'projected zero'."""
    c = build_candidate(player("Deep Bench", None), PPR)
    assert c.status == "no_projection"
    assert c.available            # he could play
    assert not c.has_projection   # but we know nothing


def test_label_reports_bye_rather_than_a_number():
    assert "BYE" in build_candidate(player("Bye Guy", 12.0, playing=False), PPR).label


def test_label_includes_matchup_and_points():
    label = build_candidate(player("Puka Nacua", 18.0), PPR).label
    assert "Puka" in label and "WAS" in label


# ---------------------------------------------------------------------------
# Ranking precedence
# ---------------------------------------------------------------------------

def test_higher_projection_starts():
    v = compare_projections([player("Low", 8.0), player("High", 17.0)], PPR)
    assert [c.name for c in v.start] == ["High"]
    assert [c.name for c in v.sit] == ["Low"]
    assert v.margin > 0


def test_bye_never_starts_over_an_available_player_even_when_projected_higher():
    """The precedence bug this module exists to prevent."""
    v = compare_projections(
        [player("Bye Star", 30.0, playing=False), player("Active Scrub", 4.0)],
        PPR,
    )
    assert [c.name for c in v.start] == ["Active Scrub"]
    assert v.sit[0].status == "bye"


def test_ruled_out_never_starts_over_an_available_player():
    v = compare_projections(
        [player("Out Star", 25.0, injury="Out"), player("Healthy Scrub", 3.0)],
        PPR,
    )
    assert [c.name for c in v.start] == ["Healthy Scrub"]


def test_projected_player_starts_over_one_with_no_projection():
    v = compare_projections([player("Unknown", None), player("Known", 1.0)], PPR)
    assert [c.name for c in v.start] == ["Known"]


def test_unknown_starts_over_someone_who_cannot_play():
    v = compare_projections(
        [player("Bye Guy", 20.0, playing=False), player("Unknown", None)],
        PPR,
    )
    assert v.start[0].name == "Unknown"


def test_availability_rank_is_never_violated_by_the_sort():
    """The global invariant: ranks are non-decreasing down the ranked list."""
    roster = [
        player("A", 22.0),
        player("B", 30.0, playing=False),
        player("C", None),
        player("D", 9.0, injury="Out"),
        player("E", 15.0, injury="Questionable"),
        player("F", None, team=None, playing=False),
    ]
    ranks = [c.availability_rank for c in compare_projections(roster, PPR, slots=2).ranked]
    assert ranks == sorted(ranks)


def test_ranked_contains_every_candidate_exactly_once():
    roster = [player(n, float(i)) for i, n in enumerate("ABCDE")]
    v = compare_projections(roster, PPR, slots=2)
    assert len(v.start) == 2
    assert len(v.ranked) == len(roster)
    assert len({c.player_id for c in v.ranked}) == len(roster)


# ---------------------------------------------------------------------------
# Margin semantics
# ---------------------------------------------------------------------------

def test_margin_compares_last_starter_to_best_bench_not_first_to_last():
    """With 2 slots and 3 players, the decision is #2 vs #3.

    Using first-vs-last would overstate a close call: here #1 is far clear of
    everyone, but the actual choice being made is a 0.5-point one.
    """
    v = compare_projections(
        [player("Elite", 30.0), player("Mid", 10.5), player("Fringe", 10.0)],
        PPR, slots=2,
    )
    assert [c.name for c in v.start] == ["Elite", "Mid"]
    assert v.margin == pytest.approx(0.5, abs=0.01)
    assert v.confidence == "coin-flip"


def test_margin_is_zero_when_nobody_is_benched():
    v = compare_projections([player("Only", 12.0)], PPR)
    assert v.sit == []
    assert v.margin == 0.0


def test_margin_is_never_negative():
    v = compare_projections(
        [player("Bye Star", 40.0, playing=False), player("Scrub", 2.0)], PPR
    )
    assert v.margin >= 0.0


def test_identical_projections_are_a_coin_flip():
    v = compare_projections([player("Alpha", 12.0), player("Beta", 12.0)], PPR)
    assert v.margin == 0.0
    assert v.confidence == "coin-flip"
    assert "coin-flip" in v.recommendation.lower()


def test_tie_break_is_deterministic():
    a = compare_projections([player("Alpha", 12.0), player("Beta", 12.0)], PPR)
    b = compare_projections([player("Beta", 12.0), player("Alpha", 12.0)], PPR)
    assert [c.name for c in a.ranked] == [c.name for c in b.ranked]


def test_bigger_gap_yields_at_least_as_much_confidence():
    rank = {"coin-flip": 0, "lean": 1, "clear": 2}
    previous = -1
    for high in (10.1, 11.0, 13.0, 18.0, 30.0):
        v = compare_projections([player("Low", 10.0), player("High", high)], PPR)
        assert rank[v.confidence] >= previous
        previous = rank[v.confidence]


# ---------------------------------------------------------------------------
# Slots, scoring, and reporting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slots", [1, 2, 3])
def test_slots_controls_how_many_start(slots):
    roster = [player(n, float(10 + i)) for i, n in enumerate("ABCD")]
    assert len(compare_projections(roster, PPR, slots=slots).start) == slots


def test_slots_beyond_the_candidate_count_starts_everyone():
    v = compare_projections([player("A", 5.0), player("B", 6.0)], PPR, slots=9)
    assert len(v.start) == 2
    assert v.sit == []


@pytest.mark.parametrize("slots", [0, -1])
def test_non_positive_slots_is_an_error(slots):
    with pytest.raises(ValueError):
        compare_projections([player("A", 5.0)], PPR, slots=slots)


def test_empty_candidate_list_says_so_instead_of_crashing():
    v = compare_projections([], PPR)
    assert v.start == [] and v.sit == []
    assert "no startable" in v.recommendation.lower()


def test_all_unavailable_reports_no_startable_candidate():
    """Slots still get filled, so this must not read as a confident 'start him'.

    Names here avoid the words 'bye'/'out' on purpose — an earlier version of
    this test passed only because a player was *named* "Bye" while the
    recommendation actually said "Start Bye."
    """
    v = compare_projections(
        [player("Alpha", 10.0, playing=False), player("Beta", 12.0, injury="Out")],
        PPR, slots=2,
    )
    assert all(not c.available for c in v.ranked)
    assert "no startable candidate" in v.recommendation.lower()
    assert "start alpha" not in v.recommendation.lower()
    assert "start beta" not in v.recommendation.lower()


def test_partially_fillable_lineup_says_how_many_slots_are_live():
    """Two slots, only one playable body — say so rather than implying both."""
    v = compare_projections(
        [player("Playable", 11.0), player("Benched", 9.0, injury="Out")],
        PPR, slots=2,
    )
    rec = v.recommendation
    assert "Playable" in rec
    assert "cannot play" in rec and "Benched" in rec


def test_negative_league_projection_still_outranks_no_projection():
    """Harsh scoring can make a real projection negative.

    It should still rank ahead of an unknown — a number we have beats a number
    we do not — but the resulting negative gap must not become a negative
    confidence.
    """
    punitive = {"pass_int": -3.0}
    turnover_machine = PlayerProjection.model_validate({
        "player_id": "bad", "week": 1, "team": "X", "opponent": "Y", "game_id": "g",
        "stats": {"pts_ppr": 2.0, "pass_int": 2.0},
        "player": {"first_name": "Pick", "last_name": "Six", "position": "QB"},
    })
    v = compare_projections([turnover_machine, player("Unknown", None)], punitive)

    assert v.start[0].name == "Pick Six"
    assert v.start[0].projected_points < 0
    assert v.margin == 0.0                      # clamped, not negative
    assert 0.0 <= v.confidence_score < 1.0


def test_missing_scoring_settings_is_warned_about():
    """Silently using generic PPR for a custom league would be a wrong answer."""
    v = compare_projections([player("A", 10.0), player("B", 12.0)], None)
    assert any("scoring_settings" in w for w in v.warnings)
    assert v.scoring_label == "ppr"


def test_supplied_scoring_settings_are_used_and_labelled():
    v = compare_projections([player("A", 10.0), player("B", 12.0)], PPR)
    assert v.scoring_label == "league scoring_settings"
    assert not any("scoring_settings" in w for w in v.warnings)


def test_custom_scoring_can_reverse_the_verdict():
    """The payoff for re-scoring: 4-pt and 6-pt passing TD leagues disagree.

    Deliberately sized so the runner sits *between* the passer's two totals:
    passer is 12 + 3 TDs (24 at 4pt, 30 at 6pt), runner is a flat 26. Under
    4-pt scoring you start the runner; under 6-pt you start the passer. A
    verdict read off Sleeper's generic `pts_ppr` cannot make that distinction.
    """
    passer = PlayerProjection.model_validate({
        "player_id": "passer", "week": 1, "team": "PHI", "opponent": "WAS",
        "game_id": "g", "stats": {"pts_ppr": 18.0, "pass_yd": 300.0, "pass_td": 3.0},
        "player": {"first_name": "Pass", "last_name": "Er", "position": "QB"},
    })
    runner = PlayerProjection.model_validate({
        "player_id": "runner", "week": 1, "team": "DAL", "opponent": "NYG",
        "game_id": "g", "stats": {"pts_ppr": 20.0, "rush_yd": 200.0, "rush_td": 1.0},
        "player": {"first_name": "Run", "last_name": "Ner", "position": "RB"},
    })

    four_pt = {"pass_yd": 0.04, "pass_td": 4.0, "rush_yd": 0.1, "rush_td": 6.0}
    six_pt = dict(four_pt, pass_td=6.0)

    assert compare_projections([passer, runner], four_pt).start[0].name == "Run Ner"
    assert compare_projections([passer, runner], six_pt).start[0].name == "Pass Er"


def test_reasons_mention_every_flagged_candidate():
    v = compare_projections(
        [player("Healthy", 15.0),
         player("Bye Guy", 12.0, playing=False),
         player("Quest", 14.0, injury="Questionable")],
        PPR, slots=2,
    )
    joined = " ".join(v.reasons)
    assert "Bye Guy" in joined and "Quest" in joined


def test_week_is_inferred_from_the_projections_when_not_given():
    v = compare_projections([player("A", 10.0, week=7), player("B", 9.0, week=7)], PPR)
    assert v.week == 7


def test_explicit_week_wins_over_the_payload():
    v = compare_projections([player("A", 10.0, week=7)], PPR, week=9)
    assert v.week == 9


def test_to_dict_is_json_serializable_and_complete():
    import json

    v = compare_projections(
        [player("A", 15.0), player("Bye Guy", 12.0, playing=False)], PPR
    )
    payload = v.to_dict()
    json.dumps(payload)     # must not raise
    for key in ("week", "slots", "scoring", "recommendation", "confidence",
                "confidence_score", "margin", "start", "sit", "reasons", "warnings"):
        assert key in payload
    assert payload["start"][0]["available"] is True
    assert payload["sit"][0]["status"] == "bye"


def test_recommendation_names_the_starter_and_the_benched():
    v = compare_projections([player("Start Me", 20.0), player("Sit Me", 8.0)], PPR)
    assert "Start Me" in v.recommendation
    assert "Sit Me" in v.recommendation
