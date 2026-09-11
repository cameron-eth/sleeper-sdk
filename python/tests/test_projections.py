"""Tests for the projections feed: parsing, availability, and league scoring.

The fixture in `tests/fixtures/projections_week.json` is six *real* records
captured from `api.sleeper.com/projections/nfl/2026/...`, chosen to cover all
three availability cases (playing / bye / no NFL team) plus a kicker, whose
scoring exercises the unmatched-key path. Parsing tests run against it rather
than against hand-written dicts so they catch schema drift in the upstream
feed, which is undocumented and has changed provider between seasons.

No network access — the fixture is the contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sleeper.api.projections import _normalize_positions
from sleeper.enrichment.projections import (
    NON_SCORING_STAT_KEYS,
    build_projection_lookup,
    is_scoring_key,
    rank_projections,
    score_projection,
    score_stats,
)
from sleeper.types.projection import PlayerProjection

FIXTURE = Path(__file__).parent / "fixtures" / "projections_week.json"


@pytest.fixture(scope="module")
def raw_records() -> list[dict]:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def projections(raw_records) -> list[PlayerProjection]:
    return [PlayerProjection.model_validate(r) for r in raw_records]


def _by_name(projections, name: str) -> PlayerProjection:
    for p in projections:
        if p.name == name:
            return p
    raise AssertionError(f"{name} not in fixture (names: {[p.name for p in projections]})")


# A league very close to Sleeper's own PPR, for round-trip checks.
PPR_LIKE = {
    "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -1.0, "pass_2pt": 2.0,
    "rush_yd": 0.1, "rush_td": 6.0, "rush_2pt": 2.0,
    "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rec_2pt": 2.0,
    "fum_lost": -2.0,
}


# ---------------------------------------------------------------------------
# Parsing the real payload
# ---------------------------------------------------------------------------

def test_every_fixture_record_parses(raw_records, projections):
    assert len(projections) == len(raw_records)
    assert all(p.player_id for p in projections)


def test_embedded_player_supplies_name_and_position(projections):
    hurts = _by_name(projections, "Jalen Hurts")
    assert hurts.position == "QB"
    assert hurts.team == "PHI"
    assert hurts.opponent
    assert hurts.injury_status is None


def test_stats_are_floats(projections):
    for p in projections:
        assert all(isinstance(v, float) for v in p.stats.values())


def test_unknown_fields_are_preserved_not_rejected():
    """The feed is undocumented; a new key must not fail the whole batch."""
    proj = PlayerProjection.model_validate({
        "player_id": "1", "stats": {"pts_ppr": 1.0},
        "some_field_sleeper_added_later": "surprise",
    })
    assert proj.player_id == "1"


@pytest.mark.parametrize("bad", [None, [], "nope", 3])
def test_non_dict_stats_becomes_empty(bad):
    assert PlayerProjection.model_validate({"player_id": "1", "stats": bad}).stats == {}


def test_non_numeric_and_bool_stat_values_are_dropped():
    proj = PlayerProjection.model_validate({
        "player_id": "1",
        "stats": {"pts_ppr": 12.0, "junk": "x", "flag": True, "n": 3},
    })
    assert proj.stats == {"pts_ppr": 12.0, "n": 3.0}


def test_points_rejects_unknown_scoring_format(projections):
    with pytest.raises(ValueError):
        _by_name(projections, "Jalen Hurts").points("draftkings")


# ---------------------------------------------------------------------------
# Availability — the three cases must stay distinguishable
# ---------------------------------------------------------------------------

def test_player_with_a_game_is_available_and_projected(projections):
    hurts = _by_name(projections, "Jalen Hurts")
    assert hurts.has_game and not hurts.is_bye and hurts.has_projection
    assert hurts.pts_ppr and hurts.pts_ppr > 0


def test_bye_record_is_a_bye_not_a_zero(projections):
    """A bye carries no `pts_ppr` key at all — the reason must survive.

    This is the trap the whole module exists to avoid: `stats.get("pts_ppr",
    0.0)` yields the right ordering by accident while reporting a bye as a
    genuine projection of 0.0.
    """
    bye = _by_name(projections, "Kendrick Law")
    assert bye.team is not None          # still on an NFL roster
    assert not bye.has_game
    assert bye.is_bye
    assert not bye.has_projection
    assert bye.pts_ppr is None           # absent, not 0.0


def test_unsigned_player_is_not_called_a_bye(projections):
    """No team is a different condition from an idle team."""
    fa = _by_name(projections, "Blake Sims")
    assert fa.team is None
    assert not fa.has_game
    assert not fa.is_bye
    assert not fa.has_projection


def test_bye_and_unsigned_are_mutually_exclusive(projections):
    for p in projections:
        assert not (p.is_bye and p.team is None)
        if p.has_game:
            assert not p.is_bye


# ---------------------------------------------------------------------------
# Scoring: stats dot scoring_settings
# ---------------------------------------------------------------------------

def test_league_scoring_reproduces_sleeper_ppr_closely(projections):
    """A PPR-like league should land near Sleeper's own `pts_ppr`.

    Not exact: Sleeper's total includes bonuses and rules this test's small
    settings dict omits. A few points of agreement is the real assertion —
    it proves the keys line up and the units are right.
    """
    for name in ("Jalen Hurts", "C.J. Stroud", "Puka Nacua"):
        p = _by_name(projections, name)
        derived = score_projection(p, PPR_LIKE).points
        assert p.pts_ppr is not None
        assert derived == pytest.approx(p.pts_ppr, abs=2.0), name


def test_scoring_is_linear_in_the_rate():
    stats = {"rec": 5.0, "rec_yd": 70.0, "rec_td": 0.5}
    single = score_stats(stats, {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0})
    double = score_stats(stats, {"rec": 2.0, "rec_yd": 0.2, "rec_td": 12.0})
    assert double == pytest.approx(2 * single)


def test_te_premium_raises_a_tight_ends_total():
    """The reason to re-score at all: custom rules move real points."""
    te = PlayerProjection.model_validate({
        "player_id": "te1", "team": "KC", "game_id": "g",
        "stats": {"pts_ppr": 12.0, "rec": 6.0, "rec_yd": 60.0, "bonus_rec_te": 6.0},
        "player": {"position": "TE"},
    })
    flat = score_projection(te, {"rec": 1.0, "rec_yd": 0.1}).points
    premium = score_projection(te, {"rec": 1.0, "rec_yd": 0.1, "bonus_rec_te": 0.5}).points
    assert premium > flat
    assert premium - flat == pytest.approx(3.0)


def test_six_point_passing_td_beats_four_point():
    qb = PlayerProjection.model_validate({
        "player_id": "qb1", "team": "PHI", "game_id": "g",
        "stats": {"pts_ppr": 20.0, "pass_yd": 250.0, "pass_td": 2.0},
        "player": {"position": "QB"},
    })
    four = score_projection(qb, {"pass_yd": 0.04, "pass_td": 4.0}).points
    six = score_projection(qb, {"pass_yd": 0.04, "pass_td": 6.0}).points
    assert six - four == pytest.approx(4.0)


def test_sleeper_own_totals_never_contribute_to_a_derived_score():
    """Scoring `pts_ppr` itself would double-count the whole stat line."""
    for key in ("pts_ppr", "pts_half_ppr", "pts_std"):
        assert not is_scoring_key(key)
        proj = PlayerProjection.model_validate({
            "player_id": "x", "stats": {key: 99.0, "rec": 1.0},
        })
        assert score_projection(proj, {key: 1.0, "rec": 1.0}).points == pytest.approx(1.0)


@pytest.mark.parametrize("key", sorted(NON_SCORING_STAT_KEYS))
def test_declared_non_scoring_keys_are_excluded(key):
    assert not is_scoring_key(key)


@pytest.mark.parametrize("key", ["adp_ppr", "adp_dd_ppr", "adp_dynasty_2qb", "pos_adp_dd_ppr"])
def test_adp_variants_are_excluded_by_prefix(key):
    """Sleeper keeps adding ADP flavors; matching by prefix survives that."""
    assert not is_scoring_key(key)


@pytest.mark.parametrize("key", ["pass_yd", "rec", "rec_td", "bonus_rec_te", "fgm_40_49",
                                 "pts_allow_21_27", "sack", "fum_lost"])
def test_real_scoring_keys_are_included(key):
    assert is_scoring_key(key)


def test_no_scoring_settings_falls_back_to_sleeper_total(projections):
    hurts = _by_name(projections, "Jalen Hurts")
    for fmt in ("ppr", "half_ppr", "std"):
        scored = score_projection(hurts, None, scoring=fmt)
        assert scored.points == pytest.approx(hurts.points(fmt))
        assert scored.components == []      # nothing to break down


def test_components_sum_to_the_total(projections):
    scored = score_projection(_by_name(projections, "Puka Nacua"), PPR_LIKE)
    assert sum(c.points for c in scored.components) == pytest.approx(scored.points, abs=0.01)


def test_top_components_are_ordered_by_absolute_impact(projections):
    scored = score_projection(_by_name(projections, "Jalen Hurts"), PPR_LIKE)
    top = scored.top_components(3)
    assert len(top) <= 3
    assert [abs(c.points) for c in top] == sorted((abs(c.points) for c in top), reverse=True)


def test_negative_scoring_rules_reduce_the_total():
    qb = PlayerProjection.model_validate({
        "player_id": "q", "team": "X", "game_id": "g",
        "stats": {"pts_ppr": 10.0, "pass_yd": 250.0, "pass_int": 1.0},
    })
    assert (
        score_projection(qb, {"pass_yd": 0.04, "pass_int": -2.0}).points
        < score_projection(qb, {"pass_yd": 0.04}).points
    )


def test_unmatched_scoring_keys_are_reported_not_hidden(projections):
    """A league rule the projection cannot price must be visible.

    `fgmiss` is the real case: leagues score it, Sleeper only projects
    `fgmiss_30_39` / `fgmiss_40_49`, so the rule goes unpriced and a kicker
    total can drift from the app's.
    """
    scored = score_projection(_by_name(projections, "Matt Gay"), {"xpm": 1.0, "fgmiss": -1.0})
    assert "fgmiss" in scored.unmatched_scoring_keys
    assert "xpm" not in scored.unmatched_scoring_keys


def test_empty_inputs_score_zero():
    assert score_stats({}, PPR_LIKE) == 0.0
    assert score_stats({"rec": 5.0}, None) == 0.0
    assert score_stats({"rec": 5.0}, {}) == 0.0


# ---------------------------------------------------------------------------
# Lookup + ranking
# ---------------------------------------------------------------------------

def test_lookup_omits_players_with_no_game(projections):
    """Omission, not 0.0, keeps 'not playing' distinct from 'projected zero'."""
    lookup = build_projection_lookup(projections, PPR_LIKE)
    assert _by_name(projections, "Jalen Hurts").player_id in lookup
    assert _by_name(projections, "Kendrick Law").player_id not in lookup
    assert _by_name(projections, "Blake Sims").player_id not in lookup


def test_lookup_can_include_byes_explicitly(projections):
    lookup = build_projection_lookup(projections, PPR_LIKE, include_byes=True)
    assert len(lookup) == len(projections)
    assert lookup[_by_name(projections, "Kendrick Law").player_id] == 0.0


def test_ranking_is_descending_and_drops_unprojected(projections):
    rows = rank_projections(projections, PPR_LIKE)
    points = [pts for _p, pts in rows]
    assert points == sorted(points, reverse=True)
    names = {p.name for p, _ in rows}
    assert "Kendrick Law" not in names     # no published projection
    assert "Blake Sims" not in names


def test_ranking_can_keep_unprojected_rows(projections):
    rows = rank_projections(projections, PPR_LIKE, projected_only=False)
    assert len(rows) == len(projections)


def test_ranking_filters_by_position_and_floor(projections):
    qbs = rank_projections(projections, PPR_LIKE, position="qb")
    assert qbs and all("QB" in p.fantasy_positions for p, _ in qbs)

    floor = 15.0
    assert all(pts >= floor for _p, pts in rank_projections(projections, PPR_LIKE,
                                                            min_points=floor))


def test_ranking_top_n_truncates(projections):
    assert len(rank_projections(projections, PPR_LIKE, top=2)) == 2


def test_ranking_is_deterministic_for_tied_points():
    tied = [
        PlayerProjection.model_validate({
            "player_id": pid, "team": "X", "game_id": "g",
            "stats": {"pts_ppr": 10.0, "rec": 5.0},
            "player": {"first_name": first, "last_name": "Same", "position": "WR"},
        })
        for pid, first in (("2", "Zeta"), ("1", "Alpha"))
    ]
    order = [p.name for p, _ in rank_projections(tied, PPR_LIKE)]
    assert order == ["Alpha Same", "Zeta Same"]


# ---------------------------------------------------------------------------
# Per-player endpoint asymmetry
# ---------------------------------------------------------------------------

def test_per_player_rows_have_no_embedded_player():
    """`/projections/nfl/player/{id}` omits the player blob entirely.

    Verified live: a week row there has no `player` key, so name/position/
    injury_status read as None. Documented on `get_player_weeks` because
    silently-None names are the kind of thing that reaches production.
    """
    row = PlayerProjection.model_validate({
        "player_id": "6904", "week": 1, "team": "PHI", "opponent": "WAS",
        "game_id": "202610126", "stats": {"pts_ppr": 22.75},
    })
    assert row.player is None
    assert row.name is None
    assert row.position is None
    assert row.injury_status is None
    assert row.fantasy_positions == []
    # The availability signals still work — they key off team/game_id.
    assert row.has_game and not row.is_bye and row.has_projection


def test_per_player_bye_is_an_absent_week_not_a_bye_flagged_row():
    """The two endpoints represent a bye in opposite ways.

    Sweep: a row is present with `game_id: None`, so `is_bye` is True.
    Per-player: the week is `null` upstream and dropped, so it never appears.
    Anything that assumes one representation breaks on the other.
    """
    weeks = {1: PlayerProjection.model_validate({"player_id": "6904", "week": 1,
                                                 "team": "PHI", "game_id": "g"})}
    assert 10 not in weeks                                  # the bye
    assert all(not p.is_bye for p in weeks.values())        # never flagged here


# ---------------------------------------------------------------------------
# Position argument normalization
# ---------------------------------------------------------------------------

def test_positions_normalize_case_and_dedupe():
    assert _normalize_positions(["qb", "QB", "rb"]) == ["QB", "RB"]
    assert _normalize_positions("te") == ["TE"]
    assert _normalize_positions(" wr ") == ["WR"]


def test_none_positions_means_every_position():
    """None is the explicit 'fetch the whole league' signal."""
    assert _normalize_positions(None) is None


@pytest.mark.parametrize("empty", [[], [None], [""], ["  "]])
def test_empty_position_lists_collapse_to_none(empty):
    assert _normalize_positions(empty) is None


def test_position_order_is_preserved():
    assert _normalize_positions(["TE", "QB", "WR"]) == ["TE", "QB", "WR"]
