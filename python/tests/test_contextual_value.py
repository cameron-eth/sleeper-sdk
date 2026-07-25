"""Tests for analytics.contextual_value (L2 — window-relative value).

The defining property: the SAME asset is worth different amounts to teams in
different windows, and the direction of that difference is the trade thesis
(contenders pay up for now-assets, rebuilders for future-assets).
"""
from __future__ import annotations

import pytest

from sleeper.analytics.base_value import Asset
from sleeper.analytics.contextual_value import (
    ALPHA,
    MULT_MAX,
    MULT_MIN,
    POSITION_PRIME_AGE,
    contextual_value,
    horizon_score,
    window_multiplier,
)


def P(pos, sf, age):
    return Asset(kind="player", id=pos + str(age), name=f"{pos}{age}",
                 position=pos, base_sf=sf, base_1qb=sf, age=age)


def PICK(sf=5000):
    return Asset(kind="pick", id="pk", name="2027 Mid 1st", position="PICK",
                 base_sf=sf, base_1qb=sf)


# ---------------------------------------------------------------------------
# horizon_score
# ---------------------------------------------------------------------------

def test_pick_is_pure_future():
    assert horizon_score(PICK()) == 1.0


def test_unknown_age_is_neutral():
    a = Asset(kind="player", id="x", name="x", position="WR",
              base_sf=5000, base_1qb=5000, age=None)
    assert horizon_score(a) == 0.0


def test_young_is_future_old_is_now():
    assert horizon_score(P("WR", 5000, 22)) > 0
    assert horizon_score(P("WR", 5000, 31)) < 0


def test_horizon_at_prime_is_zero():
    for pos, prime in POSITION_PRIME_AGE.items():
        assert horizon_score(P(pos, 5000, prime)) == pytest.approx(0.0)


def test_position_specific_prime_rb_ages_first():
    """At the same age 27, an RB is further past prime than a QB — the RB
    cliff enters here without a fragile full age curve."""
    rb = horizon_score(P("RB", 5000, 27))
    qb = horizon_score(P("QB", 5000, 27))
    assert rb < qb
    assert rb < 0 < qb


def test_horizon_is_bounded():
    assert horizon_score(P("RB", 5000, 45)) == -1.0
    assert horizon_score(P("QB", 5000, 18)) == 1.0


# ---------------------------------------------------------------------------
# window_multiplier — the sign table
# ---------------------------------------------------------------------------

def test_contender_pays_up_for_now_assets():
    aging = P("RB", 5000, 30)
    assert window_multiplier(aging, window=1.0) > 1.0


def test_contender_discounts_future_assets():
    assert window_multiplier(PICK(), window=1.0) < 1.0
    assert window_multiplier(P("WR", 5000, 21), window=1.0) < 1.0


def test_rebuilder_pays_up_for_future_assets():
    assert window_multiplier(PICK(), window=-1.0) > 1.0
    assert window_multiplier(P("WR", 5000, 21), window=-1.0) > 1.0


def test_rebuilder_discounts_aging_producers():
    assert window_multiplier(P("RB", 5000, 30), window=-1.0) < 1.0


def test_neutral_window_is_market_price():
    for a in (PICK(), P("RB", 5000, 30), P("WR", 5000, 21)):
        assert window_multiplier(a, window=0.0) == 1.0


def test_multiplier_is_clamped():
    extreme_old = P("RB", 5000, 60)
    extreme_young = PICK()
    for w in (-1.0, 1.0):
        for a in (extreme_old, extreme_young):
            m = window_multiplier(a, w)
            assert MULT_MIN <= m <= MULT_MAX


def test_multiplier_formula_matches_doc():
    a = P("RB", 5000, 30)
    w = 0.5
    expected = 1.0 - ALPHA * w * horizon_score(a)
    assert window_multiplier(a, w) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# contextual_value — the keystone disagreement
# ---------------------------------------------------------------------------

def test_same_asset_different_value_by_window():
    """THE point of L2: one asset, two holders, two numbers."""
    aging_rb = P("RB", 6000, 30)
    to_contender = contextual_value(aging_rb, window=0.9)
    to_rebuilder = contextual_value(aging_rb, window=-0.9)
    assert to_contender > 6000 > to_rebuilder


def test_pick_valuation_inverts_by_window():
    pick = PICK(5000)
    assert contextual_value(pick, window=-0.9) > 5000
    assert contextual_value(pick, window=0.9) < 5000


def test_trade_thesis_gap_is_mutual_gain():
    """A contender and rebuilder swapping an aging producer for a pick BOTH
    gain in their own contextual terms — the philosophical soundness test."""
    aging = P("WR", 6000, 30)
    pick = PICK(6000)
    c_win, r_win = 0.9, -0.9
    # Contender gives pick, gets aging producer
    c_gain = contextual_value(aging, c_win) - contextual_value(pick, c_win)
    # Rebuilder gives aging producer, gets pick
    r_gain = contextual_value(pick, r_win) - contextual_value(aging, r_win)
    assert c_gain > 0 and r_gain > 0


def test_contextual_value_respects_format():
    a = Asset(kind="player", id="q", name="q", position="QB",
              base_sf=8000, base_1qb=6000, age=24)
    assert contextual_value(a, 0.0, "sf") == 8000
    assert contextual_value(a, 0.0, "1qb") == 6000
