"""Tests for analytics.pick_ownership (L1 — pick inventory + slot projection)."""
from __future__ import annotations

import pytest

from sleeper.analytics.base_value import PickChart
from sleeper.analytics.pick_ownership import (
    PickSlot,
    pick_assets_for_roster,
    pick_inventory,
    project_slot_tiers,
)


@pytest.fixture
def chart():
    recs = []
    for season, base in (("2027", 0), ("2028", -500)):
        for rnd, v in ((1, 7000), (2, 3800)):
            for tier, adj in (("Early", 1200), ("Mid", 0), ("Late", -800)):
                recs.append({
                    "position": "RDP",
                    "name": f"{season} {tier} {'1st' if rnd == 1 else '2nd'}",
                    "sf_value": v + adj + base,
                    "oqb_value": v + adj + base,
                })
    return PickChart.from_ktc_records(recs)


# ---------------------------------------------------------------------------
# Slot projection
# ---------------------------------------------------------------------------

def test_weakest_teams_project_to_early_picks():
    strength = {1: 10_000, 2: 20_000, 3: 30_000, 4: 40_000, 5: 50_000, 6: 60_000}
    tiers = project_slot_tiers(strength)
    assert tiers[1] == "Early"    # weakest -> most valuable pick
    assert tiers[6] == "Late"     # strongest -> least valuable
    assert set(tiers.values()) == {"Early", "Mid", "Late"}


def test_slot_projection_empty():
    assert project_slot_tiers({}) == {}


def test_slot_projection_is_deterministic_on_ties():
    tiers = project_slot_tiers({1: 100, 2: 100, 3: 100})
    assert tiers[1] == "Early"    # ties break by roster_id


# ---------------------------------------------------------------------------
# Ownership reconstruction
# ---------------------------------------------------------------------------

def test_default_ownership_everyone_owns_their_own():
    inv = pick_inventory([1, 2], traded_picks=[], seasons=["2027"], draft_rounds=2)
    assert len(inv[1]) == 2 and len(inv[2]) == 2
    assert all(s.original_roster_id == 1 for s in inv[1])


def test_traded_pick_moves_to_new_owner():
    tp = [{"season": "2027", "round": 1, "roster_id": 1,
           "previous_owner_id": 1, "owner_id": 2}]
    inv = pick_inventory([1, 2], tp, seasons=["2027"], draft_rounds=2)
    # roster 1 lost its 1st; roster 2 gained it (and still has its own)
    assert not any(s.rnd == 1 and s.original_roster_id == 1 for s in inv[1])
    got = [s for s in inv[2] if s.original_roster_id == 1 and s.rnd == 1]
    assert len(got) == 1
    assert got[0].owner_roster_id == 2
    assert len(inv[2]) == 3


def test_malformed_traded_pick_is_skipped():
    tp = [{"season": "2027", "round": None, "roster_id": 1, "owner_id": 2}]
    inv = pick_inventory([1, 2], tp, seasons=["2027"], draft_rounds=1)
    assert len(inv[1]) == 1 and len(inv[2]) == 1


def test_out_of_range_traded_pick_ignored():
    """A pick for a season we're not modeling doesn't invent inventory."""
    tp = [{"season": "2035", "round": 1, "roster_id": 1, "owner_id": 2}]
    inv = pick_inventory([1, 2], tp, seasons=["2027"], draft_rounds=1)
    assert sum(len(v) for v in inv.values()) == 2


# ---------------------------------------------------------------------------
# Valuation with projected tiers
# ---------------------------------------------------------------------------

def test_pick_from_weak_team_is_worth_more(chart):
    tiers = {1: "Early", 2: "Late"}
    from_weak = pick_assets_for_roster(
        [PickSlot("2027", 1, original_roster_id=1, owner_roster_id=3)], chart, tiers)
    from_strong = pick_assets_for_roster(
        [PickSlot("2027", 1, original_roster_id=2, owner_roster_id=3)], chart, tiers)
    assert from_weak[0].base_sf > from_strong[0].base_sf


def test_unknown_season_picks_are_dropped_not_zero_valued(chart):
    out = pick_assets_for_roster(
        [PickSlot("2099", 1, original_roster_id=1, owner_roster_id=1)],
        chart, {1: "Mid"})
    assert out == []


def test_acquired_pick_is_labeled_with_source(chart):
    out = pick_assets_for_roster(
        [PickSlot("2027", 1, original_roster_id=1, owner_roster_id=2)],
        chart, {1: "Early"}, owners={1: "Rebuild Rick"})
    assert "via Rebuild Rick" in out[0].name


def test_own_pick_not_labeled(chart):
    out = pick_assets_for_roster(
        [PickSlot("2027", 1, original_roster_id=2, owner_roster_id=2)],
        chart, {2: "Mid"}, owners={2: "Me"})
    assert "via" not in out[0].name
