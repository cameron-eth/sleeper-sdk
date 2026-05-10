"""Tests for the aging-QB chip discount in analytics.chip_value.

This was previously a shadow function mirroring the inline cli.py logic.
After the May 2026 refactor, the discount lives in
`sleeper.analytics.chip_value.apply_qb_age_discount` and these tests
exercise the real implementation.

Discount table (must stay in sync with cli/trades.py callsite):
    age >= 32: 0.45x
    age >= 30: 0.55x
    age >= 28: 0.75x
    age <  28: 1.00x
"""
from __future__ import annotations

import pytest

from sleeper.analytics.chip_value import apply_qb_age_discount


@pytest.mark.parametrize("age,expected_mult", [
    (24, 1.00),
    (27, 1.00),
    (28, 0.75),
    (29, 0.75),
    (30, 0.55),
    (31, 0.55),
    (32, 0.45),
    (38, 0.45),
])
def test_age_discount_curve(age: int, expected_mult: float):
    face = 4709
    discounted = apply_qb_age_discount(face, age)
    expected = int(face * expected_mult)
    assert discounted == expected


def test_mayfield_age_31_lands_in_55pct_bucket():
    """Real-world regression: Mayfield was the test case that prompted the fix.

    Face value 4709 at age 31 must discount to 2589 (within rounding).
    """
    assert apply_qb_age_discount(4709, 31) == int(4709 * 0.55)


def test_kyler_murray_age_28_uses_lighter_75pct_bucket():
    """Kyler at 28 shouldn't get the same haircut as a 31yo Mayfield."""
    assert apply_qb_age_discount(4128, 28) == int(4128 * 0.75)


def test_young_qb_under_28_is_not_discounted():
    """Jayden Daniels (24), Burrow (27), etc. trade at full face."""
    assert apply_qb_age_discount(7500, 24) == 7500
    assert apply_qb_age_discount(7500, 27) == 7500


def test_zero_face_value_returns_zero():
    """Defensive: a player with no KTC value (rookie/IR) returns 0 regardless of age."""
    assert apply_qb_age_discount(0, 31) == 0


def test_negative_face_value_returns_zero():
    """Defensive: negative inputs collapse to 0 instead of inverting."""
    assert apply_qb_age_discount(-100, 28) == 0


def test_oldest_tier_wins_when_player_overlaps():
    """Boundary check: at the threshold, the older bucket wins."""
    # age 32 is in BOTH the 32+ bucket (0.45) and the 30+ bucket (0.55).
    # The 32+ bucket should win because it's checked first (oldest first).
    assert apply_qb_age_discount(1000, 32) == 450  # 0.45x, not 0.55x
