"""Tests for analytics.base_value (L0 — context-free base value).

The defining property of L0 is that it does NOT age-adjust: base value is
raw KTC (format-selected) for players and a slot-tiered chart value for
picks, with age carried only as metadata. These tests lock that in.
"""
from __future__ import annotations

import pytest

from sleeper.analytics.base_value import (
    Asset,
    PickChart,
    asset_from_ktc_dict,
    normalize_age,
    player_asset,
)


# ---------------------------------------------------------------------------
# normalize_age — sentinel handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (-1, None), (0, None), (-1.0, None),
    (23.4, 23.4), (29, 29.0),
    (None, None), ("nan-ish", None), ("", None),
])
def test_normalize_age(raw, expected):
    assert normalize_age(raw) == expected


# ---------------------------------------------------------------------------
# The L0 invariant: base value is raw KTC, age changes nothing
# ---------------------------------------------------------------------------

def test_base_value_is_raw_ktc_not_age_adjusted():
    """Two players with identical KTC but very different ages get the SAME
    base value. Age must not enter L0's number."""
    young = player_asset(ktc_id="1", name="Young", position="RB",
                         sf_value=6000, oqb_value=5500, age=22)
    old = player_asset(ktc_id="2", name="Old", position="RB",
                       sf_value=6000, oqb_value=5500, age=30)
    assert young.base_value("sf") == old.base_value("sf") == 6000
    assert young.base_value("1qb") == old.base_value("1qb") == 5500


def test_base_value_format_selection():
    a = player_asset(ktc_id="1", name="X", position="QB",
                     sf_value=8000, oqb_value=6000, age=25)
    assert a.base_value("sf") == 8000
    assert a.base_value("1qb") == 6000
    assert a.base_value() == 8000  # default is sf


def test_player_asset_normalizes_sentinel_age():
    a = player_asset(ktc_id="1", name="Rook", position="WR",
                     sf_value=3000, oqb_value=2800, age=-1)
    assert a.age is None
    assert a.base_value("sf") == 3000  # unknown age doesn't zero the value


def test_with_age_backfills_missing_age():
    """KTC omits age for many rookies; Sleeper's age backfills it so young
    assets aren't treated as prime-aged/neutral by the layers above."""
    rookie = player_asset(ktc_id="1", name="Rook", position="TE",
                          sf_value=2000, oqb_value=2000, age=-1)
    assert rookie.age is None
    filled = rookie.with_age(22)
    assert filled.age == 22.0
    assert filled.base_value("sf") == 2000  # value untouched


def test_with_age_never_overwrites_existing():
    a = player_asset(ktc_id="1", name="X", position="WR",
                     sf_value=5000, oqb_value=5000, age=24.4)
    assert a.with_age(30).age == pytest.approx(24.4)


def test_with_age_ignores_sentinels_and_returns_self():
    a = player_asset(ktc_id="1", name="X", position="WR",
                     sf_value=5000, oqb_value=5000, age=-1)
    assert a.with_age(-1).age is None
    assert a.with_age(None).age is None


def test_asset_position_uppercased():
    a = player_asset(ktc_id="1", name="X", position="rb",
                     sf_value=1, oqb_value=1)
    assert a.position == "RB"


# ---------------------------------------------------------------------------
# asset_from_ktc_dict — parsing the snapshot shape
# ---------------------------------------------------------------------------

def test_asset_from_ktc_dict_player():
    rec = {
        "ktc_id": "1", "name": "Saquon Barkley", "position": "RB",
        "team": "PHI", "age": 29.4,
        "sf_value": 5024, "sf_rank": 59, "sf_pos_rank": 15,
        "oqb_value": 5757, "oqb_rank": 47, "oqb_pos_rank": 15,
    }
    a = asset_from_ktc_dict(rec)
    assert a.kind == "player"
    assert a.position == "RB"
    assert a.age == pytest.approx(29.4)
    assert a.base_value("sf") == 5024
    assert a.base_value("1qb") == 5757
    assert a.pos_rank_sf == 15


def test_asset_from_ktc_dict_pick():
    rec = {"ktc_id": "9001", "name": "2027 Mid 1st", "position": "RDP",
           "sf_value": 5610, "oqb_value": 6091}
    a = asset_from_ktc_dict(rec)
    assert a.is_pick
    assert a.position == "PICK"
    assert a.season == "2027"
    assert a.rnd == 1
    assert a.tier == "Mid"
    assert a.base_value("sf") == 5610
    assert a.base_value("1qb") == 6091


# ---------------------------------------------------------------------------
# PickChart
# ---------------------------------------------------------------------------

@pytest.fixture
def chart():
    records = [
        {"position": "RDP", "name": "2027 Early 1st", "sf_value": 7123, "oqb_value": 7384},
        {"position": "RDP", "name": "2027 Mid 1st",   "sf_value": 5610, "oqb_value": 6091},
        {"position": "RDP", "name": "2027 Late 1st",  "sf_value": 4857, "oqb_value": 5566},
        {"position": "RDP", "name": "2027 Mid 2nd",   "sf_value": 3417, "oqb_value": 4157},
        {"position": "RB",  "name": "Not A Pick",     "sf_value": 9999, "oqb_value": 9999},
    ]
    return PickChart.from_ktc_records(records)


def test_pick_chart_builds_only_from_rdp(chart):
    assert len(chart) == 4  # the RB row is excluded


def test_pick_chart_exact_lookup(chart):
    assert chart.value("2027", 1, "Early", "sf") == 7123
    assert chart.value("2027", 1, "Mid", "1qb") == 6091
    assert chart.value("2027", 1, "Late", "sf") == 4857


def test_pick_chart_tier_ordering_makes_sense(chart):
    early = chart.value("2027", 1, "Early", "sf")
    mid = chart.value("2027", 1, "Mid", "sf")
    late = chart.value("2027", 1, "Late", "sf")
    assert early > mid > late  # earlier slot worth more


def test_pick_chart_tier_fallback_when_missing(chart):
    # 2027 2nd only has a Mid entry; requesting Early falls back to Mid.
    assert chart.value("2027", 2, "Early", "sf") == 3417
    assert chart.value("2027", 2, "Late", "1qb") == 4157


def test_pick_chart_unknown_returns_zero(chart):
    assert chart.value("2099", 1, "Mid", "sf") == 0
    assert chart.value("2027", 7, "Mid", "sf") == 0


def test_pick_asset_construction(chart):
    a = chart.pick_asset("2027", 1, "Mid")
    assert a.is_pick
    assert a.name == "2027 Mid 1st"
    assert a.base_value("sf") == 5610
    assert a.base_value("1qb") == 6091
    assert a.season == "2027" and a.rnd == 1 and a.tier == "Mid"
