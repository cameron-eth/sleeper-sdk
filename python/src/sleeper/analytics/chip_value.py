"""Chip-value adjustments — discounts that translate a player's face KTC
into the value they actually return as a trade chip.

The biggest gap between face KTC and chip value is the **aging QB curve**:
in Superflex leagues, a 31-year-old QB carries a high face value but
doesn't transact at face value when packaged with a young WR/RB to land
an elite stud. The market discount tracks closely with age tiers.

Tuned 2026-05 against The Meat Market trade history; ratios reproduce
the observed "needs 1st + 2nd to balance" gut-check on Fannin + Mayfield
→ JSN trades.
"""
from __future__ import annotations


# Age tier thresholds — ages at or above these get the corresponding discount.
QB_AGE_DISCOUNT_TIERS = (
    (32, 0.45),   # severely depreciated; effectively a roster filler
    (30, 0.55),   # depreciated; transacts well below face
    (28, 0.75),   # mild discount; still useful but not at face
)


def apply_qb_age_discount(face_ktc: int, age: int) -> int:
    """Return the chip value of a QB after the aging-QB market discount.

    Non-QBs and QBs under the youngest tier (28) return face value unchanged.
    Discount tiers are applied in descending age order — the OLDEST applicable
    tier wins. Result is rounded down (int truncation) to match the runtime
    behavior in cli.trades.cmd_find_trades.

    Args:
        face_ktc: KTC's listed superflex value for the player.
        age: Player age in years.

    Returns:
        Chip-equivalent KTC value, ≥ 0.

    Examples:
        >>> apply_qb_age_discount(4709, 31)   # Mayfield-shape
        2589
        >>> apply_qb_age_discount(4128, 28)   # Kyler-shape
        3096
        >>> apply_qb_age_discount(7500, 24)   # Jayden Daniels — no discount
        7500
    """
    if face_ktc <= 0:
        return 0
    for threshold_age, multiplier in QB_AGE_DISCOUNT_TIERS:
        if age >= threshold_age:
            return int(face_ktc * multiplier)
    return face_ktc
