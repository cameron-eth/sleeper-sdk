"""Tests for analytics.league_model (L1 — pure core).

Locks the properties that make the league model philosophically sound:
- starter-weighted strength (optimal lineup), not flat sums
- replacement level embeds positional scarcity
- needs are quality/scarcity-weighted, not body counts
- window score is continuous, contention-driven, trajectory-nudged
"""
from __future__ import annotations

import pytest

from sleeper.analytics.base_value import Asset, PickChart
from sleeper.analytics.league_model import (
    archetype_label,
    build_from_assets,
    contention_strength,
    dedicated_slot_counts,
    optimal_starters,
    replacement_levels,
    team_position_needs,
    trajectory,
    window_score,
)


def P(name, pos, sf, age=25, oqb=None):
    return Asset(kind="player", id=name, name=name, position=pos,
                 base_sf=sf, base_1qb=oqb if oqb is not None else sf, age=age)


def PICK(name, sf):
    return Asset(kind="pick", id=name, name=name, position="PICK",
                 base_sf=sf, base_1qb=sf)


SF_ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN", "BN"]


# ---------------------------------------------------------------------------
# Slot parsing
# ---------------------------------------------------------------------------

def test_dedicated_slot_counts_ignores_flex_and_bench():
    counts = dedicated_slot_counts(SF_ROSTER)
    assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}  # FLEX/SUPER_FLEX/BN excluded


# ---------------------------------------------------------------------------
# Optimal lineup — starter-weighted, flex-aware
# ---------------------------------------------------------------------------

def test_optimal_starters_fills_flex_and_superflex_with_best_leftovers():
    assets = [
        P("QB1", "QB", 8000), P("QB2", "QB", 6000),
        P("RB1", "RB", 7000), P("RB2", "RB", 5000), P("RB3", "RB", 3000),
        P("WR1", "WR", 6500), P("WR2", "WR", 6400), P("WR3", "WR", 1000),
        P("TE1", "TE", 4000),
    ]
    starters, total = optimal_starters(assets, SF_ROSTER, "sf")
    names = {a.name for a in starters}
    # SUPER_FLEX should take the 2nd QB (6000) over RB3 (3000)/WR3 (1000)
    assert "QB2" in names
    # FLEX takes best leftover skill (RB3 3000 vs WR3 1000) -> RB3
    assert "RB3" in names and "WR3" not in names
    # 8 starting slots filled
    assert len(starters) == 8
    assert total == 8000 + 6000 + 7000 + 5000 + 6500 + 6400 + 4000 + 3000


def test_starter_strength_beats_hoarder_flat_sum():
    """A top-heavy contender has higher STARTER strength than a hoarder with a
    bigger flat sum — the whole point of starter-weighting."""
    contender = [P(f"C{i}", "WR", v) for i, v in enumerate([9000, 8000, 7000])] + \
                [P("CQB", "QB", 8000), P("CRB", "RB", 7000), P("CRB2", "RB", 6000),
                 P("CTE", "TE", 6000), P("CQB2", "QB", 6000)]
    hoarder = [P(f"H{i}", "RB", 2500) for i in range(25)]  # huge flat sum, weak starters
    _, c_strength = optimal_starters(contender, SF_ROSTER, "sf")
    _, h_strength = optimal_starters(hoarder, SF_ROSTER, "sf")
    assert sum(a.base_sf for a in hoarder) > sum(a.base_sf for a in contender)  # flat sum favors hoarder
    assert c_strength > h_strength                                             # starters favor contender


# ---------------------------------------------------------------------------
# Replacement level embeds scarcity
# ---------------------------------------------------------------------------

def test_replacement_level_is_last_leaguewide_starter():
    # 2 teams, 1 QB slot each -> 2 league QB starting slots. Replacement QB =
    # the 2nd-best QB value in the league.
    teams = {
        1: [P("QBa", "QB", 9000), P("QBb", "QB", 3000)],
        2: [P("QBc", "QB", 7000), P("QBd", "QB", 1000)],
    }
    repl = replacement_levels(teams, ["QB", "BN"], "sf")
    # sorted QB pool: 9000,7000,3000,1000 ; 2 slots -> idx 1 -> 7000
    assert repl["QB"] == 7000


def test_scarcer_position_has_higher_replacement():
    """With equal talent, a position with FEWER league slots has a higher
    replacement floor (scarcity) — here QB (superflex demand) vs a deep pool."""
    # 2 teams. Give identical value ladders to QB and WR but 1 QB slot vs 3 WR.
    ladder = [9000, 8000, 7000, 6000, 5000, 4000]
    teams = {
        1: [P(f"q{i}", "QB", v) for i, v in enumerate(ladder[::2])] +
           [P(f"w{i}", "WR", v) for i, v in enumerate(ladder)],
        2: [P(f"Q{i}", "QB", v) for i, v in enumerate(ladder[1::2])] +
           [P(f"W{i}", "WR", v) for i, v in enumerate(ladder)],
    }
    rp = ["QB", "WR", "WR", "WR", "BN"]
    repl = replacement_levels(teams, rp, "sf")
    assert repl["QB"] > repl["WR"]  # 2 QB slots vs 6 WR slots -> QB floor higher


