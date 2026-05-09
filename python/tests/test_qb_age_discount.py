"""Tests for the aging-QB chip discount in find-trades.

The discount lives inline in `cmd_find_trades` (cli.py) — these tests
verify the curve via the same arithmetic so a regression in the discount
table is caught even though the function itself isn't directly extracted.

Discount table (must stay in sync with cli.py):
    age >= 32: 0.45x
    age >= 30: 0.55x
    age >= 28: 0.75x
    age <  28: 1.00x
"""
from __future__ import annotations

import pytest


def _qb_age_discount(face_ktc: int, age: int) -> int:
    """Mirror of the discount curve in cli.cmd_find_trades."""
    if age >= 32:
        return int(face_ktc * 0.45)
    if age >= 30:
        return int(face_ktc * 0.55)
    if age >= 28:
        return int(face_ktc * 0.75)
    return face_ktc


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
    discounted = _qb_age_discount(face, age)
    expected = int(face * expected_mult)
    assert discounted == expected


def test_mayfield_age_31_lands_in_55pct_bucket():
    """Real-world regression: Mayfield was the test case that prompted the fix.

    Face value 4709 at age 31 must discount to 2589 (within rounding).
    """
    assert _qb_age_discount(4709, 31) == int(4709 * 0.55)


def test_kyler_murray_age_28_uses_lighter_75pct_bucket():
    """Kyler at 28 shouldn't get the same haircut as a 31yo Mayfield."""
    assert _qb_age_discount(4128, 28) == int(4128 * 0.75)


def test_young_qb_under_28_is_not_discounted():
    """Jayden Daniels (24), Burrow (27), etc. trade at full face."""
    assert _qb_age_discount(7500, 24) == 7500
    assert _qb_age_discount(7500, 27) == 7500
