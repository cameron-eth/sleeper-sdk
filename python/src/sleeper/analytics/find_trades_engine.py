"""Pure scoring logic behind `find-trades`.

The CLI command is the orchestrator (fetch rosters, build chip lists,
filter, render). This module owns the *scoring* — computing whether a
candidate package is fair after the KTC value adjustment.

This is the math that had the May 2026 sign bug (premium was applied as
a credit instead of a debit). Pulling it out here means there's exactly
one implementation, unit-testable in isolation, used by find-trades and
any future trade-scoring command.
"""
from __future__ import annotations

from dataclasses import dataclass

from sleeper.analytics.value_adjustment import (
    ValueAdjustment,
    compute_value_adjustment,
)


@dataclass
class PackageScore:
    """Result of scoring a candidate trade package against a single target."""

    raw_overpay: int            # send_total - target_ktc (positive = you overpay raw)
    adjusted_overpay: int       # raw overpay corrected for stud premium / consolidation
    adjustment: ValueAdjustment  # full value adjustment record (favors, tier, rationale)


def package_overpay(
    send_values: list[int],
    target_ktc: int,
) -> PackageScore:
    """Score a "send these chips, get the target" package.

    The adjusted overpay is the canonical "fairness" number: it represents
    how much YOU are overpaying after the stud-side premium is accounted
    for. Positive values mean you're overpaying; negative means you'd be
    landing the target at a discount that the algorithm thinks is unfair
    (i.e. it would never transact at face KTC alone).

    Sign convention (this is the bit that had the May 2026 bug):

    * If the target is a stud and `favors == "receive"` (you're consolidating
      filler chips to acquire one stud), you owe the stud premium ON TOP OF
      face value. In overpay space: `adjusted = raw - premium`.
    * If you're sending the stud and `favors == "send"` (the partner is
      consolidating to acquire your stud), the partner owes YOU the
      premium. In overpay space: `adjusted = raw + premium`.
    * Even-spot trades (1-for-1) get no adjustment.

    Args:
        send_values: KTC values of chips leaving your side.
        target_ktc: KTC value of the single target you're trying to land.

    Returns:
        PackageScore with raw + adjusted overpay and the full adjustment
        record (so callers can render the rationale).
    """
    raw = sum(send_values) - target_ktc
    adj = compute_value_adjustment(
        send_values=send_values,
        receive_values=[target_ktc],
    )
    if adj.favors == "receive":
        adjusted = raw - adj.adjustment
    elif adj.favors == "send":
        adjusted = raw + adj.adjustment
    else:
        adjusted = raw
    return PackageScore(
        raw_overpay=raw,
        adjusted_overpay=adjusted,
        adjustment=adj,
    )


def is_fair_overpay(
    score: PackageScore,
    *,
    min_overpay: int,
    max_overpay: int,
) -> bool:
    """True if the adjusted overpay falls in the caller's "fair" band.

    `find-trades` mode defaults:
        normal:       +300  to +3,500   (you slightly overpay, healthy stud-tax)
        upgrade:     -5,000 to     0    (you net positive value)
        downtiering:   +300 to +5,000   (you give away depreciating talent)
    """
    return min_overpay <= score.adjusted_overpay <= max_overpay