# ---------------------------------------------------------------------------
# Needs are quality/scarcity-weighted
# ---------------------------------------------------------------------------

def test_need_is_gap_below_replacement_not_body_count():
    repl = {"QB": 6000, "RB": 5000, "WR": 5000, "TE": 4000}
    # Team has 2 RBs but both are weak (below replacement) -> real need despite
    # having bodies.
    team = [P("rb1", "RB", 3000), P("rb2", "RB", 2500),
            P("wr1", "WR", 8000), P("wr2", "WR", 7000),
            P("qb1", "QB", 8000), P("te1", "TE", 6000)]
    needs = team_position_needs(team, SF_ROSTER, repl, "sf")
    # 2 RB slots -> weakest required starter is the 2nd-best RB (2500), below repl
    assert needs["RB"].starter_floor == 2500
    assert needs["RB"].need == 5000 - 2500
    assert needs["WR"].need == 0             # WRs above replacement
    assert needs["QB"].need == 0


def test_surplus_counts_players_above_replacement_beyond_slots():
    repl = {"QB": 4000, "RB": 3000, "WR": 3000, "TE": 2000}
    team = [P("wr1", "WR", 8000), P("wr2", "WR", 7000), P("wr3", "WR", 6000),
            P("wr4", "WR", 5000)]  # 2 WR slots -> 2 surplus above repl (wr3,wr4)
    needs = team_position_needs(team, SF_ROSTER, repl, "sf")
    assert needs["WR"].surplus_count == 2


# ---------------------------------------------------------------------------
# Window score — continuous, contention-driven, trajectory-nudged
# ---------------------------------------------------------------------------

def test_contention_strength_is_gap_aware():
    assert contention_strength(100, [0, 100]) == 1.0
    assert contention_strength(0, [0, 100]) == 0.0
    assert contention_strength(50, [0, 100]) == 0.5
    assert contention_strength(5, []) == 0.5  # degenerate


def test_trajectory_sign():
    young = [P("y1", "WR", 6000, age=22), P("y2", "RB", 5000, age=23), PICK("p", 5000)]
    old = [P("o1", "WR", 6000, age=30), P("o2", "RB", 5000, age=31)]
    assert trajectory(young, "sf") > 0.5
    assert trajectory(old, "sf") < 0


def test_window_score_bounds_and_direction():
    # strong + neutral traj -> contender
    assert window_score(1.0, 0.0) == 1.0
    # weak + neutral -> rebuild
    assert window_score(0.0, 0.0) == -1.0
    # mid contention, young assets nudge toward rebuild
    assert window_score(0.5, 0.8) < window_score(0.5, -0.8)
    # always bounded
    assert -1.0 <= window_score(0.5, 1.0) <= 1.0


def test_archetype_labels_span_the_scalar():
    assert archetype_label(0.9) == "CONTENDER"
    assert archetype_label(0.3) == "RELOADING"
    assert archetype_label(0.0) == "FRINGE"
    assert archetype_label(-0.3) == "RETOOLING"
    assert archetype_label(-0.8) == "REBUILDING"


# ---------------------------------------------------------------------------
# Full assembly
# ---------------------------------------------------------------------------

def test_build_from_assets_end_to_end():
    strong = [P("SQB", "QB", 8000, 27), P("SQB2", "QB", 7000, 29),
              P("SRB", "RB", 7000, 26), P("SRB2", "RB", 6000, 27),
              P("SWR", "WR", 8000, 28), P("SWR2", "WR", 7000, 29),
              P("STE", "TE", 6000, 28), P("SWR3", "WR", 5000, 30)]
    weak = [P("WQB", "QB", 3000, 23), P("WRB", "RB", 2500, 22),
            P("WWR", "WR", 3000, 22), P("WTE", "TE", 2000, 24),
            PICK("2027 Early 1st", 7000), PICK("2027 Mid 1st", 5600)]
    model = build_from_assets(
        {1: strong, 2: weak},
        {1: "Contender Carl", 2: "Rebuild Rick"},
        SF_ROSTER, "sf",
    )
    c = model.teams[1]
    r = model.teams[2]
    assert c.starter_strength > r.starter_strength
    assert c.window > r.window
    assert c.archetype == "CONTENDER"
    assert r.window < 0                      # rebuild side is negative
    assert r.pick_capital == 12600           # picks valued and attributed
    assert c.pick_capital == 0
    # rebuilder is younger
    assert r.young_value_pct > c.young_value_pct
