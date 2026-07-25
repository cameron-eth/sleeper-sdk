"""Tests for analytics.trade_runtime (L3 — mutual-fit matching).

Locks the runtime's defining behavior: it finds exchanges where BOTH sides
gain in their OWN window, gates on market realism, and reports acceptance as
a probability rather than a verdict.
"""
from __future__ import annotations

import pytest

from sleeper.analytics.base_value import Asset
from sleeper.analytics.league_model import build_from_assets
from sleeper.analytics.trade_runtime import (
    MARKET_CEIL,
    MARKET_FLOOR,
    acceptance_probability,
    build_rationale,
    find_mutual_trades,
    market_delta,
    side_gain,
)


def P(name, pos, sf, age):
    return Asset(kind="player", id=name, name=name, position=pos,
                 base_sf=sf, base_1qb=sf, age=age)


def PICK(name, sf):
    return Asset(kind="pick", id=name, name=name, position="PICK",
                 base_sf=sf, base_1qb=sf)


SF_ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN", "BN"]


# ---------------------------------------------------------------------------
# side_gain / market_delta primitives
# ---------------------------------------------------------------------------

def test_side_gain_uses_holder_window():
    aging = P("Old", "RB", 6000, 30)
    pick = PICK("2027 1st", 6000)
    # Contender receiving the aging producer for a pick gains.
    assert side_gain([aging], [pick], window=0.9) > 0
    # Rebuilder doing the same loses.
    assert side_gain([aging], [pick], window=-0.9) < 0


def test_market_delta_sign_from_sender_perspective():
    send = [P("A", "WR", 6000, 25)]
    receive = [P("B", "WR", 5000, 25)]
    assert market_delta(send, receive) < 0  # giving more market value than getting


# ---------------------------------------------------------------------------
# acceptance probability
# ---------------------------------------------------------------------------

def test_acceptance_zero_when_partner_loses():
    assert acceptance_probability(partner_gain=-100, market_delta_for_partner=5000,
                                  need_met=True) == 0.0


def test_acceptance_rises_with_partner_market_delta():
    lo = acceptance_probability(500, -2000, False)
    hi = acceptance_probability(500, 2000, False)
    assert 0.0 <= lo < hi <= 1.0


def test_need_bonus_lifts_acceptance():
    without = acceptance_probability(500, 0, False)
    with_need = acceptance_probability(500, 0, True)
    assert with_need > without


def test_acceptance_is_bounded():
    assert acceptance_probability(9999, 999999, True) <= 1.0
    assert acceptance_probability(1, -999999, False) >= 0.0


# ---------------------------------------------------------------------------
# End-to-end: the canonical contender <-> rebuilder exchange
# ---------------------------------------------------------------------------

@pytest.fixture
def two_team_league():
    """A clear contender and a clear rebuilder, each holding what the other
    philosophically wants."""
    contender = [
        P("CQB", "QB", 8000, 27), P("CQB2", "QB", 7000, 28),
        P("CRB", "RB", 7000, 26), P("CRB2", "RB", 6000, 27),
        P("CWR", "WR", 8000, 27), P("CWR2", "WR", 7000, 28),
        P("CTE", "TE", 6000, 27),
        PICK("2027 Early 1st", 7000),          # contender holds a pick it doesn't want
    ]
    rebuilder = [
        P("RQB", "QB", 4000, 23), P("RRB", "RB", 3500, 22),
        P("RWR", "WR", 4000, 22), P("RTE", "TE", 3000, 24),
        P("RVet", "WR", 6800, 30),             # rebuilder holds an aging producer
    ]
    teams = {1: contender, 2: rebuilder}
    owners = {1: "Contender Carl", 2: "Rebuild Rick"}
    model = build_from_assets(teams, owners, SF_ROSTER, "sf")
    return model, teams


def test_finds_the_mutual_trade(two_team_league):
    model, teams = two_team_league
    # From the rebuilder's seat: ship the aging vet, get the pick.
    props = find_mutual_trades(model, my_roster_id=2, teams_assets=teams, top=20)
    assert props, "expected at least one mutually-beneficial trade"
    # Every returned proposal must be positive-sum for BOTH sides.
    for p in props:
        assert p.my_gain > 0 and p.partner_gain > 0
        assert p.mutual_gain == min(p.my_gain, p.partner_gain)
    # The thesis trade (send aging vet, receive a pick) should surface.
    found = any(
        any(a.name == "RVet" for a in p.send) and any(a.is_pick for a in p.receive)
        for p in props
    )
    assert found, "the aging-producer-for-pick exchange should be discovered"


def test_market_realism_gate_is_enforced(two_team_league):
    model, teams = two_team_league
    props = find_mutual_trades(model, my_roster_id=2, teams_assets=teams, top=50)
    for p in props:
        assert MARKET_FLOOR <= p.market_delta <= MARKET_CEIL


def test_proposals_carry_rationale_and_version(two_team_league):
    model, teams = two_team_league
    props = find_mutual_trades(model, my_roster_id=2, teams_assets=teams,
                               top=5, model_version="v_test123")
    for p in props:
        assert p.model_version == "v_test123"
        assert "window" in p.rationale
        assert p.partner_owner


def test_no_self_trades(two_team_league):
    model, teams = two_team_league
    props = find_mutual_trades(model, my_roster_id=2, teams_assets=teams, top=50)
    assert all(p.partner_roster_id != 2 for p in props)


def test_min_side_gain_filters(two_team_league):
    model, teams = two_team_league
    strict = find_mutual_trades(model, my_roster_id=2, teams_assets=teams,
                                top=50, min_side_gain=10_000)
    assert strict == []


def test_ranking_prefers_higher_mutual_gain(two_team_league):
    model, teams = two_team_league
    props = find_mutual_trades(model, my_roster_id=2, teams_assets=teams, top=10)
    scores = [p.mutual_gain * (0.5 + p.acceptance) for p in props]
    assert scores == sorted(scores, reverse=True)


def test_rationale_names_both_windows(two_team_league):
    model, teams = two_team_league
    me = model.teams[2]
    partner = model.teams[1]
    r = build_rationale(me, partner, [P("X", "WR", 6000, 30)],
                        [PICK("pk", 6000)], 500, 400)
    assert me.archetype in r and partner.archetype in r
    assert "Contender Carl" in r
